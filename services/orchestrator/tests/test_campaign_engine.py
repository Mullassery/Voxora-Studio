from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from voxora.campaign import CampaignEngine, CampaignError
from voxora.db import Database
from voxora.telephony.base import TelephonyEvent, TelephonyResult
from voxora.telephony.scripted import ScriptedTelephonyAdapter


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def _db(tmp_path) -> Database:
    db = Database(str(tmp_path / "engine.db"))
    db.initialize()
    db.ensure_tenant("t1", "Test tenant")
    return db


def _setup(db: Database, dry_run: bool = True) -> Dict[str, Any]:
    agent = db.create_agent("t1", {"name": "Agent"})
    contacts = []
    for phone in ["+15551231001", "+15551231002", "+15551231003"]:
        contacts.append(db.create_local_contact("t1", {"phone": phone, "consent_state": "granted"}))
    campaign = db.create_campaign(
        "t1",
        {
            "name": "Test",
            "agent_id": agent["id"],
            "dry_run": dry_run,
            "max_concurrency": 2,
            "max_attempts": 2,
            "retry_backoff_seconds": 60,
        },
    )
    db.add_recipients("t1", campaign["id"], [c["id"] for c in contacts])
    return {"agent": agent, "campaign": campaign, "contacts": contacts}


def _result(status: str, disposition: str, retryable: bool) -> TelephonyResult:
    return TelephonyResult(
        ok=status == "completed",
        provider="scripted",
        provider_call_id=f"script_{disposition}",
        status=status,
        disposition=disposition,
        simulated=False,
        retryable=retryable,
        duration_ms=12500 if status == "completed" else 0,
        error_code=None if status == "completed" else disposition,
        error_message=None if status == "completed" else disposition,
        events=[TelephonyEvent("dialing", "2026-01-01T00:00:00+00:00", {})],
        latency_ms={
            "audio_received": 40.0,
            "speech_endpoint": 200.0,
            "stt_result": 150.0,
            "agent_response": 400.0,
            "tts_first_audio": 200.0,
            "first_audio_transmitted": 30.0,
            "turn_completed": 1020.0,
        },
        transcript=[{"role": "assistant", "text": "hello"}],
        started_at="2026-01-01T00:00:00+00:00",
        answered_at="2026-01-01T00:00:01.2+00:00" if status == "completed" else None,
        ended_at="2026-01-01T00:00:13.7+00:00",
    )


def test_dry_run_campaign_completes_and_records_simulated_calls(tmp_path) -> None:
    db = _db(tmp_path)
    setup = _setup(db, dry_run=True)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    engine = CampaignEngine(
        db=db,
        telephony=ScriptedTelephonyAdapter([_result("completed", "answered", False)] * 10),
        worker_threads=1,
        global_concurrency=5,
        clock=clock.now,
    )
    engine.start("t1", setup["campaign"]["id"])
    processed = engine.run_until_idle()
    assert processed == 3  # max_concurrency=2: two rounds of two, then one
    counts = db.campaign_counts("t1", setup["campaign"]["id"])
    assert counts["completed"] == 3
    assert counts["failed"] == 0
    campaign = db.get_campaign("t1", setup["campaign"]["id"])
    assert campaign["status"] == "completed"
    calls, total = db.list_calls("t1", limit=10)
    assert total == 3
    assert all(call["simulated"] for call in calls)
    assert all(call["status"] == "completed" for call in calls)
    latency = db.latency_summary("t1")
    assert latency["turn_completed"]["count"] == 3


def test_transient_failure_requeues_then_fails_after_max_attempts(tmp_path) -> None:
    db = _db(tmp_path)
    setup = _setup(db, dry_run=False)
    scripting = ScriptedTelephonyAdapter(
        [
            _result("no_answer", "no_answer", True),
            _result("no_answer", "no_answer", True),
            _result("busy", "busy", True),
            _result("busy", "busy", True),
            _result("no_answer", "no_answer", True),
            _result("busy", "busy", True),
        ]
    )
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    engine = CampaignEngine(db, scripting, worker_threads=1, global_concurrency=5, clock=clock.now
    )
    engine.start("t1", setup["campaign"]["id"])
    first_round = engine.run_once()
    assert first_round == 2  # max_concurrency enforces two in-flight per round
    counts = db.campaign_counts("t1", setup["campaign"]["id"])
    assert counts["queued"] == 3
    # Attempts are now exhausted because max_attempts == 2.
    clock.advance(3600)
    processed = engine.run_until_idle()
    assert processed == 4
    counts = db.campaign_counts("t1", setup["campaign"]["id"])
    assert counts["failed"] == 3
    assert counts["queued"] == 0
    calls, _ = db.list_calls("t1")
    assert len(calls) == 6


def test_policy_blocked_recipients_never_dial(tmp_path) -> None:
    db = _db(tmp_path)
    db.ensure_tenant("t1", "t")
    agent = db.create_agent("t1", {"name": "A"})
    revoked = db.create_local_contact("t1", {"phone": "+15551239999", "consent_state": "revoked"})
    campaign = db.create_campaign(
        "t1", {"name": "Blocks only", "agent_id": agent["id"], "dry_run": False}
    )
    db.add_recipients("t1", campaign["id"], [revoked["id"]])
    adapter = ScriptedTelephonyAdapter([])
    engine = CampaignEngine(db, adapter, worker_threads=1, global_concurrency=5)
    engine.start("t1", campaign["id"])
    engine.run_until_idle()
    recipients, _ = db.list_recipients("t1", campaign["id"])
    blocked = [r for r in recipients if r["eligibility_code"]]
    assert len(blocked) == 1
    assert blocked[0]["eligibility_code"] == "consent_required"
    assert adapter.calls == []  # no dial happened for blocked contacts


def test_restart_recovery_requeues_inflight(tmp_path) -> None:
    db = _db(tmp_path)
    setup = _setup(db, dry_run=False)
    db.update_campaign_status("t1", setup["campaign"]["id"], "running")
    claimed = db.claim_recipients(10)
    assert len(claimed) == 2  # campaign max_concurrency
    recovered = db.recover_inflight()
    assert recovered >= 2
    counts = db.campaign_counts("t1", setup["campaign"]["id"])
    assert counts["queued"] == 3
    calls, _ = db.list_calls("t1")
    assert len(calls) == 2
    assert all(call["status"] == "failed" for call in calls)

    # The requeued work can be redialed with a fresh attempt.
    redialed = db.claim_recipients(10)
    recovered_redials = [item for item in redialed if item["attempts"] == 2]
    assert len(recovered_redials) == 1  # third recipient was never claimed pre-crash


def test_cannot_start_empty_campaign(tmp_path) -> None:
    db = _db(tmp_path)
    db.ensure_tenant("t1", "t")
    agent = db.create_agent("t1", {"name": "A"})
    campaign = db.create_campaign("t1", {"name": "Empty", "agent_id": agent["id"], "dry_run": True})
    engine = CampaignEngine(db, ScriptedTelephonyAdapter([]))
    with pytest.raises(CampaignError):
        engine.start("t1", campaign["id"])


def test_idempotency_keys_are_unique_per_attempt(tmp_path) -> None:
    db = _db(tmp_path)
    setup = _setup(db, dry_run=True)
    db.update_campaign_status("t1", setup["campaign"]["id"], "running")
    first = db.claim_recipients(5)
    assert len(first) == 2
    db.recover_inflight()
    second = db.claim_recipients(5)
    redialed = [item for item in second if item["attempts"] == 2]
    assert len(redialed) == 1  # one requeued recipient redialed; one fresh recipient dialed
    keys = [item["idempotency_key"] for item in second]
    assert len(keys) == len(set(keys))
