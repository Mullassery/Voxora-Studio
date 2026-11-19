"""Audio asset file store + validation (roadmap M10 Phase B).

The storage side of the PRODUCE half: generated audio lands as files under a
per-tenant tree, with paths built ONLY from internal ids (no user input ever
reaches a path), blob validation before an asset becomes downloadable, and
sanitized download headers. Generated assets are never modified destructively
— post-processing variants (Phase C) create new assets with `parent_asset_id`.

Honest duration: WAV is parsed with the stdlib `wave` module (the no-ffmpeg
ruling stands); compressed formats (mp3/opus/aac/flac) report
`duration_seconds: None` until a converter exists — never zero-fabricated.
"""

from __future__ import annotations

import io
import re
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .db import Database
from .security import new_id
from .tts_generation import (
    MAX_SCRIPT_CHARS,
    OPENAI_FORMATS,
    SPEED_MAX,
    SPEED_MIN,
    TTSProvider,
    TTSProviderError,
    TTSRequest,
    build_tts_provider,
)

MAX_ASSET_BYTES = 50 * 1024 * 1024

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_audio_blob(audio: bytes, ext: str) -> Tuple[Optional[float], int]:
    """Validate a generated blob; return (duration_seconds | None, size)."""
    if not audio:
        raise ValueError("generated audio is empty")
    if len(audio) > MAX_ASSET_BYTES:
        raise ValueError(f"generated audio exceeds the {MAX_ASSET_BYTES}-byte cap")
    if ext in ("wav",):
        try:
            with wave.open(io.BytesIO(audio)) as reader:
                frames = reader.getnframes()
                framerate = reader.getframerate() or 1
                duration = frames / float(framerate)
        except (wave.Error, EOFError) as error:
            raise ValueError(f"provider returned an unparseable WAV: {error}") from None
        if duration <= 0:
            raise ValueError("generated WAV contains no frames")
        return duration, len(audio)
    # Compressed/unknown formats pass through honestly: no duration claim.
    return None, len(audio)


class AudioFileStore:
    """Per-tenant file tree; every path derives from internal ids only."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, tenant_id: str, asset_id: str, ext: str) -> Path:
        if not _ID_RE.match(tenant_id) or not _ID_RE.match(asset_id):
            raise ValueError("tenant/asset ids must be path-safe")
        if not re.match(r"^[a-z0-9]{2,8}$", ext):
            raise ValueError(f"unsafe audio extension: {ext!r}")
        directory = self.root / tenant_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{asset_id}.{ext}"

    def save(self, path: Path, audio: bytes) -> int:
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("refusing to write outside the audio store")
        path.write_bytes(audio)
        return len(audio)

    def delete(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("refusing to delete outside the audio store")
        if path.exists():
            path.unlink()

    def download_headers(self, title: str, ext: str) -> Tuple[str, str]:
        """Sanitized Content-Type + Content-Disposition for one asset."""
        safe = re.sub(r'[^A-Za-z0-9 _.-]+', "_", title or "").strip(" ._")
        safe = safe[:120] or "audio"
        content_types = {
            "mp3": "audio/mpeg",
            "opus": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
            "pcm": "audio/pcm",
        }
        content_type = content_types.get(ext, "application/octet-stream")
        disposition = f'attachment; filename="{safe}.{ext}"'
        return content_type, disposition


class AudioGenerationError(Exception):
    """Raised when generation fails after provider validation; API maps to 502."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def generate_asset(
    db: Database,
    settings: Any,
    tenant_id: str,
    actor: str,
    payload: Dict[str, Any],
    provider: Optional[TTSProvider] = None,
) -> Dict[str, Any]:
    """Generate one asset: provider call → validate → store → DB row.

    A provider failure leaves NO row and NO file behind. `provider` is
    injectable for tests; production builds it from settings (an unconfigured
    provider raises ValueError, which the API maps to 501).
    """
    script = payload["script"]
    if not script.strip():
        raise ValueError("script must not be empty")
    if len(script) > MAX_SCRIPT_CHARS:
        raise ValueError(
            f"script exceeds the {MAX_SCRIPT_CHARS}-character provider limit;"
            " long-form chunking ships with Phase D"
        )
    speed = float(payload.get("speed", 1.0))
    if not SPEED_MIN <= speed <= SPEED_MAX:
        raise ValueError(f"speed must be in [{SPEED_MIN}, {SPEED_MAX}]")
    response_format = payload.get("response_format", "mp3")
    if response_format not in OPENAI_FORMATS:
        raise ValueError(f"unsupported response format: {response_format!r}")
    voice = payload["voice"]
    if not voice or len(voice) > 80:
        raise ValueError("voice must be a non-empty identifier (max 80 chars)")

    if provider is None:
        provider = build_tts_provider(settings)  # ValueError → 501 at the API edge
    request = TTSRequest(
        script=script,
        voice=voice,
        model=payload.get("model", "tts-1"),
        speed=speed,
        response_format=response_format,
        instructions=payload.get("instructions"),
    )
    try:
        result = provider.generate(request)
    except TTSProviderError as error:
        raise AudioGenerationError(error.status_code, error.detail) from None

    asset_id = new_id("aud")
    store = AudioFileStore(settings.audio_storage_path)
    path = store.path_for(tenant_id, asset_id, result.format)
    try:
        duration, size = validate_audio_blob(result.audio, result.format)
    except ValueError as error:
        raise AudioGenerationError(502, str(error)) from None
    store.save(path, result.audio)

    asset = db.create_audio_asset(
        tenant_id,
        {
            "id": asset_id,
            "title": payload.get("title") or "Untitled audio",
            "script": script,
            "provider": provider.id,
            "model": result.model,
            "voice": result.voice,
            "language": payload.get("language"),
            "speed": speed,
            "format": result.format,
            "duration_seconds": duration,
            "file_size": size,
            "status": "ready",
            "storage_path": f"{tenant_id}/{asset_id}.{result.format}",
            "processing_history": [],
        },
    )
    db.audit(
        tenant_id,
        actor,
        "audio.asset_generated",
        "audio_asset",
        asset_id,
        {"provider": provider.id, "voice": result.voice, "format": result.format},
    )
    return asset
