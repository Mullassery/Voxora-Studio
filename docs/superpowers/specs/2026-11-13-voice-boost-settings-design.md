# Voice Boost settings surface — design (small)

Approves the M9 "Voice Boost profiles" finishing chunk: global defaults +
per-session settings + master switch, wired to the existing Rust DSP engine.

## Intent

The Voice Boost engine (NATURAL/CLEAR/STUDIO presets, 11 independent controls,
bypass master switch) already executes inside the Rust media engine
(`crates/voice-runtime/src/dsp.rs`, commit `6a1b17f`). Nothing orchestrator-side
can configure it. This chunk adds the settings surface: config-in-db storage,
resolution, API, and a validated JSON seam into the engine — following the
established `model_profiles` config-in-db pattern.

## Approaches considered

1. **Env-only settings** (read config from environment at session start):
   rejected — no per-tenant defaults, no per-session overrides, no audit trail;
   contradicts the config-in-db pattern every other tunable uses.
2. **DB storage + API + Rust JSON seam** (chosen): tenant global row
   (`voice_boost_settings`: profile, enabled master switch, overrides config),
   per-session override column on `voice_sessions`, resolution at read time,
   PUT persists + audits, and a Rust `config_from_settings_json` constructor
   the media transport will call when it mounts. Approved 2026-11-13.
3. **Full dashboard panel in this chunk**: deferred to the M9 console item —
   the API surface is the dependency, the panel rides on it later.

## Binding decisions

- Validation rejects, never clamps, at both boundaries (API 422 and Rust
  `Err`): unknown control keys, unknown profiles, wrong types, out-of-range
  numbers (`ns_strength`/`de_ess_strength` 0..=1, `agc_target_dbfs`
  [-60, -6], `agc_max_boost_db`/`agc_max_cut_db` (0, 48]).
- `enabled: false` maps to Rust `bypass: true` (original audio preserved
  byte-identically); overrides stay resolved-but-inert so re-enabling is
  lossless.
- Merge semantics: resolution = stored global defaults + current session
  override ONLY; a session override never persists into global defaults.
  Global PUT is full-replace; session intake merges.
- Per-session boost is mutable via `update_voice_session` (create-time default
  `{}` = inherit global).
- Honesty boundary: the DSP executes in the Rust media engine (tested there);
  live-call application waits on the M7 media transport — no claim that live
  calls are enhanced. The M7/P9 contract stands: `/voice-sessions/{id}/say`
  stays 404; spoken call content comes only from the agent/instruction store.

## Left to the builder

Exact file placement, method names, test names — governed by the plan
(`docs/superpowers/plans/2026-11-13-voice-boost-settings.md`) and executed
2026-11-13 (commits `3a4d5a2`..`6d62e61`; final review found no Critical
findings, one Important fixed in `6d62e61`).
