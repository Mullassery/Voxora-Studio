"""Voice-session service: the CRM-external session lifecycle over call rows.

Ownership per the ecosystem ADR (docs/migration/migration-adr.md): Voxora
owns voice-session state, telephony, transcript records, and outbound event
delivery. The requesting application (e.g. PyInterviewBot) owns whatever the
`external_session_id` semantically references — Voxora treats it as opaque.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.request  # stdlib transport: no new dependency; tests inject fakes
from typing import Any, Callable, Dict, List, Optional

from .db import Database, iso

# voice_session.* event types per the versioned envelope contract.
EVENT_VERSION = "1.0"

_DISPOSITION_STATUS = {
    "answered": "completed",
    "completed": "completed",
    "busy": "busy",
    "no_answer": "no_answer",
    "failed": "failed",
    "canceled": "terminated",
}

_SESSION_TERMINAL = {"completed", "busy", "no_answer", "failed", "terminated", "canceled"}

TRANSPORT: Any = urllib.request.urlopen  # overridable in tests/post-deploy


class VoiceSessionError(Exception):
    """Voice-session misuse (unknown session, invalid transition)."""

    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def build_event(
    tenant_id: str,
    session: Dict[str, Any],
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[str] = None,
    event_id: str = "",
) -> Dict[str, Any]:
    """Versioned event envelope; `event_id` filled after persistence when known."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "event_version": EVENT_VERSION,
        "occurred_at": occurred_at or iso(),
        "workspace": tenant_id,
        "session_id": session["id"],
        "external_application": session["external_application"],
        "external_session_id": session["external_session_id"],
        "data": data or {},
    }


def session_status_for(disposition: str, ok: bool) -> str:
    if not ok:
        return "failed" if disposition != "canceled" else "terminated"
    return _DISPOSITION_STATUS.get(disposition, "completed" if ok else "failed")


def settle_voice_session(
    db: Database,
    call: Dict[str, Any],
    disposition: str,
    ok: bool,
    result: Optional[Dict[str, Any]] = None,
    settings: Optional[Any] = None,
) -> Optional[str]:
    """Terminal settle for a campaign-less voice-session call (Twilio callbacks).

    Keeps the sole-writer rule: only this path transitions the session after
    dialing, and every transition emits the versioned event envelope.

    Budget guard mount (roadmap M6): the per-session ceilings are checked
    here, on the sole-writer settle tick, so every settle flow (mock intake
    completion, provider callbacks, explicit cancel) enforces them. A breach
    orderly-terminates with `voice_session.terminated` reason
    `budget_exhausted` instead of the disposition; M7 adds per-turn ticks.
    """
    tenant_id = call["tenant_id"]
    session = db.get_voice_session_by_call(tenant_id, call["id"])
    if session is None:
        return None
    from .voice_session_budgets import enforce_session_budgets

    if enforce_session_budgets(db, tenant_id, session["id"], settings=settings) is not None:
        return "terminated"
    status = session_status_for(disposition, ok)
    data: Dict[str, Any] = {"status": status, "disposition": disposition}
    if result:
        data.update(result)
    prior = session["status"]
    if session["status"] != "terminated":
        db.update_voice_session(tenant_id, session["id"], status=status)
    db.record_voice_session_event(
        tenant_id,
        session["id"],
        f"voice_session.{status}",
        data,
    )
    transcript = session.get("transcript") or []
    if status == "completed" and transcript and prior != "completed":
        from .voice_analytics import compute_speech_analytics

        ready_data: Dict[str, Any] = {"transcript_available": True}
        recording_url = session.get("recording_url")
        if recording_url:
            # Speech analytics (M8/P10): computed once, on the finalize
            # tick, only when the recorded audio is actually fetchable.
            # Absent/unfetchable audio ⇒ the key stays absent — the field
            # is never zero-fabricated.
            try:
                with TRANSPORT(recording_url, timeout=5) as response:  # type: ignore[operator]
                    audio = response.read()
            except Exception:  # noqa: BLE001 - omission is the honest outcome
                audio = None
            analytics = compute_speech_analytics(transcript, audio)
            if analytics is not None:
                db.update_voice_session_metadata(
                    tenant_id, session["id"], {"speech_analytics": analytics}
                )
                ready_data["speech_analytics"] = analytics
        db.record_voice_session_event(
            tenant_id,
            session["id"],
            "transcript.ready",
            ready_data,
        )
    return status


def signed_envelope_body(
    event: Dict[str, Any], secret: str
) -> tuple:
    """Exact signed body bytes + signature value for an outbound envelope.

    Shared by the direct transport and the PyReverseETL transport so replays
    carry a byte-identical signature: signing happens here, once, and the
    secret never leaves Voxora.
    """
    body = json.dumps(event, sort_keys=True).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, f"v1={digest}"


def deliver_webhook(
    url: str,
    event: Dict[str, Any],
    secret: str,
    transport: Optional[Callable[[Any, Any], Any]] = None,
    timeout_seconds: float = 5.0,
) -> tuple:
    """Signed at-least-once delivery; returns (ok, detail).

    Signature: HMAC-SHA256 over the exact body bytes, header
    ``x-voxora-signature: v1=<hex>``. Consumers must verify before trusting
    (same rule the PyInterviewBot side already honours for inbound).
    """
    body, signature = signed_envelope_body(event, secret)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-voxora-signature": signature,
        },
        method="POST",
    )
    used_transport = transport if transport is not None else TRANSPORT
    try:
        with used_transport(request, timeout=timeout_seconds) as response:  # type: ignore[call-arg]  # transport principal
            code = getattr(response, "status", getattr(response, "getcode", lambda: 0)())
            ok = 200 <= int(code) < 300
            return ok, f"http_{code}"
    except Exception as error:  # classified honestly below
        return False, f"{type(error).__name__}: {error}"[:500]


def _event_envelope(
    db: Database, tenant_id: str, session_id: str, event: Dict[str, Any]
) -> Dict[str, Any]:
    session = db.get_voice_session(tenant_id, session_id)
    if session is None:
        raise VoiceSessionError(404, "Voice session not found", "unknown_session")
    return {
        "event_id": event["id"],
        "event_type": event["event_type"],
        "event_version": event.get("event_version", EVENT_VERSION),
        "occurred_at": event["occurred_at"],
        "session_id": session_id,
        "external_application": session["external_application"],
        "external_session_id": session["external_session_id"],
        "data": event.get("data", {}),
    }


def dispatch_pending_webhooks(
    db: Database, settings: Any, tenant_id: str, session_id: str
) -> None:
    """Best-effort production dispatch of undelivered session events.

    Never raises: delivery failures are recorded as delivery rows (and
    dead-lettered by the PyReverseETL transport) so a webhook outage must
    not fail the API/worker path that produced the event. No-op when the
    session has no webhook_url (no contract consumer registered).
    """
    session = db.get_voice_session(tenant_id, session_id)
    if session is None or not session.get("webhook_url"):
        return
    try:
        if settings.webhook_transport == "pyreverseetl":
            dispatch_session_events_via_pyreverseetl(
                db,
                tenant_id,
                session_id,
                session["webhook_url"],
                settings.outbound_webhook_secret,
                dead_letter_path=settings.webhook_dead_letter_path,
                max_attempts=settings.webhook_max_attempts,
                timeout_secs=settings.webhook_timeout_secs,
            )
        else:
            dispatch_session_events(
                db,
                tenant_id,
                session_id,
                session["webhook_url"],
                settings.outbound_webhook_secret,
            )
    except Exception as error:  # noqa: BLE001 - dispatch must not break the producing path
        logging.getLogger(__name__).warning(
            "webhook dispatch failed for session %s: %s", session_id, error
        )


def dispatch_session_events_via_pyreverseetl(
    db: Database,
    tenant_id: str,
    session_id: str,
    webhook_url: str,
    secret: str,
    *,
    dead_letter_path: str,
    max_attempts: int = 3,
    timeout_secs: float = 10.0,
    retry_base_delay: float = 0.5,
    sync_fn: Optional[Callable[..., Any]] = None,
) -> List[int]:
    """Deliver undelivered events through the PyReverseETL webhook transport.

    Ownership (roadmap M5/P1): queue buffering, backpressure, retry/backoff,
    and dead-lettering are delegated to PyReverseETL's WebhookConnector —
    Voxora does not build a dead-letter state machine. HMAC signing stays
    Voxora-side (the secret never leaves this repo); each event is one
    PyReverseETL sync so every retry/replay carries the byte-identical
    signature computed here. ``voice_session_deliveries`` rows remain the
    Voxora-side audit trail of enqueue + terminal state.

    ``sync_fn`` defaults to ``pyreverseetl.connectors.run_with_connector``;
    tests inject a fake to pin the request contract without live HTTP.
    """
    if sync_fn is None:
        from pyreverseetl.connectors import BaseConnector, WebhookConnector, run_with_connector

        class _SingleRecordSource(BaseConnector):
            name = "inline"

            def read(self, config: Any, limit: Any = None) -> List[Dict[str, Any]]:
                return [config["record"]]

            def write(self, config: Any, rows: Any) -> int:
                raise NotImplementedError

        destination = WebhookConnector()
        runner = run_with_connector
    outcomes: List[int] = []
    for event in db.list_voice_session_events(tenant_id, session_id):
        attempts = db.list_event_deliveries(tenant_id, event["id"])
        if len(attempts) >= max_attempts:
            outcomes.append(0)
            continue
        envelope = _event_envelope(db, tenant_id, session_id, event)
        body, signature = signed_envelope_body(envelope, secret)
        record = {
            "event_id": envelope["event_id"],
            "body": body.decode("utf-8"),
            "signature": signature,
        }
        config = {
            "url": webhook_url,
            "timeout_secs": timeout_secs,
            "max_attempts": max_attempts,
            "retry_base_delay": retry_base_delay,
            "signature_header": "x-voxora-signature",
            "signature_field": "signature",
            "body_field": "body",
            "dead_letter_path": dead_letter_path,
        }
        if sync_fn is not None:
            result = sync_fn(_FakeSource(record), {}, "webhook", config)
        else:
            result = runner(_SingleRecordSource(), {"record": record}, destination, config)
        ok = getattr(result, "rows_written", 0) >= 1
        detail = (
            f"pyreverseetl run {getattr(result, 'run_id', '?')}"
            f" written={getattr(result, 'rows_written', 0)}"
            f" failed={getattr(result, 'rows_failed', 0)}"
        )
        db.record_event_delivery(
            tenant_id, event["id"], session_id, len(attempts) + 1, ok, detail
        )
        outcomes.append(1 if ok else 0)
    return outcomes


class _FakeSource:
    """Test double matching the BaseConnector.read surface (sync_fn path)."""

    name = "inline-fake"

    def __init__(self, record: Dict[str, Any]) -> None:
        self.record = record

    def read(self, config: Any, limit: Any = None) -> List[Dict[str, Any]]:
        return [self.record]

    def write(self, config: Any, rows: Any) -> int:
        raise NotImplementedError


def dispatch_session_events(
    db: Database,
    tenant_id: str,
    session_id: str,
    webhook_url: str,
    secret: str,
    transport: Optional[Callable[[Any, Any], Any]] = None,
    max_attempts: int = 3,
) -> List[int]:
    """Attempt delivery of every undelivered recorded event (bounded retries)."""
    outcomes: List[int] = []
    for event in db.list_voice_session_events(tenant_id, session_id):
        envelope = {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "event_version": event.get("event_version", EVENT_VERSION),
            "occurred_at": event["occurred_at"],
            "session_id": session_id,
            "external_application": None,  # filled below from session
            "external_session_id": None,
            "data": event.get("data", {}),
        }
        session = db.get_voice_session(tenant_id, session_id)
        if session is None:
            raise VoiceSessionError(404, "Voice session not found", "unknown_session")
        envelope["external_application"] = session["external_application"]
        envelope["external_session_id"] = session["external_session_id"]
        attempts = db.list_event_deliveries(tenant_id, event["id"])
        attempt = len(attempts) + 1
        ok, detail = deliver_webhook(webhook_url, envelope, secret, transport)
        db.record_event_delivery(tenant_id, event["id"], session_id, attempt, ok, detail)
        outcomes.append(1 if ok else 0)
        if len(attempts) + 1 >= max_attempts:
            continue
    return outcomes
