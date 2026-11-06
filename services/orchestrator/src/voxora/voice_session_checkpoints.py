"""In-flight voice-session transcript checkpointing + restart recovery (P6).

Ownership per the ecosystem ADR (docs/migration/migration-adr.md): Voxora owns
voice-session state and transcript records, so the verbatim checkpoint table
(``voice_session_checkpoints``) and the recovery path live here.

Nothing in this module sits on the turn-critical path: the live turn loop (M7)
only enqueues transcript deltas into :class:`CheckpointQueue`; a separate
:class:`CheckpointFlusher` drains the queue about every
``transcript_checkpoint_interval_seconds`` and upserts the verbatim transcript
snapshot durably. A ``kill -9`` therefore loses at most one flush interval of
transcript, and restart recovery (:func:`recover_session`) rebuilds strictly
from the durable snapshot — never from in-memory queue state, which is exactly
what the crash destroyed.

Dialing rule: before the call is answered (queued/dialing/ringing) the
effective flush interval is ``max(interval, 2.5 x heartbeat)`` — while the
status ping is the only traffic keeping the call warm, the flusher must stay
at least 2.5x quieter than the ping.

Bounded queue: on overflow the OLDEST deltas are dropped (warned + counted)
rather than the newest — newer deltas are what the next due flush will
durably capture, so dropping old keeps the surviving snapshot the most
recent verbatim window.

Honest deviations (delivery conventions):
- Scoring is deferred to M7 (no scoring machinery exists yet): recovered
  answers carry a substance classification and ``source: "recovered"``
  provenance — not a score — and are persisted as the recovered answer
  records inside the recorded ``transcript.recovered`` event until answer
  rows exist.
- There was no heartbeat interval in config, so
  ``Settings.heartbeat_interval_seconds`` is introduced here as the
  pre-answer status-ping interval the dialing rule multiplies.
- Recovery restores the verbatim transcript ONLY when the stored session
  transcript is EMPTY (it cannot overwrite what the settle path persisted)
  and never touches session status: the sole-writer session-state rule holds.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .config import Settings
from .db import Database
from .voice_sessions import VoiceSessionError

logger = logging.getLogger(__name__)

# Pre-answer call statuses: the flusher dialing rule applies to these only.
PRE_ANSWER_STATUSES = frozenset({"queued", "dialing", "ringing"})

# Dialing rule (M6/P6): while dialing, the flush cadence must be at least
# 2.5x the heartbeat/status-ping interval.
MIN_DIALING_INTERVAL_MULTIPLE = 2.5

# Substance gate threshold: contact-turn text shorter than this (after strip)
# is discarded as junk during recovery. Rationale: bare acknowledgements
# ("yes", "uh-huh"), carrier noise, and garbled single-token STT segments all
# fall far below it, while any real interview answer (a name, a project
# detail, a salary figure) is materially longer. Deliberately conservative:
# recovering a borderline one-liner is preferable to resurrecting junk.
SUBSTANCE_MIN_CHARS = 16

# Default cadence constants resolve to the Settings fields so tests and
# deployments keep a single source of truth.
DEFAULT_FLUSH_INTERVAL = Settings.transcript_checkpoint_interval_seconds
DEFAULT_HEARTBEAT_INTERVAL = Settings.heartbeat_interval_seconds
DEFAULT_QUEUE_SIZE = Settings.transcript_checkpoint_queue_size


class TranscriptCheckpointError(Exception):
    """Checkpoint misuse: a snapshot that cannot be vouched for verbatim."""


def effective_flush_interval(
    interval_seconds: float,
    call_status: str,
    heartbeat_interval_seconds: float,
) -> float:
    """Effective flush interval for the call state (pure; dialing rule applied)."""
    if call_status in PRE_ANSWER_STATUSES:
        return max(
            interval_seconds, MIN_DIALING_INTERVAL_MULTIPLE * heartbeat_interval_seconds
        )
    return interval_seconds


def should_flush(
    last_flush: float,
    now: float,
    interval_seconds: float,
    call_status: str,
    heartbeat_interval_seconds: float,
) -> bool:
    """Whether a flush is due at ``now`` given the last flush time (pure)."""
    elapsed = now - last_flush
    return elapsed >= effective_flush_interval(
        interval_seconds, call_status, heartbeat_interval_seconds
    )


def parse_snapshot(raw: Any) -> List[Dict[str, Any]]:
    """Parse and vouch for a verbatim transcript snapshot; fail closed on it.

    A damaged snapshot (truncated JSON left undecoded as a string, or entries
    shaped other than ``{"role": str, "text": str}``) raises instead of
    returning something partial.
    """
    if not isinstance(raw, list):
        raise TranscriptCheckpointError(
            f"checkpoint transcript must decode to a JSON array, got {type(raw).__name__}"
        )
    for entry in raw:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("role"), str)
            or not isinstance(entry.get("text"), str)
        ):
            raise TranscriptCheckpointError(
                "checkpoint entries must be objects with string role and text"
            )
    return raw


def flush_checkpoint(
    db: Database,
    tenant_id: str,
    session_id: str,
    snapshot: List[Dict[str, Any]],
    flushed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert the verbatim transcript snapshot as the durable checkpoint."""
    entries = parse_snapshot(snapshot)
    return db.upsert_voice_session_checkpoint(tenant_id, session_id, entries, flushed_at)


class CheckpointQueue:
    """Bounded, thread-safe handoff of transcript deltas (oldest dropped)."""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._maxsize = max(1, maxsize)
        self._items: Deque[Dict[str, str]] = deque()
        self._lock = threading.Lock()
        self.dropped = 0

    def push(self, entries: List[Dict[str, str]]) -> int:
        """Enqueue verbatim delta entries; returns the number dropped (oldest)."""
        dropped_total = 0
        with self._lock:
            for entry in entries:
                while len(self._items) >= self._maxsize:
                    self._items.popleft()
                    dropped_total += 1
                self._items.append(entry)
        if dropped_total:
            self.dropped += dropped_total
            logger.warning(
                "checkpoint queue overflow: dropped %d oldest transcript delta(s)",
                dropped_total,
            )
        return dropped_total

    def drain(self) -> List[Dict[str, str]]:
        """Atomically take every queued delta; the queue is left empty."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class CheckpointFlusher:
    """Drains the delta queue into durable verbatim snapshots on its own cadence.

    The caller (worker/turn-loop seam) drives the cadence by invoking
    :meth:`maybe_flush` off the turn-critical path; flushing is only ever
    triggered through :func:`should_flush` so the dialing rule always applies.
    A crash between flushes loses only the queued deltas — at most one flush
    interval of transcript.
    """

    def __init__(
        self,
        db: Database,
        tenant_id: str,
        session_id: str,
        queue: CheckpointQueue,
        *,
        interval_seconds: float = DEFAULT_FLUSH_INTERVAL,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL,
        last_flush: float = 0.0,
        snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.queue = queue
        self.interval_seconds = interval_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.last_flush = last_flush
        self.snapshot: List[Dict[str, Any]] = list(snapshot or [])

    def enqueue(self, entries: List[Dict[str, str]]) -> int:
        return self.queue.push(entries)

    def seed(self, entries: List[Dict[str, Any]]) -> None:
        """Continue from a prior durable snapshot across a worker restart."""
        self.snapshot = list(entries)

    def maybe_flush(self, now: float, call_status: str) -> Optional[str]:
        """Flush when due; returns the flushed_at timestamp, else None.

        Queue deltas are only drained when a flush actually happens, so the
        crash-loss window is bounded by the flush interval, not the enqueue
        rate.
        """
        if not should_flush(
            self.last_flush,
            now,
            self.interval_seconds,
            call_status,
            self.heartbeat_interval_seconds,
        ):
            return None
        self.snapshot.extend(self.queue.drain())
        checkpoint = flush_checkpoint(self.db, self.tenant_id, self.session_id, self.snapshot)
        self.last_flush = now
        return checkpoint["flushed_at"]


def recover_answers(snapshot: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Substance gate: contact turns substantive enough to be real answers.

    Pure: contact-role entries at or above ``SUBSTANCE_MIN_CHARS`` survive as
    recovered answer records with provenance; shorter ones are discarded as
    junk. Scoring is deferred to M7 in the absence of scoring machinery.
    """
    recovered: List[Dict[str, Any]] = []
    for index, entry in enumerate(snapshot):
        if entry.get("role") != "contact":
            continue
        text = (entry.get("text") or "").strip()
        if len(text) < SUBSTANCE_MIN_CHARS:
            continue
        recovered.append(
            {
                "index": index,
                "role": "contact",
                "text": text,
                "classification": "substantive",
                "source": "recovered",
            }
        )
    return recovered


def recover_session(
    db: Database, tenant_id: str, session_id: str, *, persist: bool = True
) -> Dict[str, Any]:
    """Worker-restart recovery from the durable checkpoint (fail closed).

    Returns a classification record rather than raising for corrupt data:
    ``status`` is one of ``recovered`` / ``empty`` / ``corrupt``. A corrupt
    snapshot yields ``corrupt`` with NO writes (no partial junk resurrected).
    The sole-writer rule holds: the verbatim restore only back-fills a
    transcript that is EMPTY, status is never transitioned, and the
    recovered answer records are persisted via the ``transcript.recovered``
    event provenance.
    """
    session = db.get_voice_session(tenant_id, session_id)
    if session is None:
        raise VoiceSessionError(404, "Voice session not found", "unknown_session")
    checkpoint = db.get_voice_session_checkpoint(tenant_id, session_id)
    result: Dict[str, Any] = {
        "session_id": session_id,
        "checkpoint_present": checkpoint is not None,
        "status": "empty",
        "restored_entries": [],
        "recovered_answers": [],
        "reason": "",
        "transcript_restored": False,
    }
    if checkpoint is None:
        return result
    try:
        entries = parse_snapshot(checkpoint.get("transcript"))
    except TranscriptCheckpointError as error:  # fail closed, honestly
        result["status"] = "corrupt"
        result["reason"] = str(error)
        return result
    if not entries:
        return result  # durable but empty transcript: honest no-op
    result["status"] = "recovered"
    result["restored_entries"] = entries
    result["recovered_answers"] = recover_answers(entries)
    if persist and not (session.get("transcript") or []):
        db.update_voice_session(tenant_id, session_id, transcript=entries)
        result["transcript_restored"] = True
        db.record_voice_session_event(
            tenant_id,
            session_id,
            "transcript.recovered",
            {
                "loss_source": "worker_restart",
                "flushed_at": checkpoint["flushed_at"],
                "restored_entries": len(entries),
                "recovered_answers": result["recovered_answers"],
            },
        )
    return result
