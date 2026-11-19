"""Speech-analytics estimates per finalized transcript (roadmap M8/P10).

Computed ONCE per settled session, stored on the session, and exposed
through the outbound contract (transcript.ready event data + session
view). Estimates are deterministic:

* `pause_count` — interior silence runs ≥ 400 ms (lead-in/lead-out
  silence never counts as a pause; a pause needs speech on both sides).
* `duration_seconds` — full WAV duration.
* `words` / `filler_word_count` — from the transcript text.
* `words_per_minute` — words / duration (audio-measured, not guessed).

Honesty contract: NO audio ⇒ no analytics at all (the key is omitted
upstream, never zero-fabricated). Unfetchable or unparseable audio ⇒
same omission.

Ruling (2026-11-12): the roadmap said "ffmpeg-based"; ffmpeg is absent
in this environment and not guaranteed in CI, so the decoder is stdlib
`wave`/`struct` with identical semantics on PCM16 WAV input. A
compressed-audio ffmpeg path can layer on later without changing the
contract.
"""

from __future__ import annotations

import io
import math
import re
import struct
import wave
from typing import Any, Dict, List, Optional, Tuple

WINDOW_SECONDS = 0.02
PAUSE_MIN_SECONDS = 0.4
SPEECH_THRESHOLD = 0.02  # fraction of full scale (PCM16)

FILLER_PATTERN = re.compile(
    r"\b(um|uh|er|ah|hmm|like|you know|actually|basically|literally)\b",
    re.IGNORECASE,
)


def _decode_wav(audio: bytes) -> Optional[Tuple[List[int], int]]:
    """PCM16 WAV -> (mono samples, sample rate); None when unparseable."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            if reader.getsampwidth() != 2:
                return None
            rate = reader.getframerate()
            channels = reader.getnchannels() or 1
            raw = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError, OSError):
        return None
    count = len(raw) // 2
    if count == 0:
        return None
    samples: List[int] = list(struct.unpack(f"<{count}h", raw[: count * 2]))
    if channels > 1:
        samples = samples[::channels]
    return samples, rate


def _rms_per_window(samples: List[int], rate: int) -> List[float]:
    window = max(1, int(rate * WINDOW_SECONDS))
    windows: List[float] = []
    for start in range(0, len(samples), window):
        chunk = samples[start : start + window]
        if not chunk:
            break
        mean = sum(chunk) / len(chunk)
        windows.append(math.sqrt(sum((value - mean) ** 2 for value in chunk) / len(chunk)))
    return windows


def compute_speech_analytics(
    transcript: List[Dict[str, Any]],
    audio: Optional[bytes],
) -> Optional[Dict[str, Any]]:
    """Deterministic analytics, or None when there is no audio to measure."""
    if audio is None:
        return None
    decoded = _decode_wav(audio)
    if decoded is None:
        return None
    samples, rate = decoded
    if not samples:
        return None
    threshold = SPEECH_THRESHOLD * 32768.0
    windows = _rms_per_window(samples, rate)
    speech = [rms > threshold for rms in windows]

    pause_min_windows = max(1, int(PAUSE_MIN_SECONDS / WINDOW_SECONDS))
    pause_count = 0
    run_length = 0
    in_run = False
    for index, is_speech in enumerate(speech):
        if not is_speech:
            if in_run:
                run_length += 1
            elif any(speech[:index]):
                in_run = True
                run_length = 1
        else:
            if in_run and run_length >= pause_min_windows and any(speech[index:]):
                pause_count += 1
            in_run = False
            run_length = 0

    text = " ".join(str(entry.get("text") or "") for entry in transcript)
    words = len(text.split())
    duration_seconds = round(len(samples) / rate, 2)
    return {
        "duration_seconds": duration_seconds,
        "pause_count": pause_count,
        "words": words,
        "words_per_minute": round(words / duration_seconds * 60, 2) if duration_seconds > 0 else 0.0,
        "filler_word_count": len(FILLER_PATTERN.findall(text)),
    }
