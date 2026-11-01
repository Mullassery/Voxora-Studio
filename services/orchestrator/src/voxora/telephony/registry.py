"""Telephony adapter construction and honest provider status."""

from __future__ import annotations

from ..config import Settings
from .base import TelephonyAdapter
from .mock import MockTelephonyAdapter
from .voice_runtime import VoiceRuntimeTelephonyAdapter


def build_telephony_adapter(settings: Settings) -> TelephonyAdapter:
    if settings.telephony_provider == "voice-runtime":
        # Constructing the adapter does not import the extension; the first
        # call surfaces an honest error naming the maturin build command.
        return VoiceRuntimeTelephonyAdapter()
    if settings.telephony_provider != "mock":
        raise ValueError(
            f"Telephony provider '{settings.telephony_provider}' is not implemented in this slice"
        )
    return MockTelephonyAdapter(delay_seconds=settings.simulated_call_delay_seconds)


def telephony_provider_status(settings: Settings) -> list:
    return [
        {
            "id": "mock",
            "name": "Deterministic mock",
            "implemented": True,
            "configured": True,
            "live": False,
            "simulated": True,
            "message": "Default for development and CI. Never places calls.",
        },
        {
            "id": "voice-runtime",
            "name": "Rust voice bridge (scripted)",
            "implemented": True,
            "configured": True,
            "live": False,
            "simulated": True,
            "message": (
                "Deterministic Rust bridge via PyO3; simulation only, needs `make py-voice`."
            ),
        },
        {
            "id": "twilio",
            "name": "Twilio",
            "implemented": False,
            "configured": bool(
                settings.twilio_account_sid
                and settings.twilio_auth_token
                and settings.twilio_from_number
            ),
            "live": False,
            "simulated": False,
            "message": "Provider contract documented; adapter is not implemented in this slice.",
        },
    ]
