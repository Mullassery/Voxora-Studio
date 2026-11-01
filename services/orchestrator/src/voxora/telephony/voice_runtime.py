"""Voice-runtime seam: the Rust scripted bridge, exposed as a telephony adapter.

The Rust crate `voxora-voice-runtime` (PyO3 extension `voxora_voice_runtime`)
runs a scripted dialog through the deterministic turn bridge and returns
canonical JSON: `{"schemaVersion": 1, "summary": {"totalMs", "turns",
"meanTurnLatencyMs", "events": [...]}, "turns": [{"latency", "speakMs",
"startedAtMs", ...}]}`.

This module (a) maps that JSON onto the orchestrator's `TelephonyResult`
schema, and (b) wraps the extension in a `TelephonyAdapter` so campaigns can
be driven by the Rust bridge instead of the Python mock. It is still a
*simulation* provider: no real telephony. If the extension is not built, the
error names the build command instead of failing mysteriously.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from .base import CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult

SUPPORTED_SCHEMA_VERSION = 1

_STAGE_KEYS = {
    "audioReceived": "audio_received",
    "speechEndpoint": "speech_endpoint",
    "sttResult": "stt_result",
    "agentResponse": "agent_response",
    "ttsFirstAudio": "tts_first_audio",
    "firstAudioTransmitted": "first_audio_transmitted",
}

_BOOT_OFFSET_MS = 1200  # dialing + ringing before the bridge reports "answered"


def extension() -> Any:
    """Import the built `voxora_voice_runtime` extension or explain honestly."""
    try:
        import voxora_voice_runtime  # type: ignore[import-not-found,import-untyped]

        return voxora_voice_runtime
    except ImportError as error:  # pragma: no cover - depends on build state
        raise RuntimeError(
            "The voxora_voice_runtime extension is not built in this environment. "
            "Build it with `cargo install maturin && make py-voice` "
            "(maturin develop --release inside crates/voice-runtime)."
        ) from error


def telephony_result_from_runtime_json(
    payload: Dict[str, Any],
    request: CallRequest,
    clock=None,
) -> TelephonyResult:
    """Map the bridge's canonical JSON onto `TelephonyResult`.

    Raises `ValueError` on schema-version mismatch so old/Rust-side changes
    surface loudly instead of silently mis-mapping.
    """
    version = payload.get("schemaVersion")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported voice-runtime summary schema version: {version!r}"
        )
    summary = payload.get("summary") or {}
    turns = payload.get("turns") or []

    start = (clock or (lambda: datetime.now(timezone.utc)))()
    total_ms = int(summary.get("totalMs", 0))
    duration_ms = max(0, total_ms - _BOOT_OFFSET_MS)

    events: List[TelephonyEvent] = [
        TelephonyEvent(event["kind"], _at(start, int(event["atMs"])) , {"fromVoiceRuntime": True})
        for event in summary.get("events", [])
    ]

    latency: Dict[str, float] = {name: 0.0 for name in _STAGE_KEYS.values()}
    latency["turn_completed"] = 0.0
    if turns:
        for json_key, snake_key in _STAGE_KEYS.items():
            latency[snake_key] = round(
                sum(float(turn.get("latency", {}).get(json_key, 0.0)) for turn in turns)
                / len(turns),
                2,
            )
        latency["turn_completed"] = round(sum(latency.values()), 2)

    transcript: List[Dict[str, str]] = [
        {"role": "assistant", "text": str(turn.get("text", ""))} for turn in turns
    ]

    return TelephonyResult(
        ok=True,
        provider="voice-runtime",
        provider_call_id=request.call_id,
        status="completed",
        disposition="answered",
        simulated=True,
        retryable=False,
        duration_ms=duration_ms,
        error_code=None,
        error_message=None,
        events=events,
        latency_ms=latency,
        transcript=transcript,
        started_at=_at(start, 0),
        answered_at=_at(start, _BOOT_OFFSET_MS),
        ended_at=_at(start, total_ms),
    )


class VoiceRuntimeTelephonyAdapter(TelephonyAdapter):
    """Telephony adapter backed by the Rust voice bridge (simulation only)."""

    provider = "voice-runtime"

    def __init__(self, max_turns: int = 8, clock=None):
        self.max_turns = max_turns
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._results: Dict[str, TelephonyResult] = {}
        self._lock = threading.Lock()

    def start_call(self, request: CallRequest) -> TelephonyResult:
        with self._lock:
            cached = self._results.get(request.idempotency_key)
            if cached is not None:
                return cached
        script = self._script(request)
        payload = json.loads(extension().run_scripted_call_json(json.dumps(script)))
        result = telephony_result_from_runtime_json(payload, request, clock=self._clock)
        with self._lock:
            self._results[request.idempotency_key] = result
        return result

    def cancel_call(self, provider_call_id: str) -> bool:
        # The scripted bridge is synchronous; there is nothing in flight to cancel.
        return True

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "available": True,
            "simulated": True,
            "liveCallsEnabled": False,
            "message": "Rust voice bridge (scripted, deterministic); no real calls are placed",
        }

    @staticmethod
    def _script(request: CallRequest) -> Dict[str, Any]:
        name = (request.metadata.get("contactName") or "there").strip() or "there"
        return {
            "turns": [
                {
                    "text": f"Hello {name}, this is a simulated Voxora call.",
                    "latencyMs": VoiceRuntimeTelephonyAdapter._turn_latency(),
                    "speakMs": 1200,
                },
                {
                    "text": "This call came from the Rust voice bridge.",
                    "latencyMs": VoiceRuntimeTelephonyAdapter._turn_latency(),
                    "speakMs": 900,
                },
            ]
        }

    @staticmethod
    def _turn_latency() -> Dict[str, float]:
        return {
            "audioReceived": 42.0,
            "speechEndpoint": 210.0,
            "sttResult": 115.0,
            "agentResponse": 280.0,
            "ttsFirstAudio": 140.0,
            "firstAudioTransmitted": 22.0,
        }


def _at(start: datetime, offset_ms: int) -> str:
    return (start + timedelta(milliseconds=offset_ms)).isoformat()
