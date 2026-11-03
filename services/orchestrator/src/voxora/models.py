"""Validated request models for the versioned API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        raise ValueError("phone must contain 7 to 15 digits")
    return ("+" if plus else "") + digits


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_prompt: str = Field(default="", max_length=8000)
    model: str = Field(default="mock-llm", max_length=120)
    voice: str = Field(default="mock-voice", max_length=120)
    stt_provider: str = Field(default="mock-stt", max_length=120)
    tts_provider: str = Field(default="mock-tts", max_length=120)
    max_turn_seconds: int = Field(default=30, ge=2, le=300)


class ContactCreate(BaseModel):
    first_name: str = Field(default="", max_length=120)
    last_name: str = Field(default="", max_length=120)
    phone: str
    email: str = Field(default="", max_length=320)
    timezone: str = Field(default="UTC", max_length=80)
    locale: str = Field(default="en-US", max_length=32)
    consent_state: str = Field(default="unknown")
    consent_source: str = Field(default="operator", max_length=120)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)

    _phone = field_validator("phone")(normalize_phone)

    @field_validator("consent_state")
    @classmethod
    def consent_state_valid(cls, value: str) -> str:
        if value not in {"granted", "revoked", "unknown"}:
            raise ValueError("consent_state must be granted, revoked, or unknown")
        return value


class ConsentUpdate(BaseModel):
    state: str
    source: str = Field(default="operator", max_length=120)

    @field_validator("state")
    @classmethod
    def state_valid(cls, value: str) -> str:
        if value not in {"granted", "revoked", "unknown"}:
            raise ValueError("state must be granted, revoked, or unknown")
        return value


class SuppressionCreate(BaseModel):
    phone: str
    reason: str = Field(default="operator suppression", max_length=500)

    _phone = field_validator("phone")(normalize_phone)


class VoiceSessionContact(BaseModel):
    """The caller-of-record for one voice session (opaque external ids)."""

    model_config = {"extra": "forbid"}
    external_contact_id: str = Field(min_length=1, max_length=200)
    phone: str
    name: Optional[str] = Field(default=None, max_length=200)

    _phone = field_validator("phone")(normalize_phone)


class VoiceSessionAgent(BaseModel):
    model_config = {"extra": "forbid"}
    agent_id: str
    model_profile: Optional[str] = Field(default=None, max_length=120)


class VoiceSessionCreate(BaseModel):
    """Voice-session intake per the ecosystem ADR (ADR-3B).

    `session_metadata` and `external_session_id` are opaque to Voxora: the
    caller (e.g. PyInterviewBot) owns their semantics — including any
    interview template reference. Voxora never interprets them.
    """

    model_config = {"extra": "forbid"}
    external_application: str = Field(min_length=1, max_length=120)
    external_session_id: str = Field(min_length=1, max_length=200)
    contact: VoiceSessionContact
    agent: VoiceSessionAgent
    session_metadata: Dict[str, Any] = Field(default_factory=dict)
    webhook_url: Optional[str] = Field(default=None, max_length=2000)


class SessionInstruction(BaseModel):
    """Mid-call instruction; delivery depends on provider capabilities."""

    model_config = {"extra": "forbid"}
    instruction_id: str = Field(min_length=1, max_length=200)
    type: str = Field(default="speak", max_length=40)
    text: str = Field(min_length=1, max_length=2000)
    priority: str = Field(default="normal", max_length=20)


class AiDecideRequest(BaseModel):
    """One-shot model decision via Voxora's router (ADR-3D-LLM)."""

    model_config = {"extra": "forbid"}
    system_prompt: str = Field(min_length=1, max_length=20000)
    user_prompt: str = Field(min_length=1, max_length=30000)
    model_profile: Optional[str] = Field(default=None, max_length=120)


class CampaignCreate(BaseModel):
    model_config = {"extra": "forbid"}  # typos must fail loudly, not be ignored
    name: str = Field(min_length=1, max_length=160)
    agent_id: str
    crm_connection_id: Optional[str] = None
    dry_run: bool = True
    max_concurrency: int = Field(default=2, ge=1, le=50)
    max_attempts: int = Field(default=2, ge=1, le=5)
    retry_backoff_seconds: int = Field(default=30, ge=1, le=3600)
    calling_window_start: Optional[str] = None
    calling_window_end: Optional[str] = None
    timezone: str = Field(default="UTC", max_length=80)
    schedule_at: Optional[datetime] = None

    @field_validator("calling_window_start", "calling_window_end")
    @classmethod
    def time_valid(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _TIME_RE.match(value):
            raise ValueError("calling window values must use HH:MM")
        return value

    @model_validator(mode="after")
    def window_pair(self) -> "CampaignCreate":
        if (self.calling_window_start is None) != (self.calling_window_end is None):
            raise ValueError("calling window start and end must be provided together")
        return self


class RecipientAdd(BaseModel):
    contact_ids: List[str] = Field(min_length=1, max_length=5000)


class ContactImport(BaseModel):
    contacts: List[ContactCreate] = Field(min_length=1, max_length=5000)


_E164_RE = re.compile(r"^\+\d{8,15}$")


class AudienceConsent(BaseModel):
    model_config = {"extra": "forbid"}
    voice_contact_allowed: bool
    recording_allowed: bool = False


class AudienceContact(BaseModel):
    model_config = {"extra": "forbid"}
    external_contact_id: str = Field(min_length=1, max_length=200)
    name: Optional[str] = Field(default=None, max_length=200)
    phone: str = Field(min_length=8, max_length=20)
    email: Optional[str] = Field(default=None, max_length=320)
    timezone: Optional[str] = Field(default=None, max_length=80)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    consent: Optional[AudienceConsent] = None

    @field_validator("phone")
    @classmethod
    def phone_is_e164(cls, value: str) -> str:
        if not _E164_RE.match(value.replace(" ", "")):
            raise ValueError("phone must be E.164, e.g. +15551234567")
        return value.replace(" ", "")


class AudienceImport(BaseModel):
    """Squid CRM -> Voxora audience-import envelope.

    Provenance is mandatory: every campaign created from an audience records
    where it came from and which audience version fed it. Consent is
    honored, never silently widened: missing consent yields `consent: unknown`
    (engine money reflects `consent_required`); explicit refusal marks the
    contact suppressed.
    """

    model_config = {"extra": "forbid"}
    source: str = Field(default="squid-crm", max_length=64)
    audience_id: str = Field(min_length=1, max_length=200)
    audience_version: str = Field(default="v1", max_length=40)
    campaign_name: str = Field(min_length=1, max_length=160)
    agent_id: str
    contacts: List[AudienceContact] = Field(min_length=1, max_length=5000)
    dry_run: bool = True
    max_concurrency: int = Field(default=2, ge=1, le=50)
    max_attempts: int = Field(default=2, ge=1, le=5)


class CrmConnectionCreate(BaseModel):
    provider: str = Field(default="squid")
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(min_length=8, max_length=4096)
    webhook_secret: Optional[str] = Field(default=None, max_length=4096)

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, value: str) -> str:
        if value != "squid":
            raise ValueError("Only the verified Squid CRM adapter is available")
        return value

    @field_validator("base_url")
    @classmethod
    def url_valid(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not (value.startswith("https://") or value.startswith("http://localhost") or value.startswith("http://127.0.0.1")):
            raise ValueError("base_url must use https, except localhost for development")
        return value


class MappingItem(BaseModel):
    canonical_field: str = Field(min_length=1, max_length=120)
    external_field: str = Field(min_length=1, max_length=120)
    required: bool = False


class MappingsUpdate(BaseModel):
    mappings: List[MappingItem] = Field(default_factory=list, max_length=100)


class CrmSyncRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=20, ge=1, le=100)


class CampaignSyncRequest(BaseModel):
    connection_id: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=20, ge=1, le=100)
