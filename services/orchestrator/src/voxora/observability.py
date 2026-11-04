"""Structured logging and shared percentile helper."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional


class JsonFormatter(logging.Formatter):
    # Standard LogRecord attributes are never promoted; everything else in
    # `extra=` is. `_`-prefixed extras keep their legacy promotion (prefix
    # stripped) for backward compatibility with existing call sites.
    _RESERVED = frozenset(
        vars(logging.LogRecord("", 0, "", 0, "", (), None))
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("_") and key != "__":
                payload[key[1:]] = value
            elif key not in self._RESERVED:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def percentile(sorted_values: List[float], p: int) -> Optional[float]:
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, int(round((p / 100) * (len(sorted_values) - 1)))))
    return round(sorted_values[index], 2)
