"""API integration tests covering auth, tenancy, campaigns, and CRM surfaces."""

from __future__ import annotations

from typing import List

from voxora.crm.base import (
    CanonicalContact,
    ConnectionTest,
    ContactPage,
    CrmAdapter,
    CrmCapabilities,
    FieldInfo,
)

AUTH = {"Authorization": "Bearer admin-token"}
OPERATOR = {"Authorization": "Bearer operator-token"}
VIEWER = {"Authorization": "Bearer viewer-token"}
OTHER = {"Authorization": "Bearer other-tenant-token"}


def _health(client) -> dict:
    return client.get("/api/v1/health").json()


def test_health_is_public_and_honest_about_simulation(client) -> None:
    body = _health(client)
    assert body["status"] == "ok"
    assert body["mode"] == "simulation"
    assert body["liveCallsEnabled"] is False


def test_ready_reports_worker_state(client) -> None:
    body = client.get("/api/v1/ready").json()
    assert body["ready"] is True
    assert body["database"] is True
    assert body["worker"] == {"enabled": False, "running": False}


def test_missing_token_is_unauthorized(client) -> None:
    assert client.get("/api/v1/campaigns").status_code == 401
    assert client.get("/api/v1/campaigns", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_seed_demo_created_agent_and_contacts(client) -> None:
    agents = client.get("/api/v1/agents", headers=AUTH).json()["data"]
    assert agents
    contacts = client.get("/api/v1/contacts", headers=AUTH).json()
    assert contacts["pageInfo"]["total"] >= 8


def test_agent_create_requires_writer_role(client) -> None:
    response = client.post(
        "/api/v1/agents", headers=VIEWER, json={"name": "Denied agent"}
    )
    assert response.status_code == 403


def test_agent_create_and_list(client) -> None:
    created = client.post(
        "/api/v1/agents",
        headers=AUTH,
        json={"name": "Support agent", "model": "mock-llm"},
    )
    assert created.status_code == 201
    data = client.get("/api/v1/agents", headers=AUTH).json()["data"]
    assert any(agent["name"] == "Support agent" for agent in data)


def test_contact_create_validation_and_consent(client) -> None:
    bad = client.post("/api/v1/contacts", headers=AUTH, json={"phone": "123"})
    assert bad.status_code == 422
    created = client.post(
        "/api/v1/contacts",
        headers=AUTH,
        json={"first_name": "New", "last_name": "Lead", "phone": "+15551235555", "consent_state": "granted"},
    )
    assert created.status_code == 201
    contact_id = created.json()["id"]
    patched = client.patch(
        f"/api/v1/contacts/{contact_id}/consent",
        headers=OPERATOR,
        json={"state": "revoked", "source": "operator"},
    )
    assert patched.status_code == 200
    assert patched.json()["consent_state"] == "revoked"


def test_suppression_endpoints_round_trip(client) -> None:
    added = client.post(
        "/api/v1/suppression",
        headers=AUTH,
        json={"phone": "+15551237000", "reason": "DNC"},
    )
    assert added.status_code == 201
    items = client.get("/api/v1/suppression", headers=AUTH).json()["data"]
    assert any(item["phone"] == "+15551237000" for item in items)
    deleted = client.delete("/api/v1/suppression/+15551237000", headers=AUTH)
    assert deleted.status_code == 200
    assert client.get("/api/v1/suppression", headers=AUTH).json()["data"]


def _create_campaign(client, contact_ids: List[str], dry_run: bool = True) -> str:
    agent = client.post("/api/v1/agents", headers=AUTH, json={"name": "Campaign agent"}).json()
    campaign = client.post(
        "/api/v1/campaigns",
        headers=AUTH,
        json={
            "name": "Welcome calls",
            "agent_id": agent["id"],
            "dry_run": dry_run,
            "max_concurrency": 2,
            "max_attempts": 2,
            "retry_backoff_seconds": 30,
        },
    ).json()
    assert campaign["dryRun"] is True
    added = client.post(
        f"/api/v1/campaigns/{campaign['id']}/recipients",
        headers=AUTH,
        json={"contact_ids": contact_ids},
    ).json()
    assert added["added"] == len(contact_ids)
    return campaign["id"]


def test_campaign_lifecycle_runs_dry_run_end_to_end(client) -> None:
    contacts = client.get("/api/v1/contacts?limit=3", headers=AUTH).json()["data"]
    campaign_id = _create_campaign(client, [c["id"] for c in contacts])
    validate = client.get(f"/api/v1/campaigns/{campaign_id}/validate", headers=AUTH).json()
    assert validate["total"] == 3
    for contact in contacts:
        client.patch(
            f"/api/v1/contacts/{contact['id']}/consent",
            headers=AUTH,
            json={"state": "granted", "source": "demo"},
        )
    validate = client.get(f"/api/v1/campaigns/{campaign_id}/validate", headers=AUTH).json()
    assert validate["eligible"] == 3
    started = client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=OPERATOR)
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    app = client.app
    processed = app.state.engine.run_until_idle()
    assert processed == 3
    campaign = client.get(f"/api/v1/campaigns/{campaign_id}", headers=AUTH).json()
    assert campaign["status"] == "completed"
    assert campaign["recipients"]["completed"] == 3
    calls = client.get(f"/api/v1/calls?campaign_id={campaign_id}", headers=AUTH).json()
    assert calls["pageInfo"]["total"] == 3
    assert all(call["simulated"] for call in calls["data"])
    call_detail = client.get(f"/api/v1/calls/{calls['data'][0]['id']}", headers=AUTH).json()
    assert call_detail["latency"]
    assert call_detail["events"]

    guard = client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=AUTH)
    assert guard.status_code == 409


def test_campaign_recipient_validation_reports_blocks(client) -> None:
    contacts = client.get("/api/v1/contacts?limit=10", headers=AUTH).json()["data"]
    campaign_id = _create_campaign(client, [c["id"] for c in contacts])
    validate = client.get(f"/api/v1/campaigns/{campaign_id}/validate", headers=AUTH).json()
    codes = validate["byCode"]
    assert any(code != "eligible" for code in codes)


def test_tenant_isolation_blocks_cross_tenant_reads(client) -> None:
    contacts = client.get("/api/v1/contacts?limit=1", headers=AUTH).json()["data"]
    campaign_id = _create_campaign(client, [contacts[0]["id"]])
    assert client.get(f"/api/v1/campaigns/{campaign_id}", headers=OTHER).status_code == 404
    other_campaigns = client.get("/api/v1/campaigns", headers=OTHER).json()
    assert other_campaigns["pageInfo"]["total"] == 0
    tenant_a_contacts = client.get("/api/v1/contacts?limit=3", headers=AUTH).json()["data"]
    tenant_b_contacts = client.get("/api/v1/contacts?limit=10", headers=OTHER).json()["data"]
    tenant_a_ids = {contact["id"] for contact in tenant_a_contacts}
    assert not {contact["id"] for contact in tenant_b_contacts} & tenant_a_ids


def test_connection_secrets_are_never_returned(client) -> None:
    created = client.post(
        "/api/v1/integrations/crm/connections",
        headers=AUTH,
        json={
            "provider": "squid",
            "name": "Local Squid",
            "base_url": "http://127.0.0.1:3000",
            "api_key": "api_secret_key_value",
            "webhook_secret": "whsec_1234567890",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert "api_key" not in body
    assert "api_key_encrypted" not in body
    assert "webhook_secret_encrypted" not in body
    assert body["hasApiKey"] is True
    listing = client.get("/api/v1/integrations/crm/connections", headers=AUTH).json()["data"]
    assert listing
    for item in listing:
        assert "api_key" not in str(item)


def test_unsupported_provider_rejected(client) -> None:
    response = client.post(
        "/api/v1/integrations/crm/connections",
        headers=AUTH,
        json={"provider": "salesforce", "name": "SF", "base_url": "https://example.com", "api_key": "aaaaaaaaaa"},
    )
    assert response.status_code == 422


def test_telephony_providers_tell_the_truth(client) -> None:
    body = client.get("/api/v1/telephony/providers", headers=AUTH).json()
    ids = [provider["id"] for provider in body["data"]]
    assert "mock" in ids
    twilio = next(provider for provider in body["data"] if provider["id"] == "twilio")
    assert twilio["implemented"] is False
    assert twilio["configured"] is False


def test_sync_uses_adapter_and_imports_contacts(client) -> None:
    from tests.test_squid_adapter import PEOPLE
    from voxora.crm.squid import SquidCrmAdapter

    connection = client.post(
        "/api/v1/integrations/crm/connections",
        headers=AUTH,
        json={"provider": "squid", "name": "sync-test", "base_url": "http://127.0.0.1:3000", "api_key": "api_sync_key"},
    ).json()
    app = client.app
    mapping_adapter = SquidCrmAdapter(base_url="http://x", api_key="k")
    contacts = [mapping_adapter.map_record(person) for person in PEOPLE]

    class InjectedAdapter(CrmAdapter):
        provider = "squid"

        def capabilities(self) -> CrmCapabilities:
            return SquidCrmAdapter.capabilities()

        def test_connection(self) -> ConnectionTest:
            return ConnectionTest(True, "Connected to Squid", {})

        def describe_fields(self) -> List[FieldInfo]:
            return SquidCrmAdapter("http://x", "k").describe_fields()

        def list_contacts(self, limit: int = 100, offset: int = 0) -> ContactPage:
            return ContactPage(contacts[offset : offset + limit], len(contacts), False)

        def get_contact(self, external_id: str) -> CanonicalContact:
            return contacts[0]

    app._adapter_for = lambda connection: InjectedAdapter()  # type: ignore[attr-defined]
    result = client.post(
        f"/api/v1/integrations/crm/connections/{connection['id']}/sync",
        headers=AUTH,
        json={"limit": 100, "max_pages": 1},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["imported"] == 2
    assert len(body["contactIds"]) == 2
    external_contacts = client.get("/api/v1/contacts?search=Ada", headers=AUTH).json()
    assert external_contacts["pageInfo"]["total"] >= 1
    jobs = client.get("/api/v1/integrations/crm/sync-jobs", headers=AUTH).json()["data"]
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["imported"] == 2


def test_audit_log_records_promotions(client) -> None:
    contacts = client.get("/api/v1/contacts?limit=1", headers=AUTH).json()["data"]
    campaign_id = _create_campaign(client, [contacts[0]["id"]])
    client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=AUTH)
    audit = client.get("/api/v1/audit", headers=AUTH).json()["data"]
    actions = [item["action"] for item in audit]
    assert "campaign.created" in actions
    assert "campaign.started" in actions
