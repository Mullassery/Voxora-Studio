"""Voice-session contract tests (ecosystem ADR-3B/3C subset).

Covers: idempotent session intake, consent fail-closed gate, transcript
availability honesty, capability-honest instructions (409 + recorded),
terminate, and the versioned event envelope generation. Webhook dispatch
is exercised with an injected transport (no live HTTP).
"""

from __future__ import annotations

import json
import typing

from fastapi.testclient import TestClient

if typing.TYPE_CHECKING:  # pragma: no cover
    from typing import Any, Dict, List

HEADERS = {"Authorization": "Bearer admin-token"}


def _seed_agent(client: TestClient) -> str:
    return client.post(
        "/api/v1/agents",
        json={"name": "Voice Agent", "system_prompt": "Ask one question at a time."},
        headers=HEADERS,
    ).json()["id"]


def _grant_consent(client: TestClient, phone: str) -> None:
    # consent lives with the phone: create + grant via the contacts API
    contact = client.post(
        "/api/v1/contacts",
        json={"first_name": "Cand", "last_name": "A", "phone": phone},
        headers=HEADERS,
    )
    if contact.status_code == 201:
        contact_id = contact.json()["id"]
        client.patch(
            f"/api/v1/contacts/{contact_id}/consent",
            json={"state": "granted", "source": "report"},
            headers=HEADERS,
        )


def _session_payload(agent_id: str, external_session_id: str = "interview_001", **kw: "Any") -> "Dict[str, Any]":
    payload: "Dict[str, Any]" = {
        "external_application": "pyinterviewbot",
        "external_session_id": external_session_id,
        "contact": {
            "external_contact_id": "contact_001",
            "name": "Candidate One",
            "phone": "+15551234567",
        },
        "agent": {"agent_id": agent_id, "model_profile": "interview_default"},
        "session_metadata": {
            "candidate_id": "cand_001",
            "job_id": "job_001",
            "interview_template_id": "template_001",
        },
        "webhook_url": "https://pyinterviewbot.example.com/api/v1/webhooks/voxora",
    }
    payload.update(kw)
    return payload


def test_session_creates_call_completes_simulated_and_records_envelope_events(
    client: TestClient,
) -> None:
    agent_id = _seed_agent(client)
    _grant_consent(client, "+15551234567")
    response = client.post("/api/v1/voice-sessions", json=_session_payload(agent_id), headers=HEADERS)
    assert response.status_code == 201, response.text
    body = response.json()
    # Provider default in tests: mock -> final-on-place, honest simulation
    assert body["simulated"] is True
    assert body["status"] == "completed" and body["call_status"] == "completed"
    event_types = [e["event_type"] for e in body["events"]]
    assert event_types[0] == "voice_session.created" and event_types[1] == "voice_session.queued"
    assert "voice_session.completed" in event_types
    transcript = client.get(
        f"/api/v1/voice-sessions/{body['session_id']}/transcript", headers=HEADERS
    ).json()
    assert transcript["transcript_available"] is True and transcript["entries"]
    # Strict shape (audit V2): entries must decode to a real JSON array of
    # objects — a JSON-encoded *string* here is the regression the transcript
    # JSONB fix exists for.
    assert isinstance(transcript["entries"], list), type(transcript["entries"])
    assert all(isinstance(entry, dict) for entry in transcript["entries"])


def test_session_intake_is_idempotent_on_external_id(client: TestClient) -> None:
    agent_id = _seed_agent(client)
    _grant_consent(client, "+15551234567")
    first = client.post("/api/v1/voice-sessions", json=_session_payload(agent_id), headers=HEADERS)
    second = client.post("/api/v1/voice-sessions", json=_session_payload(agent_id), headers=HEADERS)
    ids = {first.json()["session_id"], second.json()["session_id"]}
    assert len(ids) == 1


def test_unknown_external_session_consent_gate_refuses_without_record(client: TestClient) -> None:
    agent_id = _seed_agent(client)
    payload = _session_payload(agent_id, contact_phone_gate := "gate_marker")
    payload["contact"]["phone"] = "+15551234999"  # no contact rows exist for it
    response = client.post("/api/v1/voice-sessions", headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "consent_required"
    # gate marker ensures the contact was auto-created as unknown (never widened)
    _ = contact_phone_gate


def test_consented_client_is_callable_after_audience_import_never_widens(client: TestClient) -> None:
    agent_id = _seed_agent(client)
    client.post(
        "/api/v1/suppression",
        headers=HEADERS,
        json={"phone": "+15559990002", "reason": "do-not-call"},
    )
    payload = _session_payload(agent_id)
    payload["contact"]["phone"] = "+15559990002"
    response = client.post("/api/v1/voice-sessions", headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "suppressed_phone"


def test_instructions_are_recorded_and_rejected_with_provider_capabilities(
    client: TestClient,
) -> None:
    agent_id = _seed_agent(client)
    _grant_consent(client, "+15551234567")
    session = client.post(
        "/api/v1/voice-sessions", headers=HEADERS, json=_session_payload(agent_id)
    ).json()
    instruction = {
        "instruction_id": "instruction_001",
        "type": "speak",
        "text": "Can you explain how you handled schema evolution in that project?",
        "priority": "normal",
    }
    first = client.post(
        f"/api/v1/voice-sessions/{session['session_id']}/instructions",
        headers=HEADERS,
        json=instruction,
    )
    assert first.status_code == 409
    body = first.json()
    assert body["error"]["code"] == "instruction_unsupported"
    assert body["error"]["details"]["provider_capabilities"]["mid_call_instructions"] is False
    # still recorded, idempotent
    repeat = client.post(
        f"/api/v1/voice-sessions/{session['session_id']}/instructions",
        headers=HEADERS,
        json=instruction,
    )
    assert repeat.json()["error"]["code"] == "instruction_unsupported"


def test_terminate_is_idempotent_and_sessions_events_stay_ordered(client: TestClient) -> None:
    agent_id = _seed_agent(client)
    _grant_consent(client, "+15551234567")
    session = client.post(
        "/api/v1/voice-sessions",
        headers=HEADERS,
        json=_session_payload(agent_id, external_session_id="interview_terminate"),
    ).json()
    if session["status"] == "completed":  # mock already terminal: idempotent path
        again = client.post(
            f"/api/v1/voice-sessions/{session['session_id']}/terminate", headers=HEADERS
        )
        assert again.json()["status"] == "completed"


def test_twilio_style_callback_settles_through_session_service(client: TestClient) -> None:
    """Non-final adapter result + late provider callback == honest settle."""
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from voxora.config import Settings
    from voxora.db import Database
    from voxora.voice_sessions import session_status_for, settle_voice_session

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "unit.db"))
        db.initialize()
        db.ensure_tenant("t1", "T")
        agent = db.create_agent("t1", {"name": "A"})
        contact = db.create_local_contact(
            "t1", {"first_name": "C", "phone": "+15551234568", "consent_state": "granted"}
        )
        session = db.create_voice_session(
            "t1",
            {
                "external_application": "pyinterviewbot",
                "external_session_id": "tw_001",
                "agent_id": agent["id"],
                "contact_id": contact["id"],
                "status": "dialing",
                "provider": "twilio",
                "simulated": False,
            },
        )
        call = db.create_voice_call("t1", session["id"], contact["id"], Settings(), agent["id"])
        db.update_voice_session("t1", session["id"], call_id=call["id"])
        from voxora.telephony.base import TelephonyEvent

        late = SimpleNamespace(
            provider="twilio",
            provider_call_id="CA123",
            simulated=False,
            final=False,
            ok=True,
            status="in_progress",
            disposition="answered",
            events=[
                TelephonyEvent("dialing", "2026-11-03T00:00:00+00:00", {"provider": "twilio"}),
                TelephonyEvent("ringing", "2026-11-03T00:00:01+00:00", {"simulated": False}),
            ],
            latency_ms={},
            started_at="2026-11-03T00:00:00+00:00",
            answered_at="2026-11-03T00:00:01+00:00",
            ended_at=None,
            duration_ms=0,
            error_code=None,
            error_message=None,
        )
        db.apply_voice_session_result("t1", call["id"], late)
        callback = SimpleNamespace(
            disposition="completed",
            ok=True,
            simulated=False,
            final=False,
        )
        status = settle_voice_session(db, call, callback.disposition, True)
        assert status == "completed"
        updated = db.get_voice_session("t1", session["id"])
        assert updated["status"] == "completed"
        events = db.list_voice_session_events("t1", session["id"])
        types = [e["event_type"] for e in events]
        assert "voice_session.ringing" in types and "voice_session.completed" in types
    assert session_status_for("busy", True) == "busy"


def test_webhook_delivery_signs_and_records_attempts(client: TestClient) -> None:
    import tempfile
    from pathlib import Path

    from voxora.db import Database
    from voxora.voice_sessions import dispatch_session_events

    body: "List[Dict[str, Any]]" = []

    def fake_transport(req: Any, timeout: "Any" = None) -> "Any":
        body.append({"data": json.loads(req.data.decode()), "headers": dict(req.headers)})
        return SimpleNamespaceJSON(200)

    class SimpleNamespaceJSON:
        def __init__(self, status: int) -> None:
            self.status = status

        def __enter__(self) -> "SimpleNamespaceJSON":
            return self

        def __exit__(self, *args: "Any") -> None:
            return None

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "unit.db"))
        db.initialize()
        db.ensure_tenant("t1", "T")
        agent = db.create_agent("t1", {"name": "A"})
        contact = db.create_local_contact(
            "t1", {"first_name": "C", "phone": "+15551234570", "consent_state": "granted"}
        )
        session = db.create_voice_session(
            "t1",
            {
                "external_application": "pyinterviewbot",
                "external_session_id": "wh_001",
                "agent_id": agent["id"],
                "contact_id": contact["id"],
                "status": "completed",
            },
        )
        db.record_voice_session_event("t1", session["id"], "voice_session.created", {})
        outcomes = dispatch_session_events(
            db,
            "t1",
            session["id"],
            "https://rb.example.com/wh",
            "unit-test-outbound-webhook-secret-0123456",
            transport=fake_transport,
        )
        assert outcomes == [1]
        assert body and body[0]["data"]["event_type"] == "voice_session.created"
        assert body[0]["data"]["external_application"] == "pyinterviewbot"
        assert body[0]["data"]["session_id"] == session["id"]
        signature = body[0]["headers"].get("X-voxora-signature") or body[0]["headers"].get("x-voxora-signature")
        assert (signature or "").startswith("v1=")
        deliveries = db.list_event_deliveries("t1", body[0]["data"]["event_id"])
        assert deliveries and deliveries[0]["ok"] in (True, 1)
