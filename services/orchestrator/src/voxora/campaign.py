"""Authoritative outbound campaign engine.

Owned responsibilities: campaign lifecycle transitions, atomic recipient
claims, idempotent call rows, pre-dial eligibility, retry classification,
concurrency limits, and restart recovery handoff.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .db import Database, iso
from .eligibility import evaluate, next_backoff, summarize
from .telephony.base import CallRequest, TelephonyAdapter

logger = logging.getLogger(__name__)
_SKIP_CODES = {"contact_suppressed", "suppression_list", "consent_required", "missing_phone", "invalid_timezone"}


class CampaignError(RuntimeError):
    pass


class CampaignEngine:
    def __init__(
        self,
        db: Database,
        telephony: TelephonyAdapter,
        from_number: Optional[str] = None,
        worker_threads: int = 4,
        global_concurrency: int = 5,
        clock: Optional[Any] = None,
    ):
        self.db = db
        self.telephony = telephony
        self.from_number = from_number
        self.worker_threads = max(1, worker_threads)
        self.global_concurrency = max(1, global_concurrency)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_lock = threading.Lock()

    def now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------ lifecycle
    def start(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        campaign = self._require_campaign(tenant_id, campaign_id)
        if campaign["status"] not in {"draft", "scheduled", "paused"}:
            raise CampaignError(f"Cannot start campaign in status {campaign['status']}")
        if not self.db.campaign_counts(tenant_id, campaign_id).get("recipients"):
            raise CampaignError("Campaign has no recipients")
        updated = self.db.update_campaign_status(
            tenant_id, campaign_id, "running", started_at=campaign.get("started_at") or iso(self.now())
        )
        self.db.audit(tenant_id, "operator", "campaign.started", "campaign", campaign_id)
        return updated  # type: ignore[return-value]

    def schedule(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        campaign = self._require_campaign(tenant_id, campaign_id)
        if not campaign.get("schedule_at"):
            raise CampaignError("Campaign has no schedule_at")
        updated = self.db.update_campaign_status(tenant_id, campaign_id, "scheduled")
        self.db.audit(tenant_id, "operator", "campaign.scheduled", "campaign", campaign_id)
        return updated  # type: ignore[return-value]

    def pause(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        self._require_campaign(tenant_id, campaign_id)
        updated = self.db.update_campaign_status(tenant_id, campaign_id, "paused", paused_at=iso(self.now()))
        self.db.audit(tenant_id, "operator", "campaign.paused", "campaign", campaign_id)
        return updated  # type: ignore[return-value]

    def resume(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        self._require_campaign(tenant_id, campaign_id)
        updated = self.db.update_campaign_status(tenant_id, campaign_id, "running")
        self.db.audit(tenant_id, "operator", "campaign.resumed", "campaign", campaign_id)
        return updated  # type: ignore[return-value]

    def cancel(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        cancelled = self.db.cancel_campaign(tenant_id, campaign_id)
        self.db.audit(tenant_id, "operator", "campaign.canceled", "campaign", campaign_id)
        return cancelled

    def validate_recipients(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        campaign = self._require_campaign(tenant_id, campaign_id)
        recipients, total = self.db.list_recipients(tenant_id, campaign_id, limit=5000)
        decisions = [
            summarize(self.db, campaign, {'id': r["contact_id"], 'phone': r["phone"], 'consent_state': r["consent_state"], 'suppressed': r["suppressed"], 'timezone': r["timezone"]})
            for r in recipients
        ]
        eligible = sum(1 for d in decisions if d["eligible"])
        by_code: Dict[str, int] = {}
        for d in decisions:
            by_code[d["code"]] = by_code.get(d["code"], 0) + 1
        return {"campaignId": campaign_id, "total": total, "eligible": eligible, "blocked": total - eligible, "byCode": by_code, "decisions": decisions[:200]}

    # ------------------------------------------------------------ execution
    def run_once(self) -> int:
        """Claim and process one bounded batch. Returns the number of dials."""
        with self._run_lock:
            for due in self.db.promote_due_campaigns():
                self.db.audit(
                    due["tenantId"], "worker", "campaign.due_promoted", "campaign", due["campaignId"]
                )
            claimed = self.db.claim_recipients(self.global_concurrency)
            if not claimed:
                return 0
            if len(claimed) == 1 or self.worker_threads == 1:
                for item in claimed:
                    self._process_item(item)
                return len(claimed)
            with ThreadPoolExecutor(max_workers=min(len(claimed), self.worker_threads)) as pool:
                list(pool.map(self._safe_process, claimed))
            return len(claimed)

    def run_until_idle(self, max_rounds: int = 25) -> int:
        processed = 0
        for _ in range(max_rounds):
            count = self.run_once()
            processed += count
            if count == 0:
                break
        return processed

    def _safe_process(self, item: Dict[str, Any]) -> None:
        try:
            self._process_item(item)
        except Exception:  # noqa: BLE001 - one bad dial must not kill the batch
            logger.exception("Campaign dial failed", extra={"callId": item.get("call_id")})
            self._fail_call(item, "internal_error", "Dial failed with an unexpected error")

    def _process_item(self, item: Dict[str, Any]) -> None:
        campaign = self.db.get_campaign_unscoped(item["campaign_id"])
        if campaign is None:
            raise CampaignError("Campaign missing while processing claim")
        tenant_id = campaign["tenant_id"]
        call_id = item["call_id"]
        contact = self.db.get_contact(tenant_id, item["contact_id"])
        if contact is None:
            self._fail_call(item, "contact_missing", "Contact disappeared before dialing")
            return

        decision = evaluate(self.db, campaign, contact, self._clock())
        if not decision.eligible:
            self._handle_ineligible(item, campaign, decision)
            return

        now = self._clock()
        self.db.update_call(tenant_id, call_id, target="dialing", started_at=iso(now))
        self.db.add_call_event(
            tenant_id, call_id, "dialing", iso(now), {"provider": self.telephony.provider}
        )

        agent = self.db.get_agent(tenant_id, campaign["agent_id"]) or {}
        request = CallRequest(
            call_id=call_id,
            campaign_id=campaign["id"],
            to_number=contact["phone"],
            from_number=self.from_number,
            idempotency_key=item["idempotency_key"],
            attempt=int(item["attempts"]),
            dry_run=bool(campaign["dry_run"]),
            agent={
                "model": agent.get("model", "mock-llm"),
                "voice": agent.get("voice", "mock-voice"),
                "systemPrompt": agent.get("system_prompt", ""),
                "maxTurnSeconds": agent.get("max_turn_seconds", 30),
            },
            metadata={
                "contactName": f"{contact.get('first_name') or ''} {contact.get('last_name') or ''}".strip(),
                "contactTimezone": contact.get("timezone"),
            },
        )
        result = self.telephony.start_call(request)
        self._apply_result(item, campaign, contact, result)
        if campaign["status"] == "running":
            self.db.complete_campaign_if_done(tenant_id, campaign["id"])

    def _apply_result(
        self,
        item: Dict[str, Any],
        campaign: Dict[str, Any],
        contact: Dict[str, Any],
        result: Any,
    ) -> None:
        tenant_id = campaign["tenant_id"]
        call_id = item["call_id"]
        attempt = int(item["attempts"])
        for event in result.events:
            self.db.add_call_event(tenant_id, call_id, event.event_type, event.at, event.data)
        for stage, duration in result.latency_ms.items():
            self.db.add_latency(tenant_id, call_id, stage, duration, result.simulated)

        fields: Dict[str, Any] = {
            "provider": result.provider,
            "provider_call_id": result.provider_call_id,
            "ended_at": result.ended_at,
            "disposition": result.disposition,
        }
        if result.status == "completed":
            fields.update(
                answered_at=result.answered_at,
                duration_ms=result.duration_ms,
            )
            self.db.update_call(tenant_id, call_id, target="completed", **fields)
            self.db.mark_recipient(
                tenant_id,
                item["id"],
                "completed",
                eligibility_code="eligible",
            )
            return

        fields.update(
            error_code=result.error_code,
            error_message=result.error_message,
            duration_ms=0,
        )
        self.db.update_call(tenant_id, call_id, target=result.status, **fields)
        if result.retryable and attempt < int(campaign["max_attempts"]):
            retry_at = next_backoff(campaign, attempt, self._clock())
            self.db.mark_recipient(
                tenant_id,
                item["id"],
                "queued",
                last_error=result.disposition,
                next_attempt_at=retry_at,
                eligibility_code=decision_code(result.disposition),
            )
        else:
            self.db.mark_recipient(
                tenant_id,
                item["id"],
                "failed",
                last_error=result.disposition,
                eligibility_code=decision_code(result.disposition),
            )

    def _handle_ineligible(
        self,
        item: Dict[str, Any],
        campaign: Dict[str, Any],
        decision: Any,
    ) -> None:
        tenant_id = campaign["tenant_id"]
        call_id = item["call_id"]
        self.db.update_call(
            tenant_id,
            call_id,
            target="canceled",
            error_code=decision.code,
            error_message=decision.reason,
            ended_at=iso(self._clock()),
        )
        self.db.add_call_event(
            tenant_id,
            call_id,
            "policy_blocked",
            iso(self._clock()),
            {"code": decision.code, "reason": decision.reason, "retryable": decision.retryable},
        )
        if decision.retryable and decision.retry_at:
            self.db.mark_recipient(
                tenant_id,
                item["id"],
                "queued",
                last_error=decision.reason,
                next_attempt_at=decision.retry_at,
                eligibility_code=decision.code,
            )
        else:
            status = "suppressed" if decision.code in {"contact_suppressed", "suppression_list"} else "skipped"
            self.db.mark_recipient(
                tenant_id,
                item["id"],
                status,
                last_error=decision.reason,
                eligibility_code=decision.code,
            )
        if decision.code not in {"outside_calling_window"}:
            self.db.audit(
                tenant_id,
                "policy",
                "campaign.recipient_blocked",
                "recipient",
                item["id"],
                {"code": decision.code, "reason": decision.reason, "campaignId": campaign["id"]},
            )
        self.db.complete_campaign_if_done(tenant_id, campaign["id"])

    def _fail_call(self, item: Dict[str, Any], code: str, message: str) -> None:
        campaign = self.db.get_campaign_unscoped(item["campaign_id"])
        if not campaign:
            return
        tenant_id = campaign["tenant_id"]
        self.db.update_call(
            tenant_id,
            item["call_id"],
            target="failed",
            error_code=code,
            error_message=message,
            ended_at=iso(self._clock()),
        )
        self.db.mark_recipient(tenant_id, item["id"], "failed", last_error=message, eligibility_code=code)
        if campaign["status"] == "running":
            self.db.complete_campaign_if_done(tenant_id, campaign["id"])

    def _require_campaign(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        campaign = self.db.get_campaign(tenant_id, campaign_id)
        if not campaign:
            raise KeyError("campaign not found")
        return campaign


def decision_code(disposition: str) -> str:
    mapping = {
        "no_answer": "transient_no_answer",
        "busy": "transient_busy",
        "provider_failure": "permanent_provider_failure",
        "answered": "eligible",
    }
    return mapping.get(disposition, disposition)
