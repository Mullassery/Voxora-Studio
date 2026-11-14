"""audio_assets table storage tests (roadmap M10 Phase B)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any, Dict

import pytest

from voxora.audio_assets import (
    MAX_ASSET_BYTES,
    AudioFileStore,
    AudioGenerationError,
    generate_asset,
)
from voxora.config import Settings
from voxora.db import Database
from voxora.tts_generation import MockTTSProvider, TTSProviderError

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


# ------------------------------------------------- file store + validation


def _wav_blob(frames: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def test_validate_wav_blob_reports_duration_and_size() -> None:
    from voxora.audio_assets import validate_audio_blob

    blob = _wav_blob()
    duration, size = validate_audio_blob(blob, "wav")
    assert duration == pytest.approx(1.0)
    assert size == len(blob)


def test_validate_compressed_blob_honestly_reports_no_duration() -> None:
    from voxora.audio_assets import validate_audio_blob

    duration, size = validate_audio_blob(b"fake-mp3-bytes", "mp3")
    assert duration is None
    assert size == len(b"fake-mp3-bytes")


def test_validate_rejects_empty_and_oversized() -> None:
    from voxora.audio_assets import validate_audio_blob

    with pytest.raises(ValueError):
        validate_audio_blob(b"", "wav")
    with pytest.raises(ValueError):
        validate_audio_blob(b"x" * (MAX_ASSET_BYTES + 1), "mp3")


def test_store_paths_stay_under_root(tmp_path: Path) -> None:
    store = AudioFileStore(tmp_path)
    path = store.path_for("tenant-a", "aud_abc123", "wav")
    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert path.name == "aud_abc123.wav"


def test_store_delete_refuses_escape_from_root(tmp_path: Path) -> None:
    store = AudioFileStore(tmp_path)
    outside = tmp_path / ".." / "outside.wav"
    with pytest.raises(ValueError):
        store.delete(outside)


def test_download_headers_sanitize_hostile_titles(tmp_path: Path) -> None:
    store = AudioFileStore(tmp_path)
    for hostile in ('../../etc/passwd"', 'evil\r\n\x00quote"file', "a/b\\c"):
        _, disposition = store.download_headers(hostile, "wav")
        filename = disposition.split('filename="')[-1].rstrip('"')
        assert '"' not in filename
        assert "\r" not in filename and "\n" not in filename and "\x00" not in filename
        assert "/" not in filename and "\\" not in filename and not filename.startswith(".")


def test_generate_asset_end_to_end_with_mock(database: Database, tmp_path: Path) -> None:
    settings = Settings(env="test", audio_storage_path=str(tmp_path / "store"))
    asset = generate_asset(
        database,
        settings,
        TENANT,
        "Admin",
        {
            "script": "Hello, welcome to Voxora Studio.",
            "voice": "alloy",
            "model": "tts-1",
            "speed": 1.0,
            "response_format": "wav",
            "title": "Welcome",
        },
    )
    assert asset["status"] == "ready"
    assert asset["duration_seconds"] is not None
    assert asset["file_size"] > 0
    stored = Path(settings.audio_storage_path) / asset["storage_path"]
    assert stored.exists() and stored.stat().st_size == asset["file_size"]


def test_generate_asset_provider_failure_leaves_no_partial_state(
    database: Database, tmp_path: Path
) -> None:
    settings = Settings(env="test", audio_storage_path=str(tmp_path / "store"))

    class _BrokenProvider(MockTTSProvider):
        def generate(self, request):
            raise TTSProviderError(502, "boom")

    with pytest.raises(AudioGenerationError):
        generate_asset(
            database,
            settings,
            TENANT,
            "Admin",
            {"script": "s", "voice": "alloy", "response_format": "wav"},
            provider=_BrokenProvider(),
        )
    items, total = database.list_audio_assets(TENANT, limit=10, offset=0, search=None)
    assert total == 0
