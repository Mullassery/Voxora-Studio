# Implementation Audit (honest verification record)

**Repo:** Mullassery/Voxora-Studio (public, was empty; scaffolded locally, not pushed)
**Date:** 2026-10-31
**Principle:** every claim below was verified by running the command and reading its output. Unsupported/version-limited checks are stated as such.

## Environment (verified)

| Tool | Version | Notes |
|---|---|---|
| Python | 3.9.6 (system) | `.venv` created for pinned deps |
| Node | 26.11.1 | |
| rustc/cargo | 1.99 | |
| Postgres | running on 5432 (accepted connections) | **not used** — orchestrator uses SQLite WAL |
| Docker | 29.5.2 | compose stack built and executed end-to-end on 2026-10-31 |

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
flow. Not fixed in this pass; worth tightening the request model later.

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
- **Docker Compose stack.** ~~Written but not verified~~ **Now verified** (see the compose section above); initial audit-writing pass recorded it as unexecuted before it was run.
- **CI workflow.** Written (`.github/workflows/ci.yml`) but not executed on GitHub (no remote push was authorized).
- **Squid Rust tests.** In the separate Squid-CRM repo, `cargo test -p squid-core` exceeded a 180 s timeout here earlier; unverified locally (recorded then, repeated now: not claimed as passing).
- **Console↔API live browser test.** The console builds and its API client targets the implemented endpoints; no browser E2E was run.
- **Postgres.** Detected in the environment but unused; storage is SQLite WAL, a real decision documented in `docs/architecture.md`.
- **Git push.** Not performed; no authorization.

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
