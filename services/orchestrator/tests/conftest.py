"""Shared test fixtures: throwaway SQLite DB and deterministic settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voxora.api import create_app
from voxora.config import Settings, TenantPrincipal


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_path=str(tmp_path / "test.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
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


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
