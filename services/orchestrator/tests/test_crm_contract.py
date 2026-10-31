"""Reusable CRM adapter contract tests run against every registered adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pytest

from tests.test_squid_adapter import BASE, PEOPLE, FakeSquidTransport
from voxora.crm.base import (
    CanonicalContact,
    ConnectionTest,
    ContactPage,
    CrmAdapter,
    CrmCapabilities,
    FieldInfo,
    NotSupportedError,
)
from voxora.crm.squid import SquidCrmAdapter

_ID = 0


def _contact() -> CanonicalContact:
    return CanonicalContact(
        external_id="generated",
        first_name="Test",
        last_name="Contact",
        phone="+15551239999",
        email="test@example.com",
    )


def _records() -> List[CanonicalContact]:
    from voxora.crm.squid import SquidCrmAdapter  # reuse mapper
    return [SquidCrmAdapter("http://x", "k").map_record(person) for person in PEOPLE]


@dataclass
class InMemoryCrmAdapter(CrmAdapter):
    provider = "memory"
    records: List[CanonicalContact] = field(default_factory=lambda: list(_records()))
    activities: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def capabilities(cls) -> CrmCapabilities:
        return CrmCapabilities(
            connection_test=True,
            field_discovery=True,
            contact_list=True,
            contact_get=True,
            contact_create=True,
            contact_update=True,
            consent_read=True,
            suppression_read=True,
            call_activity_write=True,
            webhook_ingest=False,
        )

    def test_connection(self) -> ConnectionTest:
        return ConnectionTest(True, "ok", {})

    def describe_fields(self) -> List[FieldInfo]:
        return [
            FieldInfo("first_name", "firstName", True, True),
            FieldInfo("last_name", "lastName", True, True),
            FieldInfo("email", "email", True, False),
            FieldInfo("phone", "phone", True, True),
            FieldInfo("timezone", "timezone", True, False),
            FieldInfo("locale", "locale", True, False),
            FieldInfo("consent_state", "consent", True, False),
        ]

    def list_contacts(self, limit: int = 100, offset: int = 0) -> ContactPage:
        page = self.records[offset : offset + limit]
        return ContactPage(page, len(self.records), offset + len(page) < len(self.records))

    def get_contact(self, external_id: str) -> CanonicalContact:
        for record in self.records:
            if record.external_id == external_id:
                return record
        raise NotSupportedError("missing record")

    def create_contact(self, contact: CanonicalContact, idempotency_key: str) -> CanonicalContact:
        self.records.append(contact)
        return contact

    def update_contact(self, external_id: str, contact: CanonicalContact) -> CanonicalContact:
        return contact

    def get_consent(self, external_id: str) -> Dict[str, str]:
        return {"state": "granted", "source": "memory"}

    def get_suppression(self, external_id: str) -> Dict[str, str]:
        return {"state": "clear"}

    def create_call_activity(self, external_id: str, activity: Dict[str, str]) -> Dict[str, str]:
        self.activities[external_id] = activity
        return activity


ADAPTERS = [
    pytest.param(
        lambda: SquidCrmAdapter(
            base_url=BASE,
            api_key="api_contract",
            transport=FakeSquidTransport(),
            timeout=1,
            max_retries=0,
            retry_backoff=0,
        ),
        id="squid",
    ),
    pytest.param(lambda: InMemoryCrmAdapter(), id="memory"),
]


@pytest.mark.parametrize("adapter_factory", ADAPTERS)
def test_contract_connection_test_and_capabilities(adapter_factory) -> None:
    adapter = adapter_factory()
    capabilities = adapter.capabilities().as_dict()
    assert capabilities["connectionTest"] is True
    assert capabilities["fieldDiscovery"] is True
    assert capabilities["contactList"] is True
    result = adapter.test_connection()
    assert result.ok is True
    assert "connected" in result.message.lower() or result.message == "ok"


@pytest.mark.parametrize("adapter_factory", ADAPTERS)
def test_contract_field_discovery_includes_required_canonical_fields(adapter_factory) -> None:
    fields = {item.canonical_field: item for item in adapter_factory().describe_fields()}
    for canonical in ["first_name", "last_name", "email", "phone"]:
        assert canonical in fields


@pytest.mark.parametrize("adapter_factory", ADAPTERS)
def test_contract_list_contacts_returns_page(adapter_factory) -> None:
    adapter = adapter_factory()
    page = adapter.list_contacts(limit=10, offset=0)
    assert page.contacts, "adapters must surface at least one contact"
    assert isinstance(page.has_next, bool)


@pytest.mark.parametrize("adapter_factory", ADAPTERS)
def test_contract_create_then_get_round_trip(adapter_factory) -> None:
    adapter = adapter_factory()
    capabilities = adapter.capabilities()
    if not capabilities.contact_create:
        pytest.skip(f"{adapter.provider} does not support contact_create")
    contact = CanonicalContact(
        external_id=f"new-{len(adapter.list_contacts(limit=1000).contacts)}",
        first_name="Round",
        last_name="Trip",
        phone="+15551237777",
        email="round@example.com",
    )
    created = adapter.create_contact(contact, idempotency_key="contract-1")
    fetched = adapter.get_contact(created.external_id)
    assert fetched.phone == contact.phone


@pytest.mark.parametrize("adapter_factory", ADAPTERS)
def test_contract_unsupported_operations_raise(adapter_factory) -> None:
    from voxora.crm.base import NotSupportedError

    adapter = adapter_factory()
    capabilities = adapter.capabilities()
    if capabilities.call_activity_write:
        result = adapter.create_call_activity("x", {"summary": "test"})
        assert result
    else:
        with pytest.raises(NotSupportedError):
            adapter.create_call_activity("x", {"summary": "test"})


@pytest.mark.parametrize("adapter_factory", ADAPTERS)
def test_contract_capabilities_honestly_disable_unsupported_ops(adapter_factory) -> None:
    adapter = adapter_factory()
    capabilities = adapter.capabilities().as_dict()
    assert capabilities["consentRead"] == capabilities.get("consentRead")  # always declared explicitly
    if not capabilities["consentRead"] and not capabilities["suppressionRead"]:
        with pytest.raises(NotSupportedError):
            adapter.get_consent("x")
        with pytest.raises(NotSupportedError):
            adapter.get_suppression("x")
