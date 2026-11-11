"""STT/TTS provider routing table tests (roadmap M7/P7).

The verified-quirks registry starts EMPTY by design: nothing in this repo
records observed per-provider per-language failures with dates, so every
combination must read UNVERIFIED in status output and refuse to be
attempted silently.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voxora.config import Settings, TenantPrincipal
from voxora.voice_providers import (
    LanguageRoutingTable,
    VerifiedQuirk,
    VerifiedQuirks,
    adapter_from_spec,
    provider_status,
    routing_table_from_settings,
    validate_routing_config,
)

AUTH = {"Authorization": "Bearer admin-token"}

_ADAPTERS = {
    "stt:whisper": {"name": "whisper", "modality": "stt", "capabilities": {"streaming": True}},
    "tts:piper": {"name": "piper", "modality": "tts", "capabilities": {}},
}


def _table(**chain_overrides) -> LanguageRoutingTable:
    chains = {"en": ["stt:whisper", "tts:piper"], "mixed": ["stt:whisper"]}
    chains.update(chain_overrides)
    return LanguageRoutingTable(
        adapters=[adapter_from_spec(spec) for spec in _ADAPTERS.values()],
        chains=chains,
    )


# ------------------------------------------------------------------ routing


def test_resolve_exact_mixed_and_none() -> None:
    table = _table()
    assert [adapter.id for adapter in table.resolve("en")] == ["stt:whisper", "tts:piper"]
    assert [adapter.id for adapter in table.resolve("HI")] == ["stt:whisper"]  # mixed fallback
    assert [adapter.id for adapter in table.resolve("de")] == ["stt:whisper"]  # mixed fallback
    assert table.resolve("de") is not None  # mixed chain IS the honest fallback
    # With no `mixed` chain configured, unrouted languages resolve to None.
    no_mixed = _table(mixed=[])
    assert no_mixed.resolve("de") is None


def test_resolve_empty_chain_does_not_inherit_mixed() -> None:
    table = _table(fr=[])
    assert table.resolve("fr") is None


# ------------------------------------------------------------------- quirks


def test_quirks_require_date_and_provenance() -> None:
    with pytest.raises(ValueError):
        VerifiedQuirks().add(
            VerifiedQuirk(provider="whisper", modality="stt", language="en", note="x", date="", provenance="p")
        )
    with pytest.raises(ValueError):
        VerifiedQuirks().add(
            VerifiedQuirk(provider="whisper", modality="stt", language="en", note="x", date="2026-01-01", provenance="")
        )
    with pytest.raises(ValueError):
        VerifiedQuirks().add(
            VerifiedQuirk(provider="whisper", modality="video", language="en", note="x", date="2026-01-01", provenance="p")
        )


def test_quirks_latest_observation_wins() -> None:
    quirks = VerifiedQuirks(
        [
            VerifiedQuirk(provider="whisper", modality="stt", language="en", note="old", date="2026-01-01", provenance="field"),
            VerifiedQuirk(provider="whisper", modality="stt", language="en", note="new", date="2026-02-01", provenance="field"),
        ]
    )
    assert quirks.lookup("whisper", "stt", "en").note == "new"
    assert quirks.label("whisper", "stt", "en") == "verified"
    assert quirks.label("whisper", "stt", "hi") == "UNVERIFIED"


def test_effective_adapter_verified_only() -> None:
    table = _table()
    assert table.effective_adapter("en", "stt") is None  # UNVERIFIED → refused
    table.quirks.add(
        VerifiedQuirk(provider="whisper", modality="stt", language="en", note="ok", date="2026-03-01", provenance="field")
    )
    assert table.effective_adapter("en", "stt").name == "whisper"


# ------------------------------------------------------------ attempt report


def test_attempt_report_refusals() -> None:
    report = _table().attempt_report("en", "stt")
    assert report["attemptable"] is False
    assert report["refusal"]["label"] == "UNVERIFIED"
    assert all(step["status"] == "UNVERIFIED" for step in report["steps"])

    tts_only = _table(mixed=["tts:piper"])
    report = tts_only.attempt_report("hi", "stt")
    assert report["refusal"]["reason"] == "no_step_for_modality"

    report = _table(mixed=[]).attempt_report("de", "stt")
    assert report["refusal"]["reason"] == "no_route"
    assert report["refusal"]["label"] == "no_route"


def test_quirk_step_carries_verification_metadata() -> None:
    table = _table()
    table.quirks.add(
        VerifiedQuirk(provider="whisper", modality="stt", language="en", note="verified note", date="2026-03-01", provenance="field log")
    )
    steps = table.attempt_report("en", "stt")["steps"]
    assert steps[0]["verified"] is True
    assert steps[0]["date"] == "2026-03-01"
    assert steps[0]["provenance"] == "field log"
    assert steps[0]["note"] == "verified note"
    assert steps[1]["verified"] is False and steps[1]["date"] is None


# ---------------------------------------------------------------- validation


def test_adapter_spec_validation() -> None:
    with pytest.raises(ValueError):
        adapter_from_spec({"modality": "stt"})
    with pytest.raises(ValueError):
        adapter_from_spec({"name": "whisper", "modality": "video"})
    with pytest.raises(ValueError):
        validate_routing_config({"wrong-key": _ADAPTERS["stt:whisper"]}, {})
    with pytest.raises(ValueError):
        validate_routing_config(dict(_ADAPTERS), {"en": ["stt:unknown"]})
    validate_routing_config(dict(_ADAPTERS), {"en": ["stt:whisper"]})  # ok


def test_routing_table_from_settings() -> None:
    settings = Settings(
        env="test",
        database_path="/tmp/x.db",
        secret_key="unit-test-secret-key-must-be-32-chars",
        voice_provider_adapters=dict(_ADAPTERS),
        voice_provider_chains={"en": ["stt:whisper"]},
    )
    table = routing_table_from_settings(settings)
    assert [adapter.id for adapter in table.resolve("en")] == ["stt:whisper"]
    with pytest.raises(ValueError):
        routing_table_from_settings(
            Settings(
                env="test",
                database_path="/tmp/x.db",
                secret_key="unit-test-secret-key-must-be-32-chars",
                voice_provider_adapters=dict(_ADAPTERS),
                voice_provider_chains={"en": ["stt:unknown"]},
            )
        )


# --------------------------------------------------------------------- HTTP


def _configured_client(tmp_path) -> TestClient:
    settings = Settings(
        env="test",
        database_path=str(tmp_path / "routing.db"),
        secret_key="unit-test-secret-key-must-be-32-chars",
        api_tokens={"admin-token": TenantPrincipal("tenant-a", "admin", "Admin")},
        cors_origins=["http://localhost:5173"],
        worker_enabled=False,
        voice_provider_adapters=dict(_ADAPTERS),
        voice_provider_chains={"en": ["stt:whisper", "tts:piper"]},
    )
    from voxora.api import create_app

    return TestClient(create_app(settings))


def test_status_labels_unverified(tmp_path) -> None:
    with _configured_client(tmp_path) as client:
        response = client.get("/api/v1/voice/providers", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True
    assert body["quirksRegistry"]["verifiedEntryCount"] == 0
    chains = {(chain["language"], chain["modality"]): chain for chain in body["chains"]}
    en_stt = chains[("en", "stt")]
    assert en_stt["attemptable"] is False
    assert en_stt["refusal"]["label"] == "UNVERIFIED"
    assert all(step["status"] == "UNVERIFIED" for step in en_stt["steps"])


def test_status_default_config_off(client) -> None:
    response = client.get("/api/v1/voice/providers", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False
    assert body["chains"] == []


def test_provider_status_facade() -> None:
    status = provider_status(_table())
    assert status["quirksRegistry"]["verifiedEntryCount"] == 0
    assert status["chains"]
