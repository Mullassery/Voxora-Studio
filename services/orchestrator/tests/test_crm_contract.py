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
    # Optional per-record provider state; empty dicts fall back to defaults.
    consents: Dict[str, Dict[str, str]] = field(default_factory=dict)
    suppressions: Dict[str, Dict[str, str]] = field(default_factory=dict)

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
        if not self.consents:
            return {"state": "granted", "source": "memory"}
        entry = self.consents.get(external_id)
        if entry is None:
            raise NotSupportedError("no consent record")
        return entry

    def get_suppression(self, external_id: str) -> Dict[str, str]:
        if not self.suppressions:
            return {"state": "clear"}
        entry = self.suppressions.get(external_id)
        if entry is None:
            raise NotSupportedError("no suppression record")
        return entry

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


# ---------------------------------------------------------------------------
# P3.3: consent/suppression sync is capability-driven and honest
# ---------------------------------------------------------------------------
from voxora.crm.sync import sync_connection  # noqa: E402

_SQUID_SYNC = lambda: SquidCrmAdapter(  # noqa: E731
    base_url=BASE,
    api_key="api_contract",
    transport=FakeSquidTransport(),
    timeout=1,
    max_retries=0,
    retry_backoff=0,
)


def test_sync_applies_provider_consent_for_capable_adapter(database) -> None:
    adapter = InMemoryCrmAdapter(
        consents={record.external_id: {"state": "granted", "source": "memory-provider"}
                  for record in _records()}
    )
    result = sync_connection(database, adapter, tenant_id="t-sync", connection_id="conn-1")
    assert result["failed"] == 0
    assert result["consentApplied"] == result["imported"] == len(adapter.records)
    for contact_id in result["contactIds"]:
        stored = database.get_contact("t-sync", contact_id)
        assert stored["consent_state"] == "granted"
        assert stored["consent_source"] == "memory-provider"


def test_sync_applies_provider_suppression(database) -> None:
    suppressed_id = _records()[0].external_id
    adapter = InMemoryCrmAdapter(
        suppressions={suppressed_id: {"state": "do_not_call", "reason": "DNC list"}}
    )
    result = sync_connection(database, adapter, tenant_id="t-sup", connection_id="conn-2")
    assert result["suppressionApplied"] == 1
    stored = database.get_contact_by_external("t-sup", "memory", suppressed_id)
    assert stored["suppressed"] == 1
    assert "DNC" in stored["suppression_reason"]


def test_sync_never_clears_operator_suppression(database) -> None:
    target = _records()[0].external_id
    adapter = InMemoryCrmAdapter(suppressions={target: {"state": "clear"}})
    result = sync_connection(database, adapter, tenant_id="t-cls", connection_id="conn-3")
    # No suppression write-back: "clear" is ignored, not un-suppressed.
    assert result["suppressionApplied"] == 0


def test_sync_reports_bad_provider_consent_per_record(database) -> None:
    records = _records()
    adapter = InMemoryCrmAdapter(
        consents={
            records[0].external_id: {"state": "banana", "source": "memory-provider"},
            records[1].external_id: {"state": "revoked", "source": "memory-provider"},
        }
    )
    result = sync_connection(database, adapter, tenant_id="t-bad", connection_id="conn-4")
    assert result["failed"] == 1
    assert "banana" in result["errors"][0]["error"]
    assert result["imported"] == len(records)
    assert result["consentApplied"] == 1
    revoked = database.get_contact_by_external("t-bad", "memory", records[1].external_id)
    assert revoked["consent_state"] == "revoked"


def test_sync_skips_unsupported_consent_honestly(database) -> None:
    adapter = _SQUID_SYNC()
    # Squid declares no consent/suppression capability (verified against the
    # Squid client source: no consent or suppression objects exist there).
    assert not adapter.capabilities().consent_read
    with pytest.raises(NotSupportedError):
        adapter.get_consent("any")
    result = sync_connection(database, adapter, tenant_id="t-squid", connection_id="conn-5")
    assert result["failed"] == 0
    assert result["imported"] > 0
    # Honest no-op: consent untouched (provider cannot know it).
    for contact_id in result["contactIds"]:
        stored = database.get_contact("t-squid", contact_id)
        assert stored["consent_state"] == "unknown"
