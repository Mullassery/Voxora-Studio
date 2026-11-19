"""Verified LeadConnector (GoHighLevel) CRM adapter.

Contracts read from marketplace.gohighlevel.com (HighLevel API docs,
retrieved 2026-11-12):
- Bearer auth + required ``Version: 2021-07-28`` header (Update Contact,
  Get Contacts reference pages).
- Contact schema: firstName/lastName/email/phone/timezone/dateAdded, ``dnd``
  boolean, ``dndSettings`` per-channel objects with ``status``
  active|inactive|permanent (Call channel = call suppression).
- Pagination: id cursor (``startAfterId``) on GET /contacts/ (deprecated in
  favor of POST /contacts/search; kept here because the documented cursor is
  the id of the last contact, and the sync engine drives linear pages per
  adapter instance — one sync run, one adapter instance).

Capability honesty: GHL documents no consent object → ``consent_read`` is
False and declared rather than simulated. Call suppression IS documented and
read via ``dndSettings.Call``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import (
    CanonicalContact,
    ConnectionTest,
    ContactPage,
    CrmAdapter,
    CrmCapabilities,
    CrmError,
    FieldInfo,
)
from .http import HttpResponse, HttpTransport, UrllibTransport, build_url

_API_VERSION = "2021-07-28"
_RETRYABLE = {408, 429, 500, 502, 503, 504}
_SUPPRESSED_CALL_STATUSES = {"active", "permanent"}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


class LeadConnectorCrmAdapter(CrmAdapter):
    provider = "leadconnector"

    def __init__(
        self,
        api_token: str,
        location_id: str,
        base_url: str = "https://services.leadconnectorhq.com",
        transport: Optional[HttpTransport] = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ):
        if not location_id:
            raise CrmError("LeadConnector requires a sub-account location_id (config-in-db)")
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.location_id = location_id
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        # Per-instance id cursor so the offset-driven sync engine can walk
        # GHL's startAfterId pages linearly (one instance = one sync run).
        self._page_cursor: Dict[int, str] = {}

    @classmethod
    def capabilities(cls) -> CrmCapabilities:
        return CrmCapabilities(
            connection_test=True,
            field_discovery=False,
            contact_list=True,
            contact_get=True,
            contact_create=False,
            contact_update=False,
            consent_read=False,
            suppression_read=True,
            call_activity_write=False,
            webhook_ingest=False,
            cursor_pagination=True,
            offset_pagination=False,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": self.capabilities().as_dict(),
            "locationId": self.location_id,
        }

    def test_connection(self) -> ConnectionTest:
        try:
            response = self._request("GET", "/contacts/", params={"limit": 1})
        except CrmError as error:
            if error.status in {401, 403}:
                return ConnectionTest(False, "LeadConnector rejected the token", {"status": error.status})
            return ConnectionTest(False, str(error), {"status": error.status})
        except ConnectionError as error:
            return ConnectionTest(False, f"Could not reach LeadConnector: {error}", {})
        count = len(((response.json() if response.body else {}) or {}).get("contacts") or [])
        return ConnectionTest(
            True,
            "Connected to LeadConnector and read the contacts endpoint",
            {"status": response.status, "sampleSize": count, "locationId": self.location_id},
        )

    def describe_fields(self) -> List[FieldInfo]:
        return [
            FieldInfo("first_name", "firstName", True, False, "LeadConnector contact firstName"),
            FieldInfo("last_name", "lastName", True, False, "LeadConnector contact lastName"),
            FieldInfo("email", "email", True, False, "LeadConnector contact email"),
            FieldInfo("phone", "phone", True, True, "LeadConnector contact phone (E.164 preferred)"),
            FieldInfo("timezone", "timezone", True, False, "LeadConnector contact timezone"),
            FieldInfo("locale", "", False, False, "Not present on LeadConnector contact"),
            FieldInfo("consent_state", "", False, False, "No consent object in the documented API"),
            FieldInfo("suppression", "dndSettings.Call.status", True, False, "active|permanent = do-not-call"),
            FieldInfo("call_activity", "", False, False, "No call-activity object in the documented API"),
        ]

    def list_contacts(self, limit: int = 100, offset: int = 0) -> ContactPage:
        params: Dict[str, Any] = {"locationId": self.location_id, "limit": limit}
        if offset == 0:
            # One adapter instance = one sync run; a fresh page 0 restarts.
            self._page_cursor = {}
        elif offset in self._page_cursor:
            params["startAfterId"] = self._page_cursor[offset]
        response = self._request("GET", "/contacts/", params=params)
        body = response.json() if response.body else {}
        contacts = body.get("contacts") if isinstance(body, dict) else None
        if not isinstance(contacts, list):
            raise CrmError("LeadConnector response did not contain contacts", status=response.status)
        total = body.get("count") if isinstance(body, dict) else None
        next_cursor: Optional[str] = None
        if contacts:
            last = contacts[-1]
            next_cursor = _string(last.get("id") if isinstance(last, dict) else "") or None
            if next_cursor:
                self._page_cursor[offset + len(contacts)] = next_cursor
        has_next = bool(total) and bool(contacts) and offset + len(contacts) < int(str(total))
        return ContactPage(
            contacts=[self._map_contact(contact) for contact in contacts],
            total=int(total) if total is not None else None,
            has_next=has_next,
            cursor=next_cursor,
        )

    def get_contact(self, external_id: str) -> CanonicalContact:
        response = self._request("GET", f"/contacts/{external_id}")
        body = response.json() if response.body else {}
        contact = body.get("contact") if isinstance(body, dict) else None
        if not isinstance(contact, dict):
            raise CrmError("LeadConnector response did not contain contact", status=response.status)
        return self._map_contact(contact)

    def get_suppression(self, external_id: str) -> Dict[str, Any]:
        contact = self.get_contact(external_id)
        call_dnd = ((contact.custom_fields.get("dndSettings") or {}).get("Call")) or {}
        status = _string(call_dnd.get("status")).lower()
        if status in _SUPPRESSED_CALL_STATUSES:
            reason = _string(call_dnd.get("message")) or _string(call_dnd.get("code"))
            return {"state": "do_not_call", "reason": reason or "call DND active in LeadConnector"}
        if status in {"", "inactive"}:
            return {"state": "clear"}
        return {"state": "clear"}

    def _map_contact(self, contact: Dict[str, Any]) -> CanonicalContact:
        external_id = _string(contact.get("id"))
        if not external_id:
            raise CrmError("LeadConnector contact is missing id")
        phone = _string(contact.get("phone")).replace(" ", "")
        call_dnd = ((contact.get("dndSettings") or {}).get("Call")) or {}
        status = _string(call_dnd.get("status")).lower()
        return CanonicalContact(
            external_id=external_id,
            first_name=_string(contact.get("firstName")),
            last_name=_string(contact.get("lastName")),
            phone=phone,
            email=_string(contact.get("email")),
            timezone=_string(contact.get("timezone")) or "UTC",
            locale="en-US",
            consent_state="unknown",
            consent_source="leadconnector",
            suppressed=status in _SUPPRESSED_CALL_STATUSES,
            custom_fields={
                "dndSettings": contact.get("dndSettings"),
                "tags": contact.get("tags"),
            },
            source_updated_at=contact.get("dateAdded") or contact.get("updated_at"),
        )

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> HttpResponse:
        import json

        url = build_url(self.base_url, path, params)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_token}",
            "Version": _API_VERSION,
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        safe = method in {"GET", "HEAD", "OPTIONS"}
        attempts = 1 + (self.max_retries if safe else 0)
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
        raise last_error or CrmError("LeadConnector request failed after retries", retryable=True)

    def _retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        return min(self.retry_backoff * (2**attempt), 5.0)


def _http_error(response: HttpResponse) -> CrmError:
    try:
        body = response.json()
    except Exception:
        body = response.body.decode("utf-8", errors="replace")
    if isinstance(body, dict):
        message = str(body.get("message") or body.get("error") or "LeadConnector request failed")
        code = str(body.get("errorCode") or body.get("code") or "")
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
