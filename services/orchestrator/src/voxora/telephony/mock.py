"""Deterministic, credential-free telephony adapter for development and CI."""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from .base import CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult

_STAGES = [
    "audio_received",
    "speech_endpoint",
    "stt_result",
    "agent_response",
    "tts_first_audio",
    "first_audio_transmitted",
    "turn_completed",
]


class MockTelephonyAdapter(TelephonyAdapter):
    provider = "mock"

    def __init__(self, delay_seconds: float = 0.0, clock=None):
        self.delay_seconds = max(0.0, delay_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._results: Dict[str, TelephonyResult] = {}
        self._cancelled: set = set()
        self._lock = threading.Lock()

    def start_call(self, request: CallRequest) -> TelephonyResult:
        with self._lock:
            if request.idempotency_key in self._results:
                return self._results[request.idempotency_key]
        if self.delay_seconds:
            time.sleep(self.delay_seconds)

        start = self._clock()
        digest = hashlib.sha256(
            f"{request.to_number}:{request.attempt}:{request.campaign_id}".encode("utf-8")
        ).digest()
        bucket = digest[0] % 100
        if request.dry_run or bucket < 82:
            status, disposition, retryable = "completed", "answered", False
        elif bucket < 90:
            status, disposition, retryable = "no_answer", "no_answer", True
        elif bucket < 95:
            status, disposition, retryable = "busy", "busy", True
        else:
            status, disposition, retryable = "failed", "provider_failure", False

        answered = status == "completed"
        duration_ms = 0 if not answered else 18_000 + (digest[1] * 120)
        latency = {
            "audio_received": float(35 + digest[2] % 55),
            "speech_endpoint": float(180 + digest[3] % 240),
            "stt_result": float(90 + digest[4] % 130),
            "agent_response": float(260 + digest[5] % 440),
            "tts_first_audio": float(120 + digest[6] % 260),
            "first_audio_transmitted": float(18 + digest[7] % 42),
            "turn_completed": 0.0,
        }
        latency["turn_completed"] = round(sum(latency.values()), 2)

        events = [
            TelephonyEvent("dialing", _at(start, 0), {"provider": self.provider}),
            TelephonyEvent("ringing", _at(start, 400), {"simulated": True}),
        ]
        if answered:
            events.extend(
                [
                    TelephonyEvent("in_progress", _at(start, 1200), {"simulated": True}),
                    TelephonyEvent(
                        "completed",
                        _at(start, 1200 + duration_ms),
                        {
                            "disposition": disposition,
                            "simulated": True,
                            "latencyMode": "simulated",
                        },
                    ),
                ]
            )
        else:
            events.append(
                TelephonyEvent(
                    status,
                    _at(start, 2500),
                    {"disposition": disposition, "simulated": True},
                )
            )

        result = TelephonyResult(
            ok=answered,
            provider=self.provider,
            provider_call_id="mock_" + digest.hex()[:20],
            status=status,
            disposition=disposition,
            simulated=True,
            retryable=retryable,
            duration_ms=duration_ms,
            error_code=None if answered else disposition,
            error_message=None if answered else f"Simulated {disposition}",
            events=events,
            latency_ms=latency,
            transcript=_transcript(request) if answered else [],
            started_at=start.isoformat(),
            answered_at=_at(start, 1200) if answered else None,
            ended_at=_at(start, 1200 + duration_ms) if answered else _at(start, 2500),
        )
        with self._lock:
            self._results[request.idempotency_key] = result
        return result

    def cancel_call(self, provider_call_id: str) -> bool:
        with self._lock:
            self._cancelled.add(provider_call_id)
        return True

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "available": True,
            "simulated": True,
            "liveCallsEnabled": False,
            "message": "Deterministic mock adapter; no real calls are placed",
        }


def _at(start: datetime, offset_ms: int) -> str:
    return (start + timedelta(milliseconds=offset_ms)).isoformat()


def _transcript(request: CallRequest) -> list:
    name = (request.metadata.get("contactName") or "there").strip() or "there"
    return [
        {"role": "assistant", "text": f"Hello {name}, this is a simulated Voxora call."},
        {"role": "contact", "text": "I can hear you."},
        {"role": "assistant", "text": "Thank you. This call is simulated and no real audio was sent."},
    ]
