"""Voice Boost settings storage tests (roadmap M9).

Config-in-db: one global settings row per tenant (seeded with the CLEAR
profile, enabled) plus a per-session override column on voice_sessions.
Parametrized over SQLite and Postgres like test_storage_compat.py.
"""

from __future__ import annotations

from typing import Any, Dict

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
