"""Per-session cost guards for the voice-session engine (roadmap M6).

Ownership per the ecosystem ADR (docs/migration/migration-adr.md): Voxora
solely owns voice-session state, so the hard ceilings — max duration, max
turns, token budget — live here, not at the telephony provider. A breach
orderly-terminates the session through the sole-writer transition path and
records the versioned `voice_session.terminated` event with reason
`budget_exhausted` plus the usage counters that demanded it (persisted
auditably; survives restarts by living in voice_session_events).

The live turn loop is M7 (not built yet), so real per-turn usage capture
does not exist in the orchestrator today: `usage` is an optional dict
passed in by callers, shaped like the AI-runtime usage report (
`turns`, `prompt_tokens`, `completion_tokens`, `total_tokens`,
`elapsed_seconds`, `started_at`). Absent counters fall back honestly to
what exists today — the transcript entry count for turns (one utterance ≈
one turn contribution until M7 supplies real per-turn counters) and the
session creation timestamp for elapsed time. (See M7.)

Ceiling sources and defaults (Settings): `voice_session_max_duration_
seconds` = 1200 — the competing 20-minute guardrail; `voice_session_max_
turns` = 80 — the competing 80-turn guardrail; `voice_session_token_
budget` = 60000 — roughly a full 20-minute interview of conversational
STT+prompt+TTS tokens. A session may tighten each ceiling per-session via
`session_metadata["budget_overrides"]` (values must be positive numbers);
widening overrides are ignored — `min()` against the configured ceiling
keeps the hard Voxora-side floor fail-closed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .db import Database, parse_iso, utcnow

DURATION_CEILING = "max_duration_seconds"
TURNS_CEILING = "max_turns"
TOKEN_CEILING = "token_budget"

REASON_BUDGET_EXHAUSTED = "budget_exhausted"
TERMINATED_EVENT = "voice_session.terminated"

DEFAULT_MAX_DURATION_SECONDS = 1200
DEFAULT_MAX_TURNS = 80
DEFAULT_TOKEN_BUDGET = 60000

# Same terminal set the session service settles into
# (voice_sessions.session_status_for).
SESSION_TERMINAL_STATUSES = frozenset(
    {"completed", "busy", "no_answer", "failed", "terminated", "canceled"}
)


def configured_ceilings(settings: Optional[Any]) -> Dict[str, int]:
    """The hard ceiling triple from Settings, honoring module defaults.

    Invalid or non-positive configured values keep the module default so a
    bad deployment can never remove the guard (fail-closed).
    """
    return {
        "max_duration_seconds": _positive_setting(
            settings, "voice_session_max_duration_seconds", DEFAULT_MAX_DURATION_SECONDS
        ),
        "max_turns": _positive_setting(settings, "voice_session_max_turns", DEFAULT_MAX_TURNS),
        "token_budget": _positive_setting(settings, "voice_session_token_budget", DEFAULT_TOKEN_BUDGET),
    }


def _positive_setting(settings: Optional[Any], attribute: str, default: int) -> int:
    value = getattr(settings, attribute, None) if settings is not None else None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return default
    return int(value)


def check_budget_exhausted(
    session: Dict[str, Any],
    usage: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
    *,
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS,
    max_turns: int = DEFAULT_MAX_TURNS,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> Optional[str]:
    """Pure ceiling check: the breached ceiling name, or None.

    Thresholds: duration fires at `>=` the ceiling; turns and tokens fire on
    the counter that first EXCEEDS the ceiling (the 81st turn at a ceiling
    of 80, budget+1 tokens at the budget). Overridable per-session via
    ``session_metadata["budget_overrides"]`` (tighten-only, see module
    docstring). No database access; safe to call at any state-advance tick.
    """
    usage = usage or {}
    elapsed = _elapsed_seconds(session, usage, now)
    if elapsed is not None and elapsed >= _tightened(max_duration_seconds, session, DURATION_CEILING):
        return DURATION_CEILING
    if _turns(session, usage) > _tightened(max_turns, session, TURNS_CEILING):
        return TURNS_CEILING
    if total_tokens(usage) > _tightened(token_budget, session, TOKEN_CEILING):
        return TOKEN_CEILING
    return None


def usage_report(
    session: Dict[str, Any],
    usage: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """The visible usage counters, with M7-preflight honest fallbacks."""
    usage = usage or {}
    turns_value = usage.get("turns")
    turns: float = (
        float(turns_value)
        if isinstance(turns_value, (int, float)) and not isinstance(turns_value, bool)
        else float(len(session.get("transcript") or []))
    )
    return {
        "turns": turns,
        "elapsed_seconds": _elapsed_seconds(session, usage, now),
        "prompt_tokens": _number(usage, "prompt_tokens"),
        "completion_tokens": _number(usage, "completion_tokens"),
        "total_tokens": total_tokens(usage),
    }


def total_tokens(usage: Dict[str, Any]) -> float:
    """Explicit total wins; AI-runtime reports without one sum their parts."""
    total = _number(usage, "total_tokens")
    if total <= 0:
        total = _number(usage, "prompt_tokens") + _number(usage, "completion_tokens")
    return total


def _elapsed_seconds(
    session: Dict[str, Any], usage: Dict[str, Any], now: Optional[str]
) -> Optional[float]:
    """Seconds since call start; `usage["started_at"]` (M7 domain) wins, then
    the session intake timestamp. Without a parseable anchor there is no
    evidence of duration, so None (no breach) rather than an assumption."""
    if isinstance(usage.get("elapsed_seconds"), (int, float)) and not isinstance(
        usage.get("elapsed_seconds"), bool
    ):
        return float(usage["elapsed_seconds"])
    anchor = usage.get("started_at") or session.get("created_at")
    started = parse_iso(anchor if isinstance(anchor, str) else None)
    if started is None:
        return None
    current = parse_iso(now) if isinstance(now, str) else utcnow()
    if current is None:
        return None
    return (current - started).total_seconds()
def _turns(session: Dict[str, Any], usage: Dict[str, Any]) -> float:
    turns_value = usage.get("turns")
    if isinstance(turns_value, (int, float)) and not isinstance(turns_value, bool):
        return float(turns_value)
    return float(len(session.get("transcript") or []))


def _number(usage: Dict[str, Any], key: str) -> float:
    value = usage.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _tightened(configured: int, session: Dict[str, Any], ceiling: str) -> int:
    """Apply a per-session override; invalid or widening values are ignored."""
    metadata = session.get("session_metadata")
    overrides = metadata.get("budget_overrides") if isinstance(metadata, dict) else None
    value = overrides.get(ceiling) if isinstance(overrides, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return configured
    return min(configured, int(value))


def enforce_session_budgets(
    db: Database,
    tenant_id: str,
    session_id: str,
    usage: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
    settings: Optional[Any] = None,
) -> Optional[str]:
    """Advance-path tick: check the ceilings and orderly-terminate on breach.

    Sole-writer safe: the session row is re-read at enforcement time, an
    already-terminal session is left untouched (no transition, no event),
    and the `voice_session.terminated` event with reason
    `budget_exhausted` is recorded exactly once (idempotent repeats report
    the recorded breach without duplicating it). Returns the breached
    ceiling name or None.
    """
    session = db.get_voice_session(tenant_id, session_id)
    if session is None:
        return None
    breached = check_budget_exhausted(session, usage, now, **configured_ceilings(settings))
    if breached is None:
        return None
    prior = _prior_budget_termination(db, tenant_id, session_id)
    if session["status"] in SESSION_TERMINAL_STATUSES:
        return (prior or {}).get("breached")
    if prior is not None:
        db.update_voice_session(tenant_id, session_id, status="terminated")
        return breached
    report = usage_report(session, usage, now)
    db.update_voice_session(tenant_id, session_id, status="terminated")
    db.record_voice_session_event(
        tenant_id,
        session_id,
        TERMINATED_EVENT,
        {
            "reason": REASON_BUDGET_EXHAUSTED,
            "breached": breached,
            "usage": {key: value for key, value in report.items() if value is not None},
        },
    )
    return breached


def _prior_budget_termination(
    db: Database, tenant_id: str, session_id: str
) -> Optional[Dict[str, Any]]:
    for event in db.list_voice_session_events(tenant_id, session_id):
        if event.get("event_type") == TERMINATED_EVENT:
            data = event.get("data") or {}
            if data.get("reason") == REASON_BUDGET_EXHAUSTED:
                return data
    return None
