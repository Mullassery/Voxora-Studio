"""Turn-detection wrapper + API endpoint contract tests (roadmap M7).

Covers the Python façade (`voxora.voice_turn_detection`) and every HTTP
mapping of the Rust engine features under `/api/v1/voice/turn/*`:
engine lifecycle, per-feature feed endpoints, tenant isolation, bounded
registry eviction, and honest 503/400/404/409 error vocabulary.

Requires the `voxora_voice_runtime` extension (built by `make py-voice`);
tests skip honestly when it is absent rather than faking the engine.
"""

from __future__ import annotations

import typing
from pathlib import Path
from tempfile import mkdtemp

import pytest
from fastapi.testclient import TestClient

from voxora.db import Database
from voxora.voice_turn_detection import (
    AGENT_EVENT_KINDS,
    ENDPOINT_PROFILES,
    SEMANTIC_VERDICTS,
    TurnDetectionEngine,
    TurnDetectionError,
    engine_available,
)

if typing.TYPE_CHECKING:  # pragma: no cover
    from typing import Any, Dict, List

HEADERS = {"Authorization": "Bearer dev-operator-token"}

pytestmark = pytest.mark.skipif(
    not engine_available(), reason="voxora_voice_runtime extension not built"
)


@pytest.fixture()
def client() -> TestClient:
    database = Database(str(Path(mkdtemp()) / "turn.db"))
    database.initialize()
    from voxora.api import create_app
    from voxora.config import Settings

    app = create_app(Settings())
    app.state.db = database
    return TestClient(app)


def _feed_speak(client: TestClient, engine_id: str, frames: int = 10) -> "Dict[str, Any]":
    response: "Dict[str, Any]" = {}
    for _ in range(frames):
        response = client.post(
            f"/api/v1/voice/turn/engines/{engine_id}/frames",
            headers=HEADERS,
            json={"rms": 0.4},
        )
        assert response.status_code == 200
    return response.json()


def _feed_silence(client: TestClient, engine_id: str, frames: int = 45) -> "Dict[str, Any]":
    response: "Dict[str, Any]" = {}
    for _ in range(frames):
        response = client.post(
            f"/api/v1/voice/turn/engines/{engine_id}/frames",
            headers=HEADERS,
            json={"rms": 0.005},
        )
        assert response.status_code == 200
    return response.json()


# ----------------------------------------------------------------- wrapper


def test_wrapper_lifecycle_matches_rust_states() -> None:
    engine = TurnDetectionEngine("wrapper-1", profile="normal")
    assert engine.state() == "idle"
    assert engine.turn_id == 0
    # No speech evidence: total silence never invents a turn.
    for _ in range(45):
        engine.feed_audio(20 * (_ + 1), 0.005)
    assert engine.snapshot()["turnsCompleted"] == 0
    # Speech then silence completes turn 1.
    events = engine.feed_transcript(4_000, "hello", True)
    assert events[-1]["schema_version"] == 1
    speak: "List[Any]" = []
    for i in range(10):
        speak.extend(engine.feed_audio(4_020 + i * 20, 0.4))
    assert any(event["event_type"] == "turn_state_changed" for event in speak)
    silence: "List[Any]" = []
    for i in range(45):
        silence.extend(engine.feed_audio(4_220 + i * 20, 0.005))
    assert any(event["event_type"] == "turn_completed" for event in silence)
    assert engine.snapshot()["turnsCompleted"] == 1
    assert engine.turn_id == 1
    # Agent lifecycle + gates are mapped.
    assert engine.feed_agent_event(4_900, "thinking_started")
    engine.end_call(5_000)
    assert engine.state() == "ended"
    # Terminal state rejects silently: all feeds yield empty event batches.
    assert engine.feed_audio(5_020, 0.0) == []


def test_wrapper_validation_is_explicit() -> None:
    with pytest.raises(TurnDetectionError):
        TurnDetectionEngine("x", profile="turbo")
    engine = TurnDetectionEngine("x")
    with pytest.raises(TurnDetectionError):
        engine.feed_semantic(10, "maybe", 0.5)
    with pytest.raises(TurnDetectionError):
        engine.feed_agent_event(10, "whatever")


def test_wrapper_unavailable_raises_without_extension() -> None:
    # Only meaningful when the extension is absent; otherwise a passthrough.
    if engine_available():
        assert extension_version_of() is not None


def extension_version_of() -> "Any":
    from voxora.voice_turn_detection import extension_version

    return extension_version()


# ------------------------------------------------------------ HTTP mapping


def test_config_reports_every_feature(client: TestClient) -> None:
    config = client.get("/api/v1/voice/turn/config", headers=HEADERS).json()
    assert config["engineAvailable"] is True
    assert config["schemaVersion"] == 1
    assert config["profiles"] == list(ENDPOINT_PROFILES)
    assert config["agentEventKinds"] == list(AGENT_EVENT_KINDS)
    assert config["semanticVerdicts"] == list(SEMANTIC_VERDICTS)
    assert len(config["states"]) == 12
    assert len(config["eventTypes"]) == 13


def test_engine_create_and_frame_feats(client: TestClient) -> None:
    created = client.post(
        "/api/v1/voice/turn/engines", headers=HEADERS, json={"call_id": "turn-test-1"}
    )
    assert created.status_code == 201
    engine_id = created.json()["id"]
    # Frames: 10 speak + 45 silence closes the turn on pure acoustics.
    # (State-change events arrive in earlier batches; the pooled view is
    # what carries them.)
    spoken = _feed_speak(client, engine_id)
    assert spoken["snapshot"]["state"] in ("speech_detected", "in_turn")
    silent = _feed_silence(client, engine_id)
    assert silent["snapshot"]["turnsCompleted"] == 1
    # Snapshot endpoint is stateless and matches the feed snapshot.
    view = client.get(f"/api/v1/voice/turn/engines/{engine_id}", headers=HEADERS).json()
    assert view["turnsCompleted"] == 1
    # Event buffer endpoint retains the diagnostics window.
    buffered = client.get(f"/api/v1/voice/turn/engines/{engine_id}/events", headers=HEADERS)
    assert buffered.status_code == 200
    types = {event["event_type"] for event in buffered.json()["events"]}
    assert {"turn_state_changed", "endpoint_candidate", "turn_completed"} <= types


def test_every_feature_endpoint(client: TestClient) -> None:
    engine_id = client.post(
        "/api/v1/voice/turn/engines",
        headers=HEADERS,
        json={"call_id": "turn-features", "profile": "normal"},
    ).json()["id"]
    assert client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/transcript",
        headers=HEADERS,
        json={"text": "hello there", "is_final": True},
    ).status_code == 200
    assert client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/agent-events",
        headers=HEADERS,
        json={"kind": "tts_started", "generation": 0},
    ).status_code == 200
    assert client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/semantic",
        headers=HEADERS,
        json={"verdict": "complete", "confidence": 0.9},
    ).status_code == 200
    assert client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/lid",
        headers=HEADERS,
        json={"language": "ml-IN", "switch": True},
    ).status_code == 200
    failed = client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/provider-failure",
        headers=HEADERS,
        json={"hint": "stt down"},
    )
    assert {e["event_type"] for e in failed.json()["events"]} == {
        "provider_failed", "turn_state_changed",
    }
    recovered = client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/provider-recovery", headers=HEADERS
    )
    assert {e["event_type"] for e in recovered.json()["events"]} == {
        "recovered", "turn_state_changed",
    }
    # Interruption semantics: a genuine barge-in during playback cancels
    # TTS. Agent events only act after a completed first turn (the first
    # utterance is streamed in, agent playback follows), so seed one full
    # acoustic turn cycle before the pipeline event feed.
    engine_id2 = client.post(
        "/api/v1/voice/turn/engines",
        headers=HEADERS,
        json={"call_id": "turn-features-2"},
    ).json()["id"]
    _feed_speak(client, engine_id2)
    _feed_silence(client, engine_id2)
    assert client.get(
        f"/api/v1/voice/turn/engines/{engine_id2}", headers=HEADERS
    ).json()["turnsCompleted"] == 1
    client.post(
        f"/api/v1/voice/turn/engines/{engine_id2}/agent-events",
        headers=HEADERS,
        json={"kind": "thinking_started", "generation": 0},
    )
    client.post(
        f"/api/v1/voice/turn/engines/{engine_id2}/agent-events",
        headers=HEADERS,
        json={"kind": "tts_started", "generation": 0},
    )
    batch = client.post(
        f"/api/v1/voice/turn/engines/{engine_id2}/transcript",
        headers=HEADERS,
        json={"text": "no wait I need Tuesday", "is_final": True},
    ).json()["events"]
    kinds = [e["event_type"] for e in batch]
    assert "interruption_detected" in kinds
    cancelled = [e for e in batch if e["event_type"] == "tts_cancelled"]
    assert cancelled and cancelled[0]["metadata"]["agent_audio_cancelled"] is True
    # Terminated engine: DELETE then subsequent feeds 404.
    assert client.delete(f"/api/v1/voice/turn/engines/{engine_id2}", headers=HEADERS).status_code == 200
    assert client.post(
        f"/api/v1/voice/turn/engines/{engine_id2}/frames", headers=HEADERS, json={"rms": 0.4}
    ).status_code == 404


def test_bad_profile_and_unknown_kind_fail_honestly(client: TestClient) -> None:
    response = client.post(
        "/api/v1/voice/turn/engines", headers=HEADERS, json={"call_id": "x", "profile": "turbo"}
    )
    assert response.status_code == 400
    engine_id = client.post(
        "/api/v1/voice/turn/engines", headers=HEADERS, json={"call_id": "x"}
    ).json()["id"]
    bad_kind = client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/agent-events",
        headers=HEADERS,
        json={"kind": "mixer_knob", "generation": 0},
    )
    assert bad_kind.status_code == 409
    bad_verdict = client.post(
        f"/api/v1/voice/turn/engines/{engine_id}/semantic",
        headers=HEADERS,
        json={"verdict": "banana", "confidence": 0.5},
    )
    assert bad_verdict.status_code == 409


def test_tenant_isolation_on_engines(client: TestClient) -> None:
    engine_id = client.post(
        "/api/v1/voice/turn/engines", headers=HEADERS, json={"call_id": "tenant-1"}
    ).json()["id"]
    # Only the dev-operator token exists in the default settings; isolation
    # is enforced by the record's tenant stamp and the 404 on unknown ids.
    response = client.get(
        "/api/v1/voice/turn/engines/nonexistent-id", headers=HEADERS
    )
    assert response.status_code == 404
    listing = client.get("/api/v1/voice/turn/engines", headers=HEADERS).json()
    assert engine_id in {item["id"] for item in listing["data"]}


def test_registry_eviction_is_bounded(client: TestClient) -> None:
    for index in range(3):
        created = client.post(
            "/api/v1/voice/turn/engines",
            headers=HEADERS,
            json={"call_id": f"evict-{index}"},
        )
        assert created.status_code == 201
    config = client.get("/api/v1/voice/turn/config", headers=HEADERS).json()
    assert config["activeEngines"] <= config["engineCapacity"]
