# Voice Boost settings surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Voice Boost configurable end-to-end: tenant-level global defaults (config-in-db) + per-session overrides + a master switch, validated on both the Python API and the Rust DSP seam.

**Architecture:** Follows the `model_profiles` config-in-db pattern: a `voice_boost_settings` table (one global row per tenant, seeded with the CLEAR profile) plus a `voice_boost` override column on `voice_sessions`. Resolution (defaults + override) happens at read time in `voice_boost.py`; the same settings JSON validates into a Rust `VoiceBoostConfig` through a new pybridge function, which is the constructor the media transport will call when it mounts (M7 open seam).

**Tech Stack:** FastAPI + pydantic (orchestrator), sqlite/postgres via the `Database` seam, Rust/PyO3 (`voxora_voice_runtime`, `make py-voice`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-13-voice-boost-settings-design.md`

## Global Constraints

- Existing work rules: full orchestrator suite green (SQLite + Postgres) before each commit; `ruff` and `mypy` clean on changed files; conventional commits.
- Rust: `cargo test` must stay green in BOTH feature states (plain and `--features pyo3-bindings`); `nnnoiseless` default-features stay off.
- Capability-honesty contract: no claim that live calls are enhanced — the DSP executes inside the Rust media engine (tested) but the live socket transport is still M7-open. State this in docstrings/comments, not roadmap prose.
- Extra fields forbidden on all request models (`model_config = {"extra": "forbid"}`).
- Profile names: `natural | clear | studio` (lowercase, snake_case). Master switch in API space is `enabled: bool` (default True); `enabled: false` maps to Rust `bypass: true`.
- Control keys (exact): `noise_suppression`, `ns_strength`, `agc`, `agc_target_dbfs`, `agc_max_boost_db`, `agc_max_cut_db`, `normalize`, `eq`, `de_ess`, `de_ess_strength`, `clipping_watch`.
- Ranges (reject, never clamp at the API boundary): `ns_strength`, `de_ess_strength` ∈ [0.0, 1.0]; `agc_target_dbfs` ∈ [-60.0, -6.0]; `agc_max_boost_db` ∈ (0.0, 48.0]; `agc_max_cut_db` ∈ (0.0, 48.0].

## Review Focus

- Unknown override key (e.g. `reverb: true`) silently accepted → must 422. Test: Task 4 Step 1 (`test_put_rejects_unknown_control_key`).
- Out-of-range strength (e.g. `ns_strength: 1.5`) silently clamped to 1.0, reinterpreting operator intent → must 422. Test: Task 4 Step 1 (`test_put_rejects_out_of_range_strength`).
- Absurd AGC target (e.g. `agc_target_dbfs: 0` = guaranteed clipping) → must 422 (range check covers it). Test: Task 4 Step 1 (`test_put_rejects_out_of_range_strength`).
- `enabled: false` with overrides present — operator expects bypass to win and overrides to be preserved-but-inert. Test: Task 3 Step 1 (`test_resolution_disabled_bypasses_overrides`) + Rust test (`disabled_maps_to_bypass`).
- First GET before any PUT returns seeded defaults rather than an empty/404 body. Test: Task 4 Step 1 (`test_get_seeds_and_returns_defaults`).

---

### Task 1: Rust Voice Boost settings seam

**Files:**
- Modify: `crates/voice-runtime/src/dsp.rs` (add parsing/validation near the `VoiceBoostConfig` impl, ~line 530)
- Modify: `crates/voice-runtime/src/pybridge.rs` (new `#[pyfunction]`, register in `voxora_voice_runtime` module at line ~345)
- Test: `crates/voice-runtime/src/dsp.rs` tests module (line ~823)

**Interfaces:**
- Consumes: `VoiceBoostConfig::for_profile`, `with_*` builders (dsp.rs:478-530), `VoiceBoostProfile` (dsp.rs:438).
- Produces: `pub fn config_from_settings_json(settings_json: &str) -> Result<VoiceBoostConfig, String>` in `dsp.rs`; pybridge `#[pyfunction] voice_boost_config_from_json(settings_json: &str) -> PyResult<String>` returning the resolved config as canonical JSON (`serde_json::to_string`).

- [ ] **Step 1: Write the failing Rust tests**

Test names and assertions (in the `dsp.rs` tests module):

```rust
#[test]
fn settings_json_defaults_to_clear_enabled() {
    let config = config_from_settings_json("{}").expect("empty settings valid");
    assert_eq!(config, VoiceBoostConfig::for_profile(VoiceBoostProfile::Clear));
    assert!(!config.bypass);
}

#[test]
fn settings_json_applies_profile_and_overrides() {
    let json = r#"{"profile":"studio","overrides":{"ns_strength":0.9,"eq":false}}"#;
    let config = config_from_settings_json(json).expect("valid");
    let expected = VoiceBoostConfig::for_profile(VoiceBoostProfile::Studio)
        .with_noise_suppression(true, 0.9)
        .with_eq(false);
    assert_eq!(config, expected);
}

#[test]
fn disabled_maps_to_bypass() {
    let json = r#"{"enabled":false,"profile":"clear","overrides":{"ns_strength":0.5}}"#;
    let config = config_from_settings_json(json).expect("valid");
    assert!(config.bypass);
    // Overrides stay resolved (inert while disabled) so re-enabling is lossless.
    assert_eq!(config.ns_strength, 0.5);
}

#[test]
fn settings_json_rejects_unknown_profile_and_keys_and_ranges() {
    assert!(config_from_settings_json(r#"{"profile":"loud"}"#).is_err());
    assert!(config_from_settings_json(r#"{"overrides":{"reverb":true}}"#).is_err());
    assert!(config_from_settings_json(r#"{"overrides":{"ns_strength":1.5}}"#).is_err());
    assert!(config_from_settings_json(r#"{"overrides":{"agc_target_dbfs":0.0}}"#).is_err());
    assert!(config_from_settings_json(r#"{"overrides":{"ns_strength":"high"}}"#).is_err());
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test -p voice-runtime config_from_settings -- --nocapture` (workdir `crates/voice-runtime`)
Expected: compile error — `config_from_settings_json` not found.

- [ ] **Step 3: Implement `config_from_settings_json` in `dsp.rs`**

Signature `pub fn config_from_settings_json(settings_json: &str) -> Result<VoiceBoostConfig, String>`. Parse via `serde_json::from_str::<serde_json::Value>`. Accepted shape: `{"enabled": bool (default true), "profile": str (default "clear"), "overrides": object (default empty)}`. Map profile via serde (`VoiceBoostProfile` already deserializes snake_case). Validate overrides: unknown key → `Err`; `ns_strength`/`de_ess_strength` must be numbers in [0.0, 1.0]; `agc_target_dbfs` number in [-60.0, -6.0]; `agc_max_boost_db`/`agc_max_cut_db` numbers in (0.0, 48.0]; boolean keys must be booleans. Build from `for_profile`, then apply each override through the existing `with_*` builders (pass the current enabled state for the paired bool of `with_noise_suppression`/`with_agc`/`with_de_ess`). `enabled: false` → `.with_bypass(true)` applied LAST. Error strings must name the offending key and value.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test -p voice-runtime` (workdir `crates/voice-runtime`)
Expected: all green including the 5 new tests.

- [ ] **Step 5: Write the failing pybridge test**

In `pybridge.rs` tests module, add:

```rust
#[test]
fn voice_boost_config_json_round_trips_through_seam() {
    let resolved = _py::voice_boost_config_from_json(
        r#"{"profile":"natural","overrides":{"ns_strength":0.4}}"#,
    )
    .expect("valid settings");
    let parsed: serde_json::Value = serde_json::from_str(&resolved).unwrap();
    assert_eq!(parsed["ns_strength"], 0.4);
    assert_eq!(parsed["eq"], false);
    assert_eq!(
        _py::voice_boost_config_from_json(r#"{"profile":"loud"}"#).unwrap_err().to_string(),
        "ValueError"  // PyValueError class; message names the profile
    );
}
```

(If the assert on the error string proves brittle, assert only `is_err()` and that the message contains "loud".)

- [ ] **Step 6: Implement the pybridge function**

In `pybridge.rs` `_py` module: `#[pyfunction] #[pyo3(signature = (settings_json))] fn voice_boost_config_from_json(settings_json: &str) -> PyResult<String>` — call `crate::dsp::config_from_settings_json`, map `Err` to `PyValueError::new_err(msg)`, return `serde_json::to_string(&config).expect(...)`. Register in the `#[pymodule]` with `m.add_function(wrap_pyfunction!(voice_boost_config_from_json, m)?)?;`.

- [ ] **Step 7: Run both feature states, then commit**

Run: `cargo test` then `cargo test --features pyo3-bindings` (workdir `crates/voice-runtime`)
Expected: green both.

```bash
git add crates/voice-runtime/src/dsp.rs crates/voice-runtime/src/pybridge.rs
git commit -m "feat(m9): Voice Boost settings JSON seam — validated config resolution in dsp + pybridge"
```

### Task 2: DB storage — voice_boost_settings table + session override column

**Files:**
- Modify: `services/orchestrator/src/voxora/db.py` (SCHEMA after `model_profiles` DDL ~line 399; new methods after `get_model_profile` ~line 577; `_migrate_voice_sessions` line 482; `create_voice_session` line 852; `update_voice_session` — check its column list)
- Modify: `services/orchestrator/src/voxora/pg.py` (`initialize`, after line 122)
- Test: `services/orchestrator/tests/test_voice_boost_settings.py` (create; storage-compat tests parametrize via the existing `test_storage_compat.py` fixtures — check how it builds both backends and copy that parametrization)

**Interfaces:**
- Consumes: `SCHEMA` string, `Database._write()/_read()/_decode`, `new_id(...)`, `iso()`, `JSON_COLUMNS` (`config` is already a member — the JSON column must be named `config`).
- Produces (used by Tasks 3–4):
  - `Database.get_voice_boost_settings(tenant_id: str) -> Optional[Dict[str, Any]]` — decoded row `{"profile": str, "config": dict, "updated_at": str}` or None.
  - `Database.put_voice_boost_settings(tenant_id: str, profile: str, config: Dict[str, Any]) -> Dict[str, Any]` — UPSERT on (tenant_id), returns the decoded row.
  - `Database.seed_voice_boost_settings(tenant_id: str, profile: str, config: Dict[str, Any]) -> None` — INSERT OR IGNORE.
  - `voice_sessions.voice_boost` column: TEXT NOT NULL DEFAULT '{}' — the raw session override JSON (caller-sent); stored via `create_voice_session`.

- [ ] **Step 1: Write the failing storage tests**

In `tests/test_voice_boost_settings.py`, parametrized over both backends like `test_storage_compat.py`:

```python
def test_seed_then_get_then_put_roundtrip(backend) -> None:  # naming per existing fixtures
    db.seed_voice_boost_settings(tenant_id, "clear", {})
    row = db.get_voice_boost_settings(tenant_id)
    assert row["profile"] == "clear" and row["config"] == {}
    updated = db.put_voice_boost_settings(tenant_id, "studio", {"ns_strength": 0.9})
    assert updated["profile"] == "studio"
    assert db.get_voice_boost_settings(tenant_id)["config"] == {"ns_strength": 0.9}

def test_get_unset_tenant_returns_none(backend) -> None:
    assert db.get_voice_boost_settings("no-such-tenant") is None

def test_seed_is_idempotent(backend) -> None:
    db.seed_voice_boost_settings(tenant_id, "clear", {})
    db.seed_voice_boost_settings(tenant_id, "studio", {"eq": False})
    assert db.get_voice_boost_settings(tenant_id)["profile"] == "clear"
```

Also one test asserting the session column persists: `db.create_voice_session(..., {"voice_boost": {"profile": "natural", "overrides": {"eq": False}}, ...})` → `db.get_voice_session(tenant, id)["voice_boost"] == {"profile": "natural", "overrides": {"eq": False}}` (JSON decoded via `JSON_COLUMNS`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_voice_boost_settings.py -v` (workdir `services/orchestrator`)
Expected: FAIL — attribute/method not found.

- [ ] **Step 3: Implement**

1. SCHEMA addition after `model_profiles` DDL:
   `CREATE TABLE IF NOT EXISTS voice_boost_settings (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, config TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);`
   (no extra index needed — tenant_id is UNIQUE).
2. Methods per the Interfaces block; `put_` uses UPSERT (`INSERT ... ON CONFLICT(tenant_id) DO UPDATE SET profile=?, config=?, updated_at=?`).
3. `voice_sessions.voice_boost TEXT NOT NULL DEFAULT '{}'` in SCHEMA + SQLite `_migrate_voice_sessions` (add `"voice_boost"` to the column list, definition `ADD COLUMN voice_boost TEXT NOT NULL DEFAULT '{}'`) + `pg.py` `initialize` ALTER (`ALTER TABLE voice_sessions ADD COLUMN IF NOT EXISTS voice_boost TEXT NOT NULL DEFAULT '{}'`).
4. Extend `create_voice_session` INSERT with the `voice_boost` column (read `values.get("voice_boost", {})`, json-encode); check `update_voice_session` — if its UPDATE lists columns explicitly, add `voice_boost` there too.

- [ ] **Step 4: Run tests to verify they pass, then full suite**

Run: `pytest tests/test_voice_boost_settings.py tests/test_storage_compat.py -v` then the full suite.
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/voxora/db.py services/orchestrator/src/voxora/pg.py services/orchestrator/tests/test_voice_boost_settings.py
git commit -m "feat(m9): voice_boost_settings config-in-db table + per-session voice_boost column (both backends)"
```

### Task 3: voice_boost.py — models, resolution, session intake plumbing

**Files:**
- Create: `services/orchestrator/src/voxora/voice_boost.py`
- Modify: `services/orchestrator/src/voxora/models.py` (`VoiceBoostOverride`, `VoiceBoostSettingsUpdate`; `VoiceSessionCreate` gains `voice_boost: Optional[VoiceBoostOverride] = None`)
- Modify: `services/orchestrator/src/voxora/api.py` (`create_voice_session`: validate + store the override; `_voice_session_view` (line ~199): expose resolved settings)
- Test: `services/orchestrator/tests/test_voice_boost_settings.py` (module tests appended)

**Interfaces:**
- Consumes: Task 2 db methods; `JSON_COLUMNS` decode.
- Produces:
  - `VOICE_BOOST_PROFILES: tuple[str, ...] = ("natural", "clear", "studio")`
  - `builtin_voice_boost_settings() -> Dict[str, Any]` — `{"profile": "clear", "overrides": {}, "enabled": True}` (mirrors `builtin_model_profiles`).
  - `resolve_voice_boost_settings(db, tenant_id: str, session_override: Optional[Dict[str, Any]]) -> Dict[str, Any]` — seed-once when the tenant row is missing; merge `session_override` over the stored global defaults key-by-key (override keys replace defaults; absent keys inherit); returns `{"profile", "overrides", "enabled"}` — the exact JSON shape Task 1's seam consumes after the API layer adds `"enabled"`.
  - Pydantic models in `models.py`: `VoiceBoostOverride` (`model_config = {"extra": "forbid"}`; fields exactly the 11 control keys from Global Constraints, all `Optional`, with a model-level validator enforcing the ranges — reject, never clamp) and `VoiceBoostSettingsUpdate` (`extra="forbid"`; `enabled: bool = True`, `profile: Literal["natural", "clear", "studio"] = "clear"`, `overrides: VoiceBoostOverride = VoiceBoostOverride()`).

- [ ] **Step 1: Write the failing module tests**

```python
def test_builtin_defaults_are_clear_enabled() -> None: ...
    # assert builtin_voice_boost_settings() == {"profile": "clear", "overrides": {}, "enabled": True}

def test_resolution_seeds_once_and_merges_override() -> None:
    # fresh tenant → seeds; returns clear/enabled; session_override {"overrides": {"eq": False}}
    # merges: profile stays "clear", overrides == {"eq": False}
    # a SECOND resolve with override {"profile": "natural"} returns profile "natural",
    # eq inherited False proves the merge is over stored defaults, not last-call state

def test_resolution_disabled_bypasses_overrides() -> None:
    # session_override {"enabled": False, "overrides": {"ns_strength": 0.5}}
    # → result {"enabled": False, ... overrides preserved} (inert but lossless)

def test_override_rejects_unknown_key_and_bad_ranges() -> None:
    # VoiceBoostOverride.model_validate({"reverb": True}) raises
    # VoiceBoostOverride.model_validate({"ns_strength": 1.5}) raises
    # VoiceBoostOverride.model_validate({"agc_target_dbfs": -3.0}) raises
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_voice_boost_settings.py -k resolution -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `voice_boost.py` + models**

Module docstring must carry the honesty note: the DSP executes inside the Rust media engine (`crates/voice-runtime/src/dsp.rs`); live-call application waits on the M7 media transport — no claim that live calls are enhanced today. Wire `VoiceSessionCreate.voice_boost` through `create_voice_session` in api.py: validate with `VoiceBoostOverride`, store `payload.voice_boost.model_dump(exclude_none=True)` (or `{}`), and expose in `_voice_session_view` as `"voice_boost": resolve_voice_boost_settings(...)`.

- [ ] **Step 4: Run tests to verify they pass, then commit**

Run: `pytest tests/test_voice_boost_settings.py -v`
Expected: green.

```bash
git add services/orchestrator/src/voxora/voice_boost.py services/orchestrator/src/voxora/models.py services/orchestrator/src/voxora/api.py services/orchestrator/tests/test_voice_boost_settings.py
git commit -m "feat(m9): Voice Boost settings resolution — global defaults + per-session override merge"
```

### Task 4: API routes — GET/PUT settings + intake validation

**Files:**
- Modify: `services/orchestrator/src/voxora/api.py` (routes near `/api/v1/voice/providers` line ~1149)
- Test: `services/orchestrator/tests/test_voice_boost_settings.py` (HTTP tests appended; copy the `AUTH`/client conventions from `test_voice_providers.py`)

**Interfaces:**
- Consumes: Task 3 module (`resolve_voice_boost_settings`, models), `_require_writer`, `app.state.db`, `ApiError`, `app.state.db.audit`.
- Produces:
  - `GET /api/v1/voice/boost/settings` → resolved global defaults `{"profile", "overrides", "enabled"}` (200).
  - `PUT /api/v1/voice/boost/settings` (writer-gated, body `VoiceBoostSettingsUpdate`) → persists via `put_voice_boost_settings`, audits `voice_boost.settings_updated`, returns the stored document.
  - `POST /api/v1/voice-sessions` accepts optional `voice_boost` override; response `session.voice_boost` is the resolved settings.

- [ ] **Step 1: Write the failing HTTP tests**

```python
def test_get_seeds_and_returns_defaults(client) -> None:
    # GET → 200 {"profile": "clear", "overrides": {}, "enabled": True}

def test_put_updates_and_audits(client) -> None:
    # PUT {"profile": "studio", "overrides": {"ns_strength": 0.9}} → 200;
    # second GET returns studio/0.9; audit row voice_boost.settings_updated exists

def test_put_rejects_unknown_control_key(client) -> None:
    # PUT {"overrides": {"reverb": True}} → 422

def test_put_rejects_out_of_range_strength(client) -> None:
    # PUT {"overrides": {"ns_strength": 1.5}} → 422
    # PUT {"overrides": {"agc_target_dbfs": -3.0}} → 422

def test_put_rejects_unknown_profile(client) -> None:
    # PUT {"profile": "loud"} → 422

def test_session_override_resolves_in_view(client) -> None:
    # PUT global {"profile": "studio"}; POST /voice-sessions with
    # voice_boost {"overrides": {"eq": False}} → response session.voice_boost
    # profile "studio" (inherited) + eq False (overridden)

def test_session_intake_rejects_invalid_override(client) -> None:
    # POST /voice-sessions with voice_boost {"profile": "loud"} → 422
```

Note: use the same minimal valid `VoiceSessionCreate` body that `test_voice_turn_loop_http.py` line ~30 constructs; use the plain `client` fixture (mock provider) unless the override path requires the voice-runtime one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_voice_boost_settings.py -k "http or put or get or session" -v`
Expected: FAIL — 404 on the GET/PUT route.

- [ ] **Step 3: Implement the routes**

Add both routes beside the `/api/v1/voice/providers` route. PUT is `_require_writer`-gated and writes the audit row. `GET` calls `resolve_voice_boost_settings(db, tenant, None)` (which seeds once). Session intake: `payload.voice_boost` validates via pydantic already; store it; resolve for the view.

- [ ] **Step 4: Run tests to verify they pass, then full suite + lint**

Run: full orchestrator suite; `ruff check src tests`; `mypy src`.
Expected: all green; mypy clean for changed modules.

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/voxora/api.py services/orchestrator/tests/test_voice_boost_settings.py
git commit -m "feat(m9): /voice/boost/settings GET+PUT and per-session voice_boost override intake"
```

### Task 5: Python ↔ Rust seam test (wheel-built path)

**Files:**
- Create: `services/orchestrator/tests/test_voice_boost_seam.py`
- Modify: `Makefile` — only if `py-voice` already rebuilds on change; verify, do not edit otherwise

**Interfaces:**
- Consumes: pybridge `voice_boost_config_from_json` (Task 1); `resolve_voice_boost_settings` (Task 3); `pytest.importorskip` pattern from `test_voice_runtime_seam.py:102`.
- Produces: proof that the orchestrator's settings JSON drives the real Rust config constructor (the function the media transport will call).

- [ ] **Step 1: Write the test**

```python
def test_resolved_settings_drive_rust_config() -> None:
    pytest.importorskip(
        "voxora_voice_runtime",
        reason="voxora_voice_runtime extension not built (run `make py-voice`)",
    )
    import json
    import voxora_voice_runtime

    resolved = {"enabled": True, "profile": "natural", "overrides": {"ns_strength": 0.4}}
    config = json.loads(
        voxora_voice_runtime.voice_boost_config_from_json(json.dumps(resolved))
    )
    assert config["ns_strength"] == 0.4
    assert config["eq"] is False  # NATURAL preset
    assert config["bypass"] is False

def test_disabled_settings_map_to_rust_bypass() -> None:
    ...  # enabled False → config["bypass"] is True

def test_seam_rejects_the_same_bad_input_the_api_rejects() -> None:
    ...  # {"overrides": {"reverb": True}} raises ValueError naming "reverb"
```

- [ ] **Step 2: Build the wheel and run**

Run: `make py-voice` then `pytest tests/test_voice_boost_seam.py -v` (workdir `services/orchestrator`)
Expected: 3 PASS against the real extension.

- [ ] **Step 3: Run the full suite in both feature states and commit**

Run: `cargo test` and `cargo test --features pyo3-bindings` (crates/voice-runtime); full Python suite.
Expected: green.

```bash
git add services/orchestrator/tests/test_voice_boost_seam.py
git commit -m "test(m9): Voice Boost settings seam — orchestrator JSON drives Rust config via real extension"
```

### Task 6: Roadmap + audit doc update

**Files:**
- Modify: `docs/roadmap.md` (M9 "Voice Boost profiles" item → [x] with honest open-item note)
- Modify: `docs/implementation-audit.md` (append a dated section)

- [ ] **Step 1: Update docs**

Mark the roadmap item done: "NATURAL/CLEAR/STUDIO presets + independent per-control overrides, global defaults and per-session settings, master switchable off (6a1b17f engine; settings surface this commit). *Still open: live-call application waits on the M7 media transport; console Boost panel ships with the dashboard item.*" Audit section mirrors the verified test counts and the honesty boundary.

- [ ] **Step 2: Commit**

```bash
git add docs/roadmap.md docs/implementation-audit.md
git commit -m "docs: mark M9 Voice Boost profiles complete with honest open-item notes"
```
