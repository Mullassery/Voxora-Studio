"""Tests for the deterministic mock agent backend."""

import hashlib

import pytest

from voxora_ai import AgentTurnError, MockAgent


def test_capabilities_are_honest() -> None:
    agent = MockAgent()
    assert agent.capabilities == {
        "real_llm": False,
        "audio_mixing": False,
        "barge_in_vad": False,
        "scripted": True,
    }


def test_start_returns_first_script_turn() -> None:
    agent = MockAgent()
    reply = agent.start()
    assert reply.turn_index == 1
    assert reply.text.startswith("Hello, this is a simulated call")
    assert reply.ended is False


def test_respond_without_start_raises() -> None:
    agent = MockAgent()
    with pytest.raises(AgentTurnError):
        agent.respond("hello")


def test_script_advances_and_ends() -> None:
    agent = MockAgent()
    agent.start()
    second = agent.respond("yes")
    assert second.turn_index == 2
    assert second.ended is False
    third = agent.respond("sure")
    assert third.turn_index == 3
    assert third.ended is True


def test_user_goodbye_ends_immediately() -> None:
    agent = MockAgent()
    agent.start()
    reply = agent.respond("please end call, goodbye")
    assert reply.ended is True
    assert "Goodbye" in reply.text


def test_script_exhaustion_closes_call() -> None:
    agent = MockAgent(script=["First turn."])
    agent.start()
    reply = agent.respond("go on")
    assert reply.turn_index == 2
    assert reply.ended is True


def test_latency_is_deterministic_and_hash_derived() -> None:
    agent = MockAgent()
    first = agent.start()
    again = MockAgent().start()
    assert first.latency_simulated_ms == again.latency_simulated_ms
    digest = hashlib.sha256(first.text.encode("utf-8")).digest()
    expected = 120.0 + digest[0] % 330
    assert first.latency_simulated_ms == expected
