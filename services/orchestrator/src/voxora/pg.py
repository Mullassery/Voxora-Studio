"""PostgreSQL storage backend for the orchestrator.

Reuses the portable SQL in :mod:`voxora.db` verbatim by translating the
dialect-specific bits at execution time:

- ``?`` placeholders become ``%s`` (psycopg3 format),
- ``INSERT OR IGNORE`` becomes ``... ON CONFLICT DO NOTHING``,
- ``BEGIN IMMEDIATE`` transactions become plain psycopg transactions
  (Postgres default READ COMMITTED; claims stay atomic via row locks),
- PRAGMA statements are skipped (Postgres equivalents are not needed).

Timestamps stay ISO-8601 TEXT in both backends so ordering semantics match.
``psycopg[binary]`` ships as a hard orchestrator dependency so this module
imports cleanly.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager, suppress
from typing import Any, Dict, Iterator

from psycopg import Connection, rows

from .db import JSON_COLUMNS, SCHEMA, Database


def _translate_sqlite_sql(sql: Any) -> Any:
    """Translate SQLite-specific SQL for Postgres, else pass through."""
    if not isinstance(sql, str):  # SQL/Composed objects are already PG-safe
        return sql
    had_ignore = "INSERT OR IGNORE" in sql
    translated = sql
    if had_ignore:
        translated = translated.replace("INSERT OR IGNORE", "INSERT")
        translated += " ON CONFLICT DO NOTHING"
    return translated.replace("?", "%s")


class _PgConnection(Connection[Any]):  # type: ignore[type-arg]
    """psycopg3 Connection that translates the SQLite SQL dialect."""

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        return super().execute(_translate_sqlite_sql(query), params, **kwargs)  # type: ignore[arg-type]


def _pg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url.replace("postgres://", "postgresql://", 1)


class PostgresDatabase(Database):
    def __init__(self, url: str) -> None:
        self.path = "(postgres)"
        self.url = _pg_url(url)
        self._lock = threading.RLock()

    # connection plumbing ------------------------------------------------
    def _connect(self) -> Connection[Any]:  # type: ignore[type-arg,override]
        return _PgConnection.connect(self.url, row_factory=rows.dict_row)

    @contextmanager
    def _read(self) -> Iterator[Connection[Any]]:  # type: ignore[override]
        connection = self._connect()
        try:
            connection.autocommit = True
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[Connection[Any]]:  # type: ignore[override]
        with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:  # type: ignore[override]
        result = dict(row)
        for key in list(result):
            if key in JSON_COLUMNS and isinstance(result[key], str):
                with suppress(json.JSONDecodeError):
                    result[key] = json.loads(result[key])
        return result

    # schema / lifecycle --------------------------------------------------
    def initialize(self) -> None:
        with self._write() as connection:
            statements = [
                statement.strip()
                for statement in SCHEMA.split(";")
                if statement.strip() and "PRAGMA" not in statement
            ]
            for statement in statements:
                connection.execute(statement)
            # Idempotent column additions for databases created before audience imports.
            for column, definition in (
                ("audience_source", "TEXT"),
                ("audience_id", "TEXT"),
                ("audience_version", "TEXT"),
            ):
                connection.execute(f"ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS {column} {definition}")  # noqa: S608 - fixed internal names
            # Voice-session recording references (parity with the SQLite
            # `_migrate_voice_sessions` hook): databases created before the
            # recording-callback feature need the same columns on Postgres.
            for column, definition in (
                ("recording_url", "TEXT"),
                ("recording_sid", "TEXT"),
            ):
                connection.execute(f"ALTER TABLE voice_sessions ADD COLUMN IF NOT EXISTS {column} {definition}")  # noqa: S608 - fixed internal names
            # Provider-specific connection config (e.g. LeadConnector's
            # location_id) — parity with the SQLite `_migrate_crm_connections`.
            connection.execute("ALTER TABLE crm_connections ADD COLUMN IF NOT EXISTS config TEXT NOT NULL DEFAULT '{}'")

    # multi-worker claiming (roadmap M4a) ---------------------------------
    # Postgres runs READ COMMITTED with no writer serialization, so
    # claiming needs explicit coordination: an advisory transaction lock
    # serializes the slot accounting across worker processes, and
    # FOR UPDATE SKIP LOCKED keeps concurrent workers from blocking on (or
    # double-reading) rows another worker is claiming. The shared
    # rowcount guard in Database.claim_recipients is the last line of
    # defense for lost races.
    _CLAIM_ADVISORY_KEY = 871503  # arbitrary app-specific lock id

    def _claim_prelude(self, connection: Connection[Any]) -> None:  # type: ignore[override]
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (self._CLAIM_ADVISORY_KEY,))

    def _claim_row_lock(self) -> str:  # type: ignore[override]
        return " FOR UPDATE SKIP LOCKED"
