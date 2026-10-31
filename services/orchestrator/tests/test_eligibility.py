from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from voxora.db import Database, iso
from voxora.eligibility import evaluate, next_backoff


def _db(tmp_path) -> Database:
    db = Database(str(tmp_path / "elig.db"))
    db.initialize()
    return db


def _campaign(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "tenant_id": "t1",
        "dry_run": True,
        "calling_window_start": None,
        "calling_window_end": None,
        "timezone": "UTC",
        "retry_backoff_seconds": 30,
        "max_attempts": 2,
    }
    values.update(overrides)
    return values


def _contact(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "phone": "+15551230001",
        "consent_state": "granted",
        "suppressed": 0,
        "suppression_reason": None,
        "timezone": "UTC",
    }
    values.update(overrides)
    return values


def test_missing_consent_is_terminal(tmp_path) -> None:
    db = _db(tmp_path)
    decision = evaluate(db, _campaign(), _contact(consent_state="unknown"))
    assert decision.eligible is False
    assert decision.code == "consent_required"
    assert decision.retryable is False


def test_suppression_list_blocks_before_dialing(tmp_path) -> None:
    db = _db(tmp_path)
    db.add_suppression("t1", "+15551230001", "DNC")
    decision = evaluate(db, _campaign(), _contact())
    assert decision.eligible is False
    assert decision.code == "suppression_list"


def test_contact_suppression_blocks(tmp_path) -> None:
    db = _db(tmp_path)
    decision = evaluate(db, _campaign(), _contact(suppressed=1, suppression_reason="operator"))
    assert decision.code == "contact_suppressed"


def test_calling_window_with_timezone_retry(tmp_path) -> None:
    db = _db(tmp_path)
    now = datetime(2026, 1, 5, 5, 30, tzinfo=timezone.utc)  # 00:30 America/New_York
    campaign = _campaign(
        calling_window_start="09:00",
        calling_window_end="17:00",
        timezone="America/New_York",
    )
    decision = evaluate(db, campaign, _contact(timezone="America/New_York"), now=now)
    assert decision.eligible is False
    assert decision.code == "outside_calling_window"
    assert decision.retryable is True
    assert decision.retry_at is not None
    # 09:00 America/New_York == 14:00 UTC
    assert decision.retry_at.startswith("2026-01-05T14:00:00")


def test_inside_window_is_eligible(tmp_path) -> None:
    db = _db(tmp_path)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    campaign = _campaign(
        calling_window_start="09:00",
        calling_window_end="17:00",
        timezone="America/New_York",
    )
    decision = evaluate(db, campaign, _contact(timezone="America/New_York"), now=now)
    assert decision.eligible is True


def test_invalid_timezone_is_terminal(tmp_path) -> None:
    db = _db(tmp_path)
    decision = evaluate(
        db,
        _campaign(calling_window_start="09:00", calling_window_end="17:00"),
        _contact(timezone="Mars/Olympus"),
    )
    assert decision.code == "invalid_timezone"
    assert decision.retryable is False


def test_backoff_is_exponential_and_capped(tmp_path) -> None:
    campaign = _campaign(retry_backoff_seconds=30)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = iso(datetime.fromisoformat(next_backoff(campaign, 1, now)))
    second = iso(datetime.fromisoformat(next_backoff(campaign, 2, now)))
    assert first[:19] != second[:19]
