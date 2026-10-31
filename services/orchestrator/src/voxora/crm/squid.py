"""Verified Squid CRM adapter.

Contracts were read from Mullassery/Squid-CRM at commit 015a2e8:
- REST envelopes: docs/api.md
- Person schema: crates/squid-core/src/lib.rs
- API-key bearer auth + workspace-scoped Idempotency-Key: docs/api.md
- Webhook envelope v1 + HMAC signature: crates/squid-api/src/rest.rs

Squid has no consent, suppression, or call-activity object. Those capabilities
are declared false rather than simulated.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from ..security import parse_squid_webhook
from .base import (
    CanonicalContact,
    ConnectionTest,
    ContactPage,
    CrmAdapter,
    CrmCapabilities,
    CrmError,
    FieldInfo,
    WebhookVerification,
)
from .http import HttpResponse, HttpTransport, UrllibTransport, build_url

DEFAULT_MAPPINGS = {
    "first_name": "name.firstName",
    "last_name": "name.lastName",
    "email": "emails.primaryEmail",
    "phone": "phones.primaryPhoneNumber",
}
SUPPORTED_PATHS = {
    "name.firstName",
    "name.lastName",
    "emails.primaryEmail",
    "phones.primaryPhoneNumber",
    "phones.primaryPhoneCallingCode",
    "jobTitle",
    "companyId",
    "id",
    "updatedAt",
}
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class SquidCrmAdapter(CrmAdapter):
    provider = "squid"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: Optional[HttpTransport] = None,
        mappings: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.mappings = dict(DEFAULT_MAPPINGS)
        for mapping in mappings or []:
            canonical = mapping.get("canonical_field")
            external = mapping.get("external_field")
            if canonical in {"first_name", "last_name", "email", "phone"} and external:
                if external not in SUPPORTED_PATHS:
                    raise CrmError(f"Unsupported Squid field path: {external}")
                self.mappings[canonical] = external

    @classmethod
    def capabilities(cls) -> CrmCapabilities:
        return CrmCapabilities(
            connection_test=True,
            field_discovery=True,
            contact_list=True,
            contact_get=True,
            contact_create=True,
            contact_update=True,
            consent_read=False,
            suppression_read=False,
            call_activity_write=False,
            webhook_ingest=True,
            cursor_pagination=False,
            offset_pagination=True,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": self.capabilities().as_dict(),
        }

    def test_connection(self) -> ConnectionTest:
        # /rest/me intentionally rejects API keys (user_id is None), so use the
        # least-privilege data endpoint the adapter actually needs.
        try:
            response = self._request("GET", "/rest/people", params={"limit": 1})
        except CrmError as error:
            if error.status in {401, 403}:
                return ConnectionTest(False, "Squid rejected the API key", {"status": error.status})
            if error.status == 404:
                return ConnectionTest(False, "Squid people endpoint was not found", {"status": 404})
            return ConnectionTest(False, str(error), {"status": error.status})
        except ConnectionError as error:
            return ConnectionTest(False, f"Could not reach Squid: {error}", {})
        body = response.json() if response.body else {}
        count = len(((body or {}).get("data") or {}).get("people") or [])
        return ConnectionTest(
            True,
            "Connected to Squid and read the people endpoint",
            {
                "status": response.status,
                "sampleSize": count,
                "capabilities": self.capabilities().as_dict(),
            },
        )

    def describe_fields(self) -> List[FieldInfo]:
        return [
            FieldInfo("first_name", "name.firstName", True, True, "Squid person first name"),
            FieldInfo("last_name", "name.lastName", True, True, "Squid person last name"),
            FieldInfo("email", "emails.primaryEmail", True, False, "Squid primary email"),
            FieldInfo(
                "phone",
                "phones.primaryPhoneNumber",
                True,
                True,
                "Calling code is read from phones.primaryPhoneCallingCode",
            ),
            FieldInfo("timezone", "", False, False, "Not present on Squid person"),
            FieldInfo("locale", "", False, False, "Not present on Squid person"),
            FieldInfo("consent_state", "", False, False, "Not present on Squid person; set locally"),
            FieldInfo("suppression", "", False, False, "Use the Voxora suppression list"),
            FieldInfo("call_activity", "", False, False, "No call/activity object in Squid"),
        ]

    def list_contacts(self, limit: int = 100, offset: int = 0) -> ContactPage:
        response = self._request(
            "GET", "/rest/people", params={"limit": limit, "offset": offset}
        )
        body = response.json() if response.body else {}
        data = (body or {}).get("data") or {}
        people = data.get("people")
        if not isinstance(people, list):
            raise CrmError("Squid response did not contain data.people", status=response.status)
        page_info = (body or {}).get("pageInfo") or {}
        total = (body or {}).get("totalCount")
        has_next = bool(page_info.get("hasNextPage"))
        if not page_info and total is not None:
            has_next = offset + len(people) < int(total)
        return ContactPage(
            contacts=[self._map_person(person) for person in people],
            total=int(total) if total is not None else None,
            has_next=has_next,
        )

    def get_contact(self, external_id: str) -> CanonicalContact:
        response = self._request("GET", f"/rest/people/{external_id}")
        body = response.json() if response.body else {}
        person = ((body or {}).get("data") or {}).get("person")
        if not isinstance(person, dict):
            raise CrmError("Squid response did not contain data.person", status=response.status)
        return self._map_person(person)

    def create_contact(self, contact: CanonicalContact, idempotency_key: str) -> CanonicalContact:
        payload = self._person_payload(contact)
        response = self._request(
            "POST", "/rest/people", payload=payload, idempotency_key=idempotency_key
        )
        body = response.json() if response.body else {}
        person = ((body or {}).get("data") or {}).get("createPerson")
        if not isinstance(person, dict):
            raise CrmError("Squid response did not contain data.createPerson", status=response.status)
        return self._map_person(person)

    def update_contact(self, external_id: str, contact: CanonicalContact) -> CanonicalContact:
        payload = self._person_payload(contact)
        response = self._request("PATCH", f"/rest/people/{external_id}", payload=payload)
        body = response.json() if response.body else {}
        person = ((body or {}).get("data") or {}).get("updatePerson")
        if not isinstance(person, dict):
            raise CrmError("Squid response did not contain data.updatePerson", status=response.status)
        return self._map_person(person)

    def map_record(self, person: Dict[str, Any]) -> CanonicalContact:
        """Map a raw Squid person payload (REST or webhook) to a canonical contact."""
        return self._map_person(person)

    def verify_webhook(
        self, raw_body: bytes, headers: Dict[str, str], secret: str
    ) -> WebhookVerification:
        lowered = {key.lower(): value for key, value in headers.items()}
        verified = parse_squid_webhook(
            secret,
            raw_body,
            lowered.get("x-squid-signature"),
            lowered.get("x-squid-event-id"),
            lowered.get("x-squid-event"),
        )
        return WebhookVerification(
            ok=verified.ok,
            event_id=verified.event_id,
            event_type=verified.event_type,
            payload=verified.payload,
            reason=verified.reason,
        )

    def _person_payload(self, contact: CanonicalContact) -> Dict[str, Any]:
        calling_code = ""
        number = contact.phone
        if number.startswith("+"):
            for prefix in ("+1", "+44", "+91", "+61", "+49", "+33"):
                if number.startswith(prefix):
                    calling_code = prefix
                    number = number[len(prefix) :]
                    break
        payload: Dict[str, Any] = {
            "name": {
                "firstName": contact.first_name,
                "lastName": contact.last_name,
            },
            "emails": {"primaryEmail": contact.email},
            "phones": {
                "primaryPhoneNumber": number,
                "primaryPhoneCallingCode": calling_code,
            },
        }
        if contact.custom_fields.get("jobTitle"):
            payload["jobTitle"] = contact.custom_fields["jobTitle"]
        if contact.custom_fields.get("companyId"):
            payload["companyId"] = contact.custom_fields["companyId"]
        return payload

    def _map_person(self, person: Dict[str, Any]) -> CanonicalContact:
        external_id = str(person.get("id") or "").strip()
        if not external_id:
            raise CrmError("Squid person is missing id")
        first_name = _string(_path(person, self.mappings["first_name"]))
        last_name = _string(_path(person, self.mappings["last_name"]))
        email = _string(_path(person, self.mappings["email"]))
        raw_phone = _string(_path(person, self.mappings["phone"]))
        if raw_phone and not raw_phone.startswith("+"):
            calling_code = _string(_path(person, "phones.primaryPhoneCallingCode"))
            if calling_code:
                raw_phone = f"{calling_code}{raw_phone}" if calling_code.startswith("+") else f"+{calling_code}{raw_phone}"
            else:
                raw_phone = f"+{raw_phone}"
        return CanonicalContact(
            external_id=external_id,
            first_name=first_name,
            last_name=last_name,
            phone=raw_phone,
            email=email,
            timezone="UTC",
            locale="en-US",
            consent_state="unknown",
            consent_source="squid-crm",
            custom_fields={
                "jobTitle": person.get("jobTitle"),
                "companyId": person.get("companyId"),
            },
            source_updated_at=person.get("updatedAt"),
        )

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> HttpResponse:
        import json

        url = build_url(self.base_url, path, params)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Correlation-Id": str(uuid.uuid4()),
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        safe = method in {"GET", "HEAD", "OPTIONS"}
        attempts = 1 + (self.max_retries if safe or idempotency_key else 0)
        last_error: Optional[CrmError] = None
        for attempt in range(attempts):
            try:
                response = self.transport.request(method, url, headers, body, self.timeout)
            except ConnectionError as connection_error:
                last_error = CrmError(str(connection_error), retryable=True)
                if attempt + 1 < attempts:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise last_error from connection_error
            if 200 <= response.status < 300:
                return response
            http_error = _http_error(response)
            if http_error.status in _RETRYABLE and attempt + 1 < attempts:
                last_error = http_error
                time.sleep(self._retry_delay(attempt, response.headers.get("retry-after")))
                continue
            raise http_error
        raise last_error or CrmError("Squid request failed after retries", retryable=True)

    def _retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        return min(self.retry_backoff * (2**attempt), 5.0)


def _path(value: Dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _http_error(response: HttpResponse) -> CrmError:
    try:
        body = response.json()
    except Exception:
        body = response.body.decode("utf-8", errors="replace")
    if isinstance(body, dict):
        messages = body.get("messages")
        message = (
            "; ".join(str(item) for item in messages)
            if isinstance(messages, list)
            else str(messages or body.get("error") or "Squid request failed")
        )
        code = str(body.get("error") or body.get("statusCode") or "")
    else:
        message = str(body)
        code = ""
    return CrmError(
        message,
        status=response.status,
        code=code,
        retryable=response.status in _RETRYABLE,
        body=body,
    )
