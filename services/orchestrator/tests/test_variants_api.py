"""Variant HTTP API tests (roadmap M10 Phase C).

Pins the non-destructive contract at the HTTP edge: variants are new assets,
parent bytes never change, and refusals are honest (422 with reasons, 503
when the compiled engine is unavailable).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi.testclient import TestClient

import voxora.variants as variants_module

AUTH = {"Authorization": "Bearer admin-token"}


def _parent_asset(client: TestClient) -> Dict[str, Any]:
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
    return created.json()


def test_variant_roundtrip_lists_and_preserves_parent(
    client: TestClient, tmp_path: Path
) -> None:
    parent = _parent_asset(client)
    parent_path = Path(
        client.app.state.settings.audio_storage_path, parent["storage_path"]
    )
    before = parent_path.read_bytes()

    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={"ops": [{"type": "normalize", "target_dbfs": -12.0}]},
        headers=AUTH,
    )
    assert response.status_code == 201, response.text
    variant = response.json()
    assert variant["parent_asset_id"] == parent["id"]
    assert variant["format"] == "wav"
    history = variant["processing_history"]
    assert len(history) == 1
    assert history[0]["op"] == "normalize"
    assert history[0]["diagnostics"]["ops"][0]["target_reached"] is True

    listed = client.get(f"/api/v1/audio-assets/{parent['id']}/variants", headers=AUTH)
    assert listed.status_code == 200, listed.text
    assert listed.json()["pageInfo"]["total"] == 1
    assert listed.json()["data"][0]["id"] == variant["id"]

    assert parent_path.read_bytes() == before

    downloaded = client.get(
        f"/api/v1/audio-assets/{variant['id']}/download", headers=AUTH
    )
    assert downloaded.status_code == 200, downloaded.text
    assert len(downloaded.content) == variant["file_size"]


def test_variant_unknown_parent_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/audio-assets/aud_nope/variants",
        json={"ops": [{"type": "trim_silence"}]},
        headers=AUTH,
    )
    assert response.status_code == 404


def test_variant_unknown_op_is_422(client: TestClient) -> None:
    parent = _parent_asset(client)
    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={"ops": [{"type": "shred"}]},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_variant_stereo_and_voice_boost_combo_is_422(client: TestClient) -> None:
    parent = _parent_asset(client)
    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={
            "ops": [
                {"type": "channels", "to": "stereo"},
                {"type": "voice_boost"},
            ]
        },
        headers=AUTH,
    )
    assert response.status_code == 422


def test_variant_seven_ops_is_422(client: TestClient) -> None:
    parent = _parent_asset(client)
    ops = [{"type": "trim_silence"}] * 7
    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={"ops": ops},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_variant_out_of_range_target_is_422(client: TestClient) -> None:
    parent = _parent_asset(client)
    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={"ops": [{"type": "normalize", "target_dbfs": -3.0}]},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_variant_engine_unavailable_is_503(
    client: TestClient, monkeypatch
) -> None:
    parent = _parent_asset(client)

    def _boom(*args, **kwargs):
        raise RuntimeError("voice runtime extension unavailable")

    monkeypatch.setattr(variants_module, "create_variant", _boom)
    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={"ops": [{"type": "normalize", "target_dbfs": -12.0}]},
        headers=AUTH,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "audio_engine_unavailable"


def test_variant_requires_writer_role(client: TestClient) -> None:
    parent = _parent_asset(client)
    response = client.post(
        f"/api/v1/audio-assets/{parent['id']}/variants",
        json={"ops": [{"type": "trim_silence"}]},
        headers={"Authorization": "Bearer viewer-token"},
    )
    assert response.status_code == 403
