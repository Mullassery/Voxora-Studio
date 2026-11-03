"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Optional[str], default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float(value: Optional[str], default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class TenantPrincipal:
    tenant_id: str
    role: str
    name: str

    def can_write(self) -> bool:
        return self.role in {"admin", "operator"}


DEFAULT_TOKENS = {
    "dev-operator-token": TenantPrincipal(
        tenant_id="tenant-demo", role="admin", name="Local operator"
    )
}


@dataclass
class Settings:
    env: str = "development"
    database_path: str = "./data/voxora.db"
    storage: str = "sqlite"
    database_url: Optional[str] = None
    secret_key: str = "development-only-secret-key-change-me"
    outbound_webhook_secret: str = "development-only-outbound-webhook-secret-0123456"
    api_tokens: Dict[str, TenantPrincipal] = field(default_factory=lambda: dict(DEFAULT_TOKENS))
    cors_origins: List[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    seed_demo: bool = True
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.5
    simulated_call_delay_seconds: float = 0.35
    worker_threads: int = 4
    global_concurrency: int = 5
    telephony_provider: str = "mock"
    llm_provider: str = "mock"
    allow_live_calls: bool = False
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_from_number: Optional[str] = None
    twilio_twiml_url: Optional[str] = None
    twilio_status_callback_url: Optional[str] = None
    max_campaign_recipients: int = 10000
    max_import_pages: int = 20
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        raw_tokens = os.getenv("VOXORA_API_TOKENS")
        tokens = dict(DEFAULT_TOKENS)
        if raw_tokens:
            parsed = json.loads(raw_tokens)
            tokens = {
                token: TenantPrincipal(
                    tenant_id=str(value["tenant_id"]),
                    role=str(value.get("role", "operator")),
                    name=str(value.get("name", "Operator")),
                )
                for token, value in parsed.items()
            }
        origins = [
            value.strip()
            for value in os.getenv(
                "VOXORA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if value.strip()
        ]
        settings = cls(
            env=os.getenv("VOXORA_ENV", "development"),
            database_path=os.getenv("VOXORA_DATABASE_PATH", "./data/voxora.db"),
            storage=os.getenv("VOXORA_STORAGE", "sqlite").strip().lower(),
            database_url=os.getenv("VOXORA_DATABASE_URL") or None,
            secret_key=os.getenv(
                "VOXORA_SECRET_KEY", "development-only-secret-key-change-me"
            ),
            outbound_webhook_secret=os.getenv(
                "VOXORA_OUTBOUND_WEBHOOK_SECRET",
                "development-only-outbound-webhook-secret-0123456",
            ),
            api_tokens=tokens,
            cors_origins=origins,
            seed_demo=_bool(os.getenv("VOXORA_SEED_DEMO"), True),
            worker_enabled=_bool(os.getenv("VOXORA_WORKER_ENABLED"), True),
            worker_poll_seconds=_float(os.getenv("VOXORA_WORKER_POLL_SECONDS"), 0.5),
            simulated_call_delay_seconds=_float(
                os.getenv("VOXORA_SIMULATED_CALL_DELAY_SECONDS"), 0.35
            ),
            worker_threads=_int(os.getenv("VOXORA_WORKER_THREADS"), 4),
            global_concurrency=_int(os.getenv("VOXORA_GLOBAL_CONCURRENCY"), 5),
            telephony_provider=os.getenv("VOXORA_TELEPHONY_PROVIDER", "mock"),
            llm_provider=os.getenv("VOXORA_LLM_PROVIDER", "mock"),
            allow_live_calls=_bool(os.getenv("VOXORA_ALLOW_LIVE_CALLS"), False),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID") or None,
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN") or None,
            twilio_from_number=os.getenv("TWILIO_FROM_NUMBER") or None,
            twilio_twiml_url=os.getenv("TWILIO_TWIML_URL") or None,
            twilio_status_callback_url=os.getenv("TWILIO_STATUS_CALLBACK_URL") or None,
            max_campaign_recipients=_int(os.getenv("VOXORA_MAX_CAMPAIGN_RECIPIENTS"), 10000),
            max_import_pages=_int(os.getenv("VOXORA_MAX_IMPORT_PAGES"), 20),
            log_level=os.getenv("VOXORA_LOG_LEVEL", "INFO"),
        )
        settings.validate()
        return settings

    def validate_storage(self) -> None:
        if self.storage == "sqlite":
            return
        if self.storage != "postgres":
            raise ValueError("VOXORA_STORAGE must be 'sqlite' or 'postgres'")
        if not self.database_url:
            raise ValueError("VOXORA_STORAGE=postgres requires VOXORA_DATABASE_URL")

    def validate(self) -> None:
        if len(self.secret_key) < 32:
            raise ValueError("VOXORA_SECRET_KEY must be at least 32 characters")
        if self.env == "production" and "development-only" in self.secret_key:
            raise ValueError("Refusing to start production with the development secret key")
        if self.env == "production" and "development-only" in self.outbound_webhook_secret:
            raise ValueError(
                "Refusing to start production with the development outbound webhook secret"
            )
        if len(self.outbound_webhook_secret) < 32:
            raise ValueError("VOXORA_OUTBOUND_WEBHOOK_SECRET must be at least 32 characters")
        if self.telephony_provider == "twilio":
            missing = [
                name
                for name, value in {
                    "TWILIO_ACCOUNT_SID": self.twilio_account_sid,
                    "TWILIO_AUTH_TOKEN": self.twilio_auth_token,
                    "TWILIO_FROM_NUMBER": self.twilio_from_number,
                    "TWILIO_TWIML_URL": self.twilio_twiml_url,
                    "TWILIO_STATUS_CALLBACK_URL": self.twilio_status_callback_url,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(
                    "VOXORA_TELEPHONY_PROVIDER=twilio requires: " + ", ".join(missing)
                )
        simulation_providers = {"mock", "voice-runtime"}
        if self.allow_live_calls and self.telephony_provider in simulation_providers:
            raise ValueError(
                "VOXORA_ALLOW_LIVE_CALLS requires a real telephony provider; "
                f"'{self.telephony_provider}' is simulation-only"
            )
        for principal in self.api_tokens.values():
            if principal.role not in {"admin", "operator", "viewer"}:
                raise ValueError("API token roles must be admin, operator, or viewer")

    def public_summary(self) -> Dict[str, object]:
        return {
            "env": self.env,
            "telephonyProvider": self.telephony_provider,
            "liveCallsEnabled": self.allow_live_calls,
            "workerEnabled": self.worker_enabled,
            "seedDemo": self.seed_demo,
        }
