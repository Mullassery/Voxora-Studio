# Implementation Audit (honest verification record)

**Repo:** Mullassery/Voxora-Studio (public, pushed; CI green)
**Date:** 2026-10-31 (initial audit) · **Updated:** 2026-11-01 (Phases 1–3, see end)
**Principle:** every claim below was verified by running the command and reading its output. Unsupported/version-limited checks are stated as such.

## Environment (verified)

| Tool | Version | Notes |
|---|---|---|
| Python | 3.9.6 (system) | `.venv` created for pinned deps |
| Node | 26.11.1 | |
| rustc/cargo | 1.99 | |
| Postgres | running on 5432 (accepted connections) | **used since Phase 2** — CI service container + compose + local CI database `voxora_ci` |
| Docker | 29.5.2 | compose stack built and executed end-to-end on 2026-10-31 and again 2026-11-01 (postgres-backed) |

## What actually runs (executed commands, this session)

### Orchestrator (FastAPI, SQLite)

```
$ PYTHONPATH=services/orchestrator/src .venv/bin/python -m pytest services/orchestrator/tests -q
52 passed in ~3s
```

Also combined:
```
$ PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/python -m pytest services/orchestrator/tests services/ai-runtime/tests -q
59 passed in 3.23s
```

Coverage of the 52 tests:
- state machine legality (campaign + call maps) — `test_state_machine.py`
- credential sealing/export, HMAC webhook signature, redaction — `test_secret_box.py`
- consent/suppression/calling-window/retry/backoff — `test_eligibility.py`
- campaign engine: validate, dry-run execution, retries, ineligible handling, cancel, audit — `test_campaign_engine.py`
- Squid adapter against the verified contract (auth, envelopes, retries with Retry-After, webhook HMAC/dedupe, field discovery honesty) — `test_squid_adapter.py` (8 tests)
- adapter contract: memory fake + Squid parametrized (12 tests)
- HTTP API end-to-end with tenant isolation, RBAC, lifecycle, secrets-never-returned, webhook ingestion, audit — `test_api.py` (16 tests)

Lint/type:
```
$ .venv/bin/ruff check services/orchestrator
All checks passed!
$ PYTHONPATH=services/orchestrator/src .venv/bin/mypy services/orchestrator/src
Success: no issues found in 24 source files
```

Ruff config: line-length 100, py39 target; `UP045/UP037/B008/E501` ignored (py3.9 typing compatibility + FastAPI `Depends` idiom). Full select set: E, F, I, UP, B, SIM.

The app boots from env and refuses to start without API tokens:
```
$ python -c "from voxora.api import create_app_from_env; ..."
factory ok FastAPI
```

### Docker Compose (executed after the first audit pass)

```
$ VOXORA_API_TOKENS='{"dev-token": …}' docker compose build   # both images built
$ docker compose up -d                                        # both containers Up
$ curl http://localhost:8000/api/v1/health     -> {"status":"ok","mode":"simulation","liveCallsEnabled":false}
$ curl http://localhost:8080/                  -> 200 (console SPA via nginx)
$ curl http://localhost:8080/api/v1/contacts?limit=2  -> seeded demo contacts (proxied through nginx)
```

End-to-end via the console's nginx proxy: consent grant -> add recipient -> start
campaign -> worker drains -> `status: completed`, `recipients: {recipients: 1, completed: 1}`,
one call row with `simulated: true` and `disposition: "answered"`. Stack torn down
afterwards (`docker compose down -v`).

Note (minor finding): `POST /api/v1/campaigns` silently ignores an inline
`contact_ids` key instead of rejecting it — the recipients endpoint is the real
flow. **Fixed 2026-11-01**: request model is `extra="forbid"`; unknown fields
now return 422 (tested in `test_api.py`).

### AI runtime

```
$ .venv/bin/python -m pytest services/ai-runtime/tests -q
7 passed
```

### Voice runtime (Rust)

```
$ cargo test -p voxora-voice-runtime
15 passed; 0 failed
```
(after fixing two of my initial test expectations; `cargo fix` applied for one unused-mut warning; currently warning-free.)

### Console (React + Vite + TS)

```
$ npm install    # installed clean after bumping typescript to ~5.9.3 (5.6.4 no longer published)
$ npm run build  # tsc -p tsconfig.json && vite build — 0 errors, 148 kB js / 47 kB gzip
```

## What is NOT verified / NOT present (explicit)

- **Real telephony.** The only wired provider is `mock`. The telephony registry exposes exactly one provider. No Twilio SDK code exists; `allow_live_calls=true` + `mock` is rejected by config validation. This build cannot place a real call — by design and by review.
- **Real LLM.** `ai-runtime` is a deterministic scripted agent; `real_llm: False` is declared in its capabilities.
- **Docker Compose stack.** ~~Written but not verified~~ **Now verified twice** (2026-10-31 sqlite-backed; 2026-11-01 postgres-backed with `VOXORA_STORAGE=postgres`).
- **CI workflow.** ~~Written but not executed~~ **Now green on GitHub** (runs 41192274198 and 41259238748; Python job pinned to `ubuntu-24.04` because 26.04-dropping-3.9; Rust + Node jobs green).
- **Squid Rust tests.** In the separate Squid-CRM repo, `cargo test -p squid-core` exceeded a 180 s timeout here earlier; unverified locally (recorded then, repeated now: not claimed as passing).
- **Console↔API live browser test.** The console builds and its API client targets the implemented endpoints; no browser E2E was run.
- **Live Squid server.** All Squid verification is against source-derived fake transports, never a running Squid instance (the source itself lives in this workspace's Squid-CRM repo, so contract fidelity is high, but it is still not a live-server test).

## Assumptions made (not verified)

- Squid contract details were verified from Squid-CRM source (`rest.rs`, `webhooks` signing, API-key bearer format) at commit `015a2e8` in this session's earlier work; behavior match with a *live* Squid server was not tested against a running instance beyond the shipped fake transport.
- `VOXORA_API_TOKENS` JSON shape (`token → {tenant_id, role}`) is our choice, not any external contract.
- Session-pinned versions (fastapi 0.128.8 in dev-venv vs 0.115.* in Docker/CI) were not cross-tested; the test suite runs on 0.128.8.

## Honest gaps vs. master prompt

| Requested | Status |
|---|---|
| Working minimal web app executable end-to-end | ✅ (orchestrator API + tests; console builds) |
| CRM integration working with real client | ✅ adapter verified against source; not against a live Squid server |
| All documented flows tested | ⚠️ core flows tested (59 tests); compose/browser/CI unexecuted |
| Deployment tools present and verified | ✅ compose built + exercised end-to-end via proxy |
| No unverified claims | This file is the check. |

---

## Phase 1–3 update (2026-11-01, verified this session)

### CI on GitHub (Phase 1)
- `gh run watch 41192274198` and `41259238748` — all three jobs (Python 3.9 on `ubuntu-24.04`, Rust, Node) green. Python job pins `ubuntu-24.04` because `ubuntu-latest` (26.04) no longer ships 3.9. The Python job gained a `postgres:16` service container and runs the storage-compat suite with `VOXORA_TEST_PG_URL`.

### Postgres storage + compat suite (Phase 2)
```
$ PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/python -m pytest services/orchestrator/tests services/ai-runtime/tests -q
104 passed
```
- 24 of those are `test_storage_compat.py`, parametrized over `sqlite` and `postgres` via the `database` fixture (`conftest.py`); local suite ran against a real Postgres (`postgresql://.../voxora_ci`, createdb). CI ran the same suite against its service container. Portable-SQL parity (upserts, dedupe, CASE WHEN booleans) verified on both.
- SQL dialect adapter: `voxora/pg.py` (`PostgresDatabase(Database)`); `VOXORA_STORAGE=postgres` + `VOXORA_DATABASE_URL`.

### Scheduling (Phase 2)
- `test_worker_scheduling.py` (4 tests): scheduled campaigns flip to running only when `schedule_at` arrives; worker-time enforcement verified; audit row `campaign.due_promoted` written.

### Webhook replay harness (Phase 3)
- `test_webhook_replay.py` (4 tests) drives the actual HTTP endpoint with signed raw bodies: duplicate event-id delivery → `duplicate` no-op; tampered body under original signature → 401 before dedupe; replayed upsert after a deletion does **not** resurrect (dedupe-first win); out-of-order deliveries resolve last-accepted-wins deterministically per external id — semantics pinned in the test docstring and `sync`/`db` code.

### Consent/suppression sync honesty (Phase 3)
- `crm/sync.py` enrichment is capability-driven: adapters declaring `consent_read`/`suppression_read` get per-record provider consent/suppression pulled and applied during sync; Squid (capacity verified false against client source) is never asked and stays at `unknown` — asserted in `test_crm_contract.py`. Suppression is escalate-only: a provider "clear" never un-suppresses (compliance-safe default, documented in the module docstring). Bad provider states count per-record as `failed` with the reason surfaced.
- Also honest: adapters that *claim* a capability but raise `NotSupportedError` produce per-record errors, not silent skips.

### Request-model tightening (Phase 3)
- `CampaignCreate` is `extra="forbid"`; unknown fields (e.g. inline `contact_ids`) now 422 instead of being silently ignored (`test_campaign_create_rejects_unknown_fields`).

### Console generated client + postgres compose (Phase 2)
- `npm run build` (tsc + vite) 0 errors after rewiring `api.ts` to generated paths/types.
- `docker compose up -d --build` with a `postgres:16` service + healthcheck: orchestrator healthy with `VOXORA_STORAGE=postgres`; tenants + seeded contacts verified present in Postgres; ready/overview fetched through the nginx proxy; stack torn down (`docker compose down -v`).

### Not done / unchanged (still honest)
- No live Squid server, no live telephony, no real LLM, no browser E2E — all as above.

### Voice-runtime <-> orchestrator seam (Phase 4, verified 2026-11-01)
- Rust: `bridge.rs` gained a canonical JSON export (`summary_to_json`, `run_with_timing` per-turn timing) with golden tests (17 tests plain, 20 with `pyo3-bindings`); `pybridge.rs` exposes `run_scripted_call_json` via maturin (`pyproject.toml`, abi3 py39, feature-gated so plain `cargo test` never links `extension-module`).
- Built for real: `maturin develop --release` installed `voxora_voice_runtime-0.1.0` into the venv; the seam test runs against the actual extension (no pretense).
- Python: `telephony/voice_runtime.py` maps the JSON onto `TelephonyResult` (schema-version guarded with `ValueError`), adapter is idempotent per `idempotency_key`; `telephony_provider=voice-runtime` selects it via the registry; provider status reports it as `simulated: true`.
- Config honesty maintained: `VOXORA_ALLOW_LIVE_CALLS` is rejected for BOTH `mock` and `voice-runtime` (both simulation providers).
- Full suite: 110 Python tests green, ruff/mypy clean, cargo clean both feature states.
- How to build locally: `make py-voice`. CI builds the wheel in the Python job before tests.

### Twilio telephony adapter (Phase 5, verified 2026-11-01)
- Implemented against Twilio's **documented** REST contract (documented: Calls.json via Basic auth, `.json` resource suffix, `StatusCallbackEvent`, `X-Twilio-Signature` HMAC-SHA1): `telephony/twilio.py` + 14 contract tests (fake transport; request shapes, idempotency, 429 Retry-After, cancel, dry-run provider isolation, callback map).
- Async settlement: `TelephonyResult.final` extended; pending results leave the call in-flight; `settle_provider_call()` applies terminal callbacks through the same state machine, absorbs duplicates, and fails honestly on unknown SIDs. Webhook endpoints verify signatures (`403` on tamper), `503` when Twilio is not the configured provider, `404` for unknown CallSid (tested through the HTTP layer: 5 tests).
- **Honest boundary:** no live-server verification — no Twilio credentials in this environment; dialing additionally requires `VOXORA_ALLOW_LIVE_CALLS=true` (refused otherwise, tested). Provider status reports Twilio `implemented: true, configured: false, live: false` in the default dev setup.
- Config: `VOXORA_TELEPHONY_PROVIDER=twilio` now requires all of `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_TWIML_URL`, `TWILIO_STATUS_CALLBACK_URL` (validated at startup).
- Suite: 129 Python tests green (both storage backends), ruff + mypy clean, Rust 17 green.

## M7 + M8 completion (2026-11-12, verified this session)

Branch `feature/voice-intelligence`; plan
`docs/superpowers/plans/2026-11-12-m7-m8-live-loop-routing-analytics.md`.

- **P8 model-profile dispatch**: `/api/v1/ai/decide` resolves
  (profile, task) → model from the seeded `model_profiles` table; unknown
  profile ⇒ 501 with the configured list; `ai_usage` rows persist per
  (tenant, profile, task, model) for decide (analyze) and the live turn
  loop (turn). Verified: `tests/test_model_profiles.py` (13 tests, SQLite +
  Postgres).
- **M7 live turn loop**: `voice-runtime` sessions open in-flight
  (agent opening line spoken); `POST /voice-sessions/{id}/turns` drives
  STT deltas → agent turn → spoken segments; mid-call instructions are
  spoken at the stream boundary (`source: "instruction"`); end keyword
  settles `completed` + `transcript.ready`; budget counters persist across
  requests via `completed_turns`/`resumed` engine params; agents gained
  `rearm()` (OpenRouterAgent re-arms WITHOUT spending an LLM call).
  Verified: `tests/test_voice_turn_loop_http.py` (13 tests).
- **P7 routing status**: `GET /api/v1/voice/providers` with literal
  `UNVERIFIED` labels + `attemptable: false`; found and fixed a real
  shared-mutable-singleton defect in `LanguageRoutingTable` quirks.
  Verified: `tests/test_voice_providers.py` (12 tests).
- **P9 server-authoritative TTS**: pinned by tests — no arbitrary-text TTS
  route; caller deltas never appear in spoken content; instruction cap 2000.
- **P10 speech analytics**: deterministic pause/WPM/filler estimates
  computed once at settle when recorded audio is fetchable (stdlib WAV
  decoder; ffmpeg absent by ruling); omitted honestly without audio.
  Verified: `tests/test_voice_analytics.py` (8 tests, both backends).
- Two real defects fixed along the way: Postgres never received the
  `voice_sessions.recording_url/recording_sid` columns (migration parity
  with the SQLite hook), and `conftest._PG_TABLES` truncated neither
  `voice_sessions` nor several newer tables (stale-row leakage).

Full suite this session: 247 orchestrator tests + 20 ai-runtime tests
green (SQLite + Postgres); ruff clean; mypy: 3 pre-existing
import-untyped notes on third-party modules (voxora_voice_runtime,
pyreverseetl) unchanged. Not verified live: no real telephony, no real
LLM, no live Squid — unchanged honest boundaries.

## M9 Voice Boost settings surface (2026-11-13, verified this session)

Plan `docs/superpowers/plans/2026-11-13-voice-boost-settings.md`.

- **Rust seam** (`crates/voice-runtime/src/dsp.rs::config_from_settings_json`
  + pybridge `voice_boost_config_from_json`): the orchestrator-facing
  `{"enabled", "profile", "overrides"}` document resolves into a
  `VoiceBoostConfig` — unknown profiles/keys, wrong types, and out-of-range
  numbers rejected (never clamped); `enabled: false` ⇒ bypass with overrides
  resolved-inert. Verified: 4 new dsp tests + pybridge round-trip
  (cargo 115 green); the function is proven through the REAL extension via
  the maturin-built wheel (`make py-voice`), not mocks.
  Honest note: `cargo test --features pyo3-bindings` cannot link in this
  environment (extension-module feature) — compile is verified with
  `cargo check --features pyo3-bindings`, function behavior via the wheel.
- **Storage**: `voice_boost_settings` config-in-db table (profile + enabled
  master switch + overrides config; seed-once builtin CLEAR/enabled) plus a
  per-session `voice_boost` override column on `voice_sessions`; parity
  migrations for both backends (a shared persistent Postgres server exposed
  the schema-upgrade gap and got the `enabled` migration).
- **Resolution**: `voice_boost.py::resolve_voice_boost_settings` merges
  stored global defaults with the current session override only — session
  overrides never persist into global defaults (tested).
- **API**: `GET/PUT /api/v1/voice/boost/settings` (PUT writer-gated, audited
  `voice_boost.settings_updated`; validation 422s pinned: unknown control
  key, out-of-range strengths/target, unknown profile), and per-session
  `voice_boost` override on `POST /api/v1/voice-sessions` resolved in the
  session view (`profile` inherited, `overrides` merged).
- **Seam tests**: `tests/test_voice_boost_seam.py` (3 tests) drive the real
  extension — settings resolve, disabled ⇒ Rust bypass, API-rejected input
  rejected identically at the seam.
- Honest boundary: the DSP executes inside the Rust media engine (tested
  there); live-call application waits on the M7 media transport. The M7/P9
  contract stands: `/voice-sessions/{id}/say` stays 404; no client-controlled
  call speech.

Full suite this session: 295 orchestrator tests green (SQLite + Postgres),
ruff + mypy clean, cargo 115 green both verification paths.

## M10 Audio Generation, Phases A-B (2026-11-13, verified this session)

What shipped (the PRODUCE half of the M9+M10 voice pipeline — the operator-asset
surface, deliberately separate from the live call path):

- **Provider abstraction** (`tts_generation.py`): `TTSProvider` protocol with two
  implementations — `MockTTSProvider` (deterministic sine WAV, honestly labeled
  `simulated: true`; the in-repo simulation engine, zero paid API calls) and
  `OpenAITTSProvider` implemented against the DOCUMENTED REST contract
  (`POST /v1/audio/speech` — developers.openai.com, retrieved 2026-11-13) with
  injectable transport and fake-transport tests. Contract-verified,
  LIVE-UNVERIFIED: no credentials exist in this environment. The provider
  exposes exactly the documented surface (13 voices, `tts-1|tts-1-hd|
  gpt-4o-mini-tts`, 6 formats, speed 0.25-4.0); `instructions` only on
  gpt-4o-mini-tts (refused at the model layer, dropped at the provider layer).
  OpenAI exposes NO language-selection parameter — language is recorded
  metadata only. Provider errors are sanitized: detail never carries the key
  or reaches clients.
- **Storage + validation** (`audio_assets.py`): paths built ONLY from internal
  ids (no user input ever reaches a path; id regex-validated), 50MB blob cap,
  WAV duration via stdlib `wave` (no-ffmpeg ruling stands) — compressed formats
  report `duration_seconds: None`, never zero-fabricated. Provider failure
  leaves NO row and NO file behind (tested). Download headers are sanitized
  against hostile titles (quotes/CR/LF/path chars).
- **Persistence**: `audio_assets` table on BOTH backends (parity migrations),
  `parent_asset_id` + `processing_history` columns exist NOW for Phase C
  variants; title search is `LOWER(title) LIKE LOWER(?)` for cross-backend
  case-insensitivity (Postgres LIKE is case-sensitive, SQLite is not).
- **API**: `POST /api/v1/audio-assets/generate` (writer-gated, 201; unconfigured
  provider → 501 `audio_provider_unconfigured`, provider refusal → 502
  sanitized), `GET /api/v1/audio-assets` (repo `_page` convention), `GET
  .../audio-assets/{id}`, `GET .../download` (sanitized Content-Disposition),
  `DELETE .../{id}` (row + file, audited), and `GET /api/v1/audio/providers`
  mirroring the voice_providers capability-honesty pattern (never fakes
  success; unconfigured returns an honest body, 501 only at generate time).
- **The M7/P9 boundary stands**: `/voice-sessions/{id}/say` stays 404 (pinned
  in `test_audio_api.py`). Generated audio never enters the call loop.

Verified this session: `test_tts_generation.py` (11) + `test_audio_assets.py`
(22, SQLite + Postgres) + `test_audio_api.py` (6) — full suite 356 green,
ruff clean, mypy clean apart from the pre-existing pyreverseetl
import-untyped note. Remaining Phase C-G (variants/post-processing, script
chunking + long-form, batch/background jobs) are planned, not claimed.

## M10 Audio Generation, Phase C — non-destructive variants (2026-11-13, verified this session)

The PRODUCE-half finishing pass: operators derive optional variants from a
generated WAV asset, reusing the M9 DSP so ONE engine serves both directions
of the pipeline (calls enhanced on the way in, assets enhanced on the way out).

- **Real resampler (new Rust DSP)**: `crates/voice-runtime/src/resample.rs` —
  whole-buffer Hann-windowed sinc, arbitrary ratios (8k–48k), cutoff
  0.45×min(in,out), single DC-gain norm on the input grid. Verified by
  sine-sweep tests (amplitude ±2%, zero-crossing frequency within 2%,
  ratio frame counts). Two kernel bugs were caught BY the tests (per-position
  renormalization exploded 870×; an inverted window zeroed the main lobe) —
  the RED→GREEN loop earned its keep.
- **Offline post-process seam**: `postprocess.rs` + pyfunction
  `audio_postprocess(samples, ops_json, rate)` — normalize (pure gain,
  clip-guarded: gain REDUCED rather than clipping, `target_reached` honest;
  within the API's ≤ -6 dBFS target range clipping is mathematically
  impossible and the test pins that) and voice_boost (the SAME
  `VoiceBoostStage` M9 uses, with the documented 16 kHz internal round-trip
  for non-16k assets — duration preserved ±1%, recorded in diagnostics).
  9 new Rust tests; 131 total green; `cargo check --features pyo3-bindings`
  compiles; seam verified through the REAL wheel (`make py-voice`).
- **Variant engine** (`variants.py`): WAV I/O via stdlib `wave` (16-bit
  mono/stereo only — honest 422 for compressed/non-16-bit parents, never
  misread bytes); trim_silence (amplitude-threshold scan, everything-quiet →
  422) and channel conversion (mean-mix with clip guard) in Python; variants
  written as full WAV containers; variant-of-variant chains allowed with
  accumulating `processing_history` (op, params, diagnostics, at, by).
- **API**: `POST /api/v1/audio-assets/{id}/variants` (writer-gated, 201,
  audited `audio.variant_created`; 1–6 ops, extra=forbid, voice_boost+stereo
  combo refused 422; ValueError→422 `variant_invalid`, RuntimeError→503
  `audio_engine_unavailable` — the extension is REQUIRED, never faked),
  `GET .../variants` (`_page` shape). The parent file is only ever READ —
  byte-identity pinned by test at both the engine and HTTP layer.
- Honest boundaries: WAV-16-bit parents only this phase; no compressed decode;
  voice_boost mono-only (stereo → 422); variant voice_boost resolves the op's
  own params over the builtin profile (tenant stored settings never consulted
  — a stored `enabled: false` silently no-opping an explicit op would be a
  fake success); op-params ride the M9 override validation (ranges refused,
  never clamped).

Verified this session: orchestrator suite 392 green (SQLite + Postgres),
ruff clean, mypy clean (repo config), cargo 131 green. Phase D (long-form
chunking) and Phase E (batch/background) remain planned, not claimed.

## M10 Audio Generation, Phase D — long-form generation (2026-11-13, verified this session)

What shipped (long-form: scripts far beyond the provider's 4096-char limit
become ONE assembled asset, with per-chunk honesty and retry-without-
regeneration):
- **Chunker** (`longform.py`): paragraph-boundary chunking (blank lines),
  sentence-boundary fallback for paragraphs over the limit, word-boundary
  hard split as documented last resort; greedy merge up to `max_chunk_chars`
  (default 4096, range 500-4096); 64-chunk cap (422 with the actual count);
  chunk texts concatenate back to the script verbatim (pinned by test).
- **Run engine** (`longform.py`): `generate_long_form` creates the row
  immediately (`status: processing`, real final storage_path, file_size 0,
  duration None), then generates each chunk through the EXISTING provider
  abstraction with ONE settings set — chunk intermediates are files
  (`{asset_id}_chunk_{i}.{ext}`), never asset rows (kept out of the operator
  library by design). Manifest lives in `processing_history`: header entry
  (`kind: longform`, run_id, FULL settings snapshot incl. `instructions`,
  max_chunk_chars) + per-chunk entries (text verbatim, state, sanitized
  error) + `assembled`/`assembly_failed` entries. Assembly: WAV concat is
  frame-exact with rate/channels equality enforced, PCM raw concat with
  honest duration None; assembled blob validated (50 MB cap) and frame count
  asserted == sum of chunk frames; chunk files removed after successful
  assembly. Any chunk failure → row stays `processing` (succeeded chunks
  kept); assembly failure → terminal `failed` with a message naming the
  remedy (shorter script). In-process `run_locks` guard double-runs.
- **Retry** (`retry_long_form` + `POST /api/v1/audio-assets/{id}/retry-
  failed`): validates long-form header + status + lock (409 `run_in_flight`,
  non-long-form/terminal → 409 `longform_invalid`), regenerates ONLY
  failed/missing chunks from the STORED snapshot (retry uses byte-identical
  chunk text and identical settings — pinned by tests: exactly one extra
  provider call, boundaries unchanged), then re-assembles.
- **API**: `POST /api/v1/audio-assets/generate-long` (writer-gated, 201 even
  on partial failure — the body carries `processing` status and the failed
  chunk; script ≤ 262 144 chars; ValueError→422 `longform_invalid`),
  lifecycle guards: download of a `processing` asset → 409
  `still_processing` (never a misleading 404), variant of a non-`ready`
  asset → 422; DELETE removes the assembled file AND all chunk files.
  Audited `audio.longform_completed`.
- Honest boundaries: synchronous request bounded by the 64-chunk cap (long
  scripts are long requests — Phase E adds background processing);
  WAV/PCM assembly only (no in-repo decoder for compressed formats, same
  ruling as variants); run lock is per-process (single-worker deployment —
  multi-worker would need a shared lease, documented not hidden); max legal
  input can exceed the 50 MB assembled cap → terminal `failed` with the
  remedy in the message.

Verified this session: orchestrator + ai-runtime suite 435 green (SQLite +
Postgres), ruff clean, mypy clean (repo config). Phase E (batch/background),
Phase F (console UI), Phase G remain planned, not claimed.
