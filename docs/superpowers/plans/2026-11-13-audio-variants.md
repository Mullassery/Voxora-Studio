# Audio Generation Phase C (Variants) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators derive non-destructive variants (normalize, trim silence, resample, channel conversion, Voice Boost profile) from generated WAV assets — new asset rows, parents byte-identical, one DSP layer reused from M9.

**Architecture:** One new Rust offline pyfunction (`audio_postprocess`) dispatching to the existing `VoiceBoostStage` and a NEW real windowed-sinc resampler; Python (`variants.py`) owns WAV I/O, trim/channel ops, variant rows and API routes. Seam tests drive the real maturin-built wheel.

**Tech Stack:** Rust (pyo3, existing `crates/voice-runtime`), Python/FastAPI orchestrator, stdlib `wave`, SQLite+Postgres parity.

**Spec:** `docs/superpowers/specs/2026-11-13-audio-variants-design.md` (reads with this plan; the 5 settled builder-check calls live there and are binding here).

## Global Constraints

- No ffmpeg / external audio binaries; never convert by renaming extensions.
- Parents must be WAV, 16-bit (`sampwidth == 2`), mono or stereo; everything else → 422 honest refusal.
- Variants always written as 16-bit WAV at resulting rate/channels.
- Parent file bytes must never change; original row never mutated.
- Voice Boost is mono-only and 16 kHz-hardwired internally: transparent 16k round-trip via the real resampler, recorded in diagnostics; stereo + voice_boost in one request → 422.
- Variant voice_boost resolves op params over builtin `VoiceBoostConfig::for_profile` ONLY — tenant stored settings never consulted.
- Extension unavailable → 503, never a fake pass-through.
- Ops envelope (Rust side): `{"rate": <u32>, "ops": [{"type": ..., params...}]}`; cap 6 ops, applied in listed order; diagnostics JSON returned.
- Provider/error details sanitized; never echo Rust panic/internals to clients.
- Repo bar per commit: full pytest suite green (SQLite + Postgres), ruff clean, mypy clean (the single pre-existing `pyreverseetl` import-untyped note is acceptable), `cargo test` green for Rust tasks, `cargo check --features pyo3-bindings` compiles.
- Test commands (repo root): `PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/python -m pytest services/orchestrator/tests services/ai-runtime/tests -q`; `cd crates/voice-runtime && cargo test`; wheel rebuild `make py-voice`.
- Conventional commits; ledger `.superpowers/sdd/2026-11-13-audio-variants/progress.md` via task-start script.

## Review Focus

1. **Parent bytes mutated by the variant flow** — a reasonable person expects the original file untouched forever. Pin: hash parent file before/after `POST .../variants` (Task 4).
2. **Non-16-bit or compressed parent** (24-bit WAV, mp3) — expect honest 422 naming the reason, not misread bytes or silent mono decoding. Pin: parametrized refusal tests (Task 3).
3. **Extension missing/unimportable** — expect 503 `audio_engine_unavailable`, not a Python-only fake pass. Pin: monkeypatched ImportError test (Task 4).
4. **Pitch corruption at non-16k voice_boost** — expect duration preserved and round-trip diagnostic present; a naive pass-through at 24k would pitch-shift 1.5×. Pin: seam test asserts `internal_rate_roundtrip` true and sample-count ratio ≈ 1.0 (Task 2).
5. **Trim that removes everything / resample to zero frames** — expect 422, never an empty ready asset. Pin: ValueError tests (Task 3).

---

### Task 1: Rust offline windowed-sinc resampler

**Files:**
- Create: `crates/voice-runtime/src/resample.rs`
- Modify: `crates/voice-runtime/src/lib.rs` (add `pub mod resample;`)

**Interfaces:**
- Produces (for Task 2): `pub fn resample_f32(input: &[f32], in_rate: u32, out_rate: u32) -> Vec<f32>` and `pub fn resample_i16(samples: &[i16], in_rate: u32, out_rate: u32) -> Vec<i16>` — whole-buffer, panic-free (returns input unchanged when `in_rate == out_rate`), handles empty input by returning empty.

- [ ] **Step 1: Write failing tests in `resample.rs` `#[cfg(test)]`**

Assertions (exact): identity (`16000→16000` returns same samples); ratio frame count (`1s of 24000 → 16000` yields `16000 ± 16` frames); sine SNR: 440 Hz sine at 48000 → 16000, output within ±2% amplitude of input peak, and zero-crossing count within 2% of expectation (aliasing bound); 8000→44100 works (arbitrary ratio supported); empty in → empty out; 1-sample input does not panic.

- [ ] **Step 2: Run `cargo test` in `crates/voice-runtime`** — expect FAIL (module missing / stub).

- [ ] **Step 3: Implement windowed-sinc whole-buffer resampler**

Hann-windowed sinc, ~32 taps per zero-crossing, cutoff ≈ `0.45 * min(in_rate, out_rate)`; clamp samples to `[-32768, 32767]` on the i16 path (round-half-away-from-zero). No streaming state needed (offline whole-buffer).

- [ ] **Step 4: `cargo test` green** (all existing tests still pass).

- [ ] **Step 5: Commit** `feat(voice-runtime): offline windowed-sinc resampler for asset variants`

### Task 2: Rust `audio_postprocess` pyfunction (normalize, voice_boost, dispatch)

**Files:**
- Modify: `crates/voice-runtime/src/pybridge.rs` (new `#[pyfunction]` + registration at the `m.add_function` block, pybridge.rs:356-360)
- Test: `#[cfg(test)]` in pybridge.rs (pure-Rust tests against the internal fn, NOT the pyfunction wrapper)

**Interfaces:**
- Consumes: Task 1 `resample_i16`; existing `VoiceBoostStage::new(VoiceBoostConfig)`, `process(&[i16]) -> EnhancedFrame`, `finish() -> Vec<i16>`, `count_clipped`, `VoiceBoostConfig::for_profile`, `config_from_settings_json` (all existing in dsp.rs).
- Produces (for Task 3): Python `voxora_voice_runtime.audio_postprocess(samples: list[int], ops_json: str) -> tuple[list[int], str]` — ops envelope `{"rate": u32, "ops": [{"type": "normalize", "target_dbfs": f32} | {"type": "voice_boost", "settings_json": str}]}`; returns `(samples, diagnostics_json)` with diagnostics `{"ops": [per-op records], "clipped_after": u32, "internal_rate_roundtrip": bool}`. Rust-side errors → `PyValueError` (Python maps to 422; unknown op type in Rust is also an error even though pydantic rejects first — defense in depth).

- [ ] **Step 1: Write failing Rust tests** (internal fn `audio_postprocess_impl(samples: &[i16], rate: u32, ops_json: &str) -> Result<(Vec<i16>, serde_json::Value), String>`): unknown op type → Err containing "unsupported op"; normalize raises a -40 dBFS peak to ≈ target (-16) without increasing `count_clipped`; when target would clip, gain is reduced (`clipped_after == clipped_before`, diagnostic `target_reached: false`, actual gain reported); voice_boost at rate 24000 → output sample count within ±1% of input (round-trip preserves duration), diagnostic `internal_rate_roundtrip: true`, and output differs from input; voice_boost with `settings_json` invalid → Err matching `config_from_settings_json` semantics; empty ops array → input unchanged, `clipped_after == count_clipped(input)`.

- [ ] **Step 2: `cargo test` — expect FAIL.**

- [ ] **Step 3: Implement `audio_postprocess_impl` + thin `#[pyfunction] audio_postprocess(samples: Vec<i16>, ops_json: &str) -> PyResult<(Vec<i16>, String)>` wrapper; register via `m.add_function(wrap_pyfunction!(audio_postprocess, m)?)?`.**

normalize: pure gain only (`peak = max |s|`; gain = target/peak, capped so post-gain `count_clipped == count_clipped(input)`; no limiter). voice_boost: resample→16000, `VoiceBoostStage` from `config_from_settings_json(settings_json)`, feed in ≤1600-sample chunks then `finish()`, resample back to original rate. Op order = listed order; trim/channels never appear Rust-side (Task 3 handles them in Python) — if they arrive Rust-side, error.

- [ ] **Step 4: `cargo test` green + `cargo check --features pyo3-bindings` compiles + `make py-voice` rebuilds wheel.**

- [ ] **Step 5: Commit** `feat(voice-runtime): audio_postprocess offline seam — normalize + voice_boost round-trip`

### Task 3: Python `variants.py` — WAV I/O, trim/channels, variant creation

**Files:**
- Create: `services/orchestrator/src/voxora/variants.py`
- Test: `services/orchestrator/tests/test_variants.py`

**Interfaces:**
- Consumes: Task 2 `audio_postprocess` (real extension); `db.create_audio_asset(tenant_id, values) -> dict`, `db.audit(tenant_id, actor, action, entity, entity_id, detail)` (existing); `from .security import new_id`.
- Produces (for Task 4): `create_variant(db: Database, settings: Settings, tenant_id: str, actor: str, parent: Dict[str, Any], ops: List[Dict[str, Any]]) -> Dict[str, Any]` — raises `ValueError` (→422, message is the client detail) or `RuntimeError("voice runtime extension unavailable")` (→503). New asset row: `parent_asset_id=parent["id"]`, `processing_history = parent.history + [{"op", "params", "diagnostics", "at", "by"}]`, `title = parent title + " (variant)"`, `format="wav"`, `status="ready"`, recomputed `duration_seconds`/`file_size`, `storage_path = f"{tenant_id}/{new_id}.wav"`.

- [ ] **Step 1: Write failing tests in `test_variants.py`** (build parent WAV blobs with stdlib `wave` + `tmp_path` store, parents inserted via `db.create_audio_asset`; pytest fixture `database` from conftest): trim_silence removes leading/trailing near-silence (samples reduced, content preserved mid-clip); trim that removes everything → ValueError; mono→stereo doubles frames; stereo→mono mixes with clip guard; resample 24000→16000 via REAL extension changes frame count by expected ratio and extension seam works (skip with honest `pytest.skip` only if wheel truly absent — never fake); normalize via real extension raises peak (assert new peak > old peak, clipped_after == 0); voice_boost at 24k mono → duration preserved; voice_boost on stereo parent → ValueError containing "mono"; 24-bit WAV parent → ValueError containing "16-bit"; ops applied in listed order (trim then normalize ≠ normalize then trim on a crafted blob — assert the histories differ); parent file bytes unchanged after the whole flow; success records audit `audio.variant_created` and history entries `{op, params, diagnostics}` non-empty.

- [ ] **Step 2: Run — expect FAIL (module missing).**

- [ ] **Step 3: Implement `variants.py`**

Lazy `import voxora_voice_runtime as _vr` inside the call path (ImportError → RuntimeError with the exact message above). Read parent file from `Path(settings.audio_storage_path) / parent["storage_path"]`; parse with `wave` (reject `sampwidth != 2`, `nchannels > 2`, empty frames). Python ops: `trim_silence` (amplitude threshold scan over i16 → head/tail bounds; `min_silence_ms` from op params at the CURRENT rate; zero-length result → ValueError), `channels` (mono→stereo duplicate; stereo→mono mean with clip-guard count). Rust ops: build op dict for `audio_postprocess` (`{"rate": current_rate, "ops": [single op]}` — ONE extension call per Rust op, preserving listed-order semantics between Python and Rust ops). After each op update `current_rate`/`current_channels`. Final write: 16-bit WAV. Then `create_audio_asset` row + audit.

- [ ] **Step 4: Run tests — PASS; full orchestrator suite still green.**

- [ ] **Step 5: Commit** `feat(m10): variant engine — WAV I/O, trim/channels, non-destructive variant rows`

### Task 4: Models + API routes + DB listing

**Files:**
- Modify: `services/orchestrator/src/voxora/models.py` (add `VariantOp`, `VariantOpsRequest` after `AudioAssetGenerate`)
- Modify: `services/orchestrator/src/voxora/db.py` (add `list_audio_asset_variants` next to `delete_audio_asset`, db.py:~782)
- Modify: `services/orchestrator/src/voxora/api.py` (routes after the audio-assets block)
- Test: `services/orchestrator/tests/test_variants_api.py`

**Interfaces:**
- Consumes: Task 3 `create_variant`; existing `_require_writer`, `_principal_from_request`, `ApiError`, `_page`, `Depends`.
- Produces: `VariantOpsRequest(ops: List[VariantOp])` with `VariantOp(type: Literal["normalize","trim_silence","resample","channels","voice_boost"], extra=forbid, param fields optional)` — ranges: normalize `target_dbfs: float = -16.0` ge -24 le -6; trim_silence `threshold_dbfs: float = -40.0` ge -60 le -20, `min_silence_ms: int = 120` ge 0 le 2000; resample `sample_rate: int` ge 8000 le 48000; channels `to: Literal["mono","stereo"]`; voice_boost `profile: Literal["natural","clear","studio"] = "clear"`, `overrides: Optional[VoiceBoostControlOverrides] = None`. Cross-validator: any `voice_boost` + any `channels(to="stereo")` → ValueError; ops count 1–6 (`min_length=1, max_length=6`). DB: `list_audio_asset_variants(self, tenant_id: str, parent_asset_id: str) -> List[Dict[str, Any]]` (ORDER BY created_at ASC; WHERE parent_asset_id=? AND tenant_id=?). Routes: `POST /api/v1/audio-assets/{asset_id}/variants` (writer-gated, 201, body `VariantOpsRequest`, audited `audio.variant_created`), `GET /api/v1/audio-assets/{asset_id}/variants` (`_page` shape, limit/offset params). Error mapping: parent missing → 404 `not_found`; ValueError → 422 `variant_invalid` (message string as detail); RuntimeError from Task 3 → 503 `audio_engine_unavailable`.

- [ ] **Step 1: Write failing tests in `test_variants_api.py`**: create parent via generate endpoint (wav), POST variants `{ops:[{type:"normalize", target_dbfs:-12}]}` → 201, response has `parent_asset_id == parent id`, history has 1 entry with diagnostics; GET variants lists the child; parent file hash (read from settings path) unchanged; 404 unknown parent; 422 unknown op type; 422 stereo+voice_boost combo; 422 six-plus ops; 422 out-of-range target_dbfs; 503 when `voxora_variants.create_variant` path raises RuntimeError (monkeypatch `api.generate_variant_handler`'s import or patch `variants.import_or_none` — pin the API mapping only, simplest: monkeypatch `voxora.api.create_variant` symbol to raise RuntimeError).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement models + DB method + routes.**

- [ ] **Step 4: Run tests — PASS; full suite (both backends) + ruff + mypy green.**

- [ ] **Step 5: Commit** `feat(m10): variant HTTP API — non-destructive ops with honest 422/503 mapping`

### Task 5: Docs + roadmap + ledger

**Files:**
- Modify: `docs/implementation-audit.md` (append M10 Phase C section)
- Modify: `docs/roadmap.md` (check Phase C box)
- Ledger: `.superpowers/sdd/2026-11-13-audio-variants/progress.md` (append per-task rulings as work proceeds)

- [ ] **Step 1: Audit entry** — what shipped (resampler, postprocess seam, variant engine, API), honest boundaries (WAV 16-bit only; no compressed decode; voice_boost mono-only with 16k round-trip; op-only profile resolution; extension required → 503), verified-this-session numbers (cargo tests, orchestrator suite count, seam tests through real wheel).
- [ ] **Step 2: Roadmap Phase C checkbox → `[x]`.**
- [ ] **Step 3: Commit** `docs(m10): Phase C variants audit + roadmap`

## Self-review notes

- Spec coverage: approaches (A only), 5 ops (Task 1–3), non-destructive pins (Task 3/4), honesty 422s (Tasks 3–4), 503 (Task 4), history/parent chain (Task 3), download reuse (unchanged route — no task needed), variant-of-variant chains (Task 3 accepts any `ready` wav parent; chain depth uncapped per spec).
- Types consistent: `create_variant`/`list_audio_asset_variants`/`audio_postprocess` names match across tasks; diagnostics keys match spec table.
