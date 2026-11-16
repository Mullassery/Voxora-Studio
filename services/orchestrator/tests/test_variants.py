"""Variant engine tests (roadmap M10 Phase C): non-destructive asset ops.

The variant flow: a generated WAV asset derives NEW asset rows (parent_asset_id
+ processing_history); the parent file stays byte-identical (pinned). Rust ops
(normalize, voice_boost) run through the REAL extension wheel (the M9 seam
pattern — no mocks); trim_silence/channels are pure-Python stdlib.

If the extension wheel is genuinely absent from this environment, the
extension-backed tests skip honestly — they never fake a pass.
"""

from __future__ import annotations

import io
import math
import wave
from pathlib import Path
from typing import Any, Dict

import pytest

from voxora.audio_assets import AudioFileStore
from voxora.config import Settings
from voxora.db import Database
from voxora.variants import create_variant

try:
    import voxora_voice_runtime as _vr

    HAS_EXTENSION = hasattr(_vr, "audio_postprocess")
except ImportError:
    HAS_EXTENSION = False

TENANT = "t-variant"
needs_extension = pytest.mark.skipif(
    not HAS_EXTENSION, reason="voice runtime extension wheel not built"
)


def _segments_blob(
    segments: list[tuple[float, int]], rate: int = 24000, freq: float = 440.0
) -> bytes:
    """One WAV container made of (amplitude, seconds) segments in sequence."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        frames = bytearray()
        for amp, seconds in segments:
            for i in range(int(rate * seconds)):
                sample = int(amp * math.sin(2 * math.pi * freq * i / rate) * 32768)
                frames += sample.to_bytes(2, "little", signed=True)
        writer.writeframes(bytes(frames))
    return buffer.getvalue()


def _sine_blob(
    rate: int = 24000,
    seconds: float = 1.0,
    freq: float = 440.0,
    amp: float = 0.05,
    channels: int = 1,
    sampwidth: int = 2,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sampwidth)
        writer.setframerate(rate)
        frames = bytearray()
        n = int(rate * seconds)
        for i in range(n):
            value = amp * math.sin(2 * math.pi * freq * i / rate)
            sample = int(value * 32768)
            for _ in range(channels):
                frames += sample.to_bytes(sampwidth, "little", signed=True)
        writer.writeframes(bytes(frames))
    return buffer.getvalue()


def _make_parent(
    database: Database, settings: Settings, blob: bytes, title: str = "Parent"
) -> Dict[str, Any]:
    from voxora.security import new_id

    asset_id = new_id("aud")
    parent = database.create_audio_asset(
        TENANT,
        {
            "id": asset_id,
            "title": title,
            "script": "parent script",
            "provider": "mock",
            "model": "tts-1",
            "voice": "alloy",
            "language": None,
            "speed": 1.0,
            "format": "wav",
            "duration_seconds": None,
            "file_size": len(blob),
            "status": "ready",
            "storage_path": f"{TENANT}/{asset_id}.wav",
            "parent_asset_id": None,
            "processing_history": [],
        },
    )
    AudioFileStore(settings.audio_storage_path).path_for(TENANT, asset_id, "wav").write_bytes(blob)
    return parent


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(env="test", audio_storage_path=str(tmp_path / "store"))


# ------------------------------------------------------------------ python ops


def test_trim_silence_removes_leading_and_trailing_quiet(
    database: Database, settings: Settings
) -> None:
    blob = _segments_blob([(0.0001, 0.3), (0.3, 0.5), (0.0001, 0.3)])
    parent = _make_parent(database, settings, blob)
    variant = create_variant(
        database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
    )
    assert variant["status"] == "ready"
    assert variant["file_size"] < parent["file_size"]
    assert variant["parent_asset_id"] == parent["id"]


def test_trim_that_removes_everything_is_refused(
    database: Database, settings: Settings
) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.0001))
    with pytest.raises(ValueError, match="trim"):
        create_variant(
            database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
        )


def test_short_clip_with_no_quiet_is_not_falsely_refused(
    database: Database, settings: Settings
) -> None:
    """A sub-min_silence loud clip must pass through untouched — the refusal
    message "everything is below the threshold" must never lie."""
    parent = _make_parent(database, settings, _sine_blob(seconds=0.1, amp=0.3))
    variant = create_variant(
        database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
    )
    assert variant["status"] == "ready"
    assert variant["file_size"] == parent["file_size"]


def test_mono_to_stereo_doubles_frames(database: Database, settings: Settings) -> None:
    parent = _make_parent(database, settings, _sine_blob(seconds=0.5))
    variant = create_variant(
        database, settings, TENANT, "Admin", parent, [{"type": "channels", "to": "stereo"}]
    )
    with wave.open(io.BytesIO(Path(settings.audio_storage_path, variant["storage_path"]).read_bytes())) as reader:
        assert reader.getnchannels() == 2
        assert reader.getnframes() == 12000


def test_stereo_to_mono_mixes(database: Database, settings: Settings) -> None:
    parent = _make_parent(database, settings, _sine_blob(seconds=0.5, channels=2))
    variant = create_variant(
        database, settings, TENANT, "Admin", parent, [{"type": "channels", "to": "mono"}]
    )
    with wave.open(io.BytesIO(Path(settings.audio_storage_path, variant["storage_path"]).read_bytes())) as reader:
        assert reader.getnchannels() == 1


def test_non_16bit_parent_refused_honestly(database: Database, settings: Settings) -> None:
    parent = _make_parent(database, settings, _sine_blob(sampwidth=3))
    with pytest.raises(ValueError, match="16-bit"):
        create_variant(
            database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
        )


def test_compressed_parent_refused(database: Database, settings: Settings) -> None:
    parent = database.create_audio_asset(
        TENANT,
        {
            "title": "mp3 parent",
            "script": "s",
            "provider": "mock",
            "model": "tts-1",
            "voice": "alloy",
            "language": None,
            "speed": 1.0,
            "format": "mp3",
            "duration_seconds": None,
            "file_size": 10,
            "status": "ready",
            "storage_path": "nope",
            "parent_asset_id": None,
            "processing_history": [],
        },
    )
    with pytest.raises(ValueError, match="WAV"):
        create_variant(
            database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
        )


# ------------------------------------------------------------------ rust ops


@needs_extension
def test_normalize_via_real_extension_raises_peak(
    database: Database, settings: Settings
) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.05))
    variant = create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        parent,
        [{"type": "normalize", "target_dbfs": -16.0}],
    )
    with wave.open(
        io.BytesIO(Path(settings.audio_storage_path, variant["storage_path"]).read_bytes())
    ) as reader:
        frames = reader.readframes(reader.getnframes())
    out_peak = max(abs(int.from_bytes(frames[i : i + 2], "little", signed=True)) for i in range(0, len(frames), 2))
    assert out_peak > 0.1 * 32768  # was 0.05 peak; -16 dBFS target ≈ 0.158
    history = variant["processing_history"][-1]
    assert history["diagnostics"]["ops"][0]["target_reached"] is True
    assert history["diagnostics"]["clipped_after"] == 0


@needs_extension
def test_voice_boost_at_24k_round_trips_via_real_extension(
    database: Database, settings: Settings
) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.1))
    variant = create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        parent,
        [{"type": "voice_boost", "profile": "clear", "overrides": {}}],
    )
    with wave.open(
        io.BytesIO(Path(settings.audio_storage_path, variant["storage_path"]).read_bytes())
    ) as reader:
        assert reader.getframerate() == 24000
        out_frames = reader.getnframes()
    assert abs(out_frames - 24000) < 240  # duration preserved ±1%
    history = variant["processing_history"][-1]
    assert history["diagnostics"]["internal_rate_roundtrip"] is True


def test_voice_boost_on_stereo_parent_refused(
    database: Database, settings: Settings
) -> None:
    parent = _make_parent(database, settings, _sine_blob(seconds=0.5, channels=2))
    with pytest.raises(ValueError, match="mono"):
        create_variant(
            database, settings, TENANT, "Admin", parent, [{"type": "voice_boost"}]
        )


def test_stereo_convert_to_mono_then_boost_succeeds(
    database: Database, settings: Settings
) -> None:
    """Spec call 3 sanctions convert-then-boost in one request: the mono check
    applies when the boost EXECUTES, not against the parent's channels."""
    parent = _make_parent(database, settings, _sine_blob(seconds=0.5, channels=2))
    variant = create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        parent,
        [
            {"type": "channels", "to": "mono"},
            {"type": "voice_boost", "profile": "clear", "overrides": {}},
        ],
    )
    assert variant["status"] == "ready"
    assert [entry["op"] for entry in variant["processing_history"]] == [
        "channels",
        "voice_boost",
    ]


@needs_extension
def test_resample_op_changes_rate_and_later_ops_track_it(
    database: Database, settings: Settings
) -> None:
    parent = _make_parent(database, settings, _sine_blob(seconds=1.0))
    variant = create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        parent,
        [{"type": "resample", "sample_rate": 16000}],
    )
    with wave.open(
        io.BytesIO(Path(settings.audio_storage_path, variant["storage_path"]).read_bytes())
    ) as reader:
        assert reader.getframerate() == 16000
        assert abs(reader.getnframes() - 16000) <= 16
    record = variant["processing_history"][-1]
    assert record["diagnostics"]["in_rate"] == 24000
    assert record["diagnostics"]["out_rate"] == 16000


# ------------------------------------------------------- flow + non-destructive


def test_ops_apply_in_listed_order_and_record_history(
    database: Database, settings: Settings
) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.05))
    variant = create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        parent,
        [
            {"type": "trim_silence"},
            {"type": "normalize", "target_dbfs": -12.0},
        ],
    )
    history = variant["processing_history"]
    assert [entry["op"] for entry in history] == ["trim_silence", "normalize"]
    assert all(entry["diagnostics"] for entry in history)
    assert history[0]["by"] == "Admin"


def test_parent_file_stays_byte_identical(database: Database, settings: Settings) -> None:
    blob = _sine_blob(amp=0.05)
    parent = _make_parent(database, settings, blob)
    parent_path = Path(settings.audio_storage_path, parent["storage_path"])
    before = parent_path.read_bytes()
    create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        parent,
        [{"type": "trim_silence"}, {"type": "normalize", "target_dbfs": -12.0}],
    )
    assert parent_path.read_bytes() == before


def test_variant_of_variant_chain_allowed(database: Database, settings: Settings) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.05))
    first = create_variant(
        database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
    )
    second = create_variant(
        database,
        settings,
        TENANT,
        "Admin",
        first,
        [{"type": "normalize", "target_dbfs": -12.0}],
    )
    assert second["parent_asset_id"] == first["id"]
    assert [entry["op"] for entry in second["processing_history"]] == [
        "trim_silence",
        "normalize",
    ]


def test_missing_extension_raises_runtime_error(
    database: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.05))
    import voxora.variants as variants_module

    monkeypatch.setattr(variants_module, "load_extension", lambda: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        create_variant(
            database,
            settings,
            TENANT,
            "Admin",
            parent,
            [{"type": "normalize", "target_dbfs": -12.0}],
        )


def test_success_audits_variant_created(database: Database, settings: Settings) -> None:
    parent = _make_parent(database, settings, _sine_blob(amp=0.05))
    create_variant(
        database, settings, TENANT, "Admin", parent, [{"type": "trim_silence"}]
    )
    entries = database.list_audit(TENANT, limit=10, offset=0)
    actions = [entry["action"] for entry in entries]
    assert "audio.variant_created" in actions
