"""TTS provider abstraction tests (roadmap M10 Phase A).

OpenAI integration follows the Twilio-adapter pattern: implemented against the
DOCUMENTED REST contract (developers.openai.com, retrieved 2026-11-13) with
fake-transport tests — contract-verified, LIVE-UNVERIFIED (no credentials in
this environment). The mock provider is the in-repo simulation engine: a
deterministic sine WAV, honestly labeled simulated.
"""

from __future__ import annotations

import io
import json
import wave
from typing import Any, Dict

import pytest

from voxora.config import Settings
from voxora.tts_generation import (
    MAX_SCRIPT_CHARS,
    OPENAI_FORMATS,
    OPENAI_VOICES,
    SPEED_MAX,
    SPEED_MIN,
    MockTTSProvider,
    OpenAITTSProvider,
    TTSProviderError,
    TTSRequest,
    build_tts_provider,
)


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeTransport:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list = []

    def post(
        self, url: str, headers: Dict[str, str], json: Dict[str, Any]
    ) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response


# ------------------------------------------------------------------ constants


def test_documented_constants_match_openai_contract() -> None:
    assert "alloy" in OPENAI_VOICES and "cedar" in OPENAI_VOICES and len(OPENAI_VOICES) == 13
    assert set(OPENAI_FORMATS) == {"mp3", "opus", "aac", "flac", "wav", "pcm"}
    assert (SPEED_MIN, SPEED_MAX) == (0.25, 4.0)
    assert MAX_SCRIPT_CHARS == 4096


# --------------------------------------------------------------- mock provider


def test_mock_provider_returns_valid_wav() -> None:
    provider = MockTTSProvider()
    result = provider.generate(TTSRequest(script="Hello there, welcome aboard.", voice="alloy"))
    assert result.content_type == "audio/wav"
    with wave.open(io.BytesIO(result.audio)) as reader:
        assert reader.getnchannels() == 1
        assert reader.getframerate() == 16000
        assert reader.getnframes() > 0
    again = MockTTSProvider().generate(TTSRequest(script="Hello there, welcome aboard.", voice="alloy"))
    assert again.audio == result.audio  # deterministic


def test_mock_provider_reports_simulated_status() -> None:
    status = MockTTSProvider().status()
    assert status["simulated"] is True
    assert status["implemented"] is True


# ------------------------------------------------------------- openai provider


def test_openai_request_shape_matches_documented_contract() -> None:
    transport = _FakeTransport(_FakeResponse(200, content=b"audio-bytes"))
    provider = OpenAITTSProvider(transport=transport, api_key="sk-test")
    request = TTSRequest(
        script="Hello",
        voice="alloy",
        model="gpt-4o-mini-tts",
        response_format="flac",
        speed=1.5,
        instructions="Speak warmly.",
    )
    result = provider.generate(request)
    call = transport.calls[0]
    assert call["url"] == "https://api.openai.com/v1/audio/speech"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"] == {
        "model": "gpt-4o-mini-tts",
        "input": "Hello",
        "voice": "alloy",
        "response_format": "flac",
        "speed": 1.5,
        "instructions": "Speak warmly.",
    }
    assert result.audio == b"audio-bytes"
    assert result.model == "gpt-4o-mini-tts"
    assert result.format == "flac"


def test_openai_drops_instructions_for_tts1_models() -> None:
    transport = _FakeTransport(_FakeResponse(200, content=b"x"))
    provider = OpenAITTSProvider(transport=transport, api_key="sk-test")
    provider.generate(
        TTSRequest(script="Hi", voice="nova", model="tts-1", instructions="should be dropped")
    )
    assert "instructions" not in transport.calls[0]["json"]


def test_openai_error_is_sanitized_and_never_leaks_the_key() -> None:
    transport = _FakeTransport(_FakeResponse(401, text="Invalid API key sk-test-secret"))
    provider = OpenAITTSProvider(transport=transport, api_key="sk-test-secret")
    with pytest.raises(TTSProviderError) as excinfo:
        provider.generate(TTSRequest(script="Hi", voice="alloy"))
    assert excinfo.value.status_code == 401
    assert "sk-test-secret" not in str(excinfo.value)


def test_openai_empty_body_is_an_error() -> None:
    transport = _FakeTransport(_FakeResponse(200, content=b""))
    provider = OpenAITTSProvider(transport=transport, api_key="sk-test")
    with pytest.raises(TTSProviderError):
        provider.generate(TTSRequest(script="Hi", voice="alloy"))


# --------------------------------------------------------------------- build


def test_build_provider_requires_key_for_openai() -> None:
    settings = Settings(env="test", audio_tts_provider="openai", openai_api_key=None)
    with pytest.raises(ValueError) as excinfo:
        build_tts_provider(settings)
    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_build_provider_openai_configured_and_mock_default() -> None:
    settings = Settings(env="test", audio_tts_provider="openai", openai_api_key="sk-test")
    provider = build_tts_provider(settings)
    assert isinstance(provider, OpenAITTSProvider)
    assert build_tts_provider(Settings(env="test")).status()["simulated"] is True


def test_build_provider_unknown_name_refused() -> None:
    settings = Settings(env="test", audio_tts_provider="azure")
    with pytest.raises(ValueError) as excinfo:
        build_tts_provider(settings)
    assert "azure" in str(excinfo.value)


def test_json_payload_round_trip_has_no_surprise_fields() -> None:
    payload = TTSRequest(script="s", voice="alloy").payload()
    assert set(payload) == {"model", "input", "voice", "response_format", "speed"}
    assert json.loads(json.dumps(payload)) == payload
