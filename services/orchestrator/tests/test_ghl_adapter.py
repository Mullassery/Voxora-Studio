"""Contract tests for the LeadConnector (GoHighLevel) CRM adapter.

Contracts read from marketplace.gohighlevel.com (HighLevel API docs,
Version header 2021-07-28, retrieved 2026-11-12):
- Bearer auth + Version header: Update Contact / Get Contacts pages
- Contact schema: firstName/lastName/email/phone/timezone/dateAdded,
  dnd boolean, dndSettings per channel with status active|inactive|permanent
- Pagination: id cursor (startAfterId) on the contacts list endpoint
  (list endpoint deprecated in favor of contacts/search; noted honestly)

Capability honesty: GHL documents no consent object → consent_read False,
declared rather than simulated. Call suppression IS documented (dndSettings).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from voxora.crm.base import CrmError, NotSupportedError
from voxora.crm.http import HttpResponse
from voxora.crm.leadconnector import LeadConnectorCrmAdapter

CONTACTS_PAGE_1 = [
    {
        "id": "c1",
        "locationId": "loc1",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "email": "ada@example.com",
        "phone": "+1 555 123 0001",
        "timezone": "America/Chicago",
        "dateAdded": "2026-01-01T00:00:00.000Z",
        "dnd": False,
        "dndSettings": {"Call": {"status": "inactive", "message": ""}},
    },
    {
        "id": "c2",
        "locationId": "loc1",
        "firstName": "Grace",
        "lastName": "Hopper",
        "email": "grace@example.com",
        "phone": "+1 555 123 0002",
        "timezone": "America/New_York",
        "dateAdded": "2026-01-02T00:00:00.000Z",
        "dnd": True,
        "dndSettings": {"Call": {"status": "active", "message": "Do not call me"}},
    },
]
CONTACTS_PAGE_2 = [
    {
        "id": "c3",
        "locationId": "loc1",
        "firstName": "Edsger",
        "lastName": "Dijkstra",
        "email": "edsger@example.com",
        "phone": "+31 20 555 0003",
        "timezone": "Europe/Amsterdam",
        "dateAdded": "2026-01-03T00:00:00.000Z",
        "dnd": True,
        "dndSettings": {"Call": {"status": "permanent", "message": ""}},
    },
]
BASE = "https://services.leadconnectorhq.com"


class FakeLeadConnectorTransport:
    def __init__(self):
        self.requests: List[Dict[str, Any]] = []
        self.pages: List[HttpResponse] = []

    def request(self, method, url, headers, body, timeout):
        self.requests.append(
            {"method": method, "url": url, "headers": headers,
             "body": json.loads(body.decode()) if body else None}
        )
        if self.pages:
            return self.pages.pop(0)
        status, payload = self.default(url)
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode())

    def default(self, url) -> tuple:
        path, _, query = url.partition("?")
        if path == f"{BASE}/contacts/":
            from urllib.parse import parse_qs

            params = parse_qs(query)
            page = 1 if "startAfterId" not in params else 2
            contacts = CONTACTS_PAGE_1 if page == 1 else CONTACTS_PAGE_2
            return 200, {"contacts": contacts, "count": 3}
        if path.startswith(f"{BASE}/contacts/"):
            contact_id = path.rsplit("/", 1)[-1]
            for contact in CONTACTS_PAGE_1 + CONTACTS_PAGE_2:
                if contact["id"] == contact_id:
                    return 200, {"contact": contact}
            return 404, {"success": False, "message": "contact not found"}
        return 500, {"success": False, "message": "unexpected"}


def _adapter(transport: Optional[FakeLeadConnectorTransport] = None) -> LeadConnectorCrmAdapter:
    return LeadConnectorCrmAdapter(
        base_url=BASE,
        api_token="pit-secret",
        location_id="loc1",
        transport=transport or FakeLeadConnectorTransport(),
    )


def test_capabilities_are_honest() -> None:
    capabilities = LeadConnectorCrmAdapter.capabilities().as_dict()
    assert capabilities["suppressionRead"] is True
    assert capabilities["consentRead"] is False  # no documented consent object
    assert capabilities["contactList"] is True
    assert capabilities["contactGet"] is True
    assert capabilities["webhookIngest"] is False
    assert capabilities["cursorPagination"] is True


def test_requests_carry_auth_and_version_header() -> None:
    transport = FakeLeadConnectorTransport()
    adapter = _adapter(transport)
    adapter.list_contacts(limit=2)
    request = transport.requests[0]
    assert request["headers"]["Authorization"] == "Bearer pit-secret"
    assert request["headers"]["Version"] == "2021-07-28"
    assert "locationId=loc1" in request["url"]


def test_connection_success_and_auth_failure() -> None:
    transport = FakeLeadConnectorTransport()
    adapter = _adapter(transport)
    result = adapter.test_connection()
    assert result.ok is True

    transport.pages.append(HttpResponse(401, {"content-type": "application/json"}, b'{"success": false, "message": "unauthorized"}'))
    failed = _adapter(transport).test_connection()
    assert failed.ok is False
    assert failed.details["status"] == 401


def test_list_contacts_maps_and_pages_by_id_cursor() -> None:
    transport = FakeLeadConnectorTransport()
    adapter = _adapter(transport)
    page_1 = adapter.list_contacts(limit=2, offset=0)
    assert [c.external_id for c in page_1.contacts] == ["c1", "c2"]
    first = page_1.contacts[0]
    assert first.first_name == "Ada"
    assert first.phone == "+15551230001"  # spaces stripped
    assert first.suppressed is False
    assert page_1.contacts[1].suppressed is True  # dndSettings.Call active
    assert page_1.has_next is True

    page_2 = adapter.list_contacts(limit=2, offset=2)
    assert [c.external_id for c in page_2.contacts] == ["c3"]
    assert page_2.contacts[0].suppressed is True  # permanent call DND
    assert page_2.has_next is False
    second_request = transport.requests[1]
    assert "startAfterId=c2" in second_request["url"]  # cursor from page 1


def test_get_contact_and_suppression_states() -> None:
    transport = FakeLeadConnectorTransport()
    adapter = _adapter(transport)
    contact = adapter.get_contact("c2")
    assert contact.external_id == "c2"

    suppression = adapter.get_suppression("c2")
    assert suppression == {"state": "do_not_call", "reason": "Do not call me"}
    assert adapter.get_suppression("c1") == {"state": "clear"}

    with pytest.raises(NotSupportedError):
        adapter.get_consent("c1")


def test_connection_test_reports_bad_location() -> None:
    transport = FakeLeadConnectorTransport()
    transport.pages.append(HttpResponse(404, {"content-type": "application/json"}, b'{"success": false, "message": "not found"}'))
    result = _adapter(transport).test_connection()
    assert result.ok is False
    assert result.details["status"] == 404


def test_sync_connection_applies_ghl_suppression(database) -> None:
    """Adapter-portability proof: sync engine consumes the GHL adapter end-to-end."""
    from voxora.crm.sync import sync_connection

    database.ensure_tenant("t1", "Tenant")
    adapter = _adapter()
    connection = database.create_connection(
        "t1", "leadconnector", "GHL", BASE,
        api_key_encrypted="encrypted", webhook_secret_encrypted=None,
        capabilities=adapter.capabilities().as_dict(),
        config={"location_id": "loc1"},
    )
    result = sync_connection(
        database, adapter, "t1", connection["id"], limit=2, max_pages=5
    )
    assert result["imported"] == 3
    assert result["suppressionApplied"] == 2  # c2 active + c3 permanent
    assert result["consentApplied"] == 0  # capability-honest: GHL has no consent object
    suppressed = [
        database.get_contact_by_external("t1", "leadconnector", external_id)
        for external_id in ("c2", "c3")
    ]
    assert all(contact["suppressed"] for contact in suppressed)
    not_suppressed = database.get_contact_by_external("t1", "leadconnector", "c1")
    assert not not_suppressed["suppressed"]


def test_registry_builds_the_adapter() -> None:
    from voxora.crm import build_crm_adapter, provider_catalog
    from voxora.security import SecretBox

    providers = {entry["id"]: entry for entry in provider_catalog()}
    assert "leadconnector" in providers
    assert providers["leadconnector"]["verifiedAgainst"]

    secret_box = SecretBox("unit-test-secret-key-must-be-32-chars")
    connection = {
        "provider": "leadconnector",
        "base_url": BASE,
        "api_key_encrypted": secret_box.encrypt("pit-secret"),
        "config": {"location_id": "loc1"},
    }
    adapter = build_crm_adapter(connection, secret_box)
    assert isinstance(adapter, LeadConnectorCrmAdapter)
    assert adapter.location_id == "loc1"

    with pytest.raises(ValueError):
        build_crm_adapter({**connection, "provider": "unknown"}, secret_box)

    # a LeadConnector connection without location_id fails loudly, never silently
    with pytest.raises(CrmError):
        build_crm_adapter({**connection, "config": {}}, secret_box)
