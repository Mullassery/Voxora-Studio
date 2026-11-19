"""Audio Generation HTTP API tests (roadmap M10 Phase B).

Two pins live here:
1. The M7/P9 boundary stands: `/voice-sessions/{id}/say` stays 404 — spoken
   call content comes only from the agent or the instruction store. Generated
   audio assets are an operator-asset surface and never enter the call loop.
2. Capability honesty mirrors voice_providers: unconfigured providers surface
   `live: false` / `simulated` / 501, never fake success.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer admin-token"}


def test_list_audio_assets_empty(client: TestClient) -> None:
    response = client.get("/api/v1/audio-assets", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == {"data": [], "pageInfo": {"limit": 50, "offset": 0, "total": 0, "hasNextPage": False}}


def test_generate_and_download_asset_roundtrip(client: TestClient) -> None:
    created = client.post(
        "/api/v1/audio-assets/generate",
        json={
            "script": "Hello, welcome to Voxora Studio.",
            "voice": "alloy",
            "response_format": "wav",
            "title": "Welcome",
        },
        headers=AUTH,
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["provider"] == "mock"
    assert asset["status"] == "ready"
    assert asset["duration_seconds"] is not None

    listed = client.get("/api/v1/audio-assets", headers=AUTH).json()
    assert listed["pageInfo"]["total"] == 1
    assert listed["data"][0]["id"] == asset["id"]

    downloaded = client.get(f"/api/v1/audio-assets/{asset['id']}/download", headers=AUTH)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"] == "audio/wav"
    assert 'filename="Welcome.wav"' in downloaded.headers["content-disposition"]
    assert len(downloaded.content) == asset["file_size"]


def test_generation_rejects_bad_script_and_speed(client: TestClient) -> None:
    too_long = client.post(
        "/api/v1/audio-assets/generate",
        json={"script": "x" * 4097, "voice": "alloy"},
        headers=AUTH,
    )
    assert too_long.status_code == 422
    bad_speed = client.post(
        "/api/v1/audio-assets/generate",
        json={"script": "hi", "voice": "alloy", "speed": 9.0},
        headers=AUTH,
    )
    assert bad_speed.status_code == 422


def test_delete_removes_asset_and_file(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/audio-assets/generate",
        json={"script": "Delete me", "voice": "alloy", "response_format": "wav"},
        headers=AUTH,
    ).json()
    deleted = client.delete(f"/api/v1/audio-assets/{asset['id']}", headers=AUTH)
    assert deleted.status_code in (200, 204), deleted.text
    assert (
        client.get(f"/api/v1/audio-assets/{asset['id']}/download", headers=AUTH).status_code
        == 404
    )


def test_provider_endpoint_is_capability_honest(client: TestClient) -> None:
    response = client.get("/api/v1/audio/providers", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured_provider"] == "mock"
    assert body["simulated"] is True
    assert body["live"] is False


def test_say_route_stays_unimplemented_p9_boundary(client: TestClient) -> None:
    asset = client.post(
        "/api/v1/audio-assets/generate",
        json={"script": "Not for calls", "voice": "alloy", "response_format": "wav"},
        headers=AUTH,
    ).json()
    response = client.post(
        f"/api/v1/voice-sessions/{asset['id']}/say",
        json={"text": "anything"},
        headers=AUTH,
    )
    assert response.status_code == 404
