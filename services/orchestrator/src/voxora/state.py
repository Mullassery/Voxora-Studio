"""Explicit campaign, call, and recipient state machines."""

from __future__ import annotations

from typing import Dict, FrozenSet, Set

CAMPAIGN_STATUSES = {
    "draft",
    "scheduled",
    "running",
    "paused",
    "completed",
    "canceled",
    "failed",
}
CALL_STATUSES = {
    "queued",
    "dialing",
    "ringing",
    "in_progress",
    "completed",
    "failed",
    "canceled",
    "no_answer",
    "busy",
}
RECIPIENT_STATUSES = {
    "pending",
    "queued",
    "dialing",
    "completed",
    "failed",
    "canceled",
    "skipped",
    "suppressed",
}

CAMPAIGN_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "draft": frozenset({"scheduled", "running", "canceled"}),
    "scheduled": frozenset({"running", "canceled", "paused"}),
    "running": frozenset({"paused", "canceled", "completed", "failed"}),
    "paused": frozenset({"running", "canceled"}),
    "completed": frozenset(),
    "canceled": frozenset(),
    "failed": frozenset(),
}

CALL_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "queued": frozenset({"dialing", "canceled", "failed"}),
    "dialing": frozenset({"ringing", "in_progress", "failed", "canceled", "busy", "no_answer", "completed"}),
    "ringing": frozenset({"in_progress", "failed", "canceled", "no_answer"}),
    "in_progress": frozenset({"completed", "failed", "canceled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
    "no_answer": frozenset(),
    "busy": frozenset(),
}

RECIPIENT_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "pending": frozenset({"queued", "skipped", "canceled", "suppressed"}),
    "queued": frozenset({"dialing", "skipped", "canceled", "suppressed"}),
    "dialing": frozenset({"queued", "completed", "failed", "canceled", "skipped"}),
    "completed": frozenset(),
    "failed": frozenset({"queued"}),
    "canceled": frozenset(),
    "skipped": frozenset({"queued"}),
    "suppressed": frozenset(),
}

TERMINAL_CAMPAIGN_STATUSES: Set[str] = {"completed", "canceled", "failed"}
ACTIVE_CALL_STATUSES = {"queued", "dialing", "ringing", "in_progress"}
ACTIVE_RECIPIENT_STATUSES = {"pending", "queued", "dialing"}


class InvalidTransition(ValueError):
    pass


def assert_transition(machine: Dict[str, FrozenSet[str]], current: str, target: str) -> None:
    allowed = machine.get(current)
    if allowed is None:
        raise InvalidTransition(f"Unknown state: {current}")
    if target not in allowed:
        raise InvalidTransition(f"Invalid transition {current} -> {target}")
