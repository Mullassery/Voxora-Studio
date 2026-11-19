"""HTTP contract tests for CSV batch generation (roadmap M10 Phase E).

Uses multipart upload with the deterministic mock provider and explicit stubs;
the live call path boundary is re-pinned side by side with the batch surface.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi.testclient import TestClient

import voxora.api as api_module
from voxora.audio_batches import BatchRateLimiter
from voxora.tts_generation import MockTTSProvider, TTSProviderError

AUTH = {"Authorization": "Bearer admin-token"}
VIEWER = {"Authorization": "Bearer viewer-token"}
OTHER_TENANT = {"Authorization": "Bearer other-tenant-token"}
DEFAULTS = {"voice": "alloy", "response_format": "wav", "model": "tts-1"}


def _upload(
    client: TestClient,
    csv_text: str,
    path: str = "/api/v1/audio/batches",
    defaults: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
):
    return client.post(
        path,
        files={"file": ("batch.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"defaults": json.dumps(defaults or DEFAULTS)},
        headers=headers or AUTH,
    )


def _stub_engine(client: TestClient, provider: Any) -> None:
    client.app.state.audio_batch_engine.provider = provider
    client.app.state.audio_batch_engine.limiter = BatchRateLimiter(0, 0)


class StubFailOnce(MockTTSProvider):
    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.failed = False

    def generate(self, request):  # type: ignore[override]
        if not self.failed and self.needle in request.script:
            self.failed = True
            raise TTSProviderError(502, "upstream provider unavailable")
        return super().generate(request)


def test_preview_validates_without_queuing(client: TestClient) -> None:
    response = _upload(
        client, "script,title,voice\nfirst,First,\nsecond,Second,nova\n",
        "/api/v1/audio/batches/preview",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rowCount"] == 2 and body["validCount"] == 2
    assert body["preview"][1]["voice"] == "nova"
    assert client.get("/api/v1/audio/batches", headers=AUTH).json()["data"] == []


def test_preview_rejects_unknown_columns_and_bad_defaults(client: TestClient) -> None:
    response = _upload(client, "script,title,voice,extra\na,b,c,d\n", "/api/v1/audio/batches/preview")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "batch_csv_invalid"
    bad = _upload(client, "script\na\n", "/api/v1/audio/batches/preview", defaults={"speed": 9})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "batch_defaults_invalid"


def test_create_process_report_download_and_tenant_isolation(client: TestClient) -> None:
    created = _upload(client, "script,title,voice\nfirst,First,\nsecond,Second,\n")
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "queued" and job["total"] == 2
    assert client.get(f"/api/v1/audio/batches/{job['id']}", headers=OTHER_TENANT).status_code == 404
    _stub_engine(client, MockTTSProvider())
    processed = client.post("/api/v1/audio/batches/process", headers=AUTH)
    assert processed.status_code == 200 and processed.json()["processed"] == 2
    detail = client.get(f"/api/v1/audio/batches/{job['id']}", headers=AUTH).json()
    assert detail["status"] == "completed" and detail["completed"] == 2
    asset_id = detail["items"][0]["asset_id"]
    download = client.get(f"/api/v1/audio-assets/{asset_id}/download", headers=AUTH)
    assert download.status_code == 200
    report = client.get(f"/api/v1/audio/batches/{job['id']}/report", headers=AUTH)
    assert report.status_code == 200 and "text/csv" in report.headers["content-type"]
    assert "First" in report.text and "ready" in report.text


def test_retry_failed_only_and_conflict(client: TestClient) -> None:
    created = _upload(client, "script,title,voice\nkeep,Keep,\nbreak,Break,\n")
    job = created.json()
    _stub_engine(client, StubFailOnce("break"))
    client.post("/api/v1/audio/batches/process", headers=AUTH)
    detail = client.get(f"/api/v1/audio/batches/{job['id']}", headers=AUTH).json()
    assert detail["failed"] == 1 and detail["completed"] == 1
    retried = client.post(f"/api/v1/audio/batches/{job['id']}/retry-failed", headers=AUTH)
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    client.post("/api/v1/audio/batches/process", headers=AUTH)
    final = client.get(f"/api/v1/audio/batches/{job['id']}", headers=AUTH).json()
    assert final["status"] == "completed" and final["failed"] == 0
    conflict = client.post(f"/api/v1/audio/batches/{job['id']}/retry-failed", headers=AUTH)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "batch_no_failed_items"


def test_unconfigured_provider_is_honest(client: TestClient, monkeypatch) -> None:
    def unconfigured(settings):
        raise ValueError("audio provider is not configured")

    monkeypatch.setattr(api_module, "build_tts_provider", unconfigured)
    response = _upload(client, "script\nhello\n")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "audio_provider_unconfigured"


def test_writer_gating_and_live_call_boundary(client: TestClient) -> None:
    csv_text = "script\nhello\n"
    assert _upload(client, csv_text, headers=VIEWER).status_code == 403
    assert client.post("/api/v1/audio/batches/process", headers=VIEWER).status_code == 403
    assert client.post(
        "/api/v1/audio/batches/none/retry-failed", headers=VIEWER
    ).status_code == 403
    # M7/P9 boundary still pinned beside the new PRODUCE surface.
    assert client.post("/voice-sessions/anything/say", json={"text": "hi"}, headers=AUTH).status_code == 404


def test_list_batches_uses_page_envelope(client: TestClient) -> None:
    _upload(client, "script\nhello\n")
    body = client.get("/api/v1/audio/batches", headers=AUTH).json()
    assert body["pageInfo"]["total"] == 1
    assert body["pageInfo"]["hasNextPage"] is False
