"""Shared test fixtures: throwaway SQLite DB and deterministic settings."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from voxora.api import create_app
from voxora.config import Settings, TenantPrincipal
from voxora.db import Database

_PG_URL = os.environ.get("VOXORA_TEST_PG_URL", "postgresql://localhost:5432/voxora_ci")

_PG_TABLES = [
    "call_events",
    "call_latency",
    "calls",
    "campaign_recipients",
    "campaigns",
    "contacts",
    "agents",
    "audit_logs",
    "crm_connections",
    "crm_field_mappings",
    "crm_sync_jobs",
    "suppression_list",
    "tenants",
    "webhook_events",
]


def _pg_reachable() -> bool:
    try:
        import psycopg

        connection = psycopg.connect(_PG_URL, connect_timeout=3)
        connection.close()
        return True
    except Exception:  # noqa: BLE001 - absence of a server is the normal skip case
        return False


@pytest.fixture(params=["sqlite", f"postgres:{_PG_URL}"])
def database(request, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Database]:
    """Yield Database for SQLite always, and Postgres when a server is reachable."""
    if request.param == "sqlite":
        database = Database(str(tmp_path_factory.mktemp("sqlite") / "voxora.db"))
        database.initialize()
        yield database
        return
    if not _pg_reachable():
        pytest.skip("PostgreSQL test server not reachable")
    from voxora.pg import PostgresDatabase

    database = PostgresDatabase(_PG_URL)
    database.initialize()
    with database._write() as connection:
        connection.execute("TRUNCATE " + ", ".join(_PG_TABLES))
    yield database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_path=str(tmp_path / "test.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
        outbound_webhook_secret="unit-test-outbound-webhook-secret-0123456789",
        webhook_transport="pyreverseetl",
        webhook_dead_letter_path=str(tmp_path / "webhook-dead-letter.jsonl"),
        api_tokens={
            "admin-token": TenantPrincipal("tenant-a", "admin", "Admin"),
            "operator-token": TenantPrincipal("tenant-a", "operator", "Operator"),
            "viewer-token": TenantPrincipal("tenant-a", "viewer", "Viewer"),
            "other-tenant-token": TenantPrincipal("tenant-b", "admin", "Other"),
        },
        cors_origins=["http://localhost:5173"],
        seed_demo=True,
        worker_enabled=False,
        simulated_call_delay_seconds=0.0,
        worker_threads=1,
        global_concurrency=5,
    )


class _FakePyReverseETL(types.ModuleType):
    """Hermetic stand-in for pyreverseetl.connectors in the dispatch path.

    Records every (config, records) write so tests can assert the signed
    envelope contract without live HTTP; every delivery succeeds unless a
    test overrides ``failures_remaining``.
    """

    requests: list = []
    failures_remaining = 0

    class BaseConnector:  # minimal surface for the inline source class
        name = "inline"

    class ConnectorError(RuntimeError):
        pass

    class WebhookConnector:
        name = "webhook"

        def write(self, config, rows):
            if _FakePyReverseETL.failures_remaining > 0:
                _FakePyReverseETL.failures_remaining -= 1
                return 0
            _FakePyReverseETL.requests.append(
                {"config": dict(config), "records": [dict(row) for row in rows]}
            )
            return len(rows)

    @staticmethod
    def run_with_connector(source, source_config, destination, destination_config):
        rows = source.read(source_config)
        written = destination.write(destination_config, rows)

        class _Result:
            run_id = "fake-run"
            rows_written = written
            rows_failed = max(0, len(rows) - written)

        return _Result()


@pytest.fixture(autouse=True)
def fake_pyreverseetl_transport(monkeypatch: pytest.MonkeyPatch):
    """Route webhook dispatch through the fake transport; never hit the network."""
    _FakePyReverseETL.requests = []
    _FakePyReverseETL.failures_remaining = 0
    fake_module = types.ModuleType("pyreverseetl.connectors")
    for name in ("BaseConnector", "ConnectorError", "WebhookConnector", "run_with_connector"):
        setattr(fake_module, name, getattr(_FakePyReverseETL, name))
    monkeypatch.setitem(sys.modules, "pyreverseetl.connectors", fake_module)
    return _FakePyReverseETL


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
