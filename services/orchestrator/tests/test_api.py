"""API integration tests covering auth, tenancy, campaigns, and CRM surfaces."""

from __future__ import annotations

from typing import List

import pytest
from fastapi.testclient import TestClient

from voxora.api import create_app
from voxora.config import Settings, TenantPrincipal
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


def test_campaign_create_rejects_unknown_fields(client) -> None:
    agent = client.post("/api/v1/agents", headers=AUTH, json={"name": "A"}).json()
    bad = client.post(
        "/api/v1/campaigns",
        headers=AUTH,
        json={"name": "C", "agent_id": agent["id"], "contact_ids": ["nope"]},
    )
    # Inline recipients are rejected loudly instead of silently ignored.
    assert bad.status_code == 422


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
    assert ids[:3] == ["mock", "voice-runtime", "twilio"]
    for provider in body["data"]:
        if provider["id"] in {"mock", "voice-runtime"}:
            assert provider["implemented"] is True and provider["simulated"] is True
    twilio = next(provider for provider in body["data"] if provider["id"] == "twilio")
    # Implemented, but not configured (no credentials in test settings).
    assert twilio["implemented"] is True
    assert twilio["configured"] is False and twilio["live"] is False


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


# ------------------------------------------------------------- /api/v1/ai/decide
# Route coverage (audit 2026-11-04: zero coverage). The live OpenRouter
# service is out of scope; the provider path is exercised with injected
# doubles at the OpenRouterLLM boundary.


def _decide_payload() -> dict:
    return {"system_prompt": "You are an interviewer.", "user_prompt": "Assess the reply."}


def test_ai_decide_mock_path_is_deterministic_and_audited(client) -> None:
    first = client.post("/api/v1/ai/decide", json=_decide_payload(), headers=AUTH)
    assert first.status_code == 200, first.text
    second = client.post("/api/v1/ai/decide", json=_decide_payload(), headers=AUTH)
    body_one, body_two = first.json(), second.json()
    assert body_one["source"] == "mock-llm"
    assert body_one["decision"] == body_two["decision"]  # deterministic mock
    assert body_one["recorded_usage"] == {
        "prompt_tokens": 0.0,
        "completion_tokens": 0.0,
        "total_tokens": 0.0,
        "cost_usd": 0.0,
    }
    audit = client.get("/api/v1/audit", headers=AUTH).json()["data"]
    decide_rows = [row for row in audit if row["action"] == "ai.decide"]
    assert decide_rows and decide_rows[0]["metadata"]["provider"] == "mock"


def test_ai_decide_rejects_non_writer(client) -> None:
    response = client.post("/api/v1/ai/decide", json=_decide_payload(), headers=VIEWER)
    assert response.status_code == 403


def _openrouter_client(monkeypatch: pytest.MonkeyPatch, tmp_path, *, api_key: str | None) -> TestClient:
    """App with llm_provider=openrouter; env controls OpenRouterLLM creds."""
    monkeypatch.setenv("VOXORA_LLM_PROVIDER", "openrouter")
    if api_key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", api_key)
        monkeypatch.setenv("OPENROUTER_MODEL", "test/model")
    settings = Settings(
        env="test",
        database_path=str(tmp_path / "openrouter.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
        api_tokens={"admin-token": TenantPrincipal("tenant-a", "admin", "Admin")},
        cors_origins=["http://localhost:5173"],
        worker_enabled=False,
        simulated_call_delay_seconds=0.0,
        llm_provider="openrouter",
    )
    return TestClient(create_app(settings))


def test_ai_decide_openrouter_unconfigured_is_501(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pytest.importorskip("voxora_ai")
    with _openrouter_client(monkeypatch, tmp_path, api_key=None) as scoped:
        response = scoped.post("/api/v1/ai/decide", json=_decide_payload(), headers=AUTH)
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "llm_provider_unconfigured"


def test_ai_decide_openrouter_unavailable_is_503(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pytest.importorskip("voxora_ai")
    from voxora_ai.openrouter import OpenRouterError, OpenRouterLLM

    def explode(self, *args: object, **kwargs: object) -> dict:
        raise OpenRouterError("transport failure: connection refused", retryable=True)

    monkeypatch.setattr(OpenRouterLLM, "decide", explode)
    with _openrouter_client(monkeypatch, tmp_path, api_key="sk-or-secret") as scoped:
        response = scoped.post("/api/v1/ai/decide", json=_decide_payload(), headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"


def test_ai_decide_openrouter_success_records_provider_and_usage(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pytest.importorskip("voxora_ai")
    from voxora_ai.openrouter import OpenRouterLLM

    captured: dict = {}

    def decide(self, system_prompt: str, user_prompt: str, **kwargs: object) -> dict:
        captured["prompts"] = (system_prompt, user_prompt)
        return {"next_action": "probe", "is_complete": False}

    monkeypatch.setattr(OpenRouterLLM, "decide", decide)
    monkeypatch.setattr(
        OpenRouterLLM,
        "usage_report",
        lambda self: {"prompt_tokens": 30.0, "completion_tokens": 12.0, "total_tokens": 42.0, "cost_usd": 0.0007},
    )
    with _openrouter_client(monkeypatch, tmp_path, api_key="sk-or-secret") as scoped:
        response = scoped.post(
            "/api/v1/ai/decide", json={**_decide_payload(), "model_profile": "interview_default"}, headers=AUTH
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "openrouter"
    assert body["decision"] == {"next_action": "probe", "is_complete": False}
    assert body["recorded_usage"]["cost_usd"] == 0.0007
    assert captured["prompts"] == ("You are an interviewer.", "Assess the reply.")
    audit = scoped.get("/api/v1/audit", headers=AUTH).json()["data"]
    decide_rows = [row for row in audit if row["action"] == "ai.decide"]
    assert decide_rows[0]["metadata"]["provider"] == "openrouter"
    assert decide_rows[0]["metadata"]["model_profile"] == "interview_default"
