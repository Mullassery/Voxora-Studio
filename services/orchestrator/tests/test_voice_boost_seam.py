"""Voice Boost settings Python<->Rust seam tests (roadmap M9).

Proves the orchestrator's resolved settings JSON drives the REAL Rust config
constructor (`voxora_voice_runtime.voice_boost_config_from_json`, built from
`crates/voice-runtime/src/dsp.rs::config_from_settings_json`) — the function
the media transport will call when it mounts (M7 open seam). Skips honestly
when the extension is not built.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip(
    "voxora_voice_runtime",
    reason="voxora_voice_runtime extension not built (run `make py-voice`)",
)

import voxora_voice_runtime


def test_resolved_settings_drive_rust_config() -> None:
    resolved = {"enabled": True, "profile": "natural", "overrides": {"ns_strength": 0.4}}
    config = json.loads(
        voxora_voice_runtime.voice_boost_config_from_json(json.dumps(resolved))
    )
    assert config["ns_strength"] == 0.4
    assert config["eq"] is False  # NATURAL preset
    assert config["bypass"] is False


def test_disabled_settings_map_to_rust_bypass() -> None:
    resolved = {
        "enabled": False,
        "profile": "clear",
        "overrides": {"ns_strength": 0.5},
    }
    config = json.loads(
        voxora_voice_runtime.voice_boost_config_from_json(json.dumps(resolved))
    )
    assert config["bypass"] is True
    assert config["ns_strength"] == 0.5  # resolved but inert while disabled


def test_seam_rejects_the_same_bad_input_the_api_rejects() -> None:
    with pytest.raises(ValueError) as excinfo:
        voxora_voice_runtime.voice_boost_config_from_json(
            json.dumps({"overrides": {"reverb": True}})
        )
    assert "reverb" in str(excinfo.value)
