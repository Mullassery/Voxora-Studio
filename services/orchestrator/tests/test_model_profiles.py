"""Model-profile dispatch (roadmap M7/P8) + ai_usage rows.

Profile resolution must be config-in-db (seeded from builtin profiles),
dispatch must actually change the model used, unknown profiles must 501
with the configured list, and every decision must land an ai_usage row
(honest zeros for the mock provider).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voxora.api import create_app
from voxora.config import Settings, TenantPrincipal
from voxora.db import Database

AUTH = {"Authorization": "Bearer admin-token"}


# --------------------------------------------------------------- unit / db


def test_seed_is_idempotent_and_builtin(database: Database) -> None:
    from voxora.model_profiles import seed_model_profiles

    seed_model_profiles(database, "tenant-a")
    seed_model_profiles(database, "tenant-a")
    rows = database.list_model_profiles("tenant-a")
    assert {(row["profile"], row["task"]) for row in rows} == {
        ("default", "turn"),
        ("default", "analyze"),
    }
    assert all(row["model"] == "mock-llm" for row in rows)


def test_seed_honors_env_profiles(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    from voxora.model_profiles import seed_model_profiles

    monkeypatch.setenv("VOXORA_MODEL_PROFILES", '{"budget": {"turn": "m-a", "analyze": "m-b"}}')
    seed_model_profiles(database, "tenant-a")
    rows = {(row["profile"], row["task"]): row["model"] for row in database.list_model_profiles("tenant-a")}
    assert rows[("budget", "turn")] == "m-a"
    assert rows[("budget", "analyze")] == "m-b"


def test_resolve_default_and_unknown(database: Database) -> None:
    from voxora.model_profiles import (
        UnknownModelProfileError,
        resolve_model_profile,
        seed_model_profiles,
    )

    seed_model_profiles(database, "tenant-a")
    resolved = resolve_model_profile(database, "tenant-a", None, "analyze")
    assert resolved == {"profile": "default", "task": "analyze", "model": "mock-llm", "provider": "builtin"}
    with pytest.raises(UnknownModelProfileError) as excinfo:
        resolve_model_profile(database, "tenant-a", "nope", "turn")
    assert "default" in excinfo.value.configured
    with pytest.raises(UnknownModelProfileError):
        resolve_model_profile(database, "tenant-a", None, "summarize")


def test_record_and_list_ai_usage(database: Database) -> None:
    database.record_ai_usage(
        "tenant-a",
        profile="default",
        task="analyze",
        model="mock-llm",
        provider="mock",
        prompt_tokens=1.0,
        completion_tokens=2.0,
        total_tokens=3.0,
        cost_usd=0.0,
    )
    database.record_ai_usage(
        "tenant-a",
        profile="default",
        task="analyze",
        model="mock-llm",
        provider="mock",
        prompt_tokens=4.0,
        completion_tokens=5.0,
        total_tokens=9.0,
        cost_usd=0.001,
    )
    rows = database.list_ai_usage("tenant-a")
    assert len(rows) == 2
    assert rows[0]["total_tokens"] == 9.0  # newest first
    assert rows[0]["cost_usd"] == 0.001
    assert rows[1]["total_tokens"] == 3.0


# ----------------------------------------------------- /api/v1/ai/decide


def _openrouter_client(monkeypatch: pytest.MonkeyPatch, tmp_path, *, profiles_env: str = "{}") -> TestClient:
    monkeypatch.setenv("VOXORA_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "test/model")
    monkeypatch.setenv("VOXORA_MODEL_PROFILES", profiles_env)
    settings = Settings(
        env="test",
        database_path=str(tmp_path / "profiles.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
        api_tokens={"admin-token": TenantPrincipal("tenant-a", "admin", "Admin")},
        cors_origins=["http://localhost:5173"],
        worker_enabled=False,
        simulated_call_delay_seconds=0.0,
        llm_provider="openrouter",
    )
    return TestClient(create_app(settings))


def _decide_payload() -> dict:
    return {"system_prompt": "You are an interviewer.", "user_prompt": "Assess the reply."}


def test_ai_decide_dispatches_profile_model(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pytest.importorskip("voxora_ai")
    from voxora_ai.openrouter import OpenRouterLLM

    captured: dict = {}

    real_init = OpenRouterLLM.__init__

    def init(self, *args: object, **kwargs: object) -> None:
        captured["model"] = kwargs.get("model")
        real_init(self, **{k: v for k, v in kwargs.items() if k != "model"}, model="captured")

    monkeypatch.setattr(OpenRouterLLM, "__init__", init)
    monkeypatch.setattr(
        OpenRouterLLM, "decide", lambda self, s, u, **kw: {"next_action": "probe", "is_complete": False}
    )
    monkeypatch.setattr(
        OpenRouterLLM,
        "usage_report",
        lambda self: {"prompt_tokens": 30.0, "completion_tokens": 12.0, "total_tokens": 42.0, "cost_usd": 0.0007},
    )
    with _openrouter_client(monkeypatch, tmp_path, profiles_env='{"analyze-b": {"analyze": "test/analyze-model"}}') as scoped:
        response = scoped.post(
            "/api/v1/ai/decide", json={**_decide_payload(), "model_profile": "analyze-b"}, headers=AUTH
        )
        usage_rows = scoped.app.state.db.list_ai_usage("tenant-a")  # type: ignore[attr-defined]
    assert response.status_code == 200, response.text
    body = response.json()
    assert captured["model"] == "test/analyze-model"  # profile actually dispatched
    assert body["profile"] == "analyze-b"
    assert body["task"] == "analyze"
    assert body["model"] == "test/analyze-model"
    assert body["recorded_usage"]["cost_usd"] == 0.0007
    assert len(usage_rows) == 1
    assert usage_rows[0]["task"] == "analyze"
    assert usage_rows[0]["model"] == "test/analyze-model"
    assert usage_rows[0]["provider"] == "openrouter"
    assert usage_rows[0]["total_tokens"] == 42.0


def test_ai_decide_unknown_profile_501(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pytest.importorskip("voxora_ai")
    from voxora_ai.openrouter import OpenRouterLLM

    monkeypatch.setattr(OpenRouterLLM, "decide", lambda self, s, u, **kw: {})
    with _openrouter_client(monkeypatch, tmp_path) as scoped:
        response = scoped.post(
            "/api/v1/ai/decide", json={**_decide_payload(), "model_profile": "nope"}, headers=AUTH
        )
        usage_rows = scoped.app.state.db.list_ai_usage("tenant-a")  # type: ignore[attr-defined]
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "model_profile_unknown"
    assert "default" in body["error"]["details"]["configured"]
    assert usage_rows == []


def test_ai_decide_mock_records_zero_usage(client) -> None:
    response = client.post("/api/v1/ai/decide", json=_decide_payload(), headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "mock-llm"
    assert body["profile"] == "default"
    assert body["task"] == "analyze"
    assert body["model"] == "mock-llm"
    assert body["recorded_usage"]["total_tokens"] == 0.0
    rows = client.app.state.db.list_ai_usage("tenant-a")  # type: ignore[attr-defined]
    assert len(rows) == 1
    assert rows[0]["model"] == "mock-llm"
    assert rows[0]["total_tokens"] == 0.0


# ------------------------------------------------------- turn-loop usage


def test_engine_records_turn_usage_rows(database: Database) -> None:
    from voxora.voice_turn_loop import LiveTurnEngine

    database.ensure_tenant("tenant-a", "Tenant A")
    database.create_agent("tenant-a", {"name": "agent", "system_prompt": "hi"})
    agent = database.list_agents("tenant-a")[0]
    contact = database.create_local_contact(
        "tenant-a", {"first_name": "Test", "last_name": "Caller", "phone": "+15550001111", "consent_state": "granted"}
    )
    session = database.create_voice_session(
        "tenant-a",
        {
            "external_application": "app",
            "external_session_id": "sess-1",
            "agent_id": agent["id"],
            "model_profile": "default",
            "contact_id": contact["id"],
            "status": "created",
            "provider": "voice-runtime",
            "simulated": True,
        },
    )

    from types import SimpleNamespace

    class FakeAgent:
        provider = "openrouter"

        def start(self) -> object:
            return SimpleNamespace(text="hello", ended=False)

        def respond(self, user_text: str) -> object:
            return SimpleNamespace(text="ok", ended=True)

        def usage_report(self) -> dict:
            return {"total_tokens": 12.0, "cost_usd": 0.0003}

    engine = LiveTurnEngine(database, "tenant-a", session["id"], FakeAgent())  # type: ignore[arg-type]
    engine.start()
    engine.finish_stream()
    rows = database.list_ai_usage("tenant-a")
    assert len(rows) >= 1
    assert rows[0]["task"] == "turn"
    assert rows[0]["total_tokens"] == 12.0
    assert rows[0]["cost_usd"] == 0.0003
