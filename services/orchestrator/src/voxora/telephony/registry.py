"""Telephony adapter construction and honest provider status."""

from __future__ import annotations

from ..config import Settings
from .base import TelephonyAdapter
from .mock import MockTelephonyAdapter
from .voice_runtime import VoiceRuntimeTelephonyAdapter


def build_telephony_adapter(settings: Settings) -> TelephonyAdapter:
    if settings.telephony_provider == "twilio":
        from .twilio import TwilioTelephonyAdapter

        return TwilioTelephonyAdapter(
            account_sid=settings.twilio_account_sid or "",
            auth_token=settings.twilio_auth_token or "",
            twiml_url=settings.twilio_twiml_url or "",
            from_number=settings.twilio_from_number or "",
            live_enabled=settings.allow_live_calls,
            status_callback=settings.twilio_status_callback_url,
        )
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
            "name": "Twilio Programmable Voice",
            "implemented": True,
            "configured": bool(
                settings.twilio_account_sid
                and settings.twilio_auth_token
                and settings.twilio_from_number
                and settings.twilio_twiml_url
            ),
            "live": bool(settings.allow_live_calls
                         and settings.twilio_account_sid
                         and settings.twilio_auth_token
                         and settings.twilio_twiml_url),
            "simulated": False,
            "message": (
                "Live adapter (REST contract per Twilio docs; live-server behavior not verified in this slice). "
                "Dials only with VOXORA_ALLOW_LIVE_CALLS."
            ),
        },
    ]
