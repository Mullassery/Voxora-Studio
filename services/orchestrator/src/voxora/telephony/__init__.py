"""Provider-neutral telephony contracts and adapters."""

from .base import CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult
from .mock import MockTelephonyAdapter
from .registry import build_telephony_adapter, telephony_provider_status

__all__ = [
    "CallRequest",
    "MockTelephonyAdapter",
    "TelephonyAdapter",
    "TelephonyEvent",
    "TelephonyResult",
    "build_telephony_adapter",
    "telephony_provider_status",
]
