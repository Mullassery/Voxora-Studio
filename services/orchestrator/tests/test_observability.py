"""Structured-logging contract tests (audit finding V3, roadmap M5).

The JSON formatter must promote standard `extra=` fields — silently dropping
them cost operators the fields they need during carrier incidents — while
keeping the legacy `_`-prefixed promotion and never leaking LogRecord guts.
"""

from __future__ import annotations

import json
import logging

import pytest

from voxora.observability import JsonFormatter


def _format_with(caplog_log: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(caplog_log))


def test_standard_extra_fields_are_promoted(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("voxora.test.extras")
    with caplog.at_level(logging.INFO, logger="voxora.test.extras"):
        logger.info("call placed", extra={"call_id": "call_1", "tenant": "tenant-a"})
    payload = _format_with(caplog.records[0])
    assert payload["call_id"] == "call_1"
    assert payload["tenant"] == "tenant-a"
    assert payload["message"] == "call placed"


def test_underscore_prefixed_extras_keep_legacy_promotion(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("voxora.test.legacy")
    with caplog.at_level(logging.INFO, logger="voxora.test.legacy"):
        logger.info("legacy shape", extra={"_session_id": "vxs_1"})
    payload = _format_with(caplog.records[0])
    assert payload["session_id"] == "vxs_1"
    assert "_session_id" not in payload


def test_standard_logrecord_attributes_are_not_leaked(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("voxora.test.leak")
    with caplog.at_level(logging.INFO, logger="voxora.test.leak"):
        logger.warning("nothing extra", extra={"custom_field": 7})
    payload = _format_with(caplog.records[0])
    assert payload["custom_field"] == 7
    for leaked in ("args", "levelname", "pathname", "taskName", "processName"):
        assert leaked not in payload
