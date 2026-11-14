"""TTS provider abstraction for Audio Generation (roadmap M10 Phase A).

Provider-independent speech generation for the operator asset pipeline —
deliberately separate from the live call path (the M7/P9 boundary stands:
spoken content in calls comes only from the agent or the instruction store).

OpenAI integration follows the Twilio-adapter pattern: implemented against the
documented REST contract (POST /v1/audio/speech — developers.openai.com,
retrieved 2026-11-13) with injectable transport and fake-transport tests.
Contract-verified, LIVE-UNVERIFIED: no credentials exist in this environment.
The provider exposes exactly the voices/models/formats/speeds the documented
API exposes; unsupported selections are refused by validation, never silently
defaulted. OpenAI exposes NO language-selection parameter (voices are
"optimized for English" per the docs) — language is recorded metadata only.

The in-repo mock provider is the simulation engine: a deterministic sine WAV,
honestly labeled `simulated: true`, so local runs and the standard test suite
need zero paid API calls.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple

import httpx

from .config import Settings

MAX_SCRIPT_CHARS = 4096
SPEED_MIN = 0.25
SPEED_MAX = 4.0
MAX_INSTRUCTIONS_CHARS = 4096

# Documented OpenAI TTS surface (retrieved 2026-11-13, live-unverified).
OPENAI_VOICES: Tuple[str, ...] = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)
OPENAI_FORMATS: Tuple[str, ...] = ("mp3", "opus", "aac", "flac", "wav", "pcm")
OPENAI_SPEECH_MODELS: Tuple[str, ...] = ("tts-1", "tts-1-hd", "gpt-4o-mini-tts")
INSTRUCTIONS_CAPABLE_MODELS = {"gpt-4o-mini-tts"}

MOCK_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class TTSRequest:
    """One validated generation request (validation happens at the API edge)."""

    script: str
    voice: str
    model: str = "tts-1"
    speed: float = 1.0
    response_format: str = "mp3"
    instructions: Optional[str] = None

    def payload(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model,
            "input": self.script,
            "voice": self.voice,
            "response_format": self.response_format,
            "speed": self.speed,
        }
        if self.instructions is not None and self.model in INSTRUCTIONS_CAPABLE_MODELS:
            body["instructions"] = self.instructions
        return body


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    content_type: str
    model: str
    voice: str
    format: str


class TTSProviderError(Exception):
    """Provider-side failure; `detail` is sanitized (never carries secrets)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"TTS provider error {status_code}: {detail}")


class TTSProvider(Protocol):
    id: str

    def generate(self, request: TTSRequest) -> TTSResult: ...

    def status(self) -> Dict[str, Any]: ...


def _mock_wav(script: str, speed: float) -> bytes:
    """Deterministic sine WAV: ~0.5s of audio per 20 script characters."""
    seconds = max(0.5, min(60.0, len(script) / 20.0)) / max(0.25, speed)
    frames = int(MOCK_SAMPLE_RATE * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(MOCK_SAMPLE_RATE)
        samples = bytearray()
        for index in range(frames):
            phase = 2.0 * math.pi * 440.0 * index / MOCK_SAMPLE_RATE
            value = int(0.3 * 32767.0 * math.sin(phase))
            samples += struct.pack("<h", value)
        writer.writeframes(bytes(samples))
    return buffer.getvalue()


class MockTTSProvider:
    """Deterministic in-repo simulation provider (honestly `simulated`)."""

    id = "mock"

    def generate(self, request: TTSRequest) -> TTSResult:
        return TTSResult(
            audio=_mock_wav(request.script, request.speed),
            content_type="audio/wav",
            model=request.model,
            voice=request.voice,
            format="wav",
        )

    def status(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "implemented": True,
            "configured": True,
            "live": False,
            "simulated": True,
            "voices": ["mock-voice"],
            "formats": ["wav"],
            "speed": {"min": SPEED_MIN, "max": SPEED_MAX},
            "language_selection": False,
        }


class OpenAITTSProvider:
    """Documented-REST OpenAI TTS provider (contract-verified, live-unverified)."""

    id = "openai"
    base_url = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        transport: Optional[Any] = None,
        api_key: str,
        timeout: float = 60.0,
    ) -> None:
        self._transport = transport
        self._api_key = api_key
        self._timeout = timeout

    def _client(self) -> Any:
        if self._transport is not None:
            return self._transport
        return httpx.Client(
            base_url=self.base_url,
            timeout=self._timeout,
        )

    def generate(self, request: TTSRequest) -> TTSResult:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/audio/speech"
        client = self._client()
        response = client.post(url, headers=headers, json=request.payload())
        audio = getattr(response, "content", b"")
        if response.status_code != 200:
            # Sanitize: the provider detail never reaches the client, and the
            # key never reaches a message.
            raise TTSProviderError(response.status_code, f"provider refused (HTTP {response.status_code})")
        if not audio:
            raise TTSProviderError(502, "provider returned an empty body")
        content_types = {
            "mp3": "audio/mpeg",
            "opus": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
            "pcm": "audio/pcm",
        }
        return TTSResult(
            audio=audio,
            content_type=content_types.get(request.response_format, "application/octet-stream"),
            model=request.model,
            voice=request.voice,
            format=request.response_format,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "implemented": True,
            "configured": True,
            "live": False,
            "simulated": False,
            "verified": "documented contract 2026-11-13; live-unverified",
            "voices": list(OPENAI_VOICES),
            "models": list(OPENAI_SPEECH_MODELS),
            "formats": list(OPENAI_FORMATS),
            "speed": {"min": SPEED_MIN, "max": SPEED_MAX},
            "language_selection": False,
        }


def build_tts_provider(settings: Settings) -> TTSProvider:
    """Registry: provider selection by settings, honest failures otherwise."""
    name = settings.audio_tts_provider
    if name == "mock":
        return MockTTSProvider()
    if name == "openai":
        if not settings.openai_api_key:
            raise ValueError(
                "VOXORA_AUDIO_TTS_PROVIDER=openai requires OPENAI_API_KEY;"
                " audio generation is unconfigured (501 until configured)"
            )
        return OpenAITTSProvider(api_key=settings.openai_api_key)
    raise ValueError(
        f"unknown audio TTS provider: {name} (mock|openai; azure/google deferred)"
    )
