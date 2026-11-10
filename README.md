# Voxora Studio

Open-source AI voice telephony + outbound campaign platform: a FastAPI
control plane for consented outbound calling, a deterministic Rust voice
bridge (via PyO3), a Squid-CRM adapter, and an operator console.

Simulation-only by design: every call is deterministic and flagged
`simulated: true`, and config validation refuses live calls while a
simulation provider is selected.

## What works today

| Component | State | Proof |
|---|---|---|
| FastAPI orchestrator (campaigns, contacts, consent, suppression, calling windows, retries, mock telephony, audit) | working | 110 pytest tests green |
| Postgres storage backend beside SQLite (same `Database` seam) | working | 24-test compat suite runs on both backends |
| Squid-CRM adapter (sync + webhooks, verified against Squid client contracts) | working | adapter + contract tests |
| Autonomous scheduling (`schedule_at` enforced in the worker loop) | working | worker scheduling tests |
| Voice runtime (Rust: turn-taking, scripted bridge, WAV inspect) → PyO3 seam `voxora_voice_runtime` → `VoiceRuntimeTelephonyAdapter` | working, simulation | 20 cargo tests (17 plain + 3 bridge-binding), seam test runs the real extension |
| Intelligent turn detection (Rust: VAD, adaptive endpointing, semantic waits, backchannel-aware barge-in, code-switch grace, provider-failure resile) → PyO3 engine → Python wrapper + `/api/v1/voice/turn/*` diagnostics API | working, simulation | 93 cargo tests + 9 wrapper/API contract tests |
| Webhook replay harness (duplicate / tampered / out-of-order deliveries) | working | replay tests through the real HTTP endpoint |
| Capability-driven CRM consent/suppression sync | working | contract tests (capable adapter enriched; Squid honestly incapable) |
| Operator console (React + Vite + TS, generated OpenAPI client, campaign templates, Voice turn-detection tab) | working | `npm run build` clean |
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

## Packaging: pip and uv

The repo is a uv workspace with three installable packages. The Rust
extension `voxora_voice_runtime` always builds from source (it is never on
PyPI); a Rust toolchain is required.

```bash
# uv (recommended): resolves all workspace members and builds the
# Rust extension through maturin
uv sync --all-packages
uv run pytest services/orchestrator/tests -q

# pip: editable installs in dependency order
pip install -e "services/ai-runtime[dev]"
pip install -e "services/orchestrator[dev]"     # + [voice] extra for the engine
pip install -e crates/voice-runtime             # builds the PyO3 extension

# equivalent Makefile targets
make py-install    # the three pip installs above
make uv-sync       # uv sync --all-packages
```

The orchestrator's `[voice]` extra declares the engine as an optional
accelerator: `services/voxora/voice_turn_detection.py` raises
`TurnDetectionUnavailable` (and `POST /api/v1/voice/turn/engines` returns
503) instead of faking detection when it is missing.

## Intelligent turn detection (Rust engine)

`crates/voice-runtime/src/controller.rs` is the authoritative detection
pipeline for one call: energy VAD with hysteresis, adaptive endpointing
(fast/normal/deliberate profiles with speech-rate adaptation), a semantic
wait heuristic (incomplete thoughts keep listening; timeouts are
honestly labeled), backchannel-vs-genuine barge-in classification (an
acknowledgement NEVER cancels playback), language code-switch grace,
and a provider-degraded resilience mode. States and events are
versioned (`schema_version = 1`, `td_events.rs`).

Mapped end to end:

- **Rust** → `TurnDetectionEngine` PyO3 class (`feed_audio`,
  `feed_transcript`, `feed_semantic`, `feed_lid`, `feed_agent_event`,
  `note_provider_failure/recovered`, `end_call`, `snapshot`).
- **Python wrapper** → `voxora.voice_turn_detection.TurnDetectionEngine`
  (validated args, monotonic seq, typed errors).
- **HTTP** → `/api/v1/voice/turn/*`: `GET config`,
  `POST engines`, `GET/DELETE engines/{id}`, and per-feature feeds
  `frames`, `transcript`, `semantic`, `lid`, `agent-events`,
  `provider-failure`, `provider-recovery`, plus the event replay
  `GET engines/{id}/events`. Tenant-scoped, FIFO-bounded registry.
- **Console** → the Voice tab: probe an engine, run a scripted turn,
  watch the detection event stream.

Config knob | Env | Default
---|---|---
endpoint profile | `VOXORA_TURN_ENDPOINT_PROFILE` | `normal`
semantic waits | `VOXORA_TURN_SEMANTIC_ENABLED` | `true`
word gate | `VOXORA_TURN_WORD_GATE_MIN_WORDS` | `3`
engine registry size | `VOXORA_TURN_DIAGNOSTICS_ENGINES` | `64`

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
- **No real STT/TTS/VAD provider.** The turn-detection engine consumes
  whatever the transport feeds it; Silero-class VAD evaluation, live media
  transport, and provider-plumbed end-to-end voice are future work.
- The voice bridge places no calls; real media transport (SIP/RTP/WebSocket)
  is future work behind a legal/compliance review gate.

## Repo layout

```
crates/voice-runtime/     Rust voice bridge + turn detection (vad, endpointing, semantic, controller) + PyO3 seam
services/orchestrator/    FastAPI control plane + turn-detection API + tests (SQLite/Postgres)
services/ai-runtime/      scripted agent backend + tests
apps/console/             Operator console (generated API client, Voice tab)
docs/                     architecture · implementation-audit · roadmap · risks
```

## Development

```bash
# Python (both storage backends run locally if Postgres is up)
PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/pytest services/orchestrator/tests services/ai-runtime/tests -q
.venv/bin/ruff check services && PYTHONPATH=services/orchestrator/src .venv/bin/mypy --config-file services/orchestrator/pyproject.toml services/orchestrator/src/voxora

# Rust voice bridge (tests + rebuild the Python extension)
cargo test --workspace
make py-voice

# Console
cd apps/console && npm run build
```

License: MIT (workspace-wide).
