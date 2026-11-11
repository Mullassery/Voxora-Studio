"""Speech-analytics estimates per finalized transcript (roadmap M8/P10).

Deterministic estimates computed ONCE per settled session from the
transcript plus recorded audio. Absent (or unfetchable/unparseable)
audio ⇒ the `speech_analytics` key is omitted entirely — never
zero-fabricated.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List

from voxora.db import Database
from voxora.voice_analytics import compute_speech_analytics

AUTH = {"Authorization": "Bearer admin-token"}


class _FakeSettings:
    telephony_provider = "mock"
    allow_live_calls = False

_RATE = 8000
_TRANSCRIPT: List[Dict[str, Any]] = [
    {"role": "assistant", "text": "um okay uh let me think about that"},
    {"role": "user", "text": "sure go ahead"},
]


def _write_wav(path: Path) -> str:
    """1.0 s tone (amp 0.3) + 1.0 s silence + 1.0 s tone, 8 kHz PCM16 mono."""
    tone = [int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / _RATE)) for i in range(_RATE)]
    silence = [0] * _RATE
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_RATE)
        writer.writeframes(struct.pack(f"<{len(tone + silence + tone)}h", *(tone + silence + tone)))
    return path.as_uri()


def test_wav_fixture_deterministic_estimates(tmp_path: Path) -> None:
    audio = Path(str(tmp_path) + "/clip.wav")
    uri = _write_wav(audio)
    from urllib.request import urlopen

    raw = urlopen(uri, timeout=5).read()
    first = compute_speech_analytics(_TRANSCRIPT, raw)
    second = compute_speech_analytics(_TRANSCRIPT, raw)
    assert first == second
    assert first is not None
    assert first["duration_seconds"] == 3.0
    assert first["pause_count"] == 1  # interior silence only
    assert first["words"] == 11
    assert first["filler_word_count"] == 2  # um, uh
    assert first["words_per_minute"] == round(11 / 3.0 * 60, 2)


def test_no_audio_returns_none() -> None:
    assert compute_speech_analytics(_TRANSCRIPT, None) is None


def test_unparseable_audio_returns_none() -> None:
    assert compute_speech_analytics(_TRANSCRIPT, b"not a wav file at all") is None


def test_settle_computes_and_stores_analytics(database: Database, tmp_path: Path) -> None:
    from voxora.voice_sessions import settle_voice_session

    database.ensure_tenant("tenant-a", "Tenant A")
    database.create_agent("tenant-a", {"name": "agent", "system_prompt": "hi"})
    agent = database.list_agents("tenant-a")[0]
    contact = database.create_local_contact(
        "tenant-a", {"first_name": "T", "last_name": "C", "phone": "+15558001111", "consent_state": "granted"}
    )
    session = database.create_voice_session(
        "tenant-a",
        {
            "external_application": "app",
            "external_session_id": "analytics_1",
            "agent_id": agent["id"],
            "model_profile": "default",
            "contact_id": contact["id"],
            "status": "dialing",
            "provider": "mock",
            "simulated": True,
        },
    )
    database.update_voice_session(
        "tenant-a",
        session["id"],
        recording_url=_write_wav(tmp_path / "call.wav"),
        transcript=_TRANSCRIPT,
    )
    call = database.create_voice_call("tenant-a", session["id"], contact["id"], _FakeSettings(), agent["id"])
    database.update_voice_session("tenant-a", session["id"], call_id=call["id"])
    settle_voice_session(database, {"tenant_id": "tenant-a", "id": call["id"]}, "completed", True)

    updated = database.get_voice_session("tenant-a", session["id"])
    analytics = updated["session_metadata"]["speech_analytics"]
    assert analytics["pause_count"] == 1
    assert analytics["words"] == 11
    events = database.list_voice_session_events("tenant-a", session["id"])
    ready = [event for event in events if event["event_type"] == "transcript.ready"]
    assert ready and ready[0]["data"]["speech_analytics"]["words"] == 11


def test_no_audio_omits_speech_analytics(database: Database, tmp_path: Path) -> None:
    from voxora.voice_sessions import settle_voice_session

    database.ensure_tenant("tenant-a", "Tenant A")
    database.create_agent("tenant-a", {"name": "agent", "system_prompt": "hi"})
    agent = database.list_agents("tenant-a")[0]
    contact = database.create_local_contact(
        "tenant-a", {"first_name": "T", "last_name": "C", "phone": "+15558002222", "consent_state": "granted"}
    )
    session = database.create_voice_session(
        "tenant-a",
        {
            "external_application": "app",
            "external_session_id": "analytics_2",
            "agent_id": agent["id"],
            "model_profile": "default",
            "contact_id": contact["id"],
            "status": "dialing",
            "provider": "mock",
            "simulated": True,
        },
    )
    database.update_voice_session("tenant-a", session["id"], transcript=_TRANSCRIPT)
    call = database.create_voice_call("tenant-a", session["id"], contact["id"], _FakeSettings(), agent["id"])
    database.update_voice_session("tenant-a", session["id"], call_id=call["id"])
    settle_voice_session(database, {"tenant_id": "tenant-a", "id": call["id"]}, "completed", True)

    updated = database.get_voice_session("tenant-a", session["id"])
    assert "speech_analytics" not in (updated["session_metadata"] or {})
    events = database.list_voice_session_events("tenant-a", session["id"])
    ready = [event for event in events if event["event_type"] == "transcript.ready"]
    assert ready and "speech_analytics" not in ready[0]["data"]

    # A broken recording URL must also omit — never crash the settle.
    database.update_voice_session("tenant-a", session["id"], status="dialing")
    database.update_voice_session("tenant-a", session["id"], recording_url="file:///nonexistent/x.wav")
    settle_voice_session(database, {"tenant_id": "tenant-a", "id": call["id"]}, "completed", True)
    updated = database.get_voice_session("tenant-a", session["id"])
    assert "speech_analytics" not in (updated["session_metadata"] or {})


def test_view_exposes_session_metadata(client) -> None:
    agent_id = client.post(
        "/api/v1/agents", json={"name": "A", "system_prompt": "s"}, headers=AUTH
    ).json()["id"]
    payload = {
        "external_application": "app",
        "external_session_id": "view_meta_1",
        "contact": {"external_contact_id": "c1", "name": "C One", "phone": "+15558003333"},
        "agent": {"agent_id": agent_id, "model_profile": "default"},
        "session_metadata": {"candidate_id": "cand_x"},
    }
    contact = client.post(
        "/api/v1/contacts", json={"first_name": "C", "last_name": "One", "phone": "+15558003333"}, headers=AUTH
    )
    if contact.status_code == 201:
        client.patch(
            f"/api/v1/contacts/{contact.json()['id']}/consent",
            json={"state": "granted", "source": "report"},
            headers=AUTH,
        )
    session = client.post("/api/v1/voice-sessions", json=payload, headers=AUTH).json()
    assert session["session_metadata"] == {"candidate_id": "cand_x"}
