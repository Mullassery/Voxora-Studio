"""In-flight session checkpointing + restart recovery tests (roadmap M6/P6).

Covers: deterministic flush cadence on and off the interval boundary, the
dialing >=2.5x-heartbeat rule, kill -9 style restart recovery with <=1 flush
interval of loss and ``source: "recovered"`` provenance, corrupt-snapshot
fail-closed honesty, empty no-op recovery, and bounded-queue overflow
dropping the OLDEST deltas.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from voxora.db import Database, iso
from voxora.voice_session_checkpoints import (
    SUBSTANCE_MIN_CHARS,
    CheckpointFlusher,
    CheckpointQueue,
    TranscriptCheckpointError,
    effective_flush_interval,
    flush_checkpoint,
    recover_answers,
    recover_session,
    should_flush,
)

TENANT = "t1"

QUESTION_1 = "Where did you work before this role?"
ANSWER_1 = "I led the payments migration at Acme for two years."
QUESTION_2 = "Why did you leave that position?"
ANSWER_2 = "I want to own a broader slice of the delivery process end to end."
JUNK = "yes"


def _seed_session(db: Database, external_session_id: object = None) -> dict:
    db.ensure_tenant(TENANT, "T")
    agent = db.create_agent(TENANT, {"name": "A"})
    contact = db.create_local_contact(
        TENANT,
        {"first_name": "C", "phone": "+15551234700", "consent_state": "granted"},
    )
    return db.create_voice_session(
        TENANT,
        {
            "external_application": "pyinterviewbot",
            # Unique per call: intake is idempotent on the external id, and the
            # Postgres fixture reuses one database across tests.
            "external_session_id": external_session_id or f"ckpt_{uuid.uuid4().hex[:12]}",
            "agent_id": agent["id"],
            "contact_id": contact["id"],
            "status": "in_progress",
        },
    )


def _flusher(db: Database, session_id: str) -> CheckpointFlusher:
    return CheckpointFlusher(
        db,
        TENANT,
        session_id,
        CheckpointQueue(maxsize=8),
        interval_seconds=20.0,
        heartbeat_interval_seconds=8.0,
    )


# ---------------------------------------------------- deterministic flush cadence


def test_flushes_on_interval_and_never_mid_interval_before_due(database: Database) -> None:
    """Clock-injected sequence: nothing flushes before due, transcript at due."""
    session = _seed_session(database)
    flusher = _flusher(database, session["id"])
    flusher.enqueue([{"role": "assistant", "text": QUESTION_1}])

    assert flusher.maybe_flush(5.0, "in_progress") is None
    assert database.get_voice_session_checkpoint(TENANT, session["id"]) is None

    assert flusher.maybe_flush(19.99, "in_progress") is None

    flushed_at = flusher.maybe_flush(20.0, "in_progress")
    assert flushed_at
    checkpoint = database.get_voice_session_checkpoint(TENANT, session["id"])
    assert [entry["text"] for entry in checkpoint["transcript"]] == [QUESTION_1]

    flusher.enqueue([{"role": "contact", "text": ANSWER_1}])
    assert flusher.maybe_flush(25.0, "in_progress") is None
    checkpoint = database.get_voice_session_checkpoint(TENANT, session["id"])
    assert len(checkpoint["transcript"]) == 1

    assert flusher.maybe_flush(40.0, "in_progress")
    checkpoint = database.get_voice_session_checkpoint(TENANT, session["id"])
    assert [entry["text"] for entry in checkpoint["transcript"]] == [QUESTION_1, ANSWER_1]


# ------------------------------------------------------------- dialing rule (P6)


def test_dialing_effective_interval_is_boundary_exactly_2_5x_heartbeat() -> None:
    heartbeat = 8.0
    # Exactly at the 2.5x boundary while dialing: due.
    assert should_flush(0.0, 2.5 * heartbeat, 20.0, "dialing", heartbeat) is True
    # A hair before the boundary: not due.
    assert should_flush(0.0, 2.5 * heartbeat - 0.01, 20.0, "dialing", heartbeat) is False
    # A fast interval config cannot flush faster than 2.5x heartbeat while dialing.
    assert effective_flush_interval(1.0, "dialing", heartbeat) == 2.5 * heartbeat
    assert effective_flush_interval(500.0, "dialing", heartbeat) == 500.0
    # Once answered, the configured interval applies even if it is short.
    assert effective_flush_interval(1.0, "in_progress", heartbeat) == 1.0


# ------------------------------------- kill -9 acceptance: restart <=1 interval loss


def test_kill9_restart_recovers_up_to_last_flush_with_recovered_provenance(
    database: Database,
) -> None:
    """Flush at T, deltas queued by T+<interval, crash, restart recovery."""
    session = _seed_session(database)
    flusher = _flusher(database, session["id"])
    flusher.enqueue([{"role": "assistant", "text": QUESTION_1}])
    flusher.enqueue([{"role": "contact", "text": ANSWER_1}])
    assert flusher.maybe_flush(30.0, "in_progress")  # last durable flush at T=30

    # Deltas that never got flushed before the kill -9 (crash window < interval).
    crash_deltas = [
        {"role": "contact", "text": JUNK},
        {"role": "assistant", "text": QUESTION_2},
        {"role": "contact", "text": ANSWER_2},
    ]
    flusher.enqueue(crash_deltas)

    # kill -9: the in-memory flusher (snapshot + queue) is gone; the restarted
    # worker recovers strictly from the durable checkpoint row.
    result = recover_session(database, TENANT, session["id"])
    assert result["status"] == "recovered"
    assert result["checkpoint_present"] is True

    # Everything up to the last flush is reproduced verbatim; the crash-window
    # deltas are absent (loss bounded by one flush interval).
    restored = [entry["text"] for entry in result["restored_entries"]]
    assert restored == [QUESTION_1, ANSWER_1]
    assert ANSWER_2 not in restored and QUESTION_2 not in restored

    # Recovered answers carry substance classification + provenance (scoring
    # is deferred to M7, so classification stands in for the score).
    assert len(result["recovered_answers"]) == 1
    only = result["recovered_answers"][0]
    assert only["text"] == ANSWER_1
    assert only["source"] == "recovered"
    assert only["classification"] == "substantive"

    # Persisted honestly: session transcript back-filled (it was empty) and
    # the provenance event recorded with the recovered answer records.
    updated = database.get_voice_session(TENANT, session["id"])
    assert [entry["text"] for entry in updated["transcript"]] == [QUESTION_1, ANSWER_1]
    assert updated["status"] == "in_progress"  # status never touched
    events = [
        event
        for event in database.list_voice_session_events(TENANT, session["id"])
        if event["event_type"] == "transcript.recovered"
    ]
    assert len(events) == 1
    data = events[0]["data"]
    assert data["loss_source"] == "worker_restart"
    assert [answer["source"] for answer in data["recovered_answers"]] == ["recovered"]

    # The restarted flusher continues from the durable snapshot.
    restart_flusher = _flusher(database, session["id"])
    restart_flusher.seed(result["restored_entries"])
    restart_flusher.enqueue([{"role": "contact", "text": ANSWER_2}])
    restart_flusher.maybe_flush(60.0, "in_progress")
    checkpoint = database.get_voice_session_checkpoint(TENANT, session["id"])
    assert [entry["text"] for entry in checkpoint["transcript"]] == [
        QUESTION_1,
        ANSWER_1,
        ANSWER_2,
    ]


def test_recovery_never_overwrites_an_existing_session_transcript(
    database: Database,
) -> None:
    """Sole-writer honesty: a settled transcript is never clobbered by restore."""
    session = _seed_session(database)
    flush_checkpoint(
        database, TENANT, session["id"], [{"role": "contact", "text": ANSWER_1}]
    )
    settled = [{"role": "assistant", "text": "Settled verbatim transcript line."}]
    database.update_voice_session(TENANT, session["id"], transcript=settled)

    result = recover_session(database, TENANT, session["id"])
    assert result["status"] == "recovered"
    assert result["transcript_restored"] is False
    updated = database.get_voice_session(TENANT, session["id"])
    assert updated["transcript"] == settled
    assert not [
        event
        for event in database.list_voice_session_events(TENANT, session["id"])
        if event["event_type"] == "transcript.recovered"
    ]


def test_recovery_of_unknown_session_fails_closed(database: Database) -> None:
    from voxora.voice_sessions import VoiceSessionError

    with pytest.raises(VoiceSessionError):
        recover_session(database, TENANT, "vxs-nope")


# -------------------------------------------------- corruption: fail CLOSED + honest


def test_corrupt_snapshot_fails_closed_without_resurrection(database: Database) -> None:
    """Truncated JSON in the checkpoint row: classified, no writes, no crash."""
    session = _seed_session(database)
    with database._write() as connection:
        connection.execute(
            "INSERT INTO voice_session_checkpoints (id, tenant_id, session_id,"
            " transcript, flushed_at) VALUES (?, ?, ?, ?, ?)",
            (
                "vxc-corrupt",
                TENANT,
                session["id"],
                '{"role": "contact", "text": "truncated snap',  # JSON cut mid-record
                iso(),
            ),
        )

    result = recover_session(database, TENANT, session["id"])
    assert result["status"] == "corrupt"
    assert "JSON array" in result["reason"]
    assert result["recovered_answers"] == [] and result["restored_entries"] == []
    assert result["transcript_restored"] is False
    assert database.get_voice_session(TENANT, session["id"])["transcript"] == []
    assert not [
        event
        for event in database.list_voice_session_events(TENANT, session["id"])
        if event["event_type"] == "transcript.recovered"
    ]


def test_corrupt_entry_shape_fails_closed_without_resurrection(
    database: Database,
) -> None:
    """Parseable but wrong-shaped entries (strings, missing text) are corrupt."""
    session = _seed_session(database)
    flush_checkpoint(database, TENANT, session["id"], [{"role": "contact", "text": ANSWER_1}])
    with database._write() as connection:
        connection.execute(
            "UPDATE voice_session_checkpoints SET transcript=?"
            " WHERE tenant_id=? AND session_id=?",
            ('["junk string entry", {"role": "contact"}]', TENANT, session["id"]),
        )
    result = recover_session(database, TENANT, session["id"])
    assert result["status"] == "corrupt"
    assert result["recovered_answers"] == []
    assert database.get_voice_session(TENANT, session["id"])["transcript"] == []


# --------------------------------------------------------------------------- empty


def test_empty_transcript_recovery_noops_without_errors(database: Database) -> None:
    """No checkpoint row at all, and a durable-but-empty snapshot: both no-op."""
    session = _seed_session(database)
    missing = recover_session(database, TENANT, session["id"])
    assert missing["status"] == "empty" and missing["checkpoint_present"] is False

    flush_checkpoint(database, TENANT, session["id"], [])
    empty = recover_session(database, TENANT, session["id"])
    assert empty["status"] == "empty" and empty["checkpoint_present"] is True
    assert empty["recovered_answers"] == [] and empty["transcript_restored"] is False
    assert database.get_voice_session(TENANT, session["id"])["transcript"] == []


# ------------------------------------------------------- bounded queue (drop-oldest)


def test_bounded_queue_overflow_drops_oldest_keeps_newest_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue = CheckpointQueue(maxsize=3)
    assert queue.push([{"role": "contact", "text": "e1"}]) == 0
    assert queue.push([{"role": "contact", "text": "e2"}]) == 0
    assert queue.push([{"role": "contact", "text": "e3"}]) == 0
    with caplog.at_level(logging.WARNING, logger="voxora.voice_session_checkpoints"):
        dropped = queue.push([{"role": "contact", "text": "e4"}])
    assert dropped == 1 and queue.dropped == 1
    assert "dropped 1 oldest transcript delta(s)" in caplog.text
    entries = queue.drain()
    # Nothing newer was lost: e1 (oldest) dropped, e2..e4 survive in order.
    assert [entry["text"] for entry in entries] == ["e2", "e3", "e4"]
    assert len(queue) == 0


# ------------------------------------------------------------ substance gate (pure)


def test_substance_gate_discards_short_junk_and_tags_survivors() -> None:
    snapshot = [
        {"role": "assistant", "text": QUESTION_1},
        {"role": "contact", "text": JUNK},  # short junk: discarded
        {"role": "contact", "text": "   uh huh  "},  # junk even after strip
        {"role": "assistant", "text": QUESTION_2},  # assistant turns are not answers
        {"role": "contact", "text": ANSWER_1},  # substantive: survives
    ]
    recovered = recover_answers(snapshot)
    assert len(recovered) == 1
    assert recovered[0]["text"] == ANSWER_1
    assert recovered[0]["source"] == "recovered"
    assert recovered[0]["classification"] == "substantive"
    assert recovered[0]["index"] == 4  # provenance back to the verbatim snapshot


def test_substance_threshold_constant_is_documented_rationally() -> None:
    # The gate must junk bare acknowledgements but keep real answer material.
    assert len("uh-huh") < SUBSTANCE_MIN_CHARS and len("Acme, two years.") >= SUBSTANCE_MIN_CHARS


# --------------------------------------------------------- flush refuses bad snapshots


def test_flush_refuses_to_write_a_snapshot_it_cannot_vouch_for(
    database: Database,
) -> None:
    session = _seed_session(database)
    with pytest.raises(TranscriptCheckpointError):
        flush_checkpoint(database, TENANT, session["id"], [{"role": "contact"}])
    with pytest.raises(TranscriptCheckpointError):
        flush_checkpoint(database, TENANT, session["id"], "[]")  # type: ignore[arg-type]
    assert database.get_voice_session_checkpoint(TENANT, session["id"]) is None
