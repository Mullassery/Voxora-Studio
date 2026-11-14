"""audio_assets table storage tests (roadmap M10 Phase B)."""

from __future__ import annotations

from typing import Any, Dict

from voxora.db import Database

TENANT = "t-audio"


def _asset_values(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "title": "Welcome message",
        "script": "Hello, welcome to Voxora.",
        "provider": "mock",
        "model": "tts-1",
        "voice": "alloy",
        "language": None,
        "speed": 1.0,
        "format": "wav",
        "duration_seconds": 1.5,
        "file_size": 48044,
        "status": "ready",
        "storage_path": "t-audio/aud_1.wav",
        "parent_asset_id": None,
        "processing_history": [],
    }
    values.update(overrides)
    return values


def test_create_get_roundtrip_decodes_history(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_audio_asset(TENANT, _asset_values())
    stored = database.get_audio_asset(TENANT, created["id"])
    assert stored is not None
    assert stored["title"] == "Welcome message"
    assert stored["processing_history"] == []


def test_list_paging_and_title_search(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    first = database.create_audio_asset(TENANT, _asset_values(title="Welcome"))
    database.create_audio_asset(TENANT, _asset_values(title="Win-back promo"))
    items, total = database.list_audio_assets(TENANT, limit=10, offset=0, search="welcome")
    assert total == 1 and items[0]["id"] == first["id"]
    items, total = database.list_audio_assets(TENANT, limit=1, offset=0, search=None)
    assert total == 2 and len(items) == 1


def test_update_title_and_status(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_audio_asset(TENANT, _asset_values())
    updated = database.update_audio_asset(TENANT, created["id"], title="Renamed")
    assert updated is not None and updated["title"] == "Renamed"
    blocked = database.update_audio_asset(TENANT, created["id"], provider="hijack")
    assert blocked["provider"] == "mock"  # not in the allowed set


def test_delete_returns_true_then_missing(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_audio_asset(TENANT, _asset_values())
    assert database.delete_audio_asset(TENANT, created["id"]) is True
    assert database.get_audio_asset(TENANT, created["id"]) is None
    assert database.delete_audio_asset(TENANT, created["id"]) is False


def test_count_by_status(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    database.create_audio_asset(TENANT, _asset_values(status="ready"))
    database.create_audio_asset(TENANT, _asset_values(status="failed", title="broke"))
    counts = database.count_audio_assets_by_status(TENANT)
    assert counts["ready"] == 1 and counts["failed"] == 1


def test_assets_are_tenant_scoped(database: Database) -> None:
    database.ensure_tenant(TENANT, "Tenant")
    created = database.create_audio_asset(TENANT, _asset_values())
    assert database.get_audio_asset("other-tenant", created["id"]) is None
