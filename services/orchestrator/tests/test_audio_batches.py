"""CSV batch intake, engine, worker, and report tests (roadmap M10 Phase E).

Every generation here goes through the deterministic mock provider or an
explicit failing stub — no paid API calls, no live services.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import pytest

from voxora.audio_batches import (
    AudioBatchEngine,
    BatchCsvError,
    BatchRateLimiter,
    batch_report_csv,
    parse_batch_csv,
)
from voxora.db import Database, iso
from voxora.tts_generation import MockTTSProvider, TTSProviderError

TENANT = "tenant-a"
AUTH_DEFAULTS = {"voice": "alloy", "response_format": "wav", "model": "tts-1"}


def _csv(rows: List[str], header: str = "script,title,voice") -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def _engine(db: Database, settings: Any, provider: Any) -> AudioBatchEngine:
    return AudioBatchEngine(
        db,
        settings,
        provider=provider,
        limiter=BatchRateLimiter(0, 0),
    )


class FailOnceProvider(MockTTSProvider):
    """Fails the first call whose script matches `needle`, then succeeds."""

    def __init__(self, needle: str, error: Optional[TTSProviderError] = None) -> None:
        self.needle = needle
        self.error = error or TTSProviderError(502, "upstream provider unavailable")
        self.failed = False
        self.calls: List[str] = []

    def generate(self, request):  # type: ignore[override]
        self.calls.append(request.script)
        if not self.failed and self.needle in request.script:
            self.failed = True
            raise self.error
        return super().generate(request)


def test_parse_csv_preview_applies_defaults_and_per_row_overrides() -> None:
    csv_bytes = _csv(
        [
            "hello world,First,",
            "second script,Second,nova",
        ]
    )
    result = parse_batch_csv(csv_bytes, AUTH_DEFAULTS, max_records=10)
    assert result["columns"] == ["script", "title", "voice"]
    assert result["rowCount"] == 2
    assert result["validCount"] == 2
    assert result["records"][0]["settings"]["voice"] == "alloy"
    assert result["records"][1]["settings"]["voice"] == "nova"
    assert result["preview"][0]["valid"] is True
    assert result["records"][0]["settings"]["long_form"] is False


def test_parse_csv_rejects_structural_problems() -> None:
    with pytest.raises(BatchCsvError, match="unknown CSV column"):
        parse_batch_csv(_csv(["hello,Title,alloy,extra"], "script,title,voice,extra"), AUTH_DEFAULTS, 10)
    with pytest.raises(BatchCsvError, match="duplicate columns"):
        parse_batch_csv(_csv(["hello,Title,alloy"], "script,title,title"), AUTH_DEFAULTS, 10)
    with pytest.raises(BatchCsvError, match="must have a 'script' column"):
        parse_batch_csv(_csv(["Title,alloy"], "title,voice"), AUTH_DEFAULTS, 10)


def test_parse_csv_reports_row_errors_without_blocking_valid_rows() -> None:
    csv_bytes = b"script,title,voice,speed\nbad speed,First,alloy,900\ngood,Second,alloy,1.0\n"
    result = parse_batch_csv(csv_bytes, AUTH_DEFAULTS, 10)
    assert result["validCount"] == 1
    assert result["records"][0]["title"] == "Second"
    assert any("row 2" in error for error in result["errors"])
    assert result["preview"][0]["valid"] is False


def test_parse_csv_long_script_requires_assemblable_format() -> None:
    long_script = "word " * 900
    with pytest.raises(BatchCsvError, match="no valid rows"):
        parse_batch_csv(
            _csv([f'"{long_script}",Long,alloy'], "script,title,voice"),
            {**AUTH_DEFAULTS, "response_format": "mp3"},
            10,
        )
    result = parse_batch_csv(
        _csv([f'"{long_script}",Long,alloy'], "script,title,voice"),
        AUTH_DEFAULTS,
        10,
    )
    assert result["records"][0]["settings"]["long_form"] is True


def test_parse_csv_enforces_record_cap() -> None:
    with pytest.raises(BatchCsvError, match="batch cap is 2"):
        parse_batch_csv(_csv(["a,1,alloy", "b,2,alloy", "c,3,alloy"]), AUTH_DEFAULTS, 2)


def test_engine_processes_batch_with_mock_provider(
    database: Database, settings: Any
) -> None:
    result = parse_batch_csv(
        _csv(["first,First,", "second,Second,"]), AUTH_DEFAULTS, 10
    )
    job = database.create_audio_batch(TENANT, "batch.csv", AUTH_DEFAULTS, result["records"])
    engine = _engine(database, settings, MockTTSProvider())
    assert engine.run_until_idle() == 2
    job = database.get_audio_batch(TENANT, job["id"])
    assert job is not None
    assert job["status"] == "completed"
    assert job["completed"] == 2 and job["failed"] == 0
    assert len({item["asset_id"] for item in job["items"]}) == 2
    for item in job["items"]:
        asset = database.get_audio_asset(TENANT, item["asset_id"])
        assert asset is not None and asset["status"] == "ready"
    assert engine.run_once() == 0


def test_retry_requeues_failed_items_only(
    database: Database, settings: Any
) -> None:
    result = parse_batch_csv(
        _csv(["keep,Keep,", "break,Break,", "also keep,Third,"]),
        AUTH_DEFAULTS,
        10,
    )
    job = database.create_audio_batch(TENANT, "batch.csv", AUTH_DEFAULTS, result["records"])
    provider = FailOnceProvider("break")
    engine = _engine(database, settings, provider)
    assert engine.run_until_idle() == 3
    job = database.get_audio_batch(TENANT, job["id"])
    assert job is not None and job["failed"] == 1 and job["completed"] == 2
    failed = next(item for item in job["items"] if item["status"] == "failed")
    retried = database.retry_failed_audio_batch_items(TENANT, job["id"])
    assert retried is not None
    count, job = retried
    assert count == 1 and job["status"] == "queued"
    assert engine.run_until_idle() == 1
    job = database.get_audio_batch(TENANT, job["id"])
    assert job is not None and job["status"] == "completed" and job["failed"] == 0
    assert all(item["asset_id"] for item in job["items"] if item["status"] == "ready")
    assert failed["asset_id"] is None


def test_rate_limited_item_backs_off_then_retries(
    database: Database, settings: Any
) -> None:
    limiter = BatchRateLimiter(60, 5.0)
    result = parse_batch_csv(_csv(["throttle,Throttle,"]), AUTH_DEFAULTS, 10)
    job = database.create_audio_batch(TENANT, "batch.csv", AUTH_DEFAULTS, result["records"])
    provider = FailOnceProvider("throttle", TTSProviderError(429, "rate limit reached"))
    engine = AudioBatchEngine(database, settings, provider=provider, limiter=limiter)
    engine.run_once()
    items = database.list_audio_batch_items(TENANT, job["id"])
    assert items[0]["status"] == "failed"
    assert items[0]["rate_limited"] == 1
    assert items[0]["next_attempt_at"] is not None
    assert limiter.rate_limited_events == 1
    database.retry_failed_audio_batch_items(TENANT, job["id"])
    engine.run_once()
    items = database.list_audio_batch_items(TENANT, job["id"])
    assert items[0]["status"] == "ready"
    assert items[0]["rate_limited"] == 0


def test_long_form_item_reuses_partial_asset_on_retry(
    database: Database, settings: Any
) -> None:
    long_script = "\n\n".join(
        " ".join(["alpha bravo charlie delta echo foxtrot golf hotel"] * 60) for _ in range(3)
    )
    result = parse_batch_csv(
        ("script,title,voice\n" + f'"{long_script}",Long,alloy\n').encode(),
        AUTH_DEFAULTS,
        10,
    )
    assert result["records"][0]["settings"]["long_form"] is True
    job = database.create_audio_batch(TENANT, "batch.csv", AUTH_DEFAULTS, result["records"])
    provider = FailOnceProvider("alpha", TTSProviderError(502, "upstream failed"))
    engine = _engine(database, settings, provider)
    engine.run_once()
    items = database.list_audio_batch_items(TENANT, job["id"])
    assert items[0]["status"] == "failed"
    partial_id = items[0]["asset_id"]
    assert partial_id is not None
    calls_before = len(provider.calls)
    database.retry_failed_audio_batch_items(TENANT, job["id"])
    engine.run_once()
    items = database.list_audio_batch_items(TENANT, job["id"])
    assert items[0]["status"] == "ready"
    assert items[0]["asset_id"] == partial_id
    assert calls_before + 1 <= len(provider.calls) <= calls_before + 3


def test_recover_inflight_requeues_stranded_batch_items(
    database: Database, settings: Any
) -> None:
    result = parse_batch_csv(_csv(["one,One,"]), AUTH_DEFAULTS, 10)
    job = database.create_audio_batch(TENANT, "batch.csv", AUTH_DEFAULTS, result["records"])
    claimed = database.claim_audio_batch_items(5)
    assert len(claimed) == 1
    assert database.get_audio_batch(TENANT, job["id"])["status"] == "processing"
    database.recover_inflight()
    assert database.get_audio_batch(TENANT, job["id"])["status"] == "queued"
    assert database.list_audio_batch_items(TENANT, job["id"])[0]["status"] == "queued"


def test_batch_report_csv_has_per_record_rows(database: Database, settings: Any) -> None:
    result = parse_batch_csv(_csv(["one,One,", "two,Two,"]), AUTH_DEFAULTS, 10)
    job = database.create_audio_batch(TENANT, "batch.csv", AUTH_DEFAULTS, result["records"])
    _engine(database, settings, MockTTSProvider()).run_until_idle()
    report = batch_report_csv(database.get_audio_batch(TENANT, job["id"]))
    assert "row,title,voice,status,asset_id,attempts,rate_limited,error" in report
    assert "One" in report and "Two" in report
    assert "ready" in report and "failed" not in report


def test_batch_rate_limiter_spaces_calls_with_injected_clock() -> None:
    now = [0.0]
    sleeps: List[float] = []
    limiter = BatchRateLimiter(
        60, 2.0, clock=lambda: now[0], sleep=lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds))
    )
    limiter.wait()
    assert sleeps == []
    limiter.wait()
    assert sleeps == [1.0]
    limiter.note_rate_limited()
    limiter.wait()
    assert sleeps[-1] == 2.0
    assert limiter.rate_limited_events == 1


def test_recovered_error_timestamp_is_utc() -> None:
    stamp = iso(datetime.now(timezone.utc) + timedelta(seconds=1))
    assert "+" in stamp or stamp.endswith("Z")
