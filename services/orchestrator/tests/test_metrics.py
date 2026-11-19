"""Prometheus metrics endpoint tests (roadmap M4c)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from voxora.config import Settings, TenantPrincipal
from voxora.db import Database

AUTH = {"Authorization": "Bearer admin-token"}

_METRIC_LINES = (
    "voxora_up",
    "voxora_calls_total",
    "voxora_voice_sessions_total",
    "voxora_queue_depth",
    "voxora_active_calls",
    "voxora_campaigns_total",
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_path=str(tmp_path / "metrics.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
        api_tokens={
            "admin-token": TenantPrincipal("tenant-a", "admin", "Admin"),
            "scrape-token": TenantPrincipal("tenant-a", "viewer", "Scraper"),
        },
        cors_origins=["http://localhost:5173"],
        worker_enabled=False,
        simulated_call_delay_seconds=0.0,
    )


def test_metrics_requires_a_token(tmp_path: Path) -> None:
    from voxora.api import create_app

    with TestClient(create_app(_settings(tmp_path))) as app:
        response = app.get("/metrics")
        assert response.status_code == 401


def test_metrics_exposes_expected_families(tmp_path: Path) -> None:
    from voxora.api import create_app

    with TestClient(create_app(_settings(tmp_path))) as app:
        response = app.get("/metrics", headers={"Authorization": "Bearer scrape-token"})
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    for family in _METRIC_LINES:
        assert family in body, f"missing metric family {family} in:\n{body}"
    assert "# HELP voxora_up" in body  # exposition-format comments present


def test_metrics_counters_reflect_domain_rows(tmp_path: Path) -> None:
    from voxora.api import create_app

    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as app:
        db: Database = app.app.state.db
        db.ensure_tenant("tenant-a", "Tenant A")
        agent = db.create_agent("tenant-a", {"name": "A"})
        contact = db.create_local_contact(
            "tenant-a", {"first_name": "T", "last_name": "C", "phone": "+15558000001"}
        )
        db.update_contact_consent("tenant-a", contact["id"], "granted", "operator")
        campaign = db.create_campaign(
            "tenant-a",
            {"name": "C", "agent_id": agent["id"], "dry_run": True,
             "max_concurrency": 2, "max_attempts": 2, "retry_backoff_seconds": 30},
        )
        db.update_campaign_status("tenant-a", campaign["id"], "running")
        db.add_recipients("tenant-a", campaign["id"], [contact["id"]])
        claimed = db.claim_recipients(5)
        assert len(claimed) == 1
        db.add_latency("tenant-a", claimed[0]["call_id"], "stt_result", 120.0, True)
        db.add_latency("tenant-a", claimed[0]["call_id"], "stt_result", 400.0, True)

        response = app.get("/metrics", headers=AUTH)
    body = response.text

    def _sample(name: str, labels: str) -> float:
        for line in body.splitlines():
            if line.startswith(f"{name}{labels} "):
                return float(line.rsplit(" ", 1)[1])
        raise AssertionError(f"{name}{labels} missing in:\n{body}")

    assert _sample("voxora_up", "") == 1.0
    assert _sample("voxora_campaigns_total", '{status="running"}') == 1.0
    assert _sample("voxora_calls_total", '{status="queued"}') == 1.0
    assert _sample("voxora_queue_depth", "") == 0.0  # recipient claimed → active
    assert _sample("voxora_active_calls", "") == 1.0
    assert _sample("voxora_call_latency_ms", '{stage="stt_result",quantile="0.5"}') == 120.0
    assert _sample("voxora_call_latency_ms", '{stage="stt_result",quantile="0.95"}') == 400.0
