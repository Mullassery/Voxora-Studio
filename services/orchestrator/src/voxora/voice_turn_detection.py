"""Python wrapper for the Rust turn-detection engine (roadmap M7).

`voxora_voice_runtime` (crates/voice-runtime, built via maturin — `make
py-voice`) owns the authoritative detection logic: VAD, adaptive
endpointing, semantic tail-matching, backchannel-aware barge-in, provider
failure resile, and the 12-state lifecycle. This module is a thin, typed
façade: it validates arguments, keeps a monotonic sequence per engine, and
parses the canonical-JSON event batches into Python dicts.

Honesty contract (matching the codebase's simulation rules):

* One `TurnDetectionEngine` maps to one live call; the extension is
  stateful and not thread-safe — use handles per call, single feeder thread.
* The caller supplies `seq` (strictly increasing) and `timestamp_ms`
  (transport time). The engine never reads clocks itself; a wall-clock
  helper (`now_ms()`) exists only for callers that can't source transport
  time.
* All events are versioned (`schema_version = 1`); see
  crates/voice-runtime/src/td_events.rs for the contract table.
* When the extension is not installed (``pip install -e
  crates/voice-runtime`` / ``make py-voice``), `engine_available()` is
  False and every creation raises `TurnDetectionUnavailable` — the service
  never silently falls back to fake detection.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - exercised indirectly via the guarded import below
    import voxora_voice_runtime as _runtime
except ImportError:  # pragma: no cover - packaging-disabled environments
    _runtime = None

TURN_SCHEMA_VERSION = 1

ENDPOINT_PROFILES = ("fast", "normal", "deliberate")
AGENT_EVENT_KINDS = ("thinking_started", "tts_started", "tts_ended")
SEMANTIC_VERDICTS = ("incomplete", "complete", "question", "correction")


class TurnDetectionError(Exception):
    """The engine was misused (bad profile, unknown kind, terminal state...)."""


class TurnDetectionUnavailable(TurnDetectionError):
    """The `voxora_voice_runtime` extension is not installed.

    Build it from source: `make py-voice` (or
    `pip install -e crates/voice-runtime` with a Rust toolchain present).
    """


def engine_available() -> bool:
    """True when the Rust extension imports cleanly."""
    return _runtime is not None


def extension_version() -> Optional[str]:
    """The extension's package version, None when unavailable."""
    return str(_runtime.__version__) if _runtime else None


def now_ms() -> int:
    """Monotonic-approximation milliseconds for transport-poor callers."""

    return int(time.monotonic() * 1000) & 0x7FFFFFFF


def _require_runtime() -> Any:
    if _runtime is None:
        raise TurnDetectionUnavailable(
            "voxora_voice_runtime is not installed; build it with "
            "`make py-voice` (maturin) or `pip install -e crates/voice-runtime`."
        )
    return _runtime


def _raise_value_error(message: str) -> None:
    raise TurnDetectionError(message)


class TurnDetectionEngine:
    """One live call's detection pipeline (wraps the Rust controller).

    Feed ordering mirrors the media stream: audio frames (20ms cadence),
    transcript snapshots, semantic/LID reports and agent lifecycle events
    may interleave; every feed returns the versioned events produced by
    that input. `snapshot()` returns counter/state diagnostics without
    side effects.
    """

    __slots__ = ("_engine", "_seq", "call_id", "profile", "semantic_enabled", "word_gate_min_words")

    def __init__(
        self,
        call_id: str,
        profile: str = "normal",
        semantic_enabled: bool = True,
        word_gate_min_words: int = 3,
    ) -> None:
        runtime = _require_runtime()
        if profile not in ENDPOINT_PROFILES:
            _raise_value_error(
                f"Unknown endpoint profile {profile!r}; expected one of {ENDPOINT_PROFILES}"
            )
        self.call_id = call_id
        self.profile = profile
        self.semantic_enabled = bool(semantic_enabled)
        self.word_gate_min_words = max(1, int(word_gate_min_words))
        try:
            self._engine = runtime.TurnDetectionEngine(
                call_id, profile, self.semantic_enabled, self.word_gate_min_words
            )
        except ValueError as error:  # e.g. unknown profile surfaced by Rust
            raise TurnDetectionError(str(error)) from None
        self._seq = 0

    # ---------------------------------------------------------------- feeds

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def feed_audio(
        self,
        timestamp_ms: int,
        rms: float,
        agent_playing: bool = False,
        tts_elapsed_ms: int = 0,
        since_agent_audio_ms: int = 0,
    ) -> List[Dict[str, Any]]:
        """Feed one frame of audio evidence (user channel by default).

        `agent_playing=True` marks an agent-playback frame; the TTS
        maturity and echo-cooldown fields describe the transport state.
        """
        return self._parse(self._engine.feed_audio(
            self._next_seq(), int(timestamp_ms), float(rms),
            bool(agent_playing), int(tts_elapsed_ms), int(since_agent_audio_ms),
        ))

    def feed_transcript(
        self, timestamp_ms: int, text: str, is_final: bool = False
    ) -> List[Dict[str, Any]]:
        """Feed an STT transcript snapshot (cumulative, not delta)."""
        return self._parse(self._engine.feed_transcript(
            self._next_seq(), int(timestamp_ms), text, bool(is_final)
        ))

    def feed_semantic(
        self, timestamp_ms: int, verdict: str, confidence: float
    ) -> List[Dict[str, Any]]:
        """Feed an external semantic verdict (overrides the local heuristic)."""
        if verdict not in SEMANTIC_VERDICTS:
            _raise_value_error(
                f"Unknown semantic verdict {verdict!r}; expected one of {SEMANTIC_VERDICTS}"
            )
        return self._parse(self._engine.feed_semantic(
            self._next_seq(), int(timestamp_ms), verdict, float(confidence)
        ))

    def feed_lid(
        self, timestamp_ms: int, language: str, switch: bool = False
    ) -> List[Dict[str, Any]]:
        """Feed a language-ID result; `switch=True` grants the grace window."""
        return self._parse(self._engine.feed_lid(
            self._next_seq(), int(timestamp_ms), language, bool(switch)
        ))

    def feed_agent_event(
        self, timestamp_ms: int, kind: str, generation: int = 0
    ) -> List[Dict[str, Any]]:
        """Feed one TTS-pipeline lifecycle event."""
        if kind not in AGENT_EVENT_KINDS:
            _raise_value_error(
                f"Unknown agent event kind {kind!r}; expected one of {AGENT_EVENT_KINDS}"
            )
        return self._parse(self._engine.feed_agent_event(
            int(timestamp_ms), kind, int(generation)
        ))

    def note_provider_failure(self, timestamp_ms: int, hint: str) -> List[Dict[str, Any]]:
        """Signal a provider-down moment; detection resiles acoustically."""
        return self._parse(self._engine.note_provider_failure(int(timestamp_ms), hint))

    def note_provider_recovered(self, timestamp_ms: int) -> List[Dict[str, Any]]:
        """Signal end of the provider outage; full detection re-arms."""
        return self._parse(self._engine.note_provider_recovered(int(timestamp_ms)))

    def end_call(self, timestamp_ms: int) -> List[Dict[str, Any]]:
        """End the session: terminal state; further feeds are rejected."""
        return self._parse(self._engine.end_call(int(timestamp_ms)))

    # ---------------------------------------------------------- diagnostics

    def snapshot(self) -> Dict[str, Any]:
        """Stateless counter + state readout for observability."""
        import json

        return json.loads(self._engine.snapshot())

    def state(self) -> str:
        """Current lifecycle state name (snake_case)."""
        return str(self._engine.state())

    @property
    def turn_id(self) -> int:
        """Current zero-based turn ordinal."""
        return int(self._engine.turn_id())

    @property
    def generation(self) -> int:
        """Current pipeline generation (bumped on interruption)."""
        return int(self._engine.generation())

    @staticmethod
    def _parse(raw: str) -> List[Dict[str, Any]]:
        import json

        try:
            events = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise TurnDetectionError(f"corrupt event batch: {error}") from None
        if not isinstance(events, list):
            raise TurnDetectionError("engine returned a non-list event batch")
        return events
