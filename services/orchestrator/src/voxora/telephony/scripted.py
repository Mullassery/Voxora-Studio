"""Test-only telephony adapter with scripted results."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import CallRequest, TelephonyAdapter, TelephonyResult


class ScriptedTelephonyAdapter(TelephonyAdapter):
    provider = "scripted"

    def __init__(self, results: List[TelephonyResult]):
        self._results = list(results)
        self._index = 0
        self.calls: List[CallRequest] = []

    def start_call(self, request: CallRequest) -> TelephonyResult:
        self.calls.append(request)
        if self._index >= len(self._results):
            raise AssertionError("scripted results exhausted")
        result = self._results[self._index]
        self._index += 1
        return result

    def cancel_call(self, provider_call_id: str) -> bool:
        return True

    def status(self) -> Dict[str, Any]:
        return {"provider": self.provider, "available": True, "testOnly": True}
