"""Pre-dial policy checks. Re-run immediately before every dial."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import Database, iso, parse_iso


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    code: str
    reason: str
    retryable: bool = False
    retry_at: Optional[str] = None


def _window_state(
    start: str, end: str, now_local: datetime
) -> tuple[bool, datetime]:
    start_h, start_m = (int(part) for part in start.split(":"))
    end_h, end_m = (int(part) for part in end.split(":"))
    start_time = time(start_h, start_m)
    end_time = time(end_h, end_m)
    today_start = now_local.replace(
        hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0
    )
    today_end = now_local.replace(
        hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0
    )
    if start_time == end_time:
        return True, now_local
    if start_time < end_time:
        if today_start <= now_local < today_end:
            return True, now_local
        next_open = today_start if now_local < today_start else today_start + timedelta(days=1)
        return False, next_open
    # Overnight window, e.g. 21:00 -> 06:00.
    if now_local >= today_start or now_local < today_end:
        return True, now_local
    return False, today_start


def evaluate(
    db: Database,
    campaign: Dict[str, Any],
    contact: Dict[str, Any],
    now: Optional[datetime] = None,
) -> EligibilityDecision:
    now = now or datetime.now(timezone.utc)
    phone = (contact.get("phone") or "").strip()
    if not phone:
        return EligibilityDecision(False, "missing_phone", "Contact has no phone number")
    if int(contact.get("suppressed") or 0) == 1:
        return EligibilityDecision(
            False,
            "contact_suppressed",
            contact.get("suppression_reason") or "Contact is suppressed",
        )
    if db.is_suppressed(campaign["tenant_id"], phone):
        return EligibilityDecision(
            False, "suppression_list", "Phone is on the tenant suppression list"
        )
    if contact.get("consent_state") != "granted":
        return EligibilityDecision(
            False,
            "consent_required",
            f"Consent state is {contact.get('consent_state') or 'unknown'}; granted consent is required",
        )

    start = campaign.get("calling_window_start")
    end = campaign.get("calling_window_end")
    if not start or not end:
        return EligibilityDecision(True, "eligible", "Eligible")

    tz_name = contact.get("timezone") or campaign.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return EligibilityDecision(
            False, "invalid_timezone", f"Unknown timezone: {tz_name}"
        )
    now_local = now.astimezone(tz)
    open_now, next_open = _window_state(start, end, now_local)
    if open_now:
        return EligibilityDecision(True, "eligible", "Eligible")
    retry_at = next_open.astimezone(timezone.utc)
    return EligibilityDecision(
        False,
        "outside_calling_window",
        f"Outside calling window {start}-{end} in {tz_name}",
        retryable=True,
        retry_at=iso(retry_at),
    )


def summarize(db: Database, campaign: Dict[str, Any], contact: Dict[str, Any]) -> Dict[str, Any]:
    decision = evaluate(db, campaign, contact)
    return {
        "contactId": contact.get("id"),
        "eligible": decision.eligible,
        "code": decision.code,
        "reason": decision.reason,
        "retryable": decision.retryable,
        "retryAt": decision.retry_at,
    }


def next_backoff(campaign: Dict[str, Any], attempt: int, now: datetime) -> str:
    base = max(1, int(campaign.get("retry_backoff_seconds") or 30))
    delay = min(base * (2 ** max(0, attempt - 1)), 3600)
    return iso(now + timedelta(seconds=delay))


def is_due(value: Optional[str], now: datetime) -> bool:
    parsed = parse_iso(value)
    return parsed is None or parsed <= now
