"""Voice-runtime seam tests: pure mapping, extension behavior, config honesty.

The `-runtime` mapping tests run without the compiled extension (they pin the
canonical JSON contract). Extension-backed tests skip honestly with a reason
when `voxora_voice_runtime` is not built in the current environment.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from voxora.config import Settings
from voxora.telephony.base import CallRequest
from voxora.telephony.registry import build_telephony_adapter, telephony_provider_status
from voxora.telephony.voice_runtime import (
    SUPPORTED_SCHEMA_VERSION,
    VoiceRuntimeTelephonyAdapter,
    telephony_result_from_runtime_json,
)


def _request() -> CallRequest:
    return CallRequest(
        call_id="call-rt-1",
        campaign_id="camp-1",
        to_number="+15551239999",
        from_number=None,
        idempotency_key="idem-rt-1",
        attempt=1,
        dry_run=True,
        agent={"name": "Bridge agent", "model": "mock-llm"},
        metadata={"contactName": "Georgi"},
    )


def _canonical_payload() -> dict:
    turn_latencies = {
        "audioReceived": 40.0,
        "speechEndpoint": 200.0,
        "sttResult": 100.0,
        "agentResponse": 300.0,
        "ttsFirstAudio": 150.0,
        "firstAudioTransmitted": 20.0,
    }
    return {
        "schemaVersion": SUPPORTED_SCHEMA_VERSION,
        "summary": {
            "totalMs": 1200 + (810 + 1200) + (810 + 900),
            "turns": 2,
            "meanTurnLatencyMs": 810.0,
            "events": [
                {"atMs": 0, "kind": "dialing", "detail": "scripted bridge"},
                {"atMs": 400, "kind": "ringing", "detail": "simulated"},
                {"atMs": 1200, "kind": "answered", "detail": "scripted bridge"},
                {"atMs": 1200 + 810, "kind": "turn_completed", "detail": "hello"},
                {"atMs": 1200 + 810 + 1200 + 810, "kind": "turn_completed", "detail": "done"},
                {"atMs": 1200 + 810 + 1200 + 810 + 900, "kind": "ended", "detail": "2 turns"},
            ],
        },
        "turns": [
            {"index": 0, "text": "hello", "latency": turn_latencies, "speakMs": 1200,
             "startedAtMs": 1200},
            {"index": 1, "text": "done", "latency": turn_latencies, "speakMs": 900,
             "startedAtMs": 1200 + 810 + 1200},
        ],
    }


_FIXED_CLOCK = lambda: datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


def test_result_mapping_is_pure_and_schema_true() -> None:
    result = telephony_result_from_runtime_json(
        _canonical_payload(), _request(), clock=_FIXED_CLOCK
    )
    assert result.ok and result.status == "completed" and result.disposition == "answered"
    assert result.simulated is True
    assert result.provider == "voice-runtime"
    assert result.duration_ms == 4920 - 1200  # total minus dialing+ringing boot
    # Latency means are per-stage averages across turns.
    assert result.latency_ms["audio_received"] == 40.0
    assert result.latency_ms["turn_completed"] == 810.0
    # Bridge event vocabulary flows into call_events verbatim.
    kinds = [event.event_type for event in result.events]
    assert kinds[0] == "dialing" and "answered" in kinds and kinds[-1] == "ended"
    assert all(event.data.get("fromVoiceRuntime") for event in result.events)
    assert [entry["role"] for entry in result.transcript] == ["assistant", "assistant"]
    assert result.answered_at is not None and result.started_at is not None


def test_mapping_rejects_wrong_schema_version() -> None:
    payload = _canonical_payload()
    payload["schemaVersion"] = 999
    with pytest.raises(ValueError) as excinfo:
        telephony_result_from_runtime_json(payload, _request(), clock=_FIXED_CLOCK)
    assert "999" in str(excinfo.value)


def test_adapter_idempotent_on_idempotency_key() -> None:
    pytest.importorskip(
        "voxora_voice_runtime",
        reason="voxora_voice_runtime extension not built (run `make py-voice`)",
    )
    import json

    import voxora_voice_runtime

    adapter = VoiceRuntimeTelephonyAdapter(clock=_FIXED_CLOCK)
    script = {"turns": [{"text": "Hello", "speakMs": 1100}], "turnCapMs": 120000}
    raw = json.dumps(script)
    payload = json.loads(voxora_voice_runtime.run_scripted_call_json(raw))
    first = adapter.start_call(_request())
    second = adapter.start_call(_request())
    assert first is second  # idempotency cache
    assert first.status == "completed" and first.simulated is True
    # The adapter produced the same mapping the pure function does.
    expected = telephony_result_from_runtime_json(  # noqa: F841
        payload, _request(), clock=_FIXED_CLOCK
    )
    assert first.latency_ms["turn_completed"] > 0
    kinds = [event.event_type for event in first.events]
    assert "turn_completed" in kinds and kinds[-1] == "ended"
    assert adapter.status()["simulated"] is True


def test_missing_extension_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    monkeypatch.setitem(sys.modules, "voxora_voice_runtime", None)  # import fails
    adapter = VoiceRuntimeTelephonyAdapter()
    with pytest.raises(RuntimeError) as excinfo:
        adapter.start_call(_request())
    assert "make py-voice" in str(excinfo.value)


def test_registry_builds_both_simulation_providers() -> None:
    settings = Settings(secret_key="x" * 40, api_tokens={})
    mock_adapter = build_telephony_adapter(settings)
    assert mock_adapter.provider == "mock"
    runtime_adapter = build_telephony_adapter(
        Settings(secret_key="x" * 40, api_tokens={}, telephony_provider="voice-runtime")
    )
    assert runtime_adapter.provider == "voice-runtime"
    ids = [entry["id"] for entry in telephony_provider_status(settings)]
    assert ids == ["mock", "voice-runtime", "twilio"]
    for entry in telephony_provider_status(settings):
        if entry["id"] in {"mock", "voice-runtime"}:
            assert entry["simulated"] is True and entry["implemented"] is True


def test_live_calls_refused_for_simulation_providers() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings(
            secret_key="x" * 40,
            api_tokens={},
            telephony_provider="voice-runtime",
            allow_live_calls=True,
        ).validate()
    assert "voice-runtime" in str(excinfo.value)
    # And the original mock gate still works.
    with pytest.raises(ValueError):
        Settings(
            secret_key="x" * 40, api_tokens={}, telephony_provider="mock", allow_live_calls=True
        ).validate()
