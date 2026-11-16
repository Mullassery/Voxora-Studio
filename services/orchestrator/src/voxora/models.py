"""Validated request models for the versioned API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

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


class VoiceBoostControlOverrides(BaseModel):
    """Independent Voice Boost DSP control overrides (roadmap M9).

    Exactly the 11 control keys the Rust engine accepts
    (`crates/voice-runtime/src/dsp.rs::config_from_settings_json`); unknown
    keys are refused and numeric ranges are REJECTED, never clamped — the
    API boundary does not silently reinterpret operator intent.
    """

    model_config = {"extra": "forbid"}
    noise_suppression: Optional[bool] = None
    ns_strength: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    agc: Optional[bool] = None
    agc_target_dbfs: Optional[float] = Field(default=None, ge=-60.0, le=-6.0)
    agc_max_boost_db: Optional[float] = Field(default=None, gt=0.0, le=48.0)
    agc_max_cut_db: Optional[float] = Field(default=None, gt=0.0, le=48.0)
    normalize: Optional[bool] = None
    eq: Optional[bool] = None
    de_ess: Optional[bool] = None
    de_ess_strength: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    clipping_watch: Optional[bool] = None

    def resolved(self) -> Dict[str, Any]:
        """The set overrides only (None fields dropped)."""
        return self.model_dump(exclude_none=True)


class VoiceBoostSettingsUpdate(BaseModel):
    """Voice Boost settings envelope: master switch + profile + controls.

    `enabled: false` is the master off-switch — it maps to the Rust engine's
    bypass (original audio preserved byte-identically) while overrides stay
    resolved so re-enabling is lossless.
    """

    model_config = {"extra": "forbid"}
    enabled: bool = True
    profile: Literal["natural", "clear", "studio"] = "clear"
    overrides: VoiceBoostControlOverrides = Field(default_factory=VoiceBoostControlOverrides)


class AudioAssetGenerate(BaseModel):
    """One text-to-speech generation request (operator asset surface).

    Script cap 4096 mirrors the documented provider limit; long-form chunking
    ships with Phase D. `language` is recorded metadata only — OpenAI exposes
    no language-selection parameter (voices are English-optimized per docs).
    """

    model_config = {"extra": "forbid"}

    script: str = Field(min_length=1, max_length=4096)
    voice: str = Field(min_length=1, max_length=80)
    model: Literal["tts-1", "tts-1-hd", "gpt-4o-mini-tts"] = "tts-1"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "mp3"
    instructions: Optional[str] = Field(default=None, max_length=4096)
    title: Optional[str] = Field(default=None, max_length=120)
    language: Optional[str] = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _instructions_only_on_capable_models(self) -> "AudioAssetGenerate":
        if self.instructions is not None and self.model != "gpt-4o-mini-tts":
            raise ValueError("instructions is only supported with gpt-4o-mini-tts")
        return self


class VariantOp(BaseModel):
    """One non-destructive variant operation (roadmap M10 Phase C).

    Ranges per the design doc; unknown keys refused (extra=forbid). The
    `voice_boost` op is op-ONLY resolution — its params ride the builtin
    profile config; tenant stored settings are never consulted.
    """

    model_config = {"extra": "forbid"}

    type: Literal["normalize", "trim_silence", "resample", "channels", "voice_boost"]
    target_dbfs: Optional[float] = Field(default=None, ge=-24.0, le=-6.0)
    threshold_dbfs: Optional[float] = Field(default=None, ge=-60.0, le=-20.0)
    min_silence_ms: Optional[int] = Field(default=None, ge=0, le=2000)
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=48000)
    to: Optional[Literal["mono", "stereo"]] = None
    profile: Optional[Literal["natural", "clear", "studio"]] = None
    overrides: Optional[VoiceBoostControlOverrides] = None


class VariantOpsRequest(BaseModel):
    """Envelope for `POST /audio-assets/{id}/variants`: 1-6 ops in order."""

    model_config = {"extra": "forbid"}

    ops: List[VariantOp] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _cross_op_rules(self) -> "VariantOpsRequest":
        has_boost = any(op.type == "voice_boost" for op in self.ops)
        makes_stereo = any(op.type == "channels" and op.to == "stereo" for op in self.ops)
        if has_boost and makes_stereo:
            raise ValueError(
                "voice_boost is mono-only; a channels->stereo op cannot combine with it"
            )
        for op in self.ops:
            if op.type == "resample" and op.sample_rate is None:
                raise ValueError("resample requires sample_rate (8000-48000)")
        return self


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
    voice_boost: Optional[VoiceBoostSettingsUpdate] = None


class SttDeltas(BaseModel):
    """One caller utterance as STT transcript deltas (media-backend seam).

    These are caller-side speech hypotheses only — the client never sends
    text for the assistant to speak (server-authoritative TTS, roadmap
    M7/P9). Spoken content comes exclusively from the agent or the
    instruction store.
    """

    model_config = {"extra": "forbid"}
    deltas: List[str] = Field(min_length=1, max_length=20)

    @field_validator("deltas")
    @classmethod
    def _deltas_are_speech_hypotheses(cls, value: List[str]) -> List[str]:
        for delta in value:
            if not delta.strip():
                raise ValueError("each delta must be a non-empty string")
            if len(delta) > 2000:
                raise ValueError("each delta must be at most 2000 characters")
        return value


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

class TurnEngineCreate(BaseModel):
    """Body for creating a turn-detection diagnostic engine."""

    model_config = {"extra": "forbid"}
    call_id: str = Field(min_length=1, max_length=120)
    profile: Optional[str] = Field(default=None, max_length=40)
    semantic_enabled: Optional[bool] = None
    word_gate_min_words: Optional[int] = Field(default=None, ge=1, le=10)


class TurnAudioFrame(BaseModel):
    """One frame of audio evidence for the turn-detection engine."""

    model_config = {"extra": "forbid"}
    rms: float = Field(ge=0.0, le=1.0)
    agent_playing: bool = False
    tts_elapsed_ms: int = Field(default=0, ge=0, le=3_600_000)
    since_agent_audio_ms: int = Field(default=0, ge=0, le=3_600_000)
    timestamp_ms: Optional[int] = Field(default=None, ge=0)


class TurnTranscript(BaseModel):
    """A cumulative transcript snapshot for the turn-detection engine."""

    model_config = {"extra": "forbid"}
    text: str = Field(max_length=4000)
    is_final: bool = False
    timestamp_ms: Optional[int] = Field(default=None, ge=0)


class TurnSemanticReport(BaseModel):
    """An external semantic verdict for the turn-detection engine."""

    model_config = {"extra": "forbid"}
    verdict: str = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp_ms: Optional[int] = Field(default=None, ge=0)


class TurnLidReport(BaseModel):
    """A language-ID routing result for the turn-detection engine."""

    model_config = {"extra": "forbid"}
    language: str = Field(min_length=1, max_length=40)
    switch: bool = False
    timestamp_ms: Optional[int] = Field(default=None, ge=0)


class TurnAgentEvent(BaseModel):
    """An agent-pipeline lifecycle event for the turn-detection engine."""

    model_config = {"extra": "forbid"}
    kind: str = Field(min_length=1, max_length=40)
    generation: int = Field(default=0, ge=0)
    timestamp_ms: Optional[int] = Field(default=None, ge=0)


class TurnProviderFailure(BaseModel):
    """A provider-down signal for the turn-detection engine."""

    model_config = {"extra": "forbid"}
    hint: str = Field(max_length=200)
    timestamp_ms: Optional[int] = Field(default=None, ge=0)
