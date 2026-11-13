"""Voice Boost settings resolution (roadmap M9).

Global per-tenant defaults (config-in-db `voice_boost_settings`) plus optional
per-session overrides resolve into the validated settings document
`{"profile", "overrides", "enabled"}` — the exact shape the Rust DSP seam
(`voxora_voice_runtime.voice_boost_config_from_json`, built from
`crates/voice-runtime/src/dsp.rs::config_from_settings_json`) consumes.

Capability honesty: the Voice Boost DSP executes inside the Rust media engine
(`crates/voice-runtime/src/dsp.rs`), tested there. The live-call application
point waits on the M7 media transport (`MediaTransport` socket backend) —
until that mounts, no claim is made that live calls are enhanced.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .db import Database

VOICE_BOOST_PROFILES = ("natural", "clear", "studio")


def builtin_voice_boost_settings() -> Dict[str, Any]:
    """Config-in-db seed source for global Voice Boost defaults.

    Mirrors `builtin_model_profiles`: read at seed time; once seeded, the DB
    row is the source of truth and re-seeding never overwrites operator edits.
    """
    return {"profile": "clear", "overrides": {}, "enabled": True}


def resolve_voice_boost_settings(
    db: Database,
    tenant_id: str,
    session_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve global defaults + one session override into settings JSON.

    Seeds the builtin row once for a fresh tenant. The merge is
    stored-defaults + current-override ONLY: a session override never
    persists into the tenant's global defaults (the next session resolves
    from the stored row, not from the previous call).
    """
    stored = db.get_voice_boost_settings(tenant_id)
    if stored is None:
        builtin = builtin_voice_boost_settings()
        db.seed_voice_boost_settings(tenant_id, builtin["profile"], builtin["overrides"])
        resolved: Dict[str, Any] = {
            "profile": builtin["profile"],
            "overrides": dict(builtin["overrides"]),
            "enabled": builtin["enabled"],
        }
    else:
        resolved = {
            "profile": stored["profile"],
            "overrides": dict(stored.get("config") or {}),
            "enabled": bool(stored.get("enabled", True)),
        }
    if session_override:
        if "profile" in session_override:
            resolved["profile"] = session_override["profile"]
        if "enabled" in session_override:
            resolved["enabled"] = bool(session_override["enabled"])
        if "overrides" in session_override:
            resolved["overrides"] = {**resolved["overrides"], **session_override["overrides"]}
    return resolved
