"""Session budget guard tests (roadmap M6).

Covers: pure ceiling checks (duration/turns/tokens at their exact thresholds),
sole-writer orderly termination with the versioned `budget_exhausted` event
(idempotent on repeat; terminal sessions untouched), Settings default
compatibility, per-session override semantics, and the settle-mount
integration path through the real voice-session API.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from voxora.config import Settings
from voxora.db import Database, iso, parse_iso
from voxora.voice_session_budgets import (
    DURATION_CEILING,
    REASON_BUDGET_EXHAUSTED,
    TERMINATED_EVENT,
    TOKEN_CEILING,
    TURNS_CEILING,
    check_budget_exhausted,
    enforce_session_budgets,
)

HEADERS = {"Authorization": "Bearer admin-token"}


def _mk_session(
    db: Database,
    tenant_id: str = "t1",
    *,
    status: str = "in_progress",
    transcript: Optional[List[Dict[str, Any]]] = None,
    session_metadata: Optional[Dict[str, Any]] = None,
) -> "Dict[str, Any]":
    values = {
        "external_application": "pyinterviewbot",
        # unique per call: the postgres fixture shares a persistent database
        "external_session_id": f"guard_{uuid.uuid4().hex[:12]}",
        "agent_id": "agent_guard",
        "contact_id": "contact_guard",
        "status": status,
        "transcript": transcript or [],
        "session_metadata": session_metadata or {},
    }
    return db.create_voice_session(tenant_id, values)


def _terminations(db: Database, tenant_id: str, session_id: str) -> List[Dict[str, Any]]:
    events = db.list_voice_session_events(tenant_id, session_id)
    return [event for event in events if event["event_type"] == TERMINATED_EVENT]


# ------------------------------------------------------- exact thresholds


def test_duration_ceiling_fires_at_threshold(database: Database) -> None:
    session = _mk_session(database)
    _ = session
    at_max = iso(parse_iso(session["created_at"]) + timedelta(seconds=1200))
    assert check_budget_exhausted(session, None, at_max) == DURATION_CEILING
    just_under = iso(parse_iso(session["created_at"]) + timedelta(seconds=1199))
    assert check_budget_exhausted(session, None, just_under) is None


def test_turns_ceiling_fires_at_81st_turn(database: Database) -> None:
    session = _mk_session(database)
    assert check_budget_exhausted(session, {"turns": 80}) is None
    assert check_budget_exhausted(session, {"turns": 81}) == TURNS_CEILING


def test_token_budget_fires_at_budget_plus_one(database: Database) -> None:
    session = _mk_session(database)
    assert check_budget_exhausted(session, {"total_tokens": 60000}) is None
    assert check_budget_exhausted(session, {"total_tokens": 60001}) == TOKEN_CEILING
    # AI-runtime usage_report shape: explicit total absent, prompt+completion sum
    assert check_budget_exhausted(session, {"prompt_tokens": 60000, "completion_tokens": 1}) == TOKEN_CEILING


# ------------------------------------------------------- under-ceiling no-op


def test_just_under_every_ceiling_enforces_nothing(database: Database) -> None:
    session = _mk_session(database)
    usage = {"turns": 80, "total_tokens": 60000, "elapsed_seconds": 1199}
    assert check_budget_exhausted(session, usage) is None
    assert enforce_session_budgets(database, "t1", session["id"], usage=usage) is None
    assert database.get_voice_session("t1", session["id"])["status"] == "in_progress"
    assert database.list_voice_session_events("t1", session["id"]) == []


# ------------------------------------------------------- orderly termination


def test_breach_terminates_orderly_and_records_usage_idempotently(database: Database) -> None:
    session = _mk_session(database)
    usage = {"turns": 81, "elapsed_seconds": 540.5}
    assert enforce_session_budgets(database, "t1", session["id"], usage=usage) == TURNS_CEILING
    assert database.get_voice_session("t1", session["id"])["status"] == "terminated"
    terminations = _terminations(database, "t1", session["id"])
    assert len(terminations) == 1
    data = terminations[0]["data"]
    assert data["reason"] == REASON_BUDGET_EXHAUSTED
    assert data["breached"] == TURNS_CEILING
    # usage counters that demanded the termination are persisted on the event
    assert data["usage"]["turns"] == 81.0
    assert data["usage"]["elapsed_seconds"] == 540.5
    again = enforce_session_budgets(database, "t1", session["id"], usage=usage)
    assert again == TURNS_CEILING
    assert len(_terminations(database, "t1", session["id"])) == 1
    assert database.get_voice_session("t1", session["id"])["status"] == "terminated"


def test_already_terminal_session_is_untouched(database: Database) -> None:
    session = _mk_session(database, status="completed")
    before = database.list_voice_session_events("t1", session["id"])
    assert enforce_session_budgets(database, "t1", session["id"], usage={"turns": 81}) is None
    assert database.get_voice_session("t1", session["id"])["status"] == "completed"
    assert database.list_voice_session_events("t1", session["id"]) == before


def test_enforcement_uses_settings_configured_ceiling(database: Database) -> None:
    settings = Settings(env="test", database_path="unused.db", secret_key="x" * 40, voice_session_max_turns=1)
    session = _mk_session(database)
    assert enforce_session_budgets(database, "t1", session["id"], usage={"turns": 2}, settings=settings) == TURNS_CEILING


def test_transcript_fallback_counts_as_turns(database: Database) -> None:
    transcript = [{"role": "assistant", "text": "one"}, {"role": "contact", "text": "two"}]
    session = _mk_session(database, transcript=transcript)
    assert check_budget_exhausted(session, None) is None  # 2 <= default 80
    override = _mk_session(
        database, transcript=transcript, session_metadata={"budget_overrides": {"max_turns": 1}}
    )
    assert check_budget_exhausted(override, None) == TURNS_CEILING


# ------------------------------------------------------- per-session overrides


def test_override_can_tighten_but_never_widen(database: Database) -> None:
    session = _mk_session(database, session_metadata={"budget_overrides": {"max_turns": 5}})
    assert check_budget_exhausted(session, {"turns": 6}) == TURNS_CEILING
    assert check_budget_exhausted(session, {"turns": 5}) is None
    widened = _mk_session(database, session_metadata={"budget_overrides": {"max_turns": 200}})
    assert check_budget_exhausted(widened, {"turns": 81}) == TURNS_CEILING
    assert check_budget_exhausted(widened, {"turns": 80}) is None


def test_invalid_or_partial_overrides_are_ignored(database: Database) -> None:
    session = _mk_session(
        database, session_metadata={"budget_overrides": {"max_turns": "not-a-number", "token_budget": 0}}
    )
    assert check_budget_exhausted(session, {"turns": 80}) is None
    assert check_budget_exhausted(session, {"turns": 81}) == TURNS_CEILING


# ------------------------------------------------------- Settings compatibility


def test_settings_defaults_keep_constructor_compatible() -> None:
    settings = Settings()
    assert settings.voice_session_max_duration_seconds == 1200
    assert settings.voice_session_max_turns == 80
    assert settings.voice_session_token_budget == 60000
    legacy = Settings(env="test", database_path="unused.db", api_tokens={})
    assert legacy.voice_session_max_turns == 80
    settings.validate()  # the new defaulted fields pass fail-closed validation


# ------------------------------------------------------- integration (settle mount)


def test_real_advance_path_terminates_mid_run_with_event(client: Any) -> None:
    agent_id = client.post(
        "/api/v1/agents", json={"name": "Guarded", "system_prompt": "ok."}, headers=HEADERS
    ).json()["id"]
    contact = client.post(
        "/api/v1/contacts",
        json={"first_name": "G", "phone": "+15551234567"},
        headers=HEADERS,
    ).json()
    client.patch(
        f"/api/v1/contacts/{contact['id']}/consent",
        json={"state": "granted", "source": "report"},
        headers=HEADERS,
    )
    response = client.post(
        "/api/v1/voice-sessions",
        json={
            "external_application": "pyinterviewbot",
            "external_session_id": "guard_intake_001",
            "contact": {"external_contact_id": "contact_guard", "name": "G", "phone": "+15551234567"},
            "agent": {"agent_id": agent_id, "model_profile": "interview_default"},
            "session_metadata": {"budget_overrides": {"max_turns": 2}},
            "webhook_url": "https://pyinterviewbot.example.com/api/v1/webhooks/voxora",
        },
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "terminated"
    db: Database = client.app.state.db
    terminations = _terminations(db, "tenant-a", body["session_id"])
    assert len(terminations) == 1
    data = terminations[0]["data"]
    assert data["reason"] == REASON_BUDGET_EXHAUSTED
    assert data["breached"] == TURNS_CEILING
    # the mock intake transcript (3 entries) exceeded the tightened ceiling
    assert data["usage"]["turns"] == 3.0
