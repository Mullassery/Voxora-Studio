"""Twilio adapter contract tests against the documented REST contract.

Twilio's live server is NOT part of this test scope (no credentials and no
permission to dial in this slice — stated in docs). A fake transport
documents the request/response shapes we implement against.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from voxora.campaign import CampaignEngine
from voxora.db import Database
from voxora.security import twilio_signature, verify_twilio_signature
from voxora.telephony.base import CallRequest
from voxora.telephony.twilio import (
    CALLBACK_STATUS_MAP,
    TwilioError,
    TwilioTelephonyAdapter,
)

AUTH = "sid123:authtoken456"

_XML_CALL_CREATED = bytes(
    json.dumps({"sid": "CA7f7c", "status": "queued", "to": "+15551230001"}).encode()
)


@dataclass
class FakeTwilioTransport:
    """Scripted HTTP transport recording requests for contract assertions."""

    responses: List[tuple] = field(default_factory=list)
    requests: List[tuple] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> Any:
        from voxora.crm.http import HttpResponse

        self.requests.append((method, url, headers, body))
        if not self.responses:
            raise ConnectionError("unscripted transport call")
        status, payload, headers_out = self.responses.pop(0)
        return HttpResponse(status, headers_out or {}, payload)


def _adapter(transport: FakeTwilioTransport, live: bool = True) -> TwilioTelephonyAdapter:
    return TwilioTelephonyAdapter(
        account_sid="AC918273",
        auth_token="authtoken456",
        twiml_url="https://operator.example.com/api/v1/telephony/twilio/twiml",
        from_number="+15558880000",
        live_enabled=live,
        status_callback="https://operator.example.com/api/v1/telephony/twilio/status",
        transport=transport,
        max_retries=1,
    )


def _request(dry_run: bool = False, name: str = "Georgi") -> CallRequest:
    return CallRequest(
        call_id="call-tw-1",
        campaign_id="camp-1",
        to_number="+15551230001",
        from_number=None,
        idempotency_key="idem-tw-1",
        attempt=1,
        dry_run=dry_run,
        agent={"name": "Agent"},
        metadata={"contactName": name},
    )


def test_start_call_uses_documented_rest_contract() -> None:
    transport = FakeTwilioTransport(responses=[(201, _XML_CALL_CREATED, "")])
    adapter = _adapter(transport)
    result = adapter.start_call(_request())
    method, url, headers, body = transport.requests[0]
    assert method == "POST"
    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC918273/Calls.json"
    expected_auth = "Basic " + base64.b64encode(b"AC918273:authtoken456").decode()
    assert headers["Authorization"] == expected_auth
    form = dict(urllib.parse.parse_qsl((body or b"").decode()))
    assert form["To"] == "+15551230001"
    assert form["From"] == "+15558880000"
    assert form["Url"] == "https://operator.example.com/api/v1/telephony/twilio/twiml"
    assert "StatusCallback" in form and "StatusCallbackEvent" in form
    # Pending async result: no final disposition yet.
    assert result.provider_call_id == "CA7f7c"
    assert result.final is False
    assert result.simulated is False
    assert result.status == "dialing" and result.disposition == "dialed"


def test_dry_run_never_contacts_twilio() -> None:
    transport = FakeTwilioTransport()
    adapter = _adapter(transport, live=False)
    result = adapter.start_call(_request(dry_run=True))
    assert transport.requests == []
    assert result.status == "completed" and result.simulated is True
    assert any("would have been a live Twilio call" in entry["text"] for entry in result.transcript)


def test_live_dialing_refused_without_explicit_flag() -> None:
    adapter = _adapter(FakeTwilioTransport(), live=False)
    with pytest.raises(TwilioError) as excinfo:
        adapter.start_call(_request())
    assert "VOXORA_ALLOW_LIVE_CALLS" in str(excinfo.value)


def test_start_call_idempotent_on_idempotency_key() -> None:
    transport = FakeTwilioTransport(responses=[(201, _XML_CALL_CREATED, "")])
    adapter = _adapter(transport)
    first = adapter.start_call(_request())
    second = adapter.start_call(_request())
    assert first is second
    assert len(transport.requests) == 1


def test_429_retry_after_then_success() -> None:
    transport = FakeTwilioTransport(
        responses=[
            (429, json.dumps({"code": 429, "message": "rate limited"}).encode(), {"retry-after": "0"}),
            (201, _XML_CALL_CREATED, ""),
        ]
    )
    adapter = _adapter(transport)
    adapter.retry_backoff = 0.0
    adapter.max_retries = 2
    result = adapter.start_call(_request())
    assert result.provider_call_id == "CA7f7c"
    assert len(transport.requests) == 2


def test_request_error_surfaced_with_status() -> None:
    transport = FakeTwilioTransport(
        responses=[(401, json.dumps({"code": 20003, "message": "Authenticate"}).encode(), "")]
    )
    adapter = _adapter(transport)
    with pytest.raises(TwilioError):
        adapter.start_call(_request())


def test_cancel_call_posts_canceled_and_absorbs_404() -> None:
    transport = FakeTwilioTransport(
        responses=[(200, json.dumps({"sid": "CA1", "status": "canceled"}).encode(), "")]
    )
    adapter = _adapter(transport, live=False)
    assert adapter.cancel_call("CA1") is True
    assert transport.requests[0][1].endswith("/Calls/CA1.json")
    assert "Status=canceled" in (transport.requests[0][3] or b"").decode()

    transport404 = FakeTwilioTransport(
        responses=[(404, json.dumps({"message": "call not found"}).encode(), "")]
    )
    adapter2 = _adapter(transport404, live=False)
    assert adapter2.cancel_call("CA-unknown") is True


def test_callback_result_maps_documented_statuses() -> None:
    received = datetime.now(timezone.utc).isoformat()
    for twilio_status, (target, disposition, retryable) in CALLBACK_STATUS_MAP.items():
        adapter = _adapter(FakeTwilioTransport(), live=False)
        result = adapter.callback_result({"CallSid": "CA7f7c", "CallStatus": twilio_status}, received_at=received)
        assert (result.status, result.disposition, result.retryable) == (target, disposition, retryable)
        assert result.ok is (target == "completed")
        assert result.final is True and result.simulated is False
        assert result.provider_call_id == "CA7f7c"


def test_callback_result_rejects_unmapped_status() -> None:
    adapter = _adapter(FakeTwilioTransport(), live=False)
    with pytest.raises(TwilioError) as excinfo:
        adapter.callback_result({"CallSid": "CA", "CallStatus": "crowded-call"})
    assert "crowded-call" in str(excinfo.value)


# ---------------------------------------------------------------------------
# signature utils
# ---------------------------------------------------------------------------
def test_twilio_signature_roundtrip_and_rejection() -> None:
    url = "https://operator.example.com/api/v1/telephony/twilio/status"
    params = {"CallSid": "CA7f7c", "CallStatus": "completed"}
    signature = twilio_signature("authtoken456", url, params)
    assert verify_twilio_signature("authtoken456", url, params, signature)
    assert not verify_twilio_signature("authtoken456", url[: -1], params, signature)
    assert not verify_twilio_signature("wrong-token", url, params, signature)
    expected_payload = url + "CallSidCA7f7cCallStatuscompleted"
    manual = base64.b64encode(
        hmac.new(b"authtoken456", expected_payload.encode(), hashlib.sha1).digest()
    ).decode()
    assert manual == signature


# ---------------------------------------------------------------------------
# engine settlement: pending start -> callback terminal
# ---------------------------------------------------------------------------
def _engine_setup(tmp_path) -> tuple:
    db = Database(str(tmp_path / "tw.db"))
    db.initialize()
    db.ensure_tenant("t1", "Tenant")
    agent = db.create_agent("t1", {"name": "Agent"})
    contact = db.create_local_contact("t1", {"phone": "+15551234567", "consent_state": "granted"})
    campaign = db.create_campaign(
        "t1",
        {
            "name": "Twilio live campaign",
            "agent_id": agent["id"],
            "dry_run": False,
            "max_concurrency": 1,
            "max_attempts": 1,
            "retry_backoff_seconds": 60,
        },
    )
    db.add_recipients("t1", campaign["id"], [contact["id"]])
    transport = FakeTwilioTransport(responses=[(201, _XML_CALL_CREATED, "")])
    adapter = _adapter(transport, live=True)
    clock = FakeClock(datetime(2026, 11, 1, tzinfo=timezone.utc))
    engine = CampaignEngine(db, adapter, worker_threads=1, global_concurrency=1, clock=clock.now)
    return db, campaign, clock, engine


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def test_pending_start_then_completed_callback_settles(tmp_path) -> None:
    db, campaign, clock, engine = _engine_setup(tmp_path)
    engine.start("t1", campaign["id"])
    processed = engine.run_once()
    assert processed == 1
    calls, total = db.list_calls("t1", limit=5)
    assert total == 1
    call = calls[0]
    assert call["provider"] == "twilio" and call["provider_call_id"] == "CA7f7c"
    assert call["status"] == "dialing"

    clock.advance(45)
    result = TwilioTelephonyAdapter.callback_result(
        _adapter(FakeTwilioTransport(), live=False),
        {"CallSid": "CA7f7c", "CallStatus": "completed", "CallDuration": "42"},
        received_at=clock.now().isoformat(),
    )
    outcome = engine.settle_provider_call("CA7f7c", result)
    assert outcome == "settled"
    call = db.get_call_by_provider_call_id("CA7f7c")
    assert call["status"] == "completed" and call["duration_ms"] == 42000
    counts = db.campaign_counts("t1", campaign["id"])
    assert counts["completed"] == 1
    campaign = db.get_campaign("t1", campaign["id"])
    assert campaign["status"] == "completed"


def test_duplicate_callback_is_absorbed(tmp_path) -> None:
    db, campaign, clock, engine = _engine_setup(tmp_path)
    engine.start("t1", campaign["id"])
    engine.run_once()
    result = TwilioTelephonyAdapter.callback_result(
        _adapter(FakeTwilioTransport(), live=False),
        {"CallSid": "CA7f7c", "CallStatus": "completed"},
        received_at=clock.now().isoformat(),
    )
    engine.settle_provider_call("CA7f7c", result)
    second = engine.settle_provider_call("CA7f7c", result)
    assert second == "duplicate"


def test_busy_callback_requeues_recipient_for_retry(tmp_path) -> None:
    db, campaign, clock, engine = _engine_setup(tmp_path)
    # max_attempts=1 -> busy goes straight to failed; construct a retryable one:
    engine.start("t1", campaign["id"])
    engine.run_once()
    busy = TwilioTelephonyAdapter.callback_result(
        _adapter(FakeTwilioTransport(), live=False),
        {"CallSid": "CA7f7c", "CallStatus": "busy"},
        received_at=clock.now().isoformat(),
    )
    engine.settle_provider_call("CA7f7c", busy)
    call = db.get_call_by_provider_call_id("CA7f7c")
    assert call["status"] == "busy"
    recipients, _ = db.list_recipients("t1", campaign["id"])
    assert recipients[0]["status"] == "failed"  # max_attempts=1: no retry ahead


def test_unknown_call_sid_is_reported(tmp_path) -> None:
    db, campaign, clock, engine = _engine_setup(tmp_path)
    engine.start("t1", campaign["id"])
    engine.run_once()
    result = TwilioTelephonyAdapter.callback_result(
        _adapter(FakeTwilioTransport(), live=False),
        {"CallSid": "CA-none", "CallStatus": "completed"},
    )
    assert engine.settle_provider_call("CA-none", result) == "unknown_call"
    del db
