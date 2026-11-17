"""Long-form HTTP API tests (roadmap M10 Phase D).

Pins the long-form contract at the HTTP edge: generate-long with per-chunk
honesty (201 with processing status on partial failure), retry-failed
regenerating only failed chunks, 409 on run-in-flight, and lifecycle guards
(409 still_processing downloads, 422 variants of non-ready assets).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import voxora.longform as longform_module
from voxora.tts_generation import MockTTSProvider, TTSProviderError

AUTH = {"Authorization": "Bearer admin-token"}
VIEWER_AUTH = {"Authorization": "Bearer viewer-token"}

LONG_SCRIPT = "\n\n".join(
    " ".join(["alpha bravo charlie delta echo foxtrot golf hotel india juliet"] * 120)
    for _ in range(4)
)


def _fail_payload(**overrides):
    payload = {
        "script": LONG_SCRIPT,
        "voice": "alloy",
        "response_format": "wav",
        "title": "Long story",
    }
    payload.update(overrides)
    return payload


class APIFailOnceProvider(MockTTSProvider):
    """Fails the 2nd generate call once, then always succeeds."""

    id = "mock"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._armed = True

    def generate(self, request):  # type: ignore[override]
        self.calls.append(request.script)
        if self._armed and len(self.calls) == 2:
            self._armed = False
            raise TTSProviderError(502, "upstream provider unavailable")
        return super().generate(request)


def _generate_long(client: TestClient, provider: Any = None, **overrides: Any):
    if provider is not None:
        import voxora.api as api_module

        original = api_module.build_tts_provider
        api_module.build_tts_provider = lambda settings: provider  # type: ignore[assignment]
        try:
            return client.post(
                "/api/v1/audio-assets/generate-long", json=_fail_payload(**overrides), headers=AUTH
            )
        finally:
            longform_module.build_tts_provider = original
    return client.post(
        "/api/v1/audio-assets/generate-long", json=_fail_payload(**overrides), headers=AUTH
    )


def test_generate_long_happy_path_ready_and_downloadable(client: TestClient) -> None:
    response = _generate_long(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert len([e for e in body["processing_history"] if e.get("kind") == "chunk"]) >= 2
    download = client.get(f"/api/v1/audio-assets/{body['id']}/download", headers=AUTH)
    assert download.status_code == 200
    assert download.headers["Content-Type"].startswith("audio/wav")


def test_generate_long_partial_failure_returns_201_processing(client: TestClient) -> None:
    response = _generate_long(client, provider=APIFailOnceProvider())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "processing"
    failed = [e for e in body["processing_history"] if e.get("kind") == "chunk" and e["state"] == "failed"]
    assert len(failed) == 1
    # never `ready` while a chunk is failed (anti-goal pin)
    assert body["status"] == "processing"


def test_retry_failed_completes_run(client: TestClient) -> None:
    created = _generate_long(client, provider=APIFailOnceProvider())
    asset_id = created.json()["id"]
    retried = client.post(f"/api/v1/audio-assets/{asset_id}/retry-failed", headers=AUTH)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "ready"


def test_retry_failed_on_normal_asset_is_409(client: TestClient) -> None:
    created = client.post(
        "/api/v1/audio-assets/generate",
        json={"script": "short script", "voice": "alloy", "response_format": "wav"},
        headers=AUTH,
    )
    asset_id = created.json()["id"]
    response = client.post(f"/api/v1/audio-assets/{asset_id}/retry-failed", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "longform_invalid"


def test_retry_failed_while_run_in_flight_is_409(client: TestClient) -> None:
    created = _generate_long(client)
    asset_id = created.json()["id"]
    longform_module.run_locks.add(asset_id)
    try:
        response = client.post(f"/api/v1/audio-assets/{asset_id}/retry-failed", headers=AUTH)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "run_in_flight"
    finally:
        longform_module.run_locks.discard(asset_id)


def test_generate_long_compressed_format_422(client: TestClient) -> None:
    response = _generate_long(client, response_format="mp3")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "longform_invalid"


def test_generate_long_caps(client: TestClient) -> None:
    assert _generate_long(client, max_chunk_chars=100).status_code == 422
    assert _generate_long(client, script="x" * 262145).status_code == 422


def test_download_processing_asset_is_409_still_processing(client: TestClient) -> None:
    created = _generate_long(client, provider=APIFailOnceProvider())
    asset_id = created.json()["id"]
    response = client.get(f"/api/v1/audio-assets/{asset_id}/download", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "still_processing"


def test_variant_of_processing_asset_is_422(client: TestClient) -> None:
    created = _generate_long(client, provider=APIFailOnceProvider())
    asset_id = created.json()["id"]
    response = client.post(
        f"/api/v1/audio-assets/{asset_id}/variants",
        json={"ops": [{"type": "normalize"}]},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert "not ready" in response.json()["error"]["message"]


def test_delete_longform_cleans_chunk_files(client: TestClient) -> None:
    created = _generate_long(client, provider=APIFailOnceProvider())
    asset_id = created.json()["id"]
    store_dir = Path(client.app.state.settings.audio_storage_path)
    tenant_dir = next(store_dir.iterdir())
    fake_chunks = sorted(tenant_dir.glob(f"{asset_id}_chunk_*"))
    assert fake_chunks, "failed run should leave chunk files"
    response = client.delete(f"/api/v1/audio-assets/{asset_id}", headers=AUTH)
    assert response.status_code == 200
    assert not list(tenant_dir.glob(f"{asset_id}_chunk_*"))
    assert not list(tenant_dir.glob(f"{asset_id}.*"))


def test_viewer_cannot_generate_long_or_retry(client: TestClient) -> None:
    response = client.post(
        "/api/v1/audio-assets/generate-long", json=_fail_payload(), headers=VIEWER_AUTH
    )
    assert response.status_code == 403
    retry = client.post("/api/v1/audio-assets/aud_x/retry-failed", headers=VIEWER_AUTH)
    assert retry.status_code == 403


def test_generate_long_unconfigured_provider_is_501(client: TestClient) -> None:
    import voxora.api as api_module

    def _unconfigured(settings: Any):
        raise ValueError("OPENAI_API_KEY is not configured; audio generation is unconfigured")

    original = api_module.build_tts_provider
    api_module.build_tts_provider = _unconfigured  # type: ignore[assignment]
    try:
        response = client.post(
            "/api/v1/audio-assets/generate-long", json=_fail_payload(), headers=AUTH
        )
    finally:
        api_module.build_tts_provider = original
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "audio_provider_unconfigured"
