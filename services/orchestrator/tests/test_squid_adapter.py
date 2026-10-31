"""Contract tests for the verified Squid CRM adapter using a scripted transport."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from voxora.crm.base import CrmError
from voxora.crm.http import HttpResponse
from voxora.crm.squid import SquidCrmAdapter

PEOPLE = [
    {
        "id": "1f0a3a86-6a1a-4f3a-9a35-0f0b6e1a2b3c",
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-02T00:00:00.000Z",
        "name": {"firstName": "Ada", "lastName": "Lovelace"},
        "emails": {"primaryEmail": "ada@example.com"},
        "phones": {
            "primaryPhoneNumber": "5551230001",
            "primaryPhoneCallingCode": "+1",
            "primaryPhoneCountryCode": "US",
            "additionalPhones": [],
        },
        "jobTitle": "Mathematician",
        "companyId": None,
    },
    {
        "id": "2b9e4f0d-8c0b-4f8a-9f21-1a2b3c4d5e6f",
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-03T00:00:00.000Z",
        "name": {"firstName": "Grace", "lastName": "Hopper"},
        "emails": {"primaryEmail": "grace@example.com"},
        "phones": {"primaryPhoneNumber": "7900123456", "primaryPhoneCallingCode": "+44", "primaryPhoneCountryCode": "GB", "additionalPhones": []},
        "jobTitle": "Admiral",
        "companyId": None,
    },
]
BASE = "http://127.0.0.1:3000"


class FakeSquidTransport:
    def __init__(self):
        self.requests: List[Dict[str, Any]] = []
        self.responses: List[HttpResponse] = []

    def request(self, method, url, headers, body, timeout):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body.decode()) if body else None,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return self.default(method, url, headers, body)

    def default(self, method, url, headers, body):
        path = url.split("?")[0]
        if path.endswith("/rest/people") and method == "GET":
            payload = json.dumps(
                {
                    "data": {"people": PEOPLE},
                    "totalCount": len(PEOPLE),
                    "pageInfo": {"startCursor": None, "endCursor": None, "hasNextPage": False, "hasPreviousPage": False},
                }
            ).encode()
            return HttpResponse(200, {"content-type": "application/json"}, payload)
        if path.endswith("/rest/people") and method == "POST":
            return HttpResponse(201, {"content-type": "application/json"}, json.dumps({"data": {"createPerson": {**PEOPLE[0], "id": "5c1f-uuid"}}}).encode())
        if path.startswith(f"{BASE}/rest/people/") and method == "GET":
            person_id = path.rsplit("/", 1)[-1]
            source = next(
                (p for p in PEOPLE if p["id"] == person_id),
                {
                    "id": person_id,
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                    "name": {"firstName": "Round", "lastName": "Trip"},
                    "emails": {"primaryEmail": "round@example.com"},
                    "phones": {"primaryPhoneNumber": "5551237777", "primaryPhoneCallingCode": "+1", "primaryPhoneCountryCode": "US", "additionalPhones": []},
                    "jobTitle": None,
                    "companyId": None,
                },
            )
            return HttpResponse(200, {"content-type": "application/json"}, json.dumps({"data": {"person": source}}).encode())
        if path.startswith(f"{BASE}/rest/people/") and method == "PATCH":
            person_id = path[len(f"{BASE}/rest/people/"):]
            return HttpResponse(200, {"content-type": "application/json"}, json.dumps({"data": {"updatePerson": {**PEOPLE[0], "id": person_id}}}).encode())
        return HttpResponse(404, {}, json.dumps({"statusCode": 404, "error": "NotFoundException", "messages": ["Record not found"]}).encode())


def adapter(transport: Optional[FakeSquidTransport] = None) -> SquidCrmAdapter:
    return SquidCrmAdapter(
        base_url=BASE,
        api_key="api_test_key",
        transport=transport or FakeSquidTransport(),
        timeout=1,
        max_retries=1,
        retry_backoff=0,
    )


def test_capabilities_declared() -> None:
    capabilities = adapter().capabilities().as_dict()
    assert capabilities["contactList"] is True
    assert capabilities["consentRead"] is False
    assert capabilities["callActivityWrite"] is False
    assert capabilities["suppressionRead"] is False
    assert capabilities["webhookIngest"] is True


def test_test_connection_uses_supported_data_endpoint() -> None:
    transport = FakeSquidTransport()
    result = adapter(transport).test_connection()
    assert result.ok is True
    assert transport.requests[0]["url"] == f"{BASE}/rest/people?limit=1"
    assert transport.requests[0]["headers"]["Authorization"].startswith("Bearer api_")


def test_test_connection_auth_failure_message() -> None:
    transport = FakeSquidTransport()
    transport.responses.append(HttpResponse(401, {}, json.dumps({"statusCode": 401, "messages": ["Invalid credentials"], "error": "UNAUTHORIZED"}).encode()))
    result = adapter(transport).test_connection()
    assert result.ok is False
    assert "API key" in result.message


def test_list_contacts_maps_verified_person_shape() -> None:
    page = adapter().list_contacts()
    contacts = {contact.external_id: contact for contact in page.contacts}
    assert page.has_next is False
    assert page.total == 2
    ada = contacts[PEOPLE[0]["id"]]
    assert ada.phone == "+15551230001"
    assert (ada.first_name, ada.last_name) == ("Ada", "Lovelace")
    assert ada.email == "ada@example.com"
    assert ada.consent_state == "unknown"  # Squid has no consent object
    grace = contacts[PEOPLE[1]["id"]]
    assert grace.phone == "+447900123456"


def test_create_contact_sends_idempotency_key() -> None:
    transport = FakeSquidTransport()
    created = adapter(transport).create_contact(
        type(adapter().test_connection()).__mro__[0] and __import__("voxora.crm.base", fromlist=["CanonicalContact"]).CanonicalContact(
            external_id="new",
            first_name="New",
            last_name="Person",
            phone="+15551239999",
            email="new@example.com",
        ),
        idempotency_key="key-1",
    )
    request = transport.requests[0]
    assert request["headers"]["Idempotency-Key"] == "key-1"
    assert request["body"]["name"] == {"firstName": "New", "lastName": "Person"}
    assert request["body"]["emails"] == {"primaryEmail": "new@example.com"}
    assert request["body"]["phones"]["primaryPhoneNumber"] == "5551239999"
    assert request["body"]["phones"]["primaryPhoneCallingCode"] == "+1"
    assert created.phone == "+15551230001"


def test_retry_exhaustion_raises_crm_error() -> None:
    transport = FakeSquidTransport()
    transport.responses.extend([HttpResponse(500, {}, b"{}"), HttpResponse(500, {}, b"{}")])
    with pytest.raises(CrmError) as error:
        adapter(transport).list_contacts()
    assert error.value.status == 500


def test_rate_limit_respects_retry_after_header() -> None:
    transport = FakeSquidTransport()
    transport.responses.extend(
        [
            HttpResponse(429, {"retry-after": "1"}, b"{}"),
            HttpResponse(429, {"retry-after": "1"}, b"{}"),
        ]
    )
    import time

    start = time.time()
    with pytest.raises(CrmError) as error:
        adapter(transport).list_contacts()
    elapsed = time.time() - start
    assert error.value.status == 429
    assert elapsed >= 0.8  # honored Retry-After (not the backoff default)


def test_field_discovery_marks_unsupported_capabilities() -> None:
    fields = {f.canonical_field: f for f in adapter(transport=FakeSquidTransport()).describe_fields()}
    assert fields["phone"].supported is True
    assert fields["consent_state"].supported is False
    assert fields["call_activity"].supported is False
