# Voxora Studio

Open-source AI voice telephony + outbound campaign platform: a FastAPI
control plane for consented calling, a deterministic Rust voice bridge, a
Squid-CRM adapter, and an operator console.

**Current state: simulation-only.** Every call is deterministic and flagged
`simulated: true`. There is no code path that can dial a real phone number:
live calls are disabled by default and config validation refuses to enable
live calls while a simulation provider is selected.

## What works today

| Component | State | Proof |
|---|---|---|
| FastAPI orchestrator (campaigns, contacts, consent, suppression, calling windows, retries, mock telephony, audit) | working | 110 pytest tests green |
| Postgres storage backend beside SQLite (same `Database` seam) | working | 24-test compat suite runs on both backends |
| Squid-CRM adapter (sync + webhooks, verified against Squid client contracts) | working | adapter + contract tests |
| Autonomous scheduling (`schedule_at` enforced in the worker loop) | working | worker scheduling tests |
| Voice runtime (Rust: turn-taking, scripted bridge, WAV inspect) → PyO3 seam `voxora_voice_runtime` → `VoiceRuntimeTelephonyAdapter` | working, simulation | 20 cargo tests (17 plain + 3 bridge-binding), seam test runs the real extension |
| Webhook replay harness (duplicate / tampered / out-of-order deliveries) | working | replay tests through the real HTTP endpoint |
| Capability-driven CRM consent/suppression sync | working | contract tests (capable adapter enriched; Squid honestly incapable) |
| Operator console (React + Vite + TS, generated OpenAPI client, campaign templates) | working | `npm run build` clean |
| AI runtime (deterministic scripted agent) | working | 7 pytest tests |
| Docker Compose (postgres + orchestrator + console) | verified end-to-end | compose section in `docs/implementation-audit.md` |
| CI on GitHub | green (Python incl. Postgres + PyO3 wheel build, Rust, Node) | runs on every push |

Full details: [`docs/implementation-audit.md`](docs/implementation-audit.md), [`docs/architecture.md`](docs/architecture.md), [`docs/roadmap.md`](docs/roadmap.md).

## Quick start (local, no Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -e "services/orchestrator[dev,postgres]"
.venv/bin/pip install -e services/ai-runtime

# tests (both storage backends run when Postgres is up locally)
PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/python -m pytest services/orchestrator/tests services/ai-runtime/tests -q

# build the Rust voice bridge extension (simulation seam)
make py-voice

# run the API (simulation-only)
export VOXORA_API_TOKENS='{"dev-token": {"tenant_id": "tenant-a", "role": "admin", "name": "Admin"}}'
PYTHONPATH=services/orchestrator/src .venv/bin/python -m voxora

# operator console
cd apps/console && npm install && npm run dev   # http://localhost:5173, token: dev-token
```

## Quick start (Docker)

```bash
export VOXORA_API_TOKENS='{"dev-token": {"tenant_id": "tenant-a", "role": "admin", "name": "Admin"}}'
docker compose up -d
# console: http://localhost:8080   API: http://localhost:8000/api/v1/health
```

Compose runs the orchestrator on Postgres (`VOXORA_STORAGE=postgres`) behind a
health-gated `postgres:16` service.

## Not implemented (read this)

- **No live-server integrations.** Squid verification is against client source
  contracts; a live Squid instance test is a roadmap item.
- **Twilio (or any live telephony) adapter is not implemented.** Provider
  status honestly reports Twilio as `implemented: False`.
- **No real LLM.** The agent is script-driven and says so (`real_llm: False`).
- The voice bridge places no calls; real media transport (SIP/RTP/WebSocket)
  is future work behind a legal/compliance review gate.

## Repo layout

```
crates/voice-runtime/     Rust voice bridge (turn-taking, scripted dialog, WAV) + PyO3 seam
services/orchestrator/    FastAPI control plane + tests (SQLite/Postgres)
services/ai-runtime/      scripted agent backend + tests
apps/console/             Operator console (generated API client)
docs/                     architecture · implementation-audit · roadmap · risks
```

## Development

```bash
# Python (both storage backends run locally if Postgres is up)
PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/pytest services/orchestrator/tests services/ai-runtime/tests -q
.venv/bin/ruff check services && PYTHONPATH=services/orchestrator/src .venv/bin/mypy services/orchestrator/src services/ai-runtime/src

# Rust voice bridge (tests + rebuild the Python extension)
cargo test --workspace
make py-voice

# Console
cd apps/console && npm run build
```

License: MIT (workspace-wide).
