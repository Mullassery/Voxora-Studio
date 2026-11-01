"""Telephony adapter contract. No provider-specific branches belong above this layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CallRequest:
    call_id: str
    campaign_id: str
    to_number: str
    from_number: Optional[str]
    idempotency_key: str
    attempt: int
    dry_run: bool
    agent: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TelephonyEvent:
    event_type: str
    at: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TelephonyResult:
    ok: bool
    provider: str
    provider_call_id: Optional[str]
    status: str
    disposition: str
    simulated: bool
    retryable: bool = False
    # Providers that dial asynchronously (Twilio) return final=False and are
    # settled later by a status callback; synchronous providers (mock,
    # scripted, voice-runtime) stay final=True.
    final: bool = True
    duration_ms: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    events: List[TelephonyEvent] = field(default_factory=list)
    latency_ms: Dict[str, float] = field(default_factory=dict)
    transcript: List[Dict[str, str]] = field(default_factory=list)
    started_at: Optional[str] = None
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None


class TelephonyAdapter(ABC):
    provider: str

    @abstractmethod
    def start_call(self, request: CallRequest) -> TelephonyResult:
        """Start one outbound call. Must be idempotent on request.idempotency_key."""

    @abstractmethod
    def cancel_call(self, provider_call_id: str) -> bool:
        """Attempt to cancel an in-flight call."""

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """Return provider health/readiness without leaking credentials."""
