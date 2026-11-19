"""CSV batch intake, background engine, and worker (roadmap M10 Phase E).

The intake is strict and preview-first: unknown columns, duplicate headers, and
invalid rows are refused or reported with row numbers before anything is
queued. The engine runs in a DEDICATED worker thread with an atomic DB claim —
it never shares the campaign worker's loop or touches the live call path. Every
generation goes through the existing `generate_asset` / `generate_long_form`
engines with the mock provider in tests (zero paid API calls).
"""

from __future__ import annotations

import csv
import io
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .audio_assets import AudioGenerationError, generate_asset
from .db import Database, iso
from .longform import LONGFORM_FORMATS, generate_long_form, retry_long_form
from .models import AudioBatchRecord
from .tts_generation import MAX_SCRIPT_CHARS, TTSProviderError, build_tts_provider

logger = logging.getLogger(__name__)

MAX_CSV_BYTES = 2 * 1024 * 1024
CSV_COLUMNS = (
    "script",
    "title",
    "voice",
    "language",
    "speed",
    "model",
    "response_format",
    "instructions",
)


class BatchCsvError(ValueError):
    """Intake-level CSV problem; mapped to 422 at the API edge."""


def parse_batch_csv(
    content: bytes, defaults: Dict[str, Any], max_records: int
) -> Dict[str, Any]:
    """Parse and validate CSV bytes; never writes anything.

    Returns a preview plus ready-to-persist records. Invalid rows are reported
    with their row numbers; valid rows can still be queued so one bad line does
    not waste the rest of the file.
    """
    if len(content) > MAX_CSV_BYTES:
        raise BatchCsvError(f"CSV exceeds the {MAX_CSV_BYTES}-byte cap")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BatchCsvError("CSV must be UTF-8 encoded") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BatchCsvError("CSV is empty or has no header row")
    columns = [name.strip() for name in reader.fieldnames]
    if any(not name for name in columns):
        raise BatchCsvError("CSV header contains a blank column")
    if len(set(columns)) != len(columns):
        raise BatchCsvError("CSV header has duplicate columns")
    if "script" not in columns:
        raise BatchCsvError("CSV must have a 'script' column")
    unknown = [name for name in columns if name not in CSV_COLUMNS]
    if unknown:
        raise BatchCsvError(f"unknown CSV column(s): {', '.join(unknown)}")
    reader.fieldnames = columns

    records: List[Dict[str, Any]] = []
    preview: List[Dict[str, Any]] = []
    errors: List[str] = []
    for row_number, row in enumerate(reader, start=2):
        values = {key: (row.get(key) or "").strip() for key in columns}
        if not any(values.values()):
            continue
        if None in row:
            errors.append(f"row {row_number}: more values than header columns")
            preview.append(
                {
                    "rowNumber": row_number,
                    "title": values.get("title", ""),
                    "voice": values.get("voice", ""),
                    "scriptPreview": values.get("script", "")[:120],
                    "valid": False,
                    "errors": [f"row {row_number}: more values than header columns"],
                }
            )
            continue
        resolved = dict(defaults)
        for key in (
            "title",
            "voice",
            "language",
            "speed",
            "model",
            "response_format",
            "instructions",
        ):
            if values.get(key):
                resolved[key] = values[key]
        resolved["script"] = values["script"]
        if not resolved.get("title"):
            resolved["title"] = f"Batch row {row_number}"

        row_errors: List[str] = []
        try:
            parsed = AudioBatchRecord(**resolved)
        except Exception as error:  # Pydantic ValidationError messages stay local
            detail = str(error).splitlines()[0] if str(error) else "invalid row"
            row_errors.append(f"row {row_number}: {detail}")
        settings = parsed.model_dump() if not row_errors else {}
        if (
            not row_errors
            and len(settings["script"]) > MAX_SCRIPT_CHARS
            and settings["response_format"] not in LONGFORM_FORMATS
        ):
            row_errors.append(
                f"row {row_number}: long scripts require response_format wav or pcm"
            )
        if row_errors:
            errors.extend(row_errors)
        else:
            settings["long_form"] = len(settings["script"]) > MAX_SCRIPT_CHARS
            records.append(
                {
                    "row_number": row_number,
                    "title": settings["title"],
                    "script": settings["script"],
                    "voice": settings["voice"],
                    "language": settings["language"],
                    "settings": settings,
                }
            )
        preview.append(
            {
                "rowNumber": row_number,
                "title": settings.get("title") or values.get("title", ""),
                "voice": settings.get("voice") or values.get("voice", ""),
                "scriptPreview": values.get("script", "")[:120],
                "valid": not row_errors,
                "errors": row_errors,
            }
        )

    if len(records) > max_records:
        raise BatchCsvError(
            f"CSV has {len(records)} valid rows; the batch cap is {max_records}"
        )
    if not records:
        raise BatchCsvError("CSV has no valid rows")
    return {
        "columns": columns,
        "rowCount": len(preview),
        "validCount": len(records),
        "errors": errors,
        "preview": preview,
        "records": records,
    }


def batch_report_csv(job: Dict[str, Any]) -> str:
    """Downloadable per-record report; counters live in the job response."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["row", "title", "voice", "status", "asset_id", "attempts", "rate_limited", "error"]
    )
    for item in job.get("items", []):
        writer.writerow(
            [
                item.get("row_number"),
                item.get("title"),
                item.get("voice"),
                item.get("status"),
                item.get("asset_id") or "",
                item.get("attempts", 0),
                "true" if item.get("rate_limited") else "false",
                item.get("error") or "",
            ]
        )
    return output.getvalue()


class BatchRateLimiter:
    """Client-side spacing plus 429 backoff; no provider-quota claims.

    `per_minute=0` disables spacing (explicitly allowed for local/demo use).
    Clock and sleep are injectable so tests never wait in real time.
    """

    def __init__(
        self,
        per_minute: int,
        backoff_seconds: float,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.backoff_seconds = max(0.0, backoff_seconds)
        self._interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._next_at = 0.0
        self._backoff_until = 0.0
        self.waited_seconds = 0.0
        self.rate_limited_events = 0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        now = self._clock()
        target = max(now, self._next_at, self._backoff_until)
        delay = target - now
        if delay > 0:
            self._sleep(delay)
            self.waited_seconds += delay
        self._next_at = self._clock() + self._interval

    def note_rate_limited(self) -> None:
        self.rate_limited_events += 1
        self._backoff_until = self._clock() + self.backoff_seconds


class AudioBatchEngine:
    """Claim and process one bounded set of CSV rows per run."""

    def __init__(
        self,
        db: Database,
        settings: Any,
        provider: Optional[Any] = None,
        limiter: Optional[BatchRateLimiter] = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.provider = provider
        self.limiter = limiter or BatchRateLimiter(
            settings.audio_batch_rate_per_minute,
            settings.audio_batch_rate_limit_backoff_seconds,
        )
        self._run_lock = threading.Lock()

    def run_once(self) -> int:
        with self._run_lock:
            claimed = self.db.claim_audio_batch_items(self.settings.audio_batch_claim_size)
            if not claimed:
                return 0
            for item in claimed:
                self._process(item)
            return len(claimed)

    def run_until_idle(self, max_rounds: int = 25) -> int:
        processed = 0
        for _ in range(max_rounds):
            count = self.run_once()
            processed += count
            if count == 0:
                break
        return processed

    def _process(self, item: Dict[str, Any]) -> None:
        tenant_id = item["tenant_id"]
        batch_id = item["batch_id"]
        payload = dict(item.get("settings") or {})
        try:
            self.limiter.wait()
            provider = self.provider or build_tts_provider(self.settings)
            if payload.get("long_form"):
                asset = self._generate_long_form(tenant_id, item, payload, provider)
            else:
                asset = generate_asset(
                    self.db,
                    self.settings,
                    tenant_id,
                    "worker",
                    payload,
                    provider=provider,
                )
            if asset.get("status", "ready") != "ready":
                failed = [
                    str(entry.get("error"))
                    for entry in asset.get("processing_history", [])
                    if isinstance(entry, dict) and entry.get("state") == "failed"
                ]
                detail = "long-form generation incomplete; failed chunks: " + (
                    "; ".join(failed[:3]) or "unknown"
                )
                self._fail(item, detail, False, asset.get("id"))
                return
            self.db.complete_audio_batch_item(
                tenant_id,
                batch_id,
                item["id"],
                status="ready",
                asset_id=asset["id"],
            )
        except (TTSProviderError, AudioGenerationError) as error:
            self._fail(item, error.detail, getattr(error, "status_code", None) == 429)
        except ValueError as error:
            self._fail(item, str(error), False)
        except Exception:  # noqa: BLE001 - one bad row must not kill the batch
            logger.exception(
                "audio batch item failed",
                extra={"batchId": batch_id, "itemId": item["id"]},
            )
            self._fail(item, "internal batch processing error", False)

    def _generate_long_form(
        self,
        tenant_id: str,
        item: Dict[str, Any],
        payload: Dict[str, Any],
        provider: Any,
    ) -> Dict[str, Any]:
        """Retry an existing partial run first; never regenerate ready chunks."""
        existing_id = item.get("asset_id")
        existing = self.db.get_audio_asset(tenant_id, existing_id) if existing_id else None
        if existing is not None and existing.get("status") in ("processing", "failed"):
            return retry_long_form(
                self.db,
                self.settings,
                tenant_id,
                "worker",
                existing,
                provider=provider,
            )
        return generate_long_form(
            self.db,
            self.settings,
            tenant_id,
            "worker",
            payload,
            provider=provider,
        )

    def _fail(
        self,
        item: Dict[str, Any],
        detail: str,
        rate_limited: bool,
        asset_id: Optional[str] = None,
    ) -> None:
        if rate_limited:
            self.limiter.note_rate_limited()
        next_attempt_at: Optional[str] = None
        if rate_limited and self.limiter.backoff_seconds > 0:
            until = datetime.now(timezone.utc) + timedelta(
                seconds=self.limiter.backoff_seconds
            )
            next_attempt_at = iso(until)
        self.db.complete_audio_batch_item(
            item["tenant_id"],
            item["batch_id"],
            item["id"],
            status="failed",
            asset_id=asset_id or item.get("asset_id"),
            error=detail[:500],
            rate_limited=rate_limited,
            next_attempt_at=next_attempt_at,
        )


class AudioBatchWorker(threading.Thread):
    """Dedicated daemon loop; independent of the campaign/call worker."""

    def __init__(self, engine: AudioBatchEngine, poll_seconds: float = 0.5) -> None:
        super().__init__(name="voxora-audio-batch-worker", daemon=True)
        self.engine = engine
        self.poll_seconds = max(0.05, poll_seconds)
        self._stop = threading.Event()

    def run(self) -> None:
        logger.info("audio batch worker started")
        while not self._stop.is_set():
            try:
                processed = self.engine.run_once()
                if processed == 0:
                    self._stop.wait(self.poll_seconds)
            except Exception:  # noqa: BLE001 - the worker must survive bad rows
                logger.exception("audio batch worker loop error")
                self._stop.wait(self.poll_seconds)
        logger.info("audio batch worker stopped")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self.is_alive():
            self.join(timeout=timeout)
