"""Deterministic script-driven agent backend for simulated calls.

The orchestrator's calls are simulated; the mock agent produces replies with
a hash-derived pseudo-render latency so behavior is reproducible in tests,
and it declares its capabilities honestly (no real LLM, no VAD, no audio).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, runtime_checkable

_END_KEYWORDS = ("goodbye", "bye", "stop calling", "end call")
_CLOSING = "Understood. Ending the simulated call now. Goodbye."


class AgentTurnError(Exception):
    """The backend was used incorrectly (out-of-order calls)."""


@runtime_checkable
class AgentBrain(Protocol):
    """Optional conversation-policy hook, for downstream products.

    Voxora owns the call lifecycle and (when configured) the model call; a
    brain may take over per-turn wording or end-call decisions. Returning
    ``None`` hands the turn back to Voxora's default phrasing. Implementing
    this does not create any Voxora dependency in the brain."""

    def respond(self, user_text: str, history: List[Dict[str, str]]) -> Optional["AgentReply"]:
        """Return the next agent turn, or ``None`` to defer to the engine."""
        ...


def turn_from_reply(reply: Optional[AgentReply], fallback_reply: AgentReply) -> AgentReply:
    """Prefer the brain's turn (including its explicit end_call), else the engine's."""
    if reply is None or (not reply.text.strip() and not reply.ended):
        return fallback_reply
    return reply


@dataclass(frozen=True)
class AgentReply:
    """One agent turn."""

    text: str
    ended: bool = False
    turn_index: int = 1

    @property
    def latency_simulated_ms(self) -> float:
        digest = hashlib.sha256(self.text.encode("utf-8")).digest()
        return 120.0 + digest[0] % 330


@dataclass
class MockAgent:
    """Deterministic script-driven agent.

    The script maps turn index -> reply text; anything past the script falls
    back to a closing reply marked ``ended``. A user "goodbye"/"end call"
    closes the call immediately.
    """

    name: str = "Simulated outbound agent"
    script: List[str] = field(
        default_factory=lambda: [
            "Hello, this is a simulated call from Voxora Studio. Am I reaching you?",
            "Thanks for confirming. This call is a simulation; nothing is live.",
            "Understood. Ending the simulated call now. Goodbye.",
        ]
    )
    capabilities: Dict[str, bool] = field(
        default_factory=lambda: {
            "real_llm": False,
            "audio_mixing": False,
            "barge_in_vad": False,
            "scripted": True,
        }
    )

    _turn: int = 0
    _started: bool = False

    def start(self) -> AgentReply:
        self._turn = 0
        self._started = True
        return self._reply()

    def rearm(self) -> None:
        """Re-arm for a resumed live session; no reply is (re-)spoken.

        The opening line already went out when the session first started;
        resume only restores the script position so the next `respond`
        continues the script instead of repeating it.
        """
        self._turn = 0
        self._started = True

    def respond(self, user_text: str) -> AgentReply:
        if not self._started:
            raise AgentTurnError("call has not been started; call start() first")
        self._turn += 1
        lowered = user_text.strip().lower()
        if any(keyword in lowered for keyword in _END_KEYWORDS):
            self._started = False
            return AgentReply(_CLOSING, ended=True, turn_index=self._turn + 1)
        return self._reply()

    def _reply(self) -> AgentReply:
        index = self._turn
        if index >= len(self.script):
            return AgentReply(_CLOSING, ended=True, turn_index=index + 1)
        return AgentReply(
            self.script[index],
            ended=index == len(self.script) - 1,
            turn_index=index + 1,
        )
