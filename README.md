# Voxora Studio

Open-source real-time voice telephony + outbound campaign platform.

**This build is simulation-only.** Every call is mock, deterministic, and
flagged `simulated: true`. There is no code path that can dial a real phone
number: live calls are disabled by default and config validation refuses to
enable live calls while the (only, mock) telephony provider is selected.

## What works today

| Component | State | Proof |
|---|---|---|
| FastAPI orchestrator (campaigns, contacts, consent, suppression, calling windows, retries, mock telephony, audit) | working | 52 pytest tests green |
| Squid-CRM adapter (sync + webhooks, verified against Squid client contracts) | working | 20 adapter/contract tests |
| AI runtime (deterministic scripted agent) | working | 7 pytest tests |
| Voice runtime (Rust: turn-taking, scripted bridge, WAV inspect) | working, unwired | 15 cargo tests |
| Operator console (React + Vite + TS) | builds | `npm run build` clean |
| AI-runtime honesty | `real_llm: False`, scripted agent | capabilities dict |
| Docker Compose (orchestrator + console) | verified end-to-end | compose section in `docs/implementation-audit.md` |
| CI workflow | written, **not executed on GitHub** | no push authorized |

Full details: [`docs/implementation-audit.md`](docs/implementation-audit.md), [`docs/architecture.md`](docs/architecture.md).

## Quick start (local, no Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn pydantic httpx cryptography pytest
PYTHONPATH=services/orchestrator/src .venv/bin/python -m pytest services/orchestrator/tests -q

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

## Not implemented (read this)

- **No real telephony.** No Twilio (or any provider) adapter exists; the registry exposes one provider: `mock`.
- **No real LLM.** The agent is script-driven and says so.
- **No live Squid-server test** — adapter verified against Squid-CRM source contracts.
- CI written but not run on GitHub (see audit).

## Repo layout

```
crates/voice-runtime/     Rust pipeline logic (turn-taking, bridge, WAV)
services/orchestrator/    FastAPI control plane + tests
services/ai-runtime/      mock agent backend + tests
apps/console/             Operator console
docs/                     architecture · implementation-audit · roadmap · risks
```

## Development

```bash
# Python
PYTHONPATH=services/orchestrator/src .venv/bin/pytest services/orchestrator/tests services/ai-runtime/tests -q
ruff check services && PYTHONPATH=services/orchestrator/src mypy services/orchestrator/src

# Rust
cargo test --workspace

# Console
cd apps/console && npm run build
```

License: MIT (workspace-wide).
