"""Long-form (Phase D) generation: chunking, run engine, retry.

Design: docs/superpowers/specs/2026-11-13-longform-generation-design.md
"""

import io
import json
import re
import threading
import wave
from typing import Any, Dict, List, Optional, Set

from .audio_assets import (
    MAX_ASSET_BYTES,
    AudioFileStore,
    AudioGenerationError,
    validate_audio_blob,
)
from .db import Database
from .security import new_id
from .tts_generation import (
    SPEED_MAX,
    SPEED_MIN,
    TTSProvider,
    TTSProviderError,
    TTSRequest,
    build_tts_provider,
)

MAX_CHUNKS = 64
DEFAULT_MAX_CHUNK_CHARS = 4096

# Chunk assembly formats: WAV is parsed frame-exact; PCM is raw concat.
LONGFORM_FORMATS = ("wav", "pcm")

# Per-process markers for runs currently executing (Phase D call 1: a
# `retry-failed` while one of these is held is a 409, never a double-run).
# `_RUN_GATE` makes check+add atomic — FastAPI runs sync routes in a
# threadpool even single-worker, so a bare membership test would race.
run_locks: Set[str] = set()
_RUN_GATE = threading.Lock()


class RunInFlightError(ValueError):
    """A run for this asset is already executing (API maps to 409)."""


def _acquire_run_lock(asset_id: str) -> None:
    with _RUN_GATE:
        if asset_id in run_locks:
            raise RunInFlightError("a long-form run for this asset is in flight")
        run_locks.add(asset_id)


def _release_run_lock(asset_id: str) -> None:
    with _RUN_GATE:
        run_locks.discard(asset_id)

_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _split_long_text(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    sentences = _SENTENCE_SPLIT.split(text)
    if len(sentences) > 1:
        parts: List[str] = []
        cur = ""
        for s in sentences:
            if len(s) > limit:
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.extend(_hard_split(s, limit))
            elif not cur:
                cur = s
            elif len(cur) + 1 + len(s) <= limit:
                cur = f"{cur} {s}"
            else:
                parts.append(cur)
                cur = s
        if cur:
            parts.append(cur)
        return parts
    return _hard_split(text, limit)


def _hard_split(text: str, limit: int) -> List[str]:
    parts: List[str] = []
    cur = text
    while len(cur) > limit:
        cut = cur.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(cur[:cut])
        cur = cur[cut:].lstrip(" ")
    if cur:
        parts.append(cur)
    return parts


def chunk_script(script: str, max_chunk_chars: int) -> List[str]:
    """Split a script into provider-sized chunks.

    Paragraph boundaries first (blank lines); a paragraph longer than the
    limit is split at sentence boundaries; a single sentence longer than
    the limit is hard-split at a word boundary. Paragraph merges preserve
    the original separators; sentence/hard splits rejoin with single
    spaces (prosodically equivalent — the retry-stability guarantee is
    that STORED chunk texts are reused byte-identically, not that texts
    reconstruct the source byte-for-byte).
    """
    if not script.strip():
        raise ValueError("script is empty")
    paragraphs = [p for p in _PARA_SPLIT.split(script) if p.strip()]
    chunks: List[str] = []
    cur = ""
    for p in paragraphs:
        if len(p) > max_chunk_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_split_long_text(p, max_chunk_chars))
        elif not cur:
            cur = p
        elif len(cur) + 2 + len(p) <= max_chunk_chars:
            cur = f"{cur}\n\n{p}"
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(
            f"script requires {len(chunks)} chunks, above the {MAX_CHUNKS}-chunk cap; shorten the script"
        )
    return chunks


def _validate_longform_payload(payload: Dict[str, Any]) -> None:
    script = payload.get("script", "")
    if not script.strip():
        raise ValueError("script must not be empty")
    response_format = payload.get("response_format", "wav")
    if response_format not in LONGFORM_FORMATS:
        raise ValueError(
            f"long-form assembly supports {LONGFORM_FORMATS} only (no in-repo decoder for {response_format!r})"
        )
    voice = payload.get("voice", "")
    if not voice or len(voice) > 80:
        raise ValueError("voice must be a non-empty identifier (max 80 chars)")
    speed = float(payload.get("speed", 1.0))
    if not SPEED_MIN <= speed <= SPEED_MAX:
        raise ValueError(f"speed must be in [{SPEED_MIN}, {SPEED_MAX}]")
    max_chunk_chars = int(payload.get("max_chunk_chars", DEFAULT_MAX_CHUNK_CHARS))
    if not 500 <= max_chunk_chars <= DEFAULT_MAX_CHUNK_CHARS:
        raise ValueError("max_chunk_chars must be in [500, 4096]")


def _settings_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "voice": payload["voice"],
        "model": payload.get("model", "tts-1"),
        "speed": float(payload.get("speed", 1.0)),
        "response_format": payload.get("response_format", "wav"),
        "instructions": payload.get("instructions"),
        "language": payload.get("language"),
        "title": payload.get("title"),
    }


def _chunk_request(text: str, snapshot: Dict[str, Any]) -> TTSRequest:
    return TTSRequest(
        script=text,
        voice=snapshot["voice"],
        model=snapshot["model"],
        speed=snapshot["speed"],
        response_format=snapshot["response_format"],
        instructions=snapshot["instructions"],
    )


def _chunk_path(store: AudioFileStore, tenant_id: str, asset_id: str, index: int, ext: str):
    return store.path_for(tenant_id, f"{asset_id}_chunk_{index}", ext)


def _generate_one_chunk(
    store: AudioFileStore,
    tenant_id: str,
    asset_id: str,
    index: int,
    text: str,
    snapshot: Dict[str, Any],
    provider: TTSProvider,
) -> None:
    """Generate, validate, and persist one chunk file. Raises on failure."""
    result = provider.generate(_chunk_request(text, snapshot))
    if result.format != snapshot["response_format"]:
        raise ValueError(
            f"provider returned {result.format!r} for a {snapshot['response_format']!r} chunk"
        )
    validate_audio_blob(result.audio, result.format)
    path = _chunk_path(store, tenant_id, asset_id, index, result.format)
    store.save(path, result.audio)


def _assemble_wav(store: AudioFileStore, tenant_id: str, asset_id: str, paths) -> bytes:
    """Frame-exact WAV concat; identical rate/channels enforced."""
    frames = bytearray()
    params = None
    for path in paths:
        with wave.open(str(path)) as reader:
            current = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
            if params is None:
                params = current
            elif current != params:
                raise ValueError("chunk files disagree on rate/channels; refusing to assemble")
            frames += reader.readframes(reader.getnframes())
    assert params is not None
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(params[0])
        writer.setsampwidth(params[1])
        writer.setframerate(params[2])
        writer.writeframes(bytes(frames))
    return buffer.getvalue()


def _finish(
    db: Database,
    store: AudioFileStore,
    tenant_id: str,
    asset_id: str,
    fmt: str,
    audio: bytes,
    history: list,
) -> Dict[str, Any]:
    if len(audio) > MAX_ASSET_BYTES:
        raise ValueError(
            f"assembled audio exceeds the {MAX_ASSET_BYTES}-byte cap; use a shorter script"
        )
    path = store.path_for(tenant_id, asset_id, fmt)
    store.save(path, audio)
    if fmt == "wav":
        with wave.open(io.BytesIO(audio)) as reader:
            frames = reader.getnframes()
            duration = frames / float(reader.getframerate() or 1)
    else:
        duration = None  # pcm: honest None, same ruling as validate_audio_blob
    history.append(
        {
            "kind": "assembled",
            "file": f"{tenant_id}/{asset_id}.{fmt}",
            "bytes": len(audio),
            "duration_seconds": duration,
        }
    )
    row = db.update_audio_asset(
        tenant_id,
        asset_id,
        status="ready",
        duration_seconds=duration,
        file_size=len(audio),
        processing_history=history,
    )
    if row is None:  # the row was just created in this run
        raise RuntimeError("long-form row disappeared during assembly")
    return row


def _fail_assembly(
    db: Database,
    tenant_id: str,
    asset_id: str,
    history: list,
    error: Exception,
) -> Dict[str, Any]:
    history.append({"kind": "assembly_failed", "error": str(error)})
    row = db.update_audio_asset(tenant_id, asset_id, status="failed", processing_history=history)
    if row is None:  # the row was just created in this run
        raise RuntimeError("long-form row disappeared during assembly failure")
    return row


def _chunk_entry(index: int, text: str, state: str, error: Optional[str] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "kind": "chunk",
        "chunk": index,
        "text": text,
        "chars": len(text),
        "state": state,
    }
    if error is not None:
        entry["error"] = error
    return entry


def _load_asset_history(asset: Dict[str, Any]) -> list:
    history = asset.get("processing_history")
    if isinstance(history, str):
        return json.loads(history)
    return list(history or [])


def _check_runnable(asset: Dict[str, Any]) -> list:
    history = _load_asset_history(asset)
    if not history or history[0].get("kind") != "longform":
        raise ValueError("asset is not a long-form run")
    _acquire_run_lock(asset["id"])  # raises RunInFlightError while held
    try:
        if asset.get("status") not in ("processing", "failed"):
            raise ValueError(
                f"long-form retry requires status processing/failed, not {asset.get('status')!r}"
            )
    except BaseException:
        _release_run_lock(asset["id"])
        raise
    return history


def generate_long_form(
    db: Database,
    settings: Any,
    tenant_id: str,
    actor: str,
    payload: Dict[str, Any],
    provider: Optional[TTSProvider] = None,
) -> Dict[str, Any]:
    """Chunk, generate every chunk with one settings set, assemble one asset.

    A chunk failure leaves the row `processing` with that chunk marked
    `failed` (retry via retry_long_form); an assembly failure is terminal
    `failed`. Provider injectable for tests.
    """
    _validate_longform_payload(payload)
    if provider is None:
        provider = build_tts_provider(settings)
    script = payload["script"]
    max_chunk_chars = int(payload.get("max_chunk_chars", DEFAULT_MAX_CHUNK_CHARS))
    texts = chunk_script(script, max_chunk_chars)
    fmt = payload.get("response_format", "wav")

    asset_id = new_id("aud")
    run_id = new_id("run")
    snapshot = _settings_snapshot(payload)
    history: list = [
        {"kind": "longform", "run_id": run_id, "settings": snapshot, "max_chunk_chars": max_chunk_chars}
    ]
    history.extend(_chunk_entry(i, text, "pending") for i, text in enumerate(texts))

    store = AudioFileStore(settings.audio_storage_path)
    asset = db.create_audio_asset(
        tenant_id,
        {
            "id": asset_id,
            "title": payload.get("title") or "Untitled audio",
            "script": script,
            "provider": provider.id,
            "model": snapshot["model"],
            "voice": snapshot["voice"],
            "language": snapshot["language"],
            "speed": snapshot["speed"],
            "format": fmt,
            "duration_seconds": None,
            "file_size": 0,
            "status": "processing",
            "storage_path": f"{tenant_id}/{asset_id}.{fmt}",
            "processing_history": history,
        },
    )

    _acquire_run_lock(asset_id)
    try:
        for index, entry in enumerate(history[1:]):
            try:
                _generate_one_chunk(store, tenant_id, asset_id, index, entry["text"], snapshot, provider)
                entry["state"] = "ready"
                entry["ext"] = fmt
            except (TTSProviderError, AudioGenerationError, ValueError) as error:
                entry["state"] = "failed"
                entry["error"] = str(error)
            history[index + 1] = entry
            db.update_audio_asset(tenant_id, asset_id, processing_history=history)

        if any(e["state"] != "ready" for e in history[1:]):
            # Chunk failures keep the row processing: retry-failed regenerates.
            return db.get_audio_asset(tenant_id, asset_id) or {**asset, "processing_history": history}

        try:
            paths = [_chunk_path(store, tenant_id, asset_id, i, fmt) for i in range(len(texts))]
            if fmt == "pcm":
                audio = b"".join(path.read_bytes() for path in paths)
            else:
                audio = _assemble_wav(store, tenant_id, asset_id, paths)
            finished = _finish(db, store, tenant_id, asset_id, fmt, audio, history)
            for path in paths:
                store.delete(path)
            db.audit(
                tenant_id,
                actor,
                "audio.longform_completed",
                "audio_asset",
                asset_id,
                {"chunks": len(texts), "provider": provider.id},
            )
            return finished
        except (ValueError, wave.Error, EOFError, OSError) as error:
            return _fail_assembly(db, tenant_id, asset_id, history, error)
    finally:
        _release_run_lock(asset_id)
def retry_long_form(
    db: Database,
    settings: Any,
    tenant_id: str,
    actor: str,
    asset: Dict[str, Any],
    provider: Optional[TTSProvider] = None,
) -> Dict[str, Any]:
    """Regenerate ONLY failed/missing chunks from the stored snapshot, reassemble."""
    history = _check_runnable(asset)  # also acquires the run lock
    asset_id = asset["id"]
    try:
        if provider is None:
            provider = build_tts_provider(settings)
        header = history[0]
        snapshot = header["settings"]
        fmt = snapshot["response_format"]
        if fmt not in LONGFORM_FORMATS:
            raise ValueError(f"long-form assembly supports {LONGFORM_FORMATS} only, not {fmt!r}")

        store = AudioFileStore(settings.audio_storage_path)
        chunk_entries = [e for e in history[1:] if e.get("kind") == "chunk"]
        for index, entry in enumerate(chunk_entries):
            path = _chunk_path(store, tenant_id, asset_id, index, fmt)
            if entry.get("state") == "ready" and path.exists():
                continue
            try:
                _generate_one_chunk(store, tenant_id, asset_id, index, entry["text"], snapshot, provider)
                entry["state"] = "ready"
                entry["ext"] = fmt
                entry.pop("error", None)
            except (TTSProviderError, AudioGenerationError, ValueError) as error:
                entry["state"] = "failed"
                entry["error"] = str(error)
            db.update_audio_asset(tenant_id, asset_id, processing_history=history)

        if any(e["state"] != "ready" for e in chunk_entries):
            return db.get_audio_asset(tenant_id, asset_id) or {**asset, "processing_history": history}

        try:
            paths = [_chunk_path(store, tenant_id, asset_id, i, fmt) for i in range(len(chunk_entries))]
            if fmt == "pcm":
                audio = b"".join(path.read_bytes() for path in paths)
            else:
                audio = _assemble_wav(store, tenant_id, asset_id, paths)
            finished = _finish(db, store, tenant_id, asset_id, fmt, audio, history)
            for path in paths:
                store.delete(path)
            db.audit(
                tenant_id,
                actor,
                "audio.longform_completed",
                "audio_asset",
                asset_id,
                {"chunks": len(chunk_entries), "provider": provider.id, "retry": True},
            )
            return finished
        except (ValueError, wave.Error, EOFError, OSError) as error:
            return _fail_assembly(db, tenant_id, asset_id, history, error)
    finally:
        _release_run_lock(asset_id)
