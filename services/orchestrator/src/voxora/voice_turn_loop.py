"""Simulated live turn loop (roadmap M7): the Python-side mount for M7/Rust.

This module closes the M3/M7 open item in the Python service: inside a live
voice session, each caller utterance now flows through a real chain —

    streaming STT (transcript deltas) -> agent turn (voxora_ai seam:
    MockAgent / OpenRouterAgent, the same surface `/api/v1/ai/decide` and the
    telephony bridges consume) -> streamed TTS (spoken segments emitted at
    the stream boundary) — replacing the single-shot TwiML `<Say>` the
    scripted bridge previously produced.

Honesty contract, matching the codebase's simulation-only pattern:

* The "STT" side is a deterministic delta ingest: `ingest_delta()` collects
  role-user transcript deltas (as a real media backend's partial ASR
  hypotheses would arrive) and `finish_stream()` closes the utterance at the
  quiet-gap boundary. Deltas are plain text; no audio decoding happens in
  Python — the Rust `MediaBridge` (crates/voice-runtime/src/media.rs) owns
  jitter/decode/utterance assembly and this engine is what its M7 live loop
  mounts for the decision + reply half.
* The "TTS" side records the spoken segments emitted per turn (plus
  TurnTiming-shaped latency bookkeeping like bridge.rs). Both are
  deterministic fakes: a real vendor never runs and no network is touched —
  provider seams are injected per session.
* Mid-call instructions (persisted in `voice_session_instructions`, accepted
  for the media backend since this slice) become actionable: at each stream
  boundary pending instructions are drained and SPOKEN ahead of the agent
  reply, so the injected text appears in the spoken content and the
  transcript with `source: "instruction"`.
* Sole-writer: every state advance goes through the Database methods that are
  the sole writer of voice-session state (`update_voice_session`,
  `record_voice_session_event`, `settle_voice_session`), and the M6 budget
  guards are enforced per turn tick through
  `voice_session_budgets.enforce_session_budgets` — a breach orderly-
  terminates with `voice_session.terminated` reason `budget_exhausted`
  mid-loop.
* Conversation state (agent script/LLM-history cursor and cumulative usage)
  is SESSION-durable: `export_state()`/`import_state()` on the agent seam +
  `session_metadata.agent_state` mean a fresh engine per request continues
  the same conversation instead of restarting it (the media backend is the
  process-durable owner; this module deliberately keeps no process-long
  engine registry).

NOT wired here (prepared, not mounted): the `CheckpointFlusher` checkpoint
machinery from the M6 hardening work has no live-loop call sites yet —
`POST /turns` advances are request-scoped and settle synchronously, so
nothing flushes checkpoints in this slice. State durability is the
`session_metadata.agent_state` write above, nothing more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .db import Database

logger = logging.getLogger(__name__)

# TurnTiming-shaped stage keys (snake_case), shared with the Rust bridge's
# latency vocabulary (crates/voice-runtime: audioReceived -> agentResponse ->
# ttsFirstAudio) and the Python telephony adapters.
_TURN_STAGE_LATENCY_MS = {
    "audio_received": 42.0,
    "speech_endpoint": 210.0,
    "stt_result": 115.0,
    "agent_response": 280.0,
    "tts_first_audio": 140.0,
    "first_audio_transmitted": 22.0,
}

TURN_COMPLETED_EVENT = "voice_session.turn_completed"
INSTRUCTION_SPOKEN_EVENT = "voice_session.instruction_spoken"


class VoiceTurnLoopError(Exception):
    """The turn loop was used incorrectly (terminal session, missing agent)."""


@runtime_checkable
class TurnAgent(Protocol):
    """The agent seam: MockAgent and OpenRouterAgent both satisfy this."""

    def start(self) -> Any:
        """Open the call; returns an AgentReply-like value."""
        ...

    def respond(self, user_text: str) -> Any:
        """Answer one finalized utterance; returns an AgentReply-like value."""
        ...


@dataclass
class TurnResult:
    """One spoken turn of the live loop (the Python analog of bridge.rs TurnTiming)."""

    turn_index: int
    user_text: str
    spoken_segments: List[str] = field(default_factory=list)
    instruction_ids: List[str] = field(default_factory=list)
    ended: bool = False
    terminated: Optional[str] = None
    latency_ms: Dict[str, float] = field(default_factory=dict)

    @property
    def spoken_text(self) -> str:
        """The full spoken content of the turn, stream-ordered."""
        return " ".join(self.spoken_segments)


def _reply_text(reply: Any) -> str:
    return str(getattr(reply, "text", reply or ""))


def _reply_ended(reply: Any) -> bool:
    return bool(getattr(reply, "ended", False))


def _reply_agent_latency(reply: Any, default: float) -> float:
    value = getattr(reply, "latency_simulated_ms", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return round(float(value), 2)
    return default


def _merge_usage(current: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    """Additive merge: same-key usage fields accrue, they do not overwrite."""
    merged: Dict[str, Any] = dict(current)
    for key, value in delta.items():
        try:
            merged[key] = round(float(merged.get(key, 0.0)) + float(value), 6)
        except (TypeError, ValueError):
            merged[key] = value
    return merged


def build_agent(llm_provider: str = "mock", system_prompt: str = "") -> TurnAgent:
    """Build the session agent over the voxora_ai seam (no network by default).

    `mock` yields voxora_ai's deterministic MockAgent (scripted, honest). The
    `openrouter` provider is honored per the env contract (`create_agent_
    from_env`) but requires real credentials — absent them it raises a
    plain error rather than silently falling back, which keeps the
    simulated/live distinction honest.
    """
    from voxora_ai import engine as ai_engine

    if llm_provider == "openrouter":
        from voxora_ai.openrouter import create_agent_from_env

        agent = create_agent_from_env()
        if system_prompt:
            from voxora_ai.openrouter import OpenRouterAgent

            if isinstance(agent, OpenRouterAgent):
                agent.system_prompt = system_prompt
        return agent  # type: ignore[no-any-return]
    return ai_engine.MockAgent()


class LiveTurnEngine:
    """Per-session driver: STT deltas -> agent turn -> spoken TTS segments.

    Sole-writer faithful: state advances only via the Database sole-writer
    helpers, events ride the versioned envelope, and the M6 budget guards
    run on every turn tick.
    """

    provider = "voice-runtime"

    def __init__(
        self,
        db: Database,
        tenant_id: str,
        session_id: str,
        agent: TurnAgent,
        *,
        settings: Optional[Any] = None,
        completed_turns: int = 0,
        resumed: bool = False,
        agent_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.agent = agent
        self._settings = settings
        self._buffer: List[str] = []
        # Conversation state is session-durable, not engine-durable: a fresh
        # engine (new request or new process) restores the persisted agent
        # cursor (script position / LLM history) and cumulative usage so a
        # multi-turn session actually progresses and budgets stay honest.
        self._usage: Dict[str, float] = dict((agent_state or {}).get("usage") or {})
        # Agents are rebuilt per request; usage_report() is cumulative for
        # the agent *instance*, so the session-cumulative number is
        # persisted-usage + (report - report-at-engine-start).
        self._usage_baseline: Dict[str, float] = {}
        baseline = getattr(agent, "usage_report", None)
        if callable(baseline):
            initial = baseline()
            if isinstance(initial, dict):
                self._usage_baseline = {
                    str(key): float(value or 0.0) for key, value in initial.items()
                }
        restored = bool(agent_state) and callable(getattr(agent, "import_state", None))
        if restored:
            agent.import_state(agent_state)  # type: ignore[attr-defined]
            self._started = True
        else:
            # A resumed engine without persisted agent state skips start():
            # the opening line was already spoken by the request that
            # created it. The agent seam is re-armed WITHOUT re-speaking
            # that line (rearm() when available, else a discarded start()
            # for scripted agents).
            self._started = bool(resumed)
            if resumed:
                rearm = getattr(agent, "rearm", None)
                if callable(rearm):
                    rearm()
                else:
                    agent.start()
        self._done = False
        self.completed_turns = completed_turns
        self._spoken_instruction_ids: set = set()

    # ------------------------------------------------------------- STT side
    def ingest_delta(self, delta: str) -> None:
        """Collect one STT transcript delta (simulated partial hypotheses)."""
        if self._done:
            raise VoiceTurnLoopError("the live call is already over")
        self._buffer.append(str(delta))

    def finish_stream(self) -> TurnResult:
        """Stream boundary: close the buffered utterance, run the turn.

        This is the mount point a real media backend drives from its
        utterance-assembly close (MediaBridge#emit_reply on the Rust side).
        Pending mid-call instructions are drained HERE — spoken ahead of
        the agent reply — so scheduler-injected text flips the spoken
        content from the next boundary onward.
        """
        if self._done:
            raise VoiceTurnLoopError("the live call is already over")
        user_text = " ".join(part for part in (p.strip() for p in self._buffer) if part)
        self._buffer = []

        breached = self._budget_tick()
        if breached is not None:
            return self._turn_result(
                turn_index=self.completed_turns,
                user_text=user_text,
                terminated=breached,
            )
        if not self._started:
            raise VoiceTurnLoopError("the agent has not started; call start() first")

        instructions = self._drain_pending_instructions()
        reply = self.agent.respond(user_text) if user_text else self.agent.respond("")
        segments = [instruction["text"] for instruction in instructions]
        segments.append(_reply_text(reply))
        self._persist_turn(user_text, instructions, segments)
        result = self._turn_result(
            turn_index=self.completed_turns,
            user_text=user_text,
            spoken_segments=segments,
            instruction_ids=[instruction["instruction_id"] for instruction in instructions],
            latency=self._latency(reply),
        )
        for instruction in instructions:
            self.db.record_voice_session_event(
                self.tenant_id,
                self.session_id,
                INSTRUCTION_SPOKEN_EVENT,
                {"instruction_id": instruction["instruction_id"], "type": instruction["type"]},
            )
            # Durably mark spoken so a fresh engine (next request) never
            # re-drains it — engine-local dedupe alone is per-instance.
            self.db.mark_session_instruction_spoken(
                self.tenant_id, self.session_id, instruction["instruction_id"]
            )
        self.db.record_voice_session_event(
            self.tenant_id, self.session_id, TURN_COMPLETED_EVENT, self._turn_event_data(result, reply)
        )
        self.completed_turns += 1
        self._collect_agent_usage()
        self._persist_agent_state()
        breached = self._budget_tick()
        if breached is not None:
            result.terminated = breached
            return result
        if _reply_ended(reply):
            result.ended = True
            self._settle("completed", True)
            self._done = True
        return result

    # ------------------------------------------------------------ lifecycle
    def start(self) -> TurnResult:
        """Open the live call: the agent's opening line is spoken."""
        breached = self._budget_tick()
        if breached is not None:
            return self._turn_result(turn_index=0, terminated=breached)
        if self._started:
            raise VoiceTurnLoopError("the live call has already started")
        self._started = True
        reply = self.agent.start()
        segments = [_reply_text(reply)]
        self._persist_turn(user_text="", instructions=[], segments=segments)
        result: TurnResult = self._turn_result(
            turn_index=1, spoken_segments=segments, latency=self._latency(reply)
        )
        self.db.record_voice_session_event(
            self.tenant_id, self.session_id, TURN_COMPLETED_EVENT, self._turn_event_data(result, reply)
        )
        self.completed_turns += 1
        self._collect_agent_usage()
        self._persist_agent_state()
        breached = self._budget_tick()
        if breached is not None:
            result.terminated = breached
            return result
        if _reply_ended(reply):
            result.ended = True
            self._settle("completed", True)
            self._done = True
        return result

    # ------------------------------------------------------------ internals
    def _persist_agent_state(self) -> None:
        """Session-durable conversation cursor + cumulative usage (review C1/I3)."""
        export = getattr(self.agent, "export_state", None)
        if not callable(export):
            return
        try:
            state = export()
            if isinstance(state, dict):
                state["usage"] = dict(self._usage)
                self.db.update_voice_session_metadata(
                    self.tenant_id, self.session_id, {"agent_state": state}
                )
        except Exception:  # noqa: BLE001 - a state-write failure must not kill the turn
            logger.exception("agent state persistence failed for session %s", self.session_id)
    def _session(self) -> Dict[str, Any]:
        session = self.db.get_voice_session(self.tenant_id, self.session_id)
        if session is None:
            raise VoiceTurnLoopError("voice session not found")
        return session

    def _budget_tick(self) -> Optional[str]:
        """M6 guard per turn tick; breach orderly-terminates (sole-writer path)."""
        from .voice_session_budgets import enforce_session_budgets

        breached = enforce_session_budgets(
            self.db,
            self.tenant_id,
            self.session_id,
            usage={"turns": self.completed_turns, **self._usage_extra()},
            settings=self._settings,
        )
        if breached is not None:
            self._done = True
        return breached

    def _drain_pending_instructions(self) -> List[Dict[str, Any]]:
        pending: List[Dict[str, Any]] = []
        for instruction in self.db.list_session_instructions(self.tenant_id, self.session_id):
            if instruction["id"] in self._spoken_instruction_ids:
                continue
            if (instruction.get("status") or "received") != "received":
                continue  # spoken or superseded — never re-spoken
            self._spoken_instruction_ids.add(instruction["id"])
            pending.append(instruction)
        return pending

    def _persist_turn(
        self,
        user_text: str,
        instructions: List[Dict[str, Any]],
        segments: List[str],
    ) -> None:
        entries: List[Dict[str, Any]] = []
        if user_text:
            entries.append({"role": "user", "text": user_text})
        for index, segment in enumerate(segments):
            entry: Dict[str, Any] = {"role": "agent", "text": segment}
            source = "agent"
            instruction = instructions[index] if index < len(instructions) else None
            if instruction is not None:
                source = "instruction"
                entry["instruction_id"] = instruction["instruction_id"]
            entry["source"] = source
            entries.append(entry)
        existing = (self._session().get("transcript") or [])
        self.db.update_voice_session(
            self.tenant_id, self.session_id, transcript=existing + entries
        )

    def _collect_agent_usage(self) -> None:
        """Real per-turn usage capture (M6 wanted this from M7's loop).

        The agent instance is per-request, so its report is that request's
        cumulative usage; the delta over the engine-start baseline is what
        this turn consumed, and it accrues onto the session-cumulative
        totals persisted in agent_state.usage.
        """
        usage = getattr(self.agent, "usage_report", None)
        if callable(usage):
            report = usage()
            if isinstance(report, dict):
                delta = {
                    str(key): float(value or 0.0) - self._usage_baseline.get(str(key), 0.0)
                    for key, value in report.items()
                }
                delta = {key: round(value, 6) for key, value in delta.items() if value != 0.0}
                self._usage = {**self._usage, **_merge_usage(self._usage, delta)}
                self._record_usage_row(delta)
                # The baseline becomes the last-seen report so the next
                # collection only counts what the agent spent SINCE then.
                self._usage_baseline = {
                    str(key): float(value or 0.0) for key, value in report.items()
                }

    def _record_usage_row(self, report: Dict[str, Any]) -> None:
        """Persist one ai_usage row per turn task (P8 token economics).

        Never breaks the live call: profile resolution failure (unknown
        profile row) or a storage hiccup only costs the analytics row,
        which is the honest outcome — the session is the primary record.
        """
        try:
            from .model_profiles import resolve_model_profile

            resolved = resolve_model_profile(
                self.db,
                self.tenant_id,
                (self._session().get("model_profile") or None),
                "turn",
            )
            self.db.record_ai_usage(
                self.tenant_id,
                profile=resolved["profile"],
                task="turn",
                model=resolved["model"],
                provider=str(getattr(self.agent, "provider", "mock")),
                prompt_tokens=float(report.get("prompt_tokens") or 0.0),
                completion_tokens=float(report.get("completion_tokens") or 0.0),
                total_tokens=float(report.get("total_tokens") or 0.0),
                cost_usd=float(report.get("cost_usd") or 0.0),
            )
        except Exception:  # noqa: BLE001 - analytics must not kill the call
            logger.exception("ai_usage row recording failed for session %s", self.session_id)

    def _usage_extra(self) -> Dict[str, float]:
        return dict(getattr(self, "_usage", {}) or {})

    def _latency(self, reply: Any) -> Dict[str, float]:
        latency = dict(_TURN_STAGE_LATENCY_MS)
        latency["agent_response"] = _reply_agent_latency(reply, latency["agent_response"])
        latency["turn_completed"] = round(sum(latency.values()), 2)
        return latency

    def _turn_result(
        self,
        turn_index: int,
        user_text: str = "",
        spoken_segments: Optional[List[str]] = None,
        instruction_ids: Optional[List[str]] = None,
        terminated: Optional[str] = None,
        latency: Optional[Dict[str, float]] = None,
    ) -> TurnResult:
        return TurnResult(
            turn_index=turn_index,
            user_text=user_text,
            spoken_segments=spoken_segments or [],
            instruction_ids=instruction_ids or [],
            terminated=terminated,
            latency_ms=latency or {},
        )

    def _turn_event_data(self, result: TurnResult, reply: Any) -> Dict[str, Any]:
        return {
            "turn_index": result.turn_index,
            "user_text": result.user_text,
            "spoken_segments": result.spoken_segments,
            "tts_frames": [len(segment.split()) for segment in result.spoken_segments],
            "instruction_ids": result.instruction_ids,
            "ended": _reply_ended(reply),
            "latency_ms": result.latency_ms,
        }

    def _settle(self, disposition: str, ok: bool) -> None:
        session = self._session()
        call_id = session.get("call_id")
        call = self.db.get_call(self.tenant_id, call_id) if call_id else None
        if call is not None:
            from .voice_sessions import settle_voice_session

            settle_voice_session(
                self.db,
                call,
                disposition,
                ok,
                {"simulated": True, "transcript_available": bool(session.get("transcript"))},
                settings=self._settings,
            )
            return
        from .voice_sessions import session_status_for

        status = session_status_for(disposition, ok)
        self.db.update_voice_session(self.tenant_id, self.session_id, status=status)
        self.db.record_voice_session_event(
            self.tenant_id,
            self.session_id,
            f"voice_session.{status}",
            {"status": status, "disposition": disposition},
        )


def run_live_turns(
    db: Database,
    tenant_id: str,
    session_id: str,
    agent: TurnAgent,
    utterances: List[List[str]],
    *,
    settings: Optional[Any] = None,
) -> List[TurnResult]:
    """Scripted convenience driver: ingest deltas + close the stream per entry.

    Each entry is the list of STT deltas for one turn (the deterministic fake
    transport's partial hypotheses). Stops after the first budget breach,
    agent-initiated end, or an exhausted utterance script — exactly how the
    live loop terminates. The opening turn is the agent's `start()` reply.
    """
    engine = LiveTurnEngine(db, tenant_id, session_id, agent, settings=settings)
    results: List[TurnResult] = [engine.start()]
    for deltas in utterances:
        if engine._done or results[-1].ended or results[-1].terminated is not None:
            break
        for delta in deltas:
            engine.ingest_delta(delta)
        outcome = engine.finish_stream()
        results.append(outcome)
    return results
