"""Durable SQLite persistence for campaigns, calls, contacts, and CRM state.

SQLite is intentional for the first single-node slice: it gives atomic claims,
WAL reads while the worker writes, and restart recovery without extra
infrastructure. All access goes through this module so a PostgreSQL adapter can
replace the storage layer without changing HTTP or campaign code.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .security import new_id
from .state import (
    CALL_TRANSITIONS,
    CAMPAIGN_TRANSITIONS,
    RECIPIENT_TRANSITIONS,
    assert_transition,
)

JSON_COLUMNS = {
    "capabilities",
    "custom_fields",
    "metadata",
    "data",
    "errors",
    "payload",
    "latency",
    "session_metadata",
    "transcript",
}


def database_from_settings(settings: Any) -> Database:
    """Build the storage backend requested by settings (sqlite | postgres)."""
    storage = getattr(settings, "storage", "sqlite")
    if storage == "postgres":
        from .pg import PostgresDatabase

        url = getattr(settings, "database_url", None) or ""
        return PostgresDatabase(url)
    return Database(settings.database_path)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Optional[datetime] = None) -> str:
    return (value or utcnow()).astimezone(timezone.utc).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    voice TEXT NOT NULL,
    stt_provider TEXT NOT NULL,
    tts_provider TEXT NOT NULL,
    max_turn_seconds INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agents_tenant ON agents(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    locale TEXT NOT NULL DEFAULT 'en-US',
    consent_state TEXT NOT NULL DEFAULT 'unknown',
    consent_source TEXT NOT NULL DEFAULT '',
    consent_updated_at TEXT,
    suppressed INTEGER NOT NULL DEFAULT 0,
    suppression_reason TEXT,
    custom_fields TEXT NOT NULL DEFAULT '{}',
    source_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant ON contacts(tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(tenant_id, phone);

CREATE TABLE IF NOT EXISTS suppression_list (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, phone)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    crm_connection_id TEXT,
    status TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 1,
    schedule_at TEXT,
    calling_window_start TEXT,
    calling_window_end TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    max_concurrency INTEGER NOT NULL DEFAULT 2,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    retry_backoff_seconds INTEGER NOT NULL DEFAULT 30,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    paused_at TEXT,
    last_error TEXT,
    audience_source TEXT,
    audience_id TEXT,
    audience_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_campaigns_tenant ON campaigns(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    eligibility_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(campaign_id, contact_id)
);
CREATE INDEX IF NOT EXISTS idx_recipients_claim ON campaign_recipients(campaign_id, status, next_attempt_at);

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_call_id TEXT,
    status TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'outbound',
    simulated INTEGER NOT NULL DEFAULT 1,
    attempt INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    started_at TEXT,
    answered_at TEXT,
    ended_at TEXT,
    duration_ms INTEGER,
    disposition TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_calls_tenant ON calls(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_calls_campaign ON calls(campaign_id, status);

CREATE TABLE IF NOT EXISTS call_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    at TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_call_events_call ON call_events(call_id, at);

CREATE TABLE IF NOT EXISTS call_latency (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    simulated INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_latency_tenant ON call_latency(tenant_id, stage, simulated);

CREATE TABLE IF NOT EXISTS voice_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    external_application TEXT NOT NULL,
    external_session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model_profile TEXT,
    contact_id TEXT NOT NULL,
    call_id TEXT,
    status TEXT NOT NULL,
    webhook_url TEXT,
    session_metadata TEXT NOT NULL DEFAULT '{}',
    transcript TEXT NOT NULL DEFAULT '[]',
    provider TEXT NOT NULL DEFAULT 'mock',
    simulated INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, external_application, external_session_id)
);
CREATE INDEX IF NOT EXISTS idx_voice_sessions_tenant ON voice_sessions(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS voice_session_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version TEXT NOT NULL DEFAULT '1.0',
    occurred_at TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_voice_session_events_session ON voice_session_events(session_id, occurred_at);

CREATE TABLE IF NOT EXISTS voice_session_instructions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    instruction_id TEXT NOT NULL,
    type TEXT NOT NULL,
    text TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'received',
    created_at TEXT NOT NULL,
    UNIQUE(session_id, instruction_id)
);

CREATE TABLE IF NOT EXISTS voice_session_deliveries (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_connections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    webhook_secret_encrypted TEXT,
    status TEXT NOT NULL DEFAULT 'untested',
    capabilities TEXT NOT NULL DEFAULT '{}',
    last_tested_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_connections_tenant ON crm_connections(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS crm_field_mappings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    canonical_field TEXT NOT NULL,
    external_field TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(connection_id, canonical_field)
);

CREATE TABLE IF NOT EXISTS crm_sync_jobs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    status TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'inbound',
    cursor TEXT,
    imported INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_tenant ON crm_sync_jobs(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    error TEXT,
    UNIQUE(tenant_id, connection_id, event_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id, created_at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for key in list(result):
            if key in JSON_COLUMNS and isinstance(result[key], str):
                with suppress(json.JSONDecodeError):
                    result[key] = json.loads(result[key])
        return result

    def initialize(self) -> None:
        with self._write() as connection:
            connection.executescript(SCHEMA)
            self._migrate_campaigns(connection)

    @staticmethod
    def _migrate_campaigns(connection: sqlite3.Connection) -> None:
        """Idempotent column additions for databases created before audience imports."""
        existing = {
            row[1]
            for row in connection.execute("PRAGMA table_info(campaigns)").fetchall()
        }
        for column, definition in (
            ("audience_source", "TEXT"),
            ("audience_id", "TEXT"),
            ("audience_version", "TEXT"),
        ):
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE campaigns ADD COLUMN {column} {definition}"  # noqa: S608 - fixed internal names
                )

    def ping(self) -> bool:
        with self._read() as connection:
            connection.execute("SELECT 1").fetchone()
        return True

    # ------------------------------------------------------------------ audit
    def audit(
        self,
        tenant_id: str,
        actor: str,
        action: str,
        target_type: str,
        target_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO audit_logs (id, tenant_id, actor, action, target_type, target_id, metadata, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("audit"),
                    tenant_id,
                    actor,
                    action,
                    target_type,
                    target_id,
                    json.dumps(metadata or {}),
                    iso(),
                ),
            )

    def list_audit(self, tenant_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_logs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (tenant_id, limit, offset),
            ).fetchall()
        return [self._decode(row) for row in rows]

    # ----------------------------------------------------------------- tenant
    def ensure_tenant(self, tenant_id: str, name: str) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
                (tenant_id, name, iso()),
            )

    # ----------------------------------------------------------------- agents
    def create_agent(self, tenant_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = new_id("agent")
        now = iso()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO agents (id, tenant_id, name, system_prompt, model, voice, stt_provider,"
                " tts_provider, max_turn_seconds, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    tenant_id,
                    values["name"],
                    values.get("system_prompt", ""),
                    values.get("model", "mock-llm"),
                    values.get("voice", "mock-voice"),
                    values.get("stt_provider", "mock-stt"),
                    values.get("tts_provider", "mock-tts"),
                    int(values.get("max_turn_seconds", 30)),
                    now,
                    now,
                ),
            )
        return self.get_agent(tenant_id, agent_id)  # type: ignore[return-value]

    def get_agent(self, tenant_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM agents WHERE tenant_id=? AND id=?", (tenant_id, agent_id)
            ).fetchone()
        return self._decode(row) if row else None

    def list_agents(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM agents WHERE tenant_id=? ORDER BY created_at ASC", (tenant_id,)
            ).fetchall()
        return [self._decode(row) for row in rows]

    # --------------------------------------------------------------- contacts
    def upsert_contact(
        self,
        tenant_id: str,
        provider: str,
        external_id: str,
        values: Dict[str, Any],
        preserve_consent: bool = False,
    ) -> Dict[str, Any]:
        now = iso()
        contact_id = new_id("contact")
        with self._write() as connection:
            connection.execute(
                "INSERT INTO contacts (id, tenant_id, provider, external_id, first_name, last_name,"
                " phone, email, timezone, locale, consent_state, consent_source, consent_updated_at,"
                " suppressed, suppression_reason, custom_fields, source_updated_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(tenant_id, provider, external_id) DO UPDATE SET"
                " first_name=excluded.first_name, last_name=excluded.last_name, phone=excluded.phone,"
                " email=excluded.email, timezone=excluded.timezone, locale=excluded.locale,"
                " consent_state=CASE WHEN ? THEN contacts.consent_state ELSE excluded.consent_state END,"
                " consent_source=CASE WHEN ? THEN contacts.consent_source ELSE excluded.consent_source END,"
                " consent_updated_at=CASE WHEN ? THEN contacts.consent_updated_at ELSE excluded.consent_updated_at END,"
                " suppressed=excluded.suppressed, suppression_reason=excluded.suppression_reason,"
                " custom_fields=excluded.custom_fields, source_updated_at=excluded.source_updated_at,"
                " updated_at=excluded.updated_at",
                (
                    contact_id,
                    tenant_id,
                    provider,
                    external_id,
                    values.get("first_name", ""),
                    values.get("last_name", ""),
                    values["phone"],
                    values.get("email", ""),
                    values.get("timezone", "UTC"),
                    values.get("locale", "en-US"),
                    values.get("consent_state", "unknown"),
                    values.get("consent_source", ""),
                    values.get("consent_updated_at") or now,
                    int(bool(values.get("suppressed", False))),
                    values.get("suppression_reason"),
                    json.dumps(values.get("custom_fields", {})),
                    values.get("source_updated_at"),
                    now,
                    now,
                    bool(preserve_consent),
                    bool(preserve_consent),
                    bool(preserve_consent),
                ),
            )
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND provider=? AND external_id=?",
                (tenant_id, provider, external_id),
            ).fetchone()
        return self._decode(row)  # type: ignore[arg-type]

    def create_local_contact(self, tenant_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        values = dict(values)
        values.setdefault("consent_source", "operator")
        return self.upsert_contact(
            tenant_id, "local", uuid.uuid4().hex, values, preserve_consent=False
        )

    def get_contact(self, tenant_id: str, contact_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND id=?", (tenant_id, contact_id)
            ).fetchone()
        return self._decode(row) if row else None

    def get_contact_by_external(
        self, tenant_id: str, provider: str, external_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND provider=? AND external_id=?",
                (tenant_id, provider, external_id),
            ).fetchone()
        return self._decode(row) if row else None

    def list_contacts(
        self, tenant_id: str, limit: int = 50, offset: int = 0, search: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = "tenant_id=?"
        params: List[Any] = [tenant_id]
        if search:
            where += " AND (first_name LIKE ? OR last_name LIKE ? OR phone LIKE ? OR email LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern, pattern])
        with self._read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS n FROM contacts WHERE {where}", params
            ).fetchone()["n"]
            rows = connection.execute(
                f"SELECT * FROM contacts WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return [self._decode(row) for row in rows], int(total)

    def update_contact_consent(
        self, tenant_id: str, contact_id: str, state: str, source: str
    ) -> Optional[Dict[str, Any]]:
        with self._write() as connection:
            connection.execute(
                "UPDATE contacts SET consent_state=?, consent_source=?, consent_updated_at=?,"
                " suppressed=CASE WHEN ?='revoked' THEN 1 ELSE suppressed END,"
                " suppression_reason=CASE WHEN ?='revoked' THEN ? ELSE suppression_reason END,"
                " updated_at=? WHERE tenant_id=? AND id=?",
                (state, source, iso(), state, state, source, iso(), tenant_id, contact_id),
            )
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND id=?", (tenant_id, contact_id)
            ).fetchone()
        return self._decode(row) if row else None

    def suppress_contact(
        self, tenant_id: str, contact_id: str, reason: str
    ) -> Optional[Dict[str, Any]]:
        with self._write() as connection:
            connection.execute(
                "UPDATE contacts SET suppressed=1, suppression_reason=?, updated_at=?"
                " WHERE tenant_id=? AND id=?",
                (reason, iso(), tenant_id, contact_id),
            )
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND id=?", (tenant_id, contact_id)
            ).fetchone()
        return self._decode(row) if row else None

    def revoke_contact_by_external(
        self, tenant_id: str, provider: str, external_id: str, reason: str
    ) -> Optional[Dict[str, Any]]:
        with self._write() as connection:
            connection.execute(
                "UPDATE contacts SET consent_state='revoked', suppressed=1, suppression_reason=?,"
                " consent_source=?, consent_updated_at=?, updated_at=?"
                " WHERE tenant_id=? AND provider=? AND external_id=?",
                (reason, provider, iso(), iso(), tenant_id, provider, external_id),
            )
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND provider=? AND external_id=?",
                (tenant_id, provider, external_id),
            ).fetchone()
        return self._decode(row) if row else None

    # ------------------------------------------------------------- suppression
    def add_suppression(self, tenant_id: str, phone: str, reason: str) -> Dict[str, Any]:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO suppression_list (id, tenant_id, phone, reason, created_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, phone) DO UPDATE SET reason=excluded.reason",
                (new_id("sup"), tenant_id, phone, reason, iso()),
            )
            row = connection.execute(
                "SELECT * FROM suppression_list WHERE tenant_id=? AND phone=?", (tenant_id, phone)
            ).fetchone()
        return self._decode(row)  # type: ignore[arg-type]

    def is_suppressed(self, tenant_id: str, phone: str) -> bool:
        with self._read() as connection:
            row = connection.execute(
                "SELECT 1 FROM suppression_list WHERE tenant_id=? AND phone=? LIMIT 1",
                (tenant_id, phone),
            ).fetchone()
        return row is not None

    def list_suppression(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM suppression_list WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def delete_suppression(self, tenant_id: str, phone: str) -> bool:
        with self._write() as connection:
            cursor = connection.execute(
                "DELETE FROM suppression_list WHERE tenant_id=? AND phone=?", (tenant_id, phone)
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------- voice sessions
    def create_voice_session(self, tenant_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Idempotent session creation on (tenant, external_application, external_session_id)."""
        now = iso()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO voice_sessions (id, tenant_id, external_application,"
                " external_session_id, agent_id, model_profile, contact_id, call_id, status,"
                " webhook_url, session_metadata, transcript, provider, simulated,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(tenant_id, external_application, external_session_id) DO NOTHING",
                (
                    values.get("id") or new_id("vxs"),
                    tenant_id,
                    values["external_application"],
                    values["external_session_id"],
                    values["agent_id"],
                    values.get("model_profile"),
                    values["contact_id"],
                    values.get("call_id"),
                    values.get("status", "created"),
                    values.get("webhook_url"),
                    json.dumps(values.get("session_metadata", {})),
                    json.dumps(values.get("transcript", [])),
                    values.get("provider", "mock"),
                    int(bool(values.get("simulated", True))),
                    now,
                    now,
                ),
            )
        # RETURNING-equivalent read keeps sqlite/postgres symmetry.
        session_id = values.get("id") or ""
        session = self.get_voice_session(tenant_id, session_id) or self.get_voice_session_by_external(
            tenant_id, values["external_application"], values["external_session_id"]
        )
        if session is None:  # contract: the row exists directly after the upsert
            raise RuntimeError("voice session row vanished after upsert")
        return session

    def get_voice_session(self, tenant_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM voice_sessions WHERE tenant_id=? AND id=?",
                (tenant_id, session_id),
            ).fetchone()
        return self._decode(row) if row else None

    def get_voice_session_by_external(
        self, tenant_id: str, external_application: str, external_session_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM voice_sessions WHERE tenant_id=? AND external_application=?"
                " AND external_session_id=?",
                (tenant_id, external_application, external_session_id),
            ).fetchone()
        return self._decode(row) if row else None

    def get_voice_session_by_call(self, tenant_id: str, call_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM voice_sessions WHERE tenant_id=? AND call_id=?",
                (tenant_id, call_id),
            ).fetchone()
        return self._decode(row) if row else None

    def update_voice_session(
        self, tenant_id: str, session_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "status",
            "call_id",
            "transcript",
            "provider",
            "simulated",
            "webhook_url",
        }
        updates = [(key, value) for key, value in fields.items() if key in allowed]
        if not updates:
            return self.get_voice_session(tenant_id, session_id)
        setters = ", ".join(f"{key}=?" for key, _ in updates)
        params: List[Any] = []
        for key, value in updates:
            params.append(json.dumps(value) if key in ("transcript",) else value)
        params += [iso(), tenant_id, session_id]
        with self._write() as connection:
            connection.execute(
                f"UPDATE voice_sessions SET {setters}, updated_at=? WHERE tenant_id=? AND id=?",
                params,
            )
        return self.get_voice_session(tenant_id, session_id)

    def record_voice_session_event(
        self,
        tenant_id: str,
        session_id: str,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        occurred_at: Optional[str] = None,
        event_version: str = "1.0",
    ) -> Dict[str, Any]:
        event_id = new_id("vxe")
        occurred = occurred_at or iso()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO voice_session_events (id, tenant_id, session_id, event_type,"
                " event_version, occurred_at, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, tenant_id, session_id, event_type, event_version, occurred, json.dumps(data or {})),
            )
        return {
            "event_id": event_id,
            "event_type": event_type,
            "event_version": event_version,
            "occurred_at": occurred,
            "session_id": session_id,
            "data": data or {},
        }

    def list_voice_session_events(self, tenant_id: str, session_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM voice_session_events WHERE tenant_id=? AND session_id=?"
                " ORDER BY occurred_at ASC",
                (tenant_id, session_id),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def create_session_instruction(
        self, tenant_id: str, session_id: str, values: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Idempotent instruction intake; returns the row (created or existing)."""
        with self._write() as connection:
            cursor = connection.execute(
                "INSERT INTO voice_session_instructions (id, tenant_id, session_id,"
                " instruction_id, type, text, priority, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?)"
                " ON CONFLICT(session_id, instruction_id) DO NOTHING",
                (
                    new_id("vxi"),
                    tenant_id,
                    session_id,
                    values["instruction_id"],
                    values["type"],
                    values["text"],
                    values.get("priority", "normal"),
                    iso(),
                ),
            )
            if cursor.rowcount == 0:
                with self._read() as connection:
                    row = connection.execute(
                        "SELECT * FROM voice_session_instructions WHERE session_id=? AND instruction_id=?",
                        (session_id, values["instruction_id"]),
                    ).fetchone()
                return self._decode(row) if row else None
            row = connection.execute(
                "SELECT * FROM voice_session_instructions WHERE session_id=? AND instruction_id=?",
                (session_id, values["instruction_id"]),
            ).fetchone()
        return self._decode(row) if row else None

    def list_session_instructions(self, tenant_id: str, session_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM voice_session_instructions WHERE tenant_id=? AND session_id=?"
                " ORDER BY created_at ASC",
                (tenant_id, session_id),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def record_event_delivery(
        self,
        tenant_id: str,
        event_id: str,
        session_id: str,
        attempt: int,
        ok: bool,
        detail: Optional[str],
    ) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO voice_session_deliveries (id, tenant_id, event_id, session_id,"
                " attempt, ok, detail, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id("vxd"), tenant_id, event_id, session_id, attempt, int(ok), detail, iso()),
            )

    def list_event_deliveries(self, tenant_id: str, event_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM voice_session_deliveries WHERE tenant_id=? AND event_id=?"
                " ORDER BY attempt ASC",
                (tenant_id, event_id),
            ).fetchall()
        return [self._decode(row) for row in rows]

    # --------------------------------------------------------------- campaigns
    def create_campaign(self, tenant_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        campaign_id = new_id("campaign")
        now = iso()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO campaigns (id, tenant_id, name, agent_id, crm_connection_id, status,"
                " dry_run, schedule_at, calling_window_start, calling_window_end, timezone,"
                " max_concurrency, max_attempts, retry_backoff_seconds, created_at, updated_at,"
                " audience_source, audience_id, audience_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    tenant_id,
                    values["name"],
                    values["agent_id"],
                    values.get("crm_connection_id"),
                    values.get("status", "draft"),
                    int(bool(values.get("dry_run", True))),
                    values.get("schedule_at"),
                    values.get("calling_window_start"),
                    values.get("calling_window_end"),
                    values.get("timezone", "UTC"),
                    int(values.get("max_concurrency", 2)),
                    int(values.get("max_attempts", 2)),
                    int(values.get("retry_backoff_seconds", 30)),
                    now,
                    now,
                    values.get("audience_source"),
                    values.get("audience_id"),
                    values.get("audience_version"),
                ),
            )
        return self.get_campaign(tenant_id, campaign_id)  # type: ignore[return-value]

    def get_campaign(self, tenant_id: str, campaign_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM campaigns WHERE tenant_id=? AND id=?", (tenant_id, campaign_id)
            ).fetchone()
        return self._decode(row) if row else None

    def get_campaign_unscoped(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list_campaigns(
        self, tenant_id: str, limit: int = 50, offset: int = 0, status: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = "tenant_id=?"
        params: List[Any] = [tenant_id]
        if status:
            where += " AND status=?"
            params.append(status)
        with self._read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS n FROM campaigns WHERE {where}", params
            ).fetchone()["n"]
            rows = connection.execute(
                f"SELECT * FROM campaigns WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            campaigns = [self._decode(row) for row in rows]
            for campaign in campaigns:
                campaign.update(self._campaign_counts(connection, campaign["id"]))
        return campaigns, int(total)

    def _campaign_counts(
        self, connection: sqlite3.Connection, campaign_id: str
    ) -> Dict[str, int]:
        counts = {
            "recipients": 0,
            "completed": 0,
            "failed": 0,
            "queued": 0,
            "active": 0,
        }
        row = connection.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,"
            " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued,"
            " SUM(CASE WHEN status IN ('dialing') THEN 1 ELSE 0 END) AS active"
            " FROM campaign_recipients WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        if row:
            counts = {
                "recipients": int(row["total"] or 0),
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
                "queued": int(row["queued"] or 0),
                "active": int(row["active"] or 0),
            }
        return counts

    def campaign_counts(self, tenant_id: str, campaign_id: str) -> Dict[str, int]:
        with self._read() as connection:
            campaign = connection.execute(
                "SELECT id FROM campaigns WHERE tenant_id=? AND id=?", (tenant_id, campaign_id)
            ).fetchone()
            if not campaign:
                return {}
            return self._campaign_counts(connection, campaign_id)

    def update_campaign_status(
        self, tenant_id: str, campaign_id: str, target: str, **extra: Any
    ) -> Dict[str, Any]:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM campaigns WHERE tenant_id=? AND id=?", (tenant_id, campaign_id)
            ).fetchone()
            if not row:
                raise KeyError("campaign not found")
            current = row["status"]
            assert_transition(CAMPAIGN_TRANSITIONS, current, target)
            fields = ["status=?", "updated_at=?"]
            params: List[Any] = [target, iso()]
            for key, value in extra.items():
                if key in {"started_at", "completed_at", "paused_at", "last_error", "schedule_at"}:
                    fields.append(f"{key}=?")
                    params.append(value)
            params.extend([tenant_id, campaign_id])
            connection.execute(
                f"UPDATE campaigns SET {', '.join(fields)} WHERE tenant_id=? AND id=?", params
            )
        return self.get_campaign(tenant_id, campaign_id)  # type: ignore[return-value]

    def add_recipients(self, tenant_id: str, campaign_id: str, contact_ids: Sequence[str]) -> int:
        now = iso()
        added = 0
        with self._write() as connection:
            for contact_id in contact_ids:
                contact = connection.execute(
                    "SELECT id FROM contacts WHERE tenant_id=? AND id=?", (tenant_id, contact_id)
                ).fetchone()
                if not contact:
                    continue
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO campaign_recipients"
                    " (id, tenant_id, campaign_id, contact_id, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                    (new_id("recipient"), tenant_id, campaign_id, contact_id, now, now),
                )
                added += cursor.rowcount
        return added

    def list_recipients(
        self,
        tenant_id: str,
        campaign_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with self._read() as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS n FROM campaign_recipients WHERE tenant_id=? AND campaign_id=?",
                (tenant_id, campaign_id),
            ).fetchone()["n"]
            rows = connection.execute(
                "SELECT r.*, c.first_name, c.last_name, c.phone, c.email, c.timezone,"
                " c.consent_state, c.suppressed"
                " FROM campaign_recipients r JOIN contacts c ON c.id=r.contact_id"
                " WHERE r.tenant_id=? AND r.campaign_id=?"
                " ORDER BY r.created_at ASC LIMIT ? OFFSET ?",
                (tenant_id, campaign_id, limit, offset),
            ).fetchall()
        return [self._decode(row) for row in rows], int(total)

    def promote_due_campaigns(self) -> List[Dict[str, str]]:
        """Flip 'scheduled' campaigns to 'running' once their schedule_at passes.

        Called by the worker loop before claiming so time-based scheduling is
        autonomous. Returns {"tenantId", "campaignId"} rows for auditing.
        """
        now = iso()
        promoted: List[Dict[str, str]] = []
        with self._write() as connection:
            due = connection.execute(
                "SELECT id, tenant_id, status FROM campaigns"
                " WHERE status='scheduled' AND schedule_at IS NOT NULL AND schedule_at <= ?",
                (now,),
            ).fetchall()
            for row in due:
                assert_transition(CAMPAIGN_TRANSITIONS, row["status"], "running")
                connection.execute(
                    "UPDATE campaigns SET status='running', started_at=?, updated_at=?"
                    " WHERE id=? AND status='scheduled'",
                    (dict(row).get("started_at") or now, now, row["id"]),
                )
                promoted.append({"tenantId": row["tenant_id"], "campaignId": row["id"]})
        return promoted

    def claim_recipients(self, limit: int) -> List[Dict[str, Any]]:
        """Atomically claim queued recipients and create idempotent call rows."""
        now = iso()
        claimed: List[Dict[str, Any]] = []
        with self._write() as connection:
            campaigns = connection.execute(
                "SELECT * FROM campaigns WHERE status='running'"
                " AND (schedule_at IS NULL OR schedule_at <= ?) ORDER BY created_at ASC",
                (now,),
            ).fetchall()
            for campaign in campaigns:
                if len(claimed) >= limit:
                    break
                active = connection.execute(
                    "SELECT COUNT(*) AS n FROM calls WHERE campaign_id=?"
                    " AND status IN ('queued','dialing','ringing','in_progress')",
                    (campaign["id"],),
                ).fetchone()["n"]
                slots = min(int(campaign["max_concurrency"]) - int(active), limit - len(claimed))
                if slots <= 0:
                    continue
                recipients = connection.execute(
                    "SELECT r.*, c.phone, c.first_name, c.last_name, c.email, c.timezone,"
                    " c.consent_state, c.suppressed"
                    " FROM campaign_recipients r JOIN contacts c ON c.id=r.contact_id"
                    " WHERE r.campaign_id=? AND r.status='queued'"
                    " AND (r.next_attempt_at IS NULL OR r.next_attempt_at <= ?)"
                    " ORDER BY r.created_at ASC LIMIT ?",
                    (campaign["id"], now, slots),
                ).fetchall()
                for recipient in recipients:
                    attempt = int(recipient["attempts"]) + 1
                    idempotency_key = f"{campaign['id']}:{recipient['contact_id']}:{attempt}"
                    call_id = new_id("call")
                    connection.execute(
                        "UPDATE campaign_recipients SET status='dialing', attempts=?, updated_at=?"
                        " WHERE id=? AND status='queued'",
                        (attempt, now, recipient["id"]),
                    )
                    connection.execute(
                        "INSERT INTO calls (id, tenant_id, campaign_id, recipient_id, contact_id,"
                        " provider, status, direction, simulated, attempt, idempotency_key,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, 'mock', 'queued', 'outbound', ?, ?, ?, ?, ?)",
                        (
                            call_id,
                            campaign["tenant_id"],
                            campaign["id"],
                            recipient["id"],
                            recipient["contact_id"],
                            int(bool(campaign["dry_run"])),
                            attempt,
                            idempotency_key,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO call_events (id, tenant_id, call_id, event_type, at, data)"
                        " VALUES (?, ?, ?, 'queued', ?, ?)",
                        (
                            new_id("evt"),
                            campaign["tenant_id"],
                            call_id,
                            now,
                            json.dumps({"idempotencyKey": idempotency_key}),
                        ),
                    )
                    item = self._decode(recipient)
                    item["attempts"] = attempt
                    item["call_id"] = call_id
                    item["idempotency_key"] = idempotency_key
                    item["campaign"] = self._decode(campaign)
                    claimed.append(item)
        return claimed

    def get_recipient(self, tenant_id: str, recipient_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM campaign_recipients WHERE tenant_id=? AND id=?",
                (tenant_id, recipient_id),
            ).fetchone()
        return self._decode(row) if row else None

    def mark_recipient(
        self,
        tenant_id: str,
        recipient_id: str,
        status: str,
        last_error: Optional[str] = None,
        next_attempt_at: Optional[str] = None,
        eligibility_code: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM campaign_recipients WHERE tenant_id=? AND id=?",
                (tenant_id, recipient_id),
            ).fetchone()
            if not row:
                return None
            if row["status"] != status:
                assert_transition(RECIPIENT_TRANSITIONS, row["status"], status)
            connection.execute(
                "UPDATE campaign_recipients SET status=?, last_error=?, next_attempt_at=?,"
                " eligibility_code=?, updated_at=? WHERE tenant_id=? AND id=?",
                (status, last_error, next_attempt_at, eligibility_code, iso(), tenant_id, recipient_id),
            )
        return self.get_recipient(tenant_id, recipient_id)

    def complete_campaign_if_done(self, tenant_id: str, campaign_id: str) -> Optional[str]:
        with self._read() as connection:
            campaign = connection.execute(
                "SELECT status FROM campaigns WHERE tenant_id=? AND id=?", (tenant_id, campaign_id)
            ).fetchone()
            if not campaign or campaign["status"] != "running":
                return campaign["status"] if campaign else None
            remaining = connection.execute(
                "SELECT COUNT(*) AS n FROM campaign_recipients WHERE campaign_id=?"
                " AND status IN ('pending','queued','dialing')",
                (campaign_id,),
            ).fetchone()["n"]
        if int(remaining) == 0:
            self.update_campaign_status(
                tenant_id, campaign_id, "completed", completed_at=iso()
            )
            return "completed"
        return "running"

    def cancel_campaign(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        now = iso()
        with self._write() as connection:
            campaign = connection.execute(
                "SELECT * FROM campaigns WHERE tenant_id=? AND id=?", (tenant_id, campaign_id)
            ).fetchone()
            if not campaign:
                raise KeyError("campaign not found")
            assert_transition(CAMPAIGN_TRANSITIONS, campaign["status"], "canceled")
            connection.execute(
                "UPDATE campaign_recipients SET status='canceled', updated_at=?"
                " WHERE campaign_id=? AND status IN ('pending','queued','dialing')",
                (now, campaign_id),
            )
            connection.execute(
                "UPDATE calls SET status='canceled', ended_at=?, updated_at=?"
                " WHERE campaign_id=? AND status IN ('queued','dialing','ringing','in_progress')",
                (now, now, campaign_id),
            )
            connection.execute(
                "UPDATE campaigns SET status='canceled', updated_at=? WHERE tenant_id=? AND id=?",
                (now, tenant_id, campaign_id),
            )
        return self.get_campaign(tenant_id, campaign_id)  # type: ignore[return-value]

    def recover_inflight(self) -> int:
        """Requeue or fail work left behind by an unclean restart."""
        now = iso()
        recovered = 0
        with self._write() as connection:
            calls = connection.execute(
                "SELECT * FROM calls WHERE status IN ('queued','dialing','ringing','in_progress')"
            ).fetchall()
            for call in calls:
                campaign = connection.execute(
                    "SELECT max_attempts FROM campaigns WHERE id=?", (call["campaign_id"],)
                ).fetchone()
                recipient = connection.execute(
                    "SELECT * FROM campaign_recipients WHERE id=?", (call["recipient_id"],)
                ).fetchone()
                connection.execute(
                    "UPDATE calls SET status='failed', error_code='worker_restart',"
                    " error_message='Recovered after worker restart', ended_at=?, updated_at=?"
                    " WHERE id=?",
                    (now, now, call["id"]),
                )
                connection.execute(
                    "INSERT INTO call_events (id, tenant_id, call_id, event_type, at, data)"
                    " VALUES (?, ?, ?, 'failed', ?, ?)",
                    (
                        new_id("evt"),
                        call["tenant_id"],
                        call["id"],
                        now,
                        json.dumps({"reason": "worker_restart"}),
                    ),
                )
                if recipient and recipient["status"] == "dialing" and campaign:
                    if int(recipient["attempts"]) < int(campaign["max_attempts"]):
                        connection.execute(
                            "UPDATE campaign_recipients SET status='queued', next_attempt_at=?,"
                            " last_error=?, updated_at=? WHERE id=?",
                            (
                                now,
                                "Recovered after worker restart",
                                now,
                                recipient["id"],
                            ),
                        )
                    else:
                        connection.execute(
                            "UPDATE campaign_recipients SET status='failed', last_error=?, updated_at=?"
                            " WHERE id=?",
                            ("Recovered after worker restart", now, recipient["id"]),
                        )
                recovered += 1
            # A recipient can be marked dialing without a call row if the process
            # died between the two writes. Requeue those as well.
            orphans = connection.execute(
                "SELECT r.*, c.max_attempts FROM campaign_recipients r"
                " JOIN campaigns c ON c.id=r.campaign_id"
                " LEFT JOIN calls ca ON ca.recipient_id=r.id"
                " WHERE r.status='dialing' AND ca.id IS NULL"
            ).fetchall()
            for orphan in orphans:
                target = (
                    "queued" if int(orphan["attempts"]) < int(orphan["max_attempts"]) else "failed"
                )
                connection.execute(
                    "UPDATE campaign_recipients SET status=?, next_attempt_at=?, last_error=?,"
                    " updated_at=? WHERE id=?",
                    (target, now, "Recovered after worker restart", now, orphan["id"]),
                )
                recovered += 1
        return recovered

    # ------------------------------------------------------------------- calls
    def get_contact_by_phone(self, phone: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Consent lives with the phone, not the provider namespace."""
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM contacts WHERE tenant_id=? AND phone=? ORDER BY updated_at DESC",
                (tenant_id, phone),
            ).fetchone()
        return self._decode(row) if row else None

    def create_voice_call(
        self,
        tenant_id: str,
        session_id: str,
        contact_id: str,
        settings: Any,
        agent_id: str,
    ) -> Dict[str, Any]:
        """Session-sourced call row (campaign column carries the session id)."""
        call_id = new_id("call")
        idempotency_key = f"vxs:{session_id}:1"
        simulated = settings.telephony_provider != "twilio" or not settings.allow_live_calls
        now = iso()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO calls (id, tenant_id, campaign_id, recipient_id, contact_id,"
                " provider, status, direction, simulated, attempt, idempotency_key,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'queued', 'outbound', ?, 1, ?, ?, ?)",
                (
                    call_id,
                    tenant_id,
                    session_id,
                    contact_id,
                    contact_id,
                    settings.telephony_provider,
                    int(bool(simulated)),
                    idempotency_key,
                    now,
                    now,
                ),
            )
        self.add_call_event(tenant_id, call_id, "queued", data={"idempotencyKey": idempotency_key})
        call = self.get_call(tenant_id, call_id)
        if call is None:  # caller contract: the row exists right after insert
            raise RuntimeError("voice call row vanished after insert")
        return call

    def apply_voice_session_result(self, tenant_id: str, call_id: str, result: Any) -> None:
        """Apply a telephony result to a session call row (no recipient fans-out)."""
        for event in result.events:
            self.add_call_event(tenant_id, call_id, event.event_type, event.at, event.data)
        # Mirror provider progress events onto the session envelope honestly.
        session = self.get_voice_session_by_call(tenant_id, call_id)
        if session is not None:
            mirrored = {
                "dialing": "voice_session.ringing",
                "ringing": "voice_session.ringing",
                "in_progress": "voice_session.in_progress",
                "answered": "voice_session.answered",
            }
            for event in result.events:
                session_event = mirrored.get(event.event_type)
                if session_event:
                    self.record_voice_session_event(
                        tenant_id, session["id"], session_event, dict(event.data)
                    )
        if not getattr(result, "final", True):
            self.update_call(
                tenant_id,
                call_id,
                target="dialing",
                provider=result.provider,
                provider_call_id=result.provider_call_id,
                started_at=result.started_at,
            )
            return
        for stage, duration in result.latency_ms.items():
            self.add_latency(tenant_id, call_id, stage, duration, result.simulated)
        fields: Dict[str, Any] = {
            "provider": result.provider,
            "provider_call_id": result.provider_call_id,
            "ended_at": result.ended_at,
            "disposition": result.disposition,
        }
        if result.ok and result.status == "completed":
            fields.update(answered_at=result.answered_at, duration_ms=result.duration_ms)
            self.update_call(tenant_id, call_id, target="completed", **fields)
            return
        target_map = {
            "no_answer": "no_answer",
            "busy": "busy",
        }
        target = target_map.get(result.status, "failed")
        if result.error_code:
            fields["error_code"] = result.error_code
        fields["error_message"] = result.error_message
        self.update_call(tenant_id, call_id, target=target, **fields)

    def get_call(self, tenant_id: str, call_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM calls WHERE tenant_id=? AND id=?", (tenant_id, call_id)
            ).fetchone()
        return self._decode(row) if row else None

    def get_call_by_provider_call_id(self, provider_call_id: str) -> Optional[Dict[str, Any]]:
        """Provider callbacks carry only their SID; lookup unconditionally."""
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM calls WHERE provider=? AND provider_call_id=?",
                ("twilio", provider_call_id),
            ).fetchone()
        return self._decode(row) if row else None

    def update_call(
        self, tenant_id: str, call_id: str, target: Optional[str] = None, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "provider",
            "provider_call_id",
            "started_at",
            "answered_at",
            "ended_at",
            "duration_ms",
            "disposition",
            "error_code",
            "error_message",
            "status",
        }
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM calls WHERE tenant_id=? AND id=?", (tenant_id, call_id)
            ).fetchone()
            if not row:
                return None
            if target and target != row["status"]:
                assert_transition(CALL_TRANSITIONS, row["status"], target)
                fields["status"] = target
            updates = [(key, value) for key, value in fields.items() if key in allowed]
            if not updates:
                return self._decode(row)
            setters = ", ".join(f"{key}=?" for key, _ in updates)
            params = [value for _, value in updates] + [iso(), tenant_id, call_id]
            connection.execute(
                f"UPDATE calls SET {setters}, updated_at=? WHERE tenant_id=? AND id=?", params
            )
        return self.get_call(tenant_id, call_id)

    def add_call_event(
        self,
        tenant_id: str,
        call_id: str,
        event_type: str,
        at: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO call_events (id, tenant_id, call_id, event_type, at, data)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_id("evt"), tenant_id, call_id, event_type, at or iso(), json.dumps(data or {})),
            )

    def add_latency(
        self,
        tenant_id: str,
        call_id: str,
        stage: str,
        duration_ms: float,
        simulated: bool,
        at: Optional[str] = None,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO call_latency (id, tenant_id, call_id, stage, duration_ms, simulated, at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("lat"),
                    tenant_id,
                    call_id,
                    stage,
                    float(duration_ms),
                    int(simulated),
                    at or iso(),
                ),
            )

    def list_calls(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
        campaign_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = "ca.tenant_id=?"
        params: List[Any] = [tenant_id]
        if campaign_id:
            where += " AND ca.campaign_id=?"
            params.append(campaign_id)
        if status:
            where += " AND ca.status=?"
            params.append(status)
        with self._read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS n FROM calls ca WHERE {where}", params
            ).fetchone()["n"]
            rows = connection.execute(
                "SELECT ca.*, c.first_name, c.last_name, c.phone, c.timezone,"
                " cp.name AS campaign_name, cp.dry_run"
                " FROM calls ca JOIN contacts c ON c.id=ca.contact_id"
                " JOIN campaigns cp ON cp.id=ca.campaign_id"
                f" WHERE {where} ORDER BY ca.created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return [self._decode(row) for row in rows], int(total)

    def get_call_detail(self, tenant_id: str, call_id: str) -> Optional[Dict[str, Any]]:
        call = self.get_call(tenant_id, call_id)
        if not call:
            return None
        with self._read() as connection:
            contact = connection.execute(
                "SELECT id, first_name, last_name, phone, email, timezone, consent_state"
                " FROM contacts WHERE tenant_id=? AND id=?",
                (tenant_id, call["contact_id"]),
            ).fetchone()
            campaign = connection.execute(
                "SELECT id, name, dry_run, status FROM campaigns WHERE tenant_id=? AND id=?",
                (tenant_id, call["campaign_id"]),
            ).fetchone()
            events = connection.execute(
                "SELECT * FROM call_events WHERE call_id=? ORDER BY at ASC", (call_id,)
            ).fetchall()
            latency = connection.execute(
                "SELECT stage, duration_ms, simulated, at FROM call_latency WHERE call_id=?"
                " ORDER BY at ASC",
                (call_id,),
            ).fetchall()
        call["contact"] = self._decode(contact) if contact else None
        call["campaign"] = self._decode(campaign) if campaign else None
        call["events"] = [self._decode(row) for row in events]
        call["latency"] = [self._decode(row) for row in latency]
        return call

    def latency_summary(self, tenant_id: str, simulated: Optional[bool] = None) -> Dict[str, Any]:
        where = "tenant_id=?"
        params: List[Any] = [tenant_id]
        if simulated is not None:
            where += " AND simulated=?"
            params.append(int(simulated))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT stage, duration_ms FROM call_latency WHERE {where}", params
            ).fetchall()
        grouped: Dict[str, List[float]] = {}
        for row in rows:
            grouped.setdefault(row["stage"], []).append(float(row["duration_ms"]))
        summary: Dict[str, Any] = {}
        for stage, values in grouped.items():
            values.sort()
            summary[stage] = {
                "count": len(values),
                "p50": _percentile(values, 50),
                "p95": _percentile(values, 95),
                "p99": _percentile(values, 99),
                "simulated": simulated if simulated is not None else True,
            }
        return summary

    def overview(self, tenant_id: str) -> Dict[str, Any]:
        with self._read() as connection:
            campaigns = connection.execute(
                "SELECT status, COUNT(*) AS n FROM campaigns WHERE tenant_id=? GROUP BY status",
                (tenant_id,),
            ).fetchall()
            calls = connection.execute(
                "SELECT status, COUNT(*) AS n FROM calls WHERE tenant_id=? GROUP BY status",
                (tenant_id,),
            ).fetchall()
            queue_depth = connection.execute(
                "SELECT COUNT(*) AS n FROM campaign_recipients WHERE tenant_id=? AND status='queued'",
                (tenant_id,),
            ).fetchone()["n"]
            active_calls = connection.execute(
                "SELECT COUNT(*) AS n FROM calls WHERE tenant_id=?"
                " AND status IN ('queued','dialing','ringing','in_progress')",
                (tenant_id,),
            ).fetchone()["n"]
            recent_errors = connection.execute(
                "SELECT ca.id, ca.status, ca.error_code, ca.error_message, ca.updated_at,"
                " c.first_name, c.last_name, cp.name AS campaign_name"
                " FROM calls ca JOIN contacts c ON c.id=ca.contact_id"
                " JOIN campaigns cp ON cp.id=ca.campaign_id"
                " WHERE ca.tenant_id=? AND ca.status IN ('failed','no_answer','busy')"
                " ORDER BY ca.updated_at DESC LIMIT 10",
                (tenant_id,),
            ).fetchall()
        campaign_counts = {row["status"]: int(row["n"]) for row in campaigns}
        call_counts = {row["status"]: int(row["n"]) for row in calls}
        return {
            "campaigns": {
                "total": sum(campaign_counts.values()),
                "byStatus": campaign_counts,
            },
            "calls": {"total": sum(call_counts.values()), "byStatus": call_counts},
            "queueDepth": int(queue_depth),
            "activeCalls": int(active_calls),
            "recentErrors": [self._decode(row) for row in recent_errors],
            "latency": self.latency_summary(tenant_id, simulated=True),
        }

    # -------------------------------------------------------------------- CRM
    def create_connection(
        self,
        tenant_id: str,
        provider: str,
        name: str,
        base_url: str,
        api_key_encrypted: str,
        webhook_secret_encrypted: Optional[str],
        capabilities: Dict[str, Any],
    ) -> Dict[str, Any]:
        connection_id = new_id("crm")
        now = iso()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO crm_connections (id, tenant_id, provider, name, base_url,"
                " api_key_encrypted, webhook_secret_encrypted, status, capabilities, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'untested', ?, ?, ?)",
                (
                    connection_id,
                    tenant_id,
                    provider,
                    name,
                    base_url,
                    api_key_encrypted,
                    webhook_secret_encrypted,
                    json.dumps(capabilities),
                    now,
                    now,
                ),
            )
        return self.get_connection(tenant_id, connection_id)  # type: ignore[return-value]

    def get_connection(self, tenant_id: str, connection_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM crm_connections WHERE tenant_id=? AND id=?",
                (tenant_id, connection_id),
            ).fetchone()
        return self._decode(row) if row else None

    def get_connection_unscoped(self, connection_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM crm_connections WHERE id=?", (connection_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list_connections(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM crm_connections WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def update_connection_test(
        self,
        tenant_id: str,
        connection_id: str,
        status: str,
        capabilities: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._write() as connection:
            if capabilities is not None:
                connection.execute(
                    "UPDATE crm_connections SET status=?, last_tested_at=?, last_error=?,"
                    " capabilities=?, updated_at=? WHERE tenant_id=? AND id=?",
                    (status, iso(), error, json.dumps(capabilities), iso(), tenant_id, connection_id),
                )
            else:
                connection.execute(
                    "UPDATE crm_connections SET status=?, last_tested_at=?, last_error=?,"
                    " updated_at=? WHERE tenant_id=? AND id=?",
                    (status, iso(), error, iso(), tenant_id, connection_id),
                )
        return self.get_connection(tenant_id, connection_id)

    def replace_mappings(
        self, tenant_id: str, connection_id: str, mappings: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        with self._write() as connection:
            connection.execute(
                "DELETE FROM crm_field_mappings WHERE tenant_id=? AND connection_id=?",
                (tenant_id, connection_id),
            )
            for mapping in mappings:
                connection.execute(
                    "INSERT INTO crm_field_mappings (id, tenant_id, connection_id, canonical_field,"
                    " external_field, required, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("map"),
                        tenant_id,
                        connection_id,
                        mapping["canonical_field"],
                        mapping["external_field"],
                        int(bool(mapping.get("required", False))),
                        iso(),
                    ),
                )
        return self.get_mappings(tenant_id, connection_id)

    def get_mappings(self, tenant_id: str, connection_id: str) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM crm_field_mappings WHERE tenant_id=? AND connection_id=?"
                " ORDER BY canonical_field",
                (tenant_id, connection_id),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def create_sync_job(self, tenant_id: str, connection_id: str) -> Dict[str, Any]:
        job_id = new_id("sync")
        with self._write() as connection:
            connection.execute(
                "INSERT INTO crm_sync_jobs (id, tenant_id, connection_id, status, created_at)"
                " VALUES (?, ?, ?, 'queued', ?)",
                (job_id, tenant_id, connection_id, iso()),
            )
        return self.get_sync_job(tenant_id, job_id)  # type: ignore[return-value]

    def update_sync_job(
        self, tenant_id: str, job_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "status",
            "cursor",
            "imported",
            "updated",
            "failed",
            "errors",
            "started_at",
            "completed_at",
        }
        updates = [(key, value) for key, value in fields.items() if key in allowed]
        if not updates:
            return self.get_sync_job(tenant_id, job_id)
        if "errors" in fields and not isinstance(fields["errors"], str):
            fields["errors"] = json.dumps(fields["errors"])
            updates = [(key, fields[key] if key == "errors" else value) for key, value in updates]
        with self._write() as connection:
            setters = ", ".join(f"{key}=?" for key, _ in updates)
            params = [value for _, value in updates] + [tenant_id, job_id]
            connection.execute(
                f"UPDATE crm_sync_jobs SET {setters} WHERE tenant_id=? AND id=?", params
            )
        return self.get_sync_job(tenant_id, job_id)

    def get_sync_job(self, tenant_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM crm_sync_jobs WHERE tenant_id=? AND id=?", (tenant_id, job_id)
            ).fetchone()
        return self._decode(row) if row else None

    def list_sync_jobs(self, tenant_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM crm_sync_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def record_webhook_event(
        self,
        tenant_id: str,
        connection_id: str,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        with self._write() as connection:
            cursor = connection.execute(
                "INSERT INTO webhook_events (id, tenant_id, connection_id, event_id, event_type,"
                " payload, status, received_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'received', ?)"
                " ON CONFLICT (tenant_id, connection_id, event_id) DO NOTHING",
                (
                    new_id("wh"),
                    tenant_id,
                    connection_id,
                    event_id,
                    event_type,
                    json.dumps(payload),
                    iso(),
                ),
            )
            return cursor.rowcount > 0

    def mark_webhook_processed(
        self, tenant_id: str, connection_id: str, event_id: str, status: str, error: Optional[str]
    ) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE webhook_events SET status=?, processed_at=?, error=?"
                " WHERE tenant_id=? AND connection_id=? AND event_id=?",
                (status, iso(), error, tenant_id, connection_id, event_id),
            )


def _percentile(sorted_values: List[float], percentile: int) -> Optional[float]:
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, int(round((percentile / 100) * (len(sorted_values) - 1)))))
    return round(sorted_values[index], 2)
