"""Review-fix pass tests (final branch review findings C1, I1, I2, I3, I5, I6)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from voxora.db import Database
from voxora.voice_providers import LanguageRoutingTable, ProviderAdapter, VerifiedQuirk, VerifiedQuirks

AUTH = {"Authorization": "Bearer admin-token"}

_MOCK_SCRIPT = [
    "Hello, this is a simulated call from Voxora Studio. Am I reaching you?",
    "Thanks for confirming. This call is a simulation; nothing is live.",
    "Understood. Ending the simulated call now. Goodbye.",
]


def _seed(client: TestClient, external_session_id: str) -> Dict[str, Any]:
    agent_id = client.post(
        "/api/v1/agents", json={"name": "A", "system_prompt": "s"}, headers=AUTH
    ).json()["id"]
    contact = client.post(
        "/api/v1/contacts",
        json={"first_name": "C", "last_name": "A", "phone": "+15556001111"},
        headers=AUTH,
    )
    if contact.status_code == 201:
        client.patch(
            f"/api/v1/contacts/{contact.json()['id']}/consent",
            json={"state": "granted", "source": "report"},
            headers=AUTH,
        )
    response = client.post(
        "/api/v1/voice-sessions",
        json={
            "external_application": "app",
            "external_session_id": external_session_id,
            "contact": {"external_contact_id": "c1", "name": "C A", "phone": "+15556001111"},
            "agent": {"agent_id": agent_id, "model_profile": "default"},
            "webhook_url": "https://pyinterviewbot.example.com/api/v1/webhooks/voxora",
        },
        headers=AUTH,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _turn(client: TestClient, session_id: str, deltas: list) -> Dict[str, Any]:
    response = client.post(
        f"/api/v1/voice-sessions/{session_id}/turns", json={"deltas": deltas}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------- C1


def test_agent_script_advances_across_requests(voice_runtime_client: TestClient) -> None:
    body = _seed(voice_runtime_client, "fix_c1")
    first = _turn(voice_runtime_client, body["session_id"], ["yes"])["turn"]
    second = _turn(voice_runtime_client, body["session_id"], ["go on"])["turn"]
    assert first["spoken_segments"] == [_MOCK_SCRIPT[1]]
    assert second["spoken_segments"] == [_MOCK_SCRIPT[2]]  # script advanced, not repeated
    assert second["ended"] is True
    assert second["spoken_segments"] != first["spoken_segments"]


# --------------------------------------------------------- C1 / I3 (state)


def test_agent_export_import_state_round_trip() -> None:
    pytest.importorskip("voxora_ai")
    from voxora_ai import MockAgent
    from voxora_ai.openrouter import OpenRouterAgent, create_agent_from_env  # noqa: F401

    agent = MockAgent()
    agent.start()
    agent.respond("yes")
    state = agent.export_state()
    fresh = MockAgent()
    fresh.import_state(state)
    reply = fresh.respond("go on")
    assert reply.text == _MOCK_SCRIPT[2]


def test_openrouter_agent_state_carries_history() -> None:
    pytest.importorskip("voxora_ai")
    import httpx

    from voxora_ai.openrouter import OpenRouterAgent

    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["messages"] = httpx._content.decode(request.read())  # type: ignore[attr-defined]
        import json as _json

        body = _json.loads(request.read())
        captured["messages"] = body["messages"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"text": "ok", "end_call": false}'}}]},
        )

    agent = OpenRouterAgent(
        api_key="sk-or-secret",
        model="test/model",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"),
    )
    agent.rearm()
    agent.respond("first question")
    state = agent.export_state()
    fresh = OpenRouterAgent(
        api_key="sk-or-secret",
        model="test/model",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://openrouter.test/api/v1"),
    )
    fresh.import_state(state)
    fresh.respond("second question")
    contents = [message["content"] for message in captured["messages"]]
    assert any("first question" in content for content in contents)  # history survived the rebuild


def test_engine_usage_persists_across_engines(database: Database) -> None:
    from voxora.voice_turn_loop import LiveTurnEngine

    database.ensure_tenant("tenant-a", "Tenant A")
    database.create_agent("tenant-a", {"name": "agent", "system_prompt": "hi"})
    agent = database.list_agents("tenant-a")[0]
    contact = database.create_local_contact(
        "tenant-a", {"first_name": "T", "last_name": "C", "phone": "+15556101111", "consent_state": "granted"}
    )
    session = database.create_voice_session(
        "tenant-a",
        {
            "external_application": "app",
            "external_session_id": "fix_usage",
            "agent_id": agent["id"],
            "model_profile": "default",
            "contact_id": contact["id"],
            "status": "created",
            "provider": "voice-runtime",
            "simulated": True,
        },
    )

    class FakeAgent:
        provider = "openrouter"

        def __init__(self) -> None:
            self._started = False

        def start(self) -> object:
            self._started = True
            return SimpleNamespace(text="hello", ended=False)

        def respond(self, user_text: str) -> object:
            return SimpleNamespace(text="ok", ended=False)

        def usage_report(self) -> dict:
            return {"total_tokens": 12.0}

        def export_state(self) -> dict:
            return {"started": True}

        def import_state(self, state: dict) -> None:
            self._started = True

    first_agent = FakeAgent()
    engine1 = LiveTurnEngine(database, "tenant-a", session["id"], first_agent)  # type: ignore[arg-type]
    engine1.start()
    engine1.finish_stream()
    persisted = database.get_voice_session("tenant-a", session["id"])["session_metadata"]["agent_state"]
    assert persisted["usage"]["total_tokens"] == 12.0

    engine2 = LiveTurnEngine(
        database, "tenant-a", session["id"], FakeAgent(),  # type: ignore[arg-type]
        resumed=True, agent_state=persisted,
    )
    assert engine2._usage_extra()["total_tokens"] == 12.0
    engine2.finish_stream()
    persisted2 = database.get_voice_session("tenant-a", session["id"])["session_metadata"]["agent_state"]
    assert persisted2["usage"]["total_tokens"] == 24.0  # cumulative, not reset


# ------------------------------------------------------------------- I1


def test_instruction_spoken_once_across_requests(voice_runtime_client: TestClient) -> None:
    body = _seed(voice_runtime_client, "fix_i1")
    voice_runtime_client.post(
        f"/api/v1/voice-sessions/{body['session_id']}/instructions",
        json={"instruction_id": "once", "type": "speak", "text": "SAY THIS ONCE"},
        headers=AUTH,
    )
    first = _turn(voice_runtime_client, body["session_id"], ["hello"])["turn"]
    assert first["instruction_ids"] == ["once"]
    assert first["spoken_segments"][0] == "SAY THIS ONCE"
    second = _turn(voice_runtime_client, body["session_id"], ["more"])["turn"]
    assert second["instruction_ids"] == []  # NOT re-spoken
    assert second["spoken_segments"] == [_MOCK_SCRIPT[1]]


# ------------------------------------------------------------------- I2


def test_effective_adapter_respects_modality() -> None:
    adapters = [
        ProviderAdapter(name="whisper", modality="stt", capabilities={}),
        ProviderAdapter(name="piper", modality="tts", capabilities={}),
    ]
    table = LanguageRoutingTable(
        adapters=adapters,
        chains={"en": ["stt:whisper", "tts:piper"]},
        quirks=VerifiedQuirks(
            [VerifiedQuirk(provider="whisper", modality="stt", language="en", note="n", date="2026-01-01", provenance="p")]
        ),
    )
    assert table.effective_adapter("en", "stt").name == "whisper"
    assert table.effective_adapter("en", "tts") is None  # stt quirk must not satisfy tts


# ------------------------------------------------------------------- I5


def test_restart_recovery_terminates_voice_session(database: Database) -> None:
    database.ensure_tenant("tenant-a", "Tenant A")
    database.create_agent("tenant-a", {"name": "agent", "system_prompt": "hi"})
    agent = database.list_agents("tenant-a")[0]
    contact = database.create_local_contact(
        "tenant-a", {"first_name": "T", "last_name": "C", "phone": "+15556201111", "consent_state": "granted"}
    )
    session = database.create_voice_session(
        "tenant-a",
        {
            "external_application": "app",
            "external_session_id": "fix_recover",
            "agent_id": agent["id"],
            "model_profile": "default",
            "contact_id": contact["id"],
            "status": "in_flight",
            "provider": "voice-runtime",
            "simulated": True,
        },
    )
    call = database.create_voice_call(
        "tenant-a", session["id"], contact["id"],
        type("S", (), {"telephony_provider": "voice-runtime", "allow_live_calls": False})(), agent["id"],
    )
    database.update_voice_session("tenant-a", session["id"], call_id=call["id"])
    database.update_call("tenant-a", call["id"], target="dialing")
    database.update_call("tenant-a", call["id"], target="in_progress")

    database.recover_inflight()

    assert database.get_call("tenant-a", call["id"])["status"] == "failed"
    recovered = database.get_voice_session("tenant-a", session["id"])
    assert recovered["status"] in {"failed", "terminated"}  # session follows the call
    events = database.list_voice_session_events("tenant-a", session["id"])
    assert any(event["event_type"].startswith("voice_session.") for event in events)


# ------------------------------------------------------------------- I6


def test_voice_runtime_501_leaves_no_session(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from voxora.api import create_app
    from voxora.config import Settings, TenantPrincipal

    monkeypatch.setenv("VOXORA_LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = Settings(
        env="test",
        database_path=str(tmp_path / "fix_i6.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
        api_tokens={"admin-token": TenantPrincipal("tenant-a", "admin", "Admin")},
        cors_origins=["http://localhost:5173"],
        worker_enabled=False,
        simulated_call_delay_seconds=0.0,
        telephony_provider="voice-runtime",
        llm_provider="openrouter",
    )
    with TestClient(create_app(settings)) as client:
        agent_id = client.post(
            "/api/v1/agents", json={"name": "A", "system_prompt": "s"}, headers=AUTH
        ).json()["id"]
        contact = client.post(
            "/api/v1/contacts",
            json={"first_name": "C", "last_name": "A", "phone": "+15556301111"},
            headers=AUTH,
        )
        client.patch(
            f"/api/v1/contacts/{contact.json()['id']}/consent",
            json={"state": "granted", "source": "report"},
            headers=AUTH,
        )
        payload = {
            "external_application": "app",
            "external_session_id": "fix_i6_session",
            "contact": {"external_contact_id": "c1", "name": "C A", "phone": "+15556301111"},
            "agent": {"agent_id": agent_id, "model_profile": "default"},
        }
        first = client.post("/api/v1/voice-sessions", json=payload, headers=AUTH)
        assert first.status_code == 501
        second = client.post("/api/v1/voice-sessions", json=payload, headers=AUTH)
        assert second.status_code == 501  # no zombie row to return instead
        assert client.app.state.db.get_voice_session_by_external("tenant-a", "app", "fix_i6_session") is None  # type: ignore[attr-defined]
