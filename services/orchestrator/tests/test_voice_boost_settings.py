"""Voice Boost settings storage tests (roadmap M9).

Config-in-db: one global settings row per tenant (seeded with the CLEAR
profile, enabled) plus a per-session override column on voice_sessions.
Parametrized over SQLite and Postgres like test_storage_compat.py.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from voxora.db import Database

TENANT = "t-boost"


def _session_values(voice_boost: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "external_application": "app",
        "external_session_id": "sess-1",
        "agent_id": "agent-1",
        "contact_id": "contact-1",
        "status": "created",
        "voice_boost": voice_boost,
    }


def test_seed_then_get_then_put_roundtrip(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    database.seed_voice_boost_settings(TENANT, "clear", {})
    row = database.get_voice_boost_settings(TENANT)
    assert row is not None
    assert row["profile"] == "clear"
    assert row["config"] == {}
    updated = database.put_voice_boost_settings(TENANT, "studio", {"ns_strength": 0.9})
    assert updated["profile"] == "studio"
    assert database.get_voice_boost_settings(TENANT)["config"] == {"ns_strength": 0.9}


def test_get_unset_tenant_returns_none(database: Database) -> None:
    assert database.get_voice_boost_settings("no-such-tenant") is None


def test_seed_is_idempotent(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    database.seed_voice_boost_settings(TENANT, "clear", {})
    database.seed_voice_boost_settings(TENANT, "studio", {"eq": False})
    assert database.get_voice_boost_settings(TENANT)["profile"] == "clear"


def test_session_voice_boost_column_persists_and_decodes(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_voice_session(
        TENANT,
        _session_values({"profile": "natural", "overrides": {"eq": False}}),
    )
    stored = database.get_voice_session(TENANT, created["id"])
    assert stored["voice_boost"] == {"profile": "natural", "overrides": {"eq": False}}


# --------------------------------------------------- resolution (voice_boost.py)


def test_builtin_defaults_are_clear_enabled() -> None:
    from voxora.voice_boost import builtin_voice_boost_settings

    assert builtin_voice_boost_settings() == {
        "profile": "clear",
        "overrides": {},
        "enabled": True,
    }


def test_resolution_seeds_once_and_merges_override(database: Database) -> None:
    from voxora.voice_boost import resolve_voice_boost_settings

    database.ensure_tenant(TENANT, "Tenant")
    first = resolve_voice_boost_settings(
        database, TENANT, {"overrides": {"eq": False}}
    )
    assert first["profile"] == "clear" and first["enabled"] is True
    assert first["overrides"] == {"eq": False}
    # Session overrides never persist into global defaults: a second session
    # with a different profile inherits only the stored global defaults.
    second = resolve_voice_boost_settings(database, TENANT, {"profile": "natural"})
    assert second["profile"] == "natural"
    assert second["overrides"] == {}


def test_resolution_disabled_bypasses_overrides(database: Database) -> None:
    from voxora.voice_boost import resolve_voice_boost_settings

    database.ensure_tenant(TENANT, "Tenant")
    resolved = resolve_voice_boost_settings(
        database, TENANT, {"enabled": False, "overrides": {"ns_strength": 0.5}}
    )
    assert resolved["enabled"] is False
    assert resolved["overrides"] == {"ns_strength": 0.5}


def test_override_rejects_unknown_key_and_bad_ranges() -> None:
    from voxora.models import VoiceBoostControlOverrides

    with pytest.raises(ValidationError):
        VoiceBoostControlOverrides.model_validate({"reverb": True})
    with pytest.raises(ValidationError):
        VoiceBoostControlOverrides.model_validate({"ns_strength": 1.5})
    with pytest.raises(ValidationError):
        VoiceBoostControlOverrides.model_validate({"agc_target_dbfs": -3.0})


# ------------------------------------------------------------------ HTTP API

AUTH = {"Authorization": "Bearer admin-token"}


def _session_payload(agent_id: str, external_session_id: str = "boost_001") -> Dict[str, Any]:
    return {
        "external_application": "pyinterviewbot",
        "external_session_id": external_session_id,
        "contact": {
            "external_contact_id": "contact_boost",
            "name": "Candidate Boost",
            "phone": "+15558009999",
        },
        "agent": {"agent_id": agent_id, "model_profile": "default"},
        "session_metadata": {},
        "webhook_url": None,
    }


def _seed_agent_and_consent(client: TestClient) -> str:
    agent_id = client.post(
        "/api/v1/agents", json={"name": "Boost Agent"}, headers=AUTH
    ).json()["id"]
    contact = client.post(
        "/api/v1/contacts",
        json={"first_name": "B", "last_name": "C", "phone": "+15558009999"},
        headers=AUTH,
    )
    client.patch(
        f"/api/v1/contacts/{contact.json()['id']}/consent",
        json={"state": "granted", "source": "operator"},
        headers=AUTH,
    )
    return agent_id


def test_get_seeds_and_returns_defaults(client: TestClient) -> None:
    response = client.get("/api/v1/voice/boost/settings", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"] == "clear"
    assert body["overrides"] == {}
    assert body["enabled"] is True


def test_put_updates_and_audits(client: TestClient) -> None:
    response = client.put(
        "/api/v1/voice/boost/settings",
        json={"profile": "studio", "overrides": {"ns_strength": 0.9}},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json()["profile"] == "studio"
    again = client.get("/api/v1/voice/boost/settings", headers=AUTH).json()
    assert again["profile"] == "studio"
    assert again["overrides"] == {"ns_strength": 0.9}
    audit = client.get("/api/v1/audit", headers=AUTH).json()["data"]
    assert "voice_boost.settings_updated" in [item["action"] for item in audit]


def test_put_rejects_unknown_control_key(client: TestClient) -> None:
    response = client.put(
        "/api/v1/voice/boost/settings",
        json={"overrides": {"reverb": True}},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_put_rejects_out_of_range_strength(client: TestClient) -> None:
    assert (
        client.put(
            "/api/v1/voice/boost/settings",
            json={"overrides": {"ns_strength": 1.5}},
            headers=AUTH,
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/voice/boost/settings",
            json={"overrides": {"agc_target_dbfs": -3.0}},
            headers=AUTH,
        ).status_code
        == 422
    )


def test_put_rejects_unknown_profile(client: TestClient) -> None:
    assert (
        client.put(
            "/api/v1/voice/boost/settings",
            json={"profile": "loud"},
            headers=AUTH,
        ).status_code
        == 422
    )


def test_session_override_resolves_in_view(client: TestClient) -> None:
    client.put(
        "/api/v1/voice/boost/settings",
        json={"profile": "studio"},
        headers=AUTH,
    )
    agent_id = _seed_agent_and_consent(client)
    payload = _session_payload(agent_id)
    payload["voice_boost"] = {"overrides": {"eq": False}}
    response = client.post("/api/v1/voice-sessions", json=payload, headers=AUTH)
    assert response.status_code == 201, response.text
    view = response.json()["session"] if "session" in response.json() else response.json()
    resolved = view["voice_boost"]
    assert resolved["profile"] == "studio"  # inherited from global defaults
    assert resolved["overrides"] == {"eq": False}  # session override applied


def test_session_intake_rejects_invalid_override(client: TestClient) -> None:
    agent_id = _seed_agent_and_consent(client)
    payload = _session_payload(agent_id, external_session_id="boost_bad")
    payload["voice_boost"] = {"profile": "loud"}
    response = client.post("/api/v1/voice-sessions", json=payload, headers=AUTH)
    assert response.status_code == 422


def test_update_voice_session_accepts_voice_boost(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_voice_session(TENANT, _session_values({}))
    updated = database.update_voice_session(
        TENANT, created["id"], voice_boost={"profile": "natural", "overrides": {"eq": False}}
    )
    assert updated is not None
    assert updated["voice_boost"] == {"profile": "natural", "overrides": {"eq": False}}


def test_update_voice_session_accepts_voice_boost(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_voice_session(TENANT, _session_values({}))
    updated = database.update_voice_session(
        TENANT, created["id"], voice_boost={"profile": "natural", "overrides": {"eq": False}}
    )
    assert updated is not None
    assert updated["voice_boost"] == {"profile": "natural", "overrides": {"eq": False}}
