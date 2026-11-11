"""OpenRouter agent contract tests (ported/adapted from PyInterviewBot).

The live OpenRouter service is not part of this test scope; the fake transport
pins the documented request/response shapes we implement against, including
the OpenRouter usage/billing fields (`usage.include` => per-call `cost`).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

voxora_ai = pytest.importorskip(
    "voxora_ai",
    reason="services/ai-runtime tests import via their own pythonpath fixture",
)
try:
    import httpx
except ImportError:  # pragma: no cover
    pytest.skip("httpx not installed", allow_module_level=True)

from voxora_ai.openrouter import (  # noqa: E402 - below guarded pytest.skip
    DEFAULT_BASE_URL,
    OpenRouterAgent,
    create_agent_from_env,
)

REPLY = {"text": "Hello, what matters most to you?", "end_call": False}
DECISION = {
    "choices": [
        {
            "message": {"content": json.dumps(REPLY)},
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180, "cost": "0.0025"},
}


def make_client(monkeypatch: pytest.MonkeyPatch, handler) -> Any:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://openrouter.test/api/v1")


def build_agent(client: Any, **overrides: Any) -> OpenRouterAgent:
    kwargs = dict(api_key="sk-or-secret", model="anthropic/claude-3.5-sonnet", client=client)
    kwargs.update(overrides)
    return OpenRouterAgent(**kwargs)


def test_request_contract_and_usage_billing_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["title"] = request.headers.get("x-title")
        body = json.loads(request.read())
        captured["body"] = body
        captured["usage_costs"] = bool(body.get("usage", {}).get("include"))
        return httpx.Response(200, json=DECISION)

    agent = build_agent(
        make_client(monkeypatch, handler),
        system_prompt="You are a campaign voice agent.",
        app_name="Voxora Studio",
    )
    agent.start()
    reply = agent.respond("I am listening.")
    assert reply.text == REPLY["text"] and reply.ended is False
    assert captured["path"] == "/api/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-or-secret"
    assert captured["title"] == "Voxora Studio"
    assert captured["body"]["model"] == "anthropic/claude-3.5-sonnet"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["usage_costs"] is True
    # Billing per call, honestly accumulated from OpenRouter's usage response.
    assert agent.last_usage["cost_usd"] == 0.0025
    assert agent.last_usage["total_tokens"] == 180
    report = agent.usage_report()
    # start + respond = two model calls billed by OpenRouter.
    assert report["cost_usd"] == 0.005 and report["total_tokens"] == 360.0
    assert agent.capabilities["real_llm"] is True


def test_missing_cost_is_recorded_as_zero_not_fabricated(monkeypatch: pytest.MonkeyPatch) -> None:
    body = dict(DECISION)
    body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    agent = build_agent(make_client(monkeypatch, handler))
    agent.start()
    assert agent.last_usage["cost_usd"] == 0.0
    assert agent.last_usage["total_tokens"] == 15


def test_retries_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=DECISION)

    agent = build_agent(make_client(monkeypatch, handler), max_retries=1, retry_base_delay=0)
    agent.start()
    assert calls["count"] == 2
    assert agent.respond("yes").ended is False


def test_retries_invalid_json_with_explicit_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        if len(body["messages"]) > 2:
            calls.append("corrected")
            return httpx.Response(200, json=DECISION)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})

    agent = build_agent(make_client(monkeypatch, handler), max_retries=1, retry_base_delay=0)
    agent.start()
    assert calls == ["corrected"]


def test_response_format_fallback_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: List[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen.append(body.get("response_format"))
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(200, json=DECISION)

    agent = build_agent(make_client(monkeypatch, handler), max_retries=1)
    agent.start()
    assert seen[0] == {"type": "json_object"} and seen[1] is None
    assert agent.respond("ok").text == REPLY["text"]


def test_end_call_flag_and_local_end_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    ended_body = {
        "choices": [{"message": {"content": json.dumps({"text": "Goodbye.", "end_call": True})}}],
        "usage": {},
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ended_body)

    agent = build_agent(make_client(monkeypatch, handler), max_retries=0)
    agent.start()
    reply = agent.respond("wrap it up")
    assert reply.ended is True and reply.text == "Goodbye."

    # Local belt-and-suspenders: end keywords close without model access.
    kw_agent = build_agent(make_client(monkeypatch, handler))
    kw_agent.start()
    kw_agent._client = httpx.Client(
        transport=httpx.MockTransport(lambda _req: pytest.fail("network reached after end")),
        base_url=DEFAULT_BASE_URL,
    )
    reply = kw_agent.respond("please stop calling, goodbye")
    assert reply.ended is True and "Goodbye" in reply.text


def test_health_and_key_model_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": True})

    agent = build_agent(make_client(monkeypatch, handler))
    assert agent.health() is True
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    def handler_dead(_r: httpx.Request) -> httpx.Response:
        pytest.fail("should not reach network")
    with pytest.raises(ValueError) as key_error:
        OpenRouterAgent(api_key=None, model=None, client=make_client(monkeypatch, handler_dead))
    assert "OPENROUTER_API_KEY" in str(key_error.value)


def test_agent_brain_takes_over_turns_and_can_end(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxora_ai.engine import AgentBrain, AgentReply

    net_calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        net_calls["n"] += 1
        return httpx.Response(200, json=DECISION)

    class InterviewBrain:
        """What a downstream interview product would implement."""

        def __init__(self) -> None:
            self.turns = 0

        def respond(self, _user_text: str, history: List[Dict[str, str]]) -> Optional[AgentReply]:
            self.turns += 1
            if self.turns >= 2:
                return AgentReply("Evidence collected; ending per interview policy.", ended=True, turn_index=len(history) + 1)
            return None  # defer to Voxora's OpenRouter phrasing

    brain = InterviewBrain()
    assert isinstance(brain, AgentBrain)
    agent = build_agent(make_client(monkeypatch, handler), brain=brain)
    agent.start()
    first = agent.respond("fine, thanks")
    assert brain.turns == 1
    assert "Hello, what matters" in first.text and first.ended is False  # engine phrased it
    second = agent.respond("anything else?")
    assert brain.turns == 2 and net_calls["n"] == 2  # brain turn, zero extra model calls
    assert second.ended is True and "Evidence collected" in second.text
    assert agent._started is False


def test_provider_selection_is_explicit_and_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOXORA_LLM_PROVIDER", raising=False)
    assert type(create_agent_from_env({})).__name__ == "MockAgent"
    with pytest.raises(ValueError) as fail_closed:
        create_agent_from_env({"VOXORA_LLM_PROVIDER": "openrouter"})
    assert "OPENROUTER_API_KEY" in str(fail_closed.value)
    with pytest.raises(ValueError):
        create_agent_from_env({"VOXORA_LLM_PROVIDER": "gpt4-turbo"})


# ---------------------------------------------------------------------------
# Regression (audit 2026-11-04, V1): the default transport must be the
# synchronous `httpx.Client`. Voxora's agent/decide contract is synchronous;
# an AsyncClient default returned unawaited coroutines and crashed every
# default-constructor call path (e.g. `/api/v1/ai/decide` 500s).


def _default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")


def test_default_transport_is_synchronous_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _default_env(monkeypatch)
    agent = OpenRouterAgent()
    assert isinstance(agent._client, httpx.Client)
    assert not isinstance(agent._client, httpx.AsyncClient)

    from voxora_ai.openrouter import OpenRouterLLM

    llm = OpenRouterLLM()
    assert isinstance(llm._client, httpx.Client)
    assert not isinstance(llm._client, httpx.AsyncClient)


def test_llm_decide_round_trip_pins_route_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxora_ai.openrouter import OpenRouterLLM

    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"next_action": "probe"})}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42, "cost": "0.0007"},
            },
        )

    llm = OpenRouterLLM(
        api_key="sk-or-secret",
        model="anthropic/claude-3.5-sonnet",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url=DEFAULT_BASE_URL),
    )
    decision = llm.decide("system prompt", "user prompt", temperature=0.2)
    assert decision == {"next_action": "probe"}
    assert captured["path"] == "/api/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-or-secret"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "system prompt"}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "user prompt"}
    assert captured["body"]["usage"] == {"include": True}
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0.2
    usage = llm.usage_report()
    assert usage["total_tokens"] == 42.0 and usage["cost_usd"] == 0.0007


def test_rearm_resumes_without_calling_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("rearm() must not spend an LLM call")

    agent = build_agent(make_client(monkeypatch, forbidden))
    agent.rearm()
    assert agent._started is True
    assert agent._turn == 0
    assert agent._history == []
