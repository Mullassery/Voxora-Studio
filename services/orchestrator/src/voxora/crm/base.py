"""Provider-neutral CRM adapter contract and canonical data models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class NotSupportedError(RuntimeError):
    pass


class CrmError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        code: Optional[str] = None,
        retryable: bool = False,
        body: Any = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.body = body


@dataclass(frozen=True)
class CrmCapabilities:
    connection_test: bool = False
    field_discovery: bool = False
    contact_list: bool = False
    contact_get: bool = False
    contact_create: bool = False
    contact_update: bool = False
    consent_read: bool = False
    suppression_read: bool = False
    call_activity_write: bool = False
    webhook_ingest: bool = False
    cursor_pagination: bool = False
    offset_pagination: bool = True

    def as_dict(self) -> Dict[str, bool]:
        return {
            "connectionTest": self.connection_test,
            "fieldDiscovery": self.field_discovery,
            "contactList": self.contact_list,
            "contactGet": self.contact_get,
            "contactCreate": self.contact_create,
            "contactUpdate": self.contact_update,
            "consentRead": self.consent_read,
            "suppressionRead": self.suppression_read,
            "callActivityWrite": self.call_activity_write,
            "webhookIngest": self.webhook_ingest,
            "cursorPagination": self.cursor_pagination,
            "offsetPagination": self.offset_pagination,
        }


@dataclass(frozen=True)
class ConnectionTest:
    ok: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldInfo:
    canonical_field: str
    external_field: str
    supported: bool
    required: bool = False
    note: str = ""


@dataclass(frozen=True)
class CanonicalContact:
    external_id: str
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    email: str = ""
    timezone: str = "UTC"
    locale: str = "en-US"
    consent_state: str = "unknown"
    consent_source: str = ""
    suppressed: bool = False
    custom_fields: Dict[str, Any] = field(default_factory=dict)
    source_updated_at: Optional[str] = None

    def as_values(self) -> Dict[str, Any]:
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "email": self.email,
            "timezone": self.timezone,
            "locale": self.locale,
            "consent_state": self.consent_state,
            "consent_source": self.consent_source,
            "suppressed": self.suppressed,
            "custom_fields": self.custom_fields,
            "source_updated_at": self.source_updated_at,
        }


@dataclass(frozen=True)
class ContactPage:
    contacts: List[CanonicalContact]
    total: Optional[int]
    has_next: bool
    cursor: Optional[str] = None


@dataclass(frozen=True)
class WebhookVerification:
    ok: bool
    event_id: Optional[str]
    event_type: Optional[str]
    payload: Optional[Dict[str, Any]]
    reason: Optional[str] = None


class CrmAdapter(ABC):
    provider: str

    @abstractmethod
    def capabilities(self) -> CrmCapabilities:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> ConnectionTest:
        raise NotImplementedError

    @abstractmethod
    def describe_fields(self) -> List[FieldInfo]:
        raise NotImplementedError

    @abstractmethod
    def list_contacts(self, limit: int = 100, offset: int = 0) -> ContactPage:
        raise NotImplementedError

    def get_contact(self, external_id: str) -> CanonicalContact:
        raise NotSupportedError("contact_get is not supported")

    def create_contact(self, contact: CanonicalContact, idempotency_key: str) -> CanonicalContact:
        raise NotSupportedError("contact_create is not supported")

    def update_contact(self, external_id: str, contact: CanonicalContact) -> CanonicalContact:
        raise NotSupportedError("contact_update is not supported")

    def get_consent(self, external_id: str) -> Dict[str, Any]:
        raise NotSupportedError("consent_read is not supported")

    def get_suppression(self, external_id: str) -> Dict[str, Any]:
        raise NotSupportedError("suppression_read is not supported")

    def create_call_activity(self, external_id: str, activity: Dict[str, Any]) -> Dict[str, Any]:
        raise NotSupportedError("call_activity_write is not supported")

    def verify_webhook(
        self, raw_body: bytes, headers: Dict[str, str], secret: str
    ) -> WebhookVerification:
        raise NotSupportedError("webhook_ingest is not supported")

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": self.capabilities().as_dict(),
        }
