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
