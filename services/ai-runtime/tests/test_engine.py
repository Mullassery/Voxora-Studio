"""Tests for the deterministic mock agent backend."""

import hashlib

import pytest

voxora_ai = pytest.importorskip(
    "voxora_ai",
    reason="services/ai-runtime tests import via their own pythonpath fixture",
)


def test_capabilities_are_honest() -> None:
    agent = voxora_ai.MockAgent()
    assert agent.capabilities == {
        "real_llm": False,
        "audio_mixing": False,
        "barge_in_vad": False,
        "scripted": True,
    }


def test_start_returns_first_script_turn() -> None:
    agent = voxora_ai.MockAgent()
    reply = agent.start()
    assert reply.turn_index == 1
    assert reply.text.startswith("Hello, this is a simulated call")
    assert reply.ended is False


def test_respond_without_start_raises() -> None:
    agent = voxora_ai.MockAgent()
    with pytest.raises(voxora_ai.AgentTurnError):
        agent.respond("hello")


def test_script_advances_and_ends() -> None:
    agent = voxora_ai.MockAgent()
    agent.start()
    second = agent.respond("yes")
    assert second.turn_index == 2
    assert second.ended is False
    third = agent.respond("sure")
    assert third.turn_index == 3
    assert third.ended is True


def test_user_goodbye_ends_immediately() -> None:
    agent = voxora_ai.MockAgent()
    agent.start()
    reply = agent.respond("please end call, goodbye")
    assert reply.ended is True
    assert "Goodbye" in reply.text


def test_script_exhaustion_closes_call() -> None:
    agent = voxora_ai.MockAgent(script=["First turn."])
    agent.start()
    reply = agent.respond("go on")
    assert reply.turn_index == 2
    assert reply.ended is True


def test_latency_is_deterministic_and_hash_derived() -> None:
    agent = voxora_ai.MockAgent()
    first = agent.start()
    again = voxora_ai.MockAgent().start()
    assert first.latency_simulated_ms == again.latency_simulated_ms
    digest = hashlib.sha256(first.text.encode("utf-8")).digest()
    expected = 120.0 + digest[0] % 330
    assert first.latency_simulated_ms == expected


def test_rearm_resumes_script_without_repeating_the_opening_line() -> None:
    agent = voxora_ai.MockAgent()
    agent.start()
    agent.respond("yes")
    agent.rearm()
    reply = agent.respond("go on")
    # A resumed session must NOT re-speak script[0]; the caller already heard it.
    assert reply.text != "Hello, this is a simulated call from Voxora Studio. Am I reaching you?"
    assert reply.turn_index == 2
    assert reply.ended is False
