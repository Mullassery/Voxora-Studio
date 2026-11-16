"""Variant engine (roadmap M10 Phase C): non-destructive asset post-processing.

One DSP layer serves both pipeline directions: normalize and voice_boost run
through the Rust engine (`voxora_voice_runtime.audio_postprocess` — the SAME
`VoiceBoostStage` M9 uses for calls, plus the real windowed-sinc resampler);
trim_silence and channel conversion are pure-Python stdlib (amplitude scan,
mean-mix with clip guard). WAV I/O stays stdlib `wave` — the no-ffmpeg ruling
stands, and conversion is NEVER done by renaming extensions.

Non-destructive by construction: the parent file is only ever READ; a variant
is a NEW asset row (parent_asset_id + extended processing_history) with its
own file. Variants of variants are allowed — the chain is the parent_id
traversal, and history accumulates.

Honest refusals (ValueError → 422 at the API edge): compressed or non-16-bit
parents (no in-repo decoder — never misread bytes), voice_boost on stereo
(the Rust chain is mono-only), trims that would remove everything. Extension
missing → RuntimeError → 503, never a Python-only fake pass.
"""

from __future__ import annotations

import io
import json
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .audio_assets import MAX_ASSET_BYTES, AudioFileStore, validate_audio_blob
from .config import Settings
from .db import Database
from .security import new_id

_EXTENSION_UNAVAILABLE = "voice runtime extension unavailable; asset variants require the compiled engine (503)"


def load_extension() -> Optional[Any]:
    """Import the compiled voice runtime; None when genuinely unavailable."""
    try:
        import voxora_voice_runtime as extension

        if not hasattr(extension, "audio_postprocess"):
            return None
        return extension
    except ImportError:
        return None


def _read_parent_wav(settings: Settings, parent: Dict[str, Any]) -> Tuple[bytes, int, int]:
    """Read a parent asset file; WAV 16-bit mono/stereo only, honest refusal."""
    fmt = parent.get("format")
    if fmt != "wav":
        raise ValueError(
            f"variants require a WAV parent (provider-native {fmt!r} passes through"
            " untouched; in-repo decode of compressed formats ships later)"
        )
    path = Path(settings.audio_storage_path) / parent["storage_path"]
    if not path.exists():
        raise ValueError("parent audio file is missing")
    blob = path.read_bytes()
    try:
        with wave.open(io.BytesIO(blob)) as reader:
            channels = reader.getnchannels()
            sampwidth = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as error:
        raise ValueError(f"parent WAV is unparseable: {error}") from None
    if sampwidth != 2:
        raise ValueError(
            f"variants support 16-bit WAV parents only (parent is {sampwidth * 8}-bit);"
            " refusing to misread bytes"
        )
    if channels not in (1, 2):
        raise ValueError(f"variants support mono/stereo parents only (parent has {channels} channels)")
    if not frames:
        raise ValueError("parent WAV contains no frames")
    return frames, rate, channels


def _frames_to_i16(frames: bytes) -> List[int]:
    return [
        int.from_bytes(frames[i : i + 2], "little", signed=True)
        for i in range(0, len(frames), 2)
    ]


def _i16_to_frames(samples: List[int]) -> bytes:
    out = bytearray()
    for s in samples:
        out += int(max(-32768, min(32767, s))).to_bytes(2, "little", signed=True)
    return bytes(out)


def _trim_silence(
    samples: List[int], rate: int, threshold_dbfs: float, min_silence_ms: int
) -> Tuple[List[int], Dict[str, Any]]:
    """Amplitude-threshold head/tail trim (pure slicing, content untouched)."""
    threshold = 32768.0 * (10.0 ** (threshold_dbfs / 20.0))
    min_silence = int(rate * min_silence_ms / 1000)
    quiet = [abs(s) < threshold for s in samples]
    head = 0
    while head < len(quiet) and quiet[head]:
        head += 1
    tail = len(quiet)
    while tail > head and quiet[tail - 1]:
        tail -= 1
    if len(samples) - (head + (len(samples) - tail)) < min_silence:
        raise ValueError(
            "trim_silence would remove the entire asset (everything is below the threshold)"
        )
    trimmed = samples[head:tail]
    return trimmed, {
        "op": "trim_silence",
        "threshold_dbfs": threshold_dbfs,
        "trimmed_head": head,
        "trimmed_tail": len(samples) - tail,
    }


def _convert_channels(
    samples: List[int], channels: int, to: str
) -> Tuple[List[int], int, Dict[str, Any]]:
    """Mono↔stereo; mix is the mean of channels with a hard clip guard."""
    if to == "stereo":
        if channels == 2:
            return samples, 2, {"op": "channels", "to": "stereo", "changed": False}
        stereo: List[int] = []
        for s in samples:
            stereo.extend([s, s])
        return stereo, 2, {"op": "channels", "to": "stereo", "changed": True}
    if channels == 1:
        return samples, 1, {"op": "channels", "to": "mono", "changed": False}
    mono: List[int] = []
    clipped = 0
    for i in range(0, len(samples), 2):
        mixed = (samples[i] + samples[i + 1]) // 2
        if abs(mixed) > 32767:
            clipped += 1
        mono.append(max(-32768, min(32767, mixed)))
    return mono, 1, {"op": "channels", "to": "mono", "changed": True, "clipped": clipped}


_PY_OPS = {"trim_silence", "channels"}
_RUST_OPS = {"normalize", "voice_boost"}


def _apply_rust_op(
    extension: Any, samples: List[int], rate: int, op: Dict[str, Any]
) -> Tuple[List[int], Dict[str, Any]]:
    """One extension call per Rust op so listed-order semantics hold across
    the Python/Rust boundary."""
    kind = op["type"]
    if kind == "normalize":
        rust_op: Dict[str, Any] = {
            "type": "normalize",
            "target_dbfs": float(op.get("target_dbfs", -16.0)),
        }
    else:
        overrides = op.get("overrides") or {}
        rust_op = {
            "type": "voice_boost",
            "settings_json": _voice_boost_settings_json(
                op.get("profile", "clear"), overrides
            ),
        }
    ops_json = json.dumps({"ops": [rust_op]})
    out_samples, diagnostics_json = extension.audio_postprocess(samples, ops_json, rate)
    diagnostics = json.loads(diagnostics_json)
    record = dict(diagnostics)
    record["op"] = kind
    return list(out_samples), record


def _voice_boost_settings_json(profile: str, overrides: Dict[str, Any]) -> str:
    """Variant voice_boost is op-ONLY resolution (builder-check call 4): the
    op's own params over the builtin profile config — tenant stored settings
    are never consulted (a stored `enabled: false` silently no-opping an
    explicit op would be a fake success)."""
    document: Dict[str, Any] = {"enabled": True, "profile": profile, "overrides": overrides}
    return json.dumps(document)


def create_variant(
    db: Database,
    settings: Settings,
    tenant_id: str,
    actor: str,
    parent: Dict[str, Any],
    ops: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Derive one non-destructive variant from a parent asset.

    Raises ValueError (→ 422) for unsupported/refusing inputs, RuntimeError
    (→ 503) when the compiled engine is unavailable. Returns the new asset.
    """
    if not ops:
        raise ValueError("at least one operation is required")
    frames, rate, channels = _read_parent_wav(settings, parent)
    samples = _frames_to_i16(frames)
    current_rate = rate
    current_channels = channels
    has_voice_boost = any(op.get("type") == "voice_boost" for op in ops)
    if has_voice_boost and current_channels == 2:
        raise ValueError(
            "voice_boost is mono-only (the Rust chain is mono); convert to mono first"
        )
    history: List[Dict[str, Any]] = list(parent.get("processing_history") or [])
    extension: Optional[Any] = None
    if any(op.get("type") in _RUST_OPS for op in ops):
        extension = load_extension()
        if extension is None:
            raise RuntimeError(_EXTENSION_UNAVAILABLE)

    for op in ops:
        kind = op.get("type")
        params = {k: v for k, v in op.items() if k != "type"}
        if kind == "trim_silence":
            samples, record = _trim_silence(
                samples,
                current_rate,
                float(op.get("threshold_dbfs", -40.0)),
                int(op.get("min_silence_ms", 120)),
            )
        elif kind == "channels":
            samples, current_channels, record = _convert_channels(
                samples, current_channels, str(op.get("to", "mono"))
            )
        elif kind in _RUST_OPS:
            samples, record = _apply_rust_op(extension, samples, current_rate, op)
            record["params"] = params
        else:
            raise ValueError(f"unsupported variant op: {kind!r}")
        history.append(
            {
                "op": kind,
                "params": params,
                "diagnostics": record,
                "at": datetime.now(timezone.utc).isoformat(),
                "by": actor,
            }
        )

    if not samples:
        raise ValueError("variant would be empty")

    pcm = _i16_to_frames(samples)
    container = io.BytesIO()
    with wave.open(container, "wb") as writer:
        writer.setnchannels(current_channels)
        writer.setsampwidth(2)
        writer.setframerate(current_rate)
        writer.writeframes(pcm)
    audio = container.getvalue()
    asset_id = new_id("aud")
    store_path = f"{tenant_id}/{asset_id}.wav"
    duration, size = validate_audio_blob(audio, "wav")
    if size > MAX_ASSET_BYTES:
        raise ValueError("variant exceeds the asset size cap")
    AudioFileStore(settings.audio_storage_path).save(
        Path(settings.audio_storage_path) / store_path, audio
    )
    asset = db.create_audio_asset(
        tenant_id,
        {
            "id": asset_id,
            "title": f"{parent.get('title') or 'Untitled'} (variant)",
            "script": parent.get("script"),
            "provider": parent.get("provider"),
            "model": parent.get("model"),
            "voice": parent.get("voice"),
            "language": parent.get("language"),
            "speed": parent.get("speed"),
            "format": "wav",
            "duration_seconds": duration,
            "file_size": size,
            "status": "ready",
            "storage_path": store_path,
            "parent_asset_id": parent["id"],
            "processing_history": history,
        },
    )
    db.audit(
        tenant_id,
        actor,
        "audio.variant_created",
        "audio_asset",
        asset_id,
        {"parent": parent["id"], "ops": [op.get("type") for op in ops]},
    )
    return asset
