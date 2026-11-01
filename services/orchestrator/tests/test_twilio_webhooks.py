"""Twilio webhook endpoints: signature-verified settlement through the API.

These tests exercise `/api/v1/telephony/twilio/status` and the TwiML endpoint
end-to-end with the real settle path: campaign -> claim -> pending Twilio
start -> signed status callback -> final state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tests.test_twilio_adapter import _XML_CALL_CREATED, FakeTwilioTransport
from voxora.campaign import CampaignEngine
from voxora.telephony.twilio import TwilioTelephonyAdapter


def _create_conversation(client) -> str:
    """Create contact + campaign + recipient through the API."""
    AUTH = {"Authorization": "Bearer admin-token"}
    contact = client.post(
        "/api/v1/contacts",
        headers=AUTH,
        json={"first_name": "Geo", "last_name": "Twi", "phone": "+15551234999", "consent_state": "granted"},
    ).json()
    agent = client.post("/api/v1/agents", headers=AUTH, json={"name": "Agent"}).json()
    campaign = client.post(
        "/api/v1/campaigns",
        headers=AUTH,
        json={
            "name": "Twilio API campaign",
            "agent_id": agent["id"],
            "dry_run": False,
            "max_concurrency": 1,
            "max_attempts": 1,
        },
    ).json()
    client.post(
        f"/api/v1/campaigns/{campaign['id']}/recipients",
        headers=AUTH,
        json={"contact_ids": [contact["id"]]},
    )
    return campaign["id"]


def _twilio_adapter(client, live: bool = True, responses: Optional[List[tuple]] = None) -> TwilioTelephonyAdapter:
    twilio = TwilioTelephonyAdapter(
        account_sid="AC918273",
        auth_token="unit-auth-token",
        twiml_url="https://operator.example.com/api/v1/telephony/twilio/twiml",
        from_number="+15558880000",
        live_enabled=live,
        transport=FakeTwilioTransport(responses=responses or []),
        status_callback="https://operator.example.com/api/v1/telephony/twilio/status",
    )
    client.app.state.telephony = twilio
    client.app.state.engine = CampaignEngine(
        db=client.app.state.db,
        telephony=twilio,
        from_number="+15558880000",
        worker_threads=1,
        global_concurrency=1,
    )
    return twilio


def _configured(settings) -> Any:
    """Twilio-capable settings for this app run."""
    settings.telephony_provider = "twilio"
    settings.twilio_account_sid = "AC918273"
    settings.twilio_auth_token = "unit-auth-token"
    settings.twilio_from_number = "+15558880000"
    settings.twilio_twiml_url = "https://operator.example.com/api/v1/telephony/twilio/twiml"
    settings.twilio_status_callback_url = "https://operator.example.com/api/v1/telephony/twilio/status"
    settings.allow_live_calls = False
    return settings


def _signed_form(url: str, form: Dict[str, str], token: str = "unit-auth-token") -> Dict[str, str]:
    from voxora.security import twilio_signature

    return {"x-twilio-signature": twilio_signature(token, url, form)}


def test_status_callback_settles_call_end_to_end(settings, client) -> None:
    settings = _configured(settings)
    # Rebuild app state so the webhook endpoints see the twilio provider.
    client.app.state.settings = settings
    _twilio_adapter(client, live=True, responses=[(201, _XML_CALL_CREATED, "")])
    campaign_id = _create_conversation(client)
    AUTH = {"Authorization": "Bearer admin-token"}
    client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=AUTH)
    engine = client.app.state.engine
    assert engine.run_once() == 1

    db = client.app.state.db
    calls, _ = db.list_calls("tenant-a", limit=5)
    assert calls[0]["provider"] == "twilio" and calls[0]["status"] == "dialing"

    url = "http://testserver/api/v1/telephony/twilio/status"
    form = {"CallSid": "CA7f7c", "CallStatus": "completed", "CallDuration": "30"}
    # Wrong signature first
    bad = client.post(
        "/api/v1/telephony/twilio/status",
        content="CallSid=CA7f7c&CallStatus=completed&CallDuration=30",
        headers={"Content-Type": "application/x-www-form-urlencoded", "x-twilio-signature": "bogus"},
    )
    assert bad.status_code == 403
    # Valid signed callback
    good = client.post(
        "/api/v1/telephony/twilio/status",
        content="CallSid=CA7f7c&CallStatus=completed&CallDuration=30",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **_signed_form(url, form),
        },
    )
    assert good.status_code == 200
    call = db.get_call_by_provider_call_id("CA7f7c")
    assert call["status"] == "completed"
    counts = db.campaign_counts("tenant-a", campaign_id)
    assert counts["completed"] == 1


def test_status_callback_without_twilio_configured_is_503(settings, client) -> None:
    # The default `settings` fixture builds an app whose provider is mock.
    response = client.post("/api/v1/telephony/twilio/status", content="CallSid=CA", headers={})
    assert response.status_code == 503


def test_status_callback_unknown_call_sid_is_404(settings, client) -> None:
    settings = _configured(settings)
    client.app.state.settings = settings
    _twilio_adapter(client, live=False)
    url = "http://testserver/api/v1/telephony/twilio/status"
    form = {"CallSid": "CA-known", "CallStatus": "completed"}
    response = client.post(
        "/api/v1/telephony/twilio/status",
        content="CallSid=CA-known&CallStatus=completed",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **_signed_form(url, form),
        },
    )
    assert response.status_code == 404


def test_twiml_endpoint_returns_signed_xml(settings, client) -> None:
    settings = _configured(settings)
    client.app.state.settings = settings
    _twilio_adapter(
        client,
        responses=[(201, _XML_CALL_CREATED, "")],
    )
    # Seed a live twilio call minimally via engine flow.
    campaign_id = _create_conversation(client)
    AUTH = {"Authorization": "Bearer admin-token"}
    client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=AUTH)
    assert client.app.state.engine.run_once() == 1

    url = "http://testserver/api/v1/telephony/twilio/twiml/CA7f7c"
    body = ""
    response = client.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **_signed_form(url, {}),
            "Content-Length": "0",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response><Say" in response.text
    assert "<Hangup/>" in response.text


def test_async_provider_leaves_calls_in_flight_not_fake_completed(settings, client) -> None:
    _configured(settings)
    client.app.state.settings = settings
    _twilio_adapter(client, responses=[(201, _XML_CALL_CREATED, "")])
    campaign_id = _create_conversation(client)
    AUTH = {"Authorization": "Bearer admin-token"}
    client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=AUTH)
    assert client.app.state.engine.run_once() == 1
    db = client.app.state.db
    calls, _ = db.list_calls("tenant-a", limit=5)
    # Async provider left the call in flight, not fake-completed.
    assert calls[0]["status"] in {"dialing", "ringing"}
    details = db.get_call_detail("tenant-a", calls[0]["id"])
    events = [event["event_type"] for event in details["events"]]
    assert "dialing" in events
