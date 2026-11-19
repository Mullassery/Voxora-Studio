# Voxora Studio

[![CI](https://github.com/Mullassery/Voxora-Studio/actions/workflows/ci.yml/badge.svg)](https://github.com/Mullassery/Voxora-Studio/actions/workflows/ci.yml)
[![PyPI - orchestrator](https://img.shields.io/pypi/v/voxora-orchestrator.svg)](https://pypi.org/project/voxora-orchestrator/)
[![PyPI - AI runtime](https://img.shields.io/pypi/v/voxora-ai-runtime.svg)](https://pypi.org/project/voxora-ai-runtime/)
[![Python](https://img.shields.io/pypi/pyversions/voxora-orchestrator.svg)](https://pypi.org/project/voxora-orchestrator/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)
[![Rust](https://img.shields.io/badge/rust-stable-orange.svg)](https://www.rust-lang.org/)

Open-source AI voice telephony and outbound campaign platform: a FastAPI control plane for consented calling, a deterministic Rust voice bridge (PyO3), a Squid-CRM adapter, an audio generation studio, and an operator console.

> **Status:** Pre-1.0 and simulation-first. Live provider seams exist, but are contract-verified with fakes rather than live-verified. See [Project status](#project-status) and [docs/implementation-audit.md](docs/implementation-audit.md).

## What it is

Voxora Studio owns the campaign and call state: consent checks, suppression, calling windows, retries, audit events, and a deterministic mock telephony path. A Rust bridge models scripted turn-taking and intelligent turn detection behind a PyO3 seam; the console exposes campaigns, calls, voice diagnostics, and audio generation.

The default demo has no code path to a real phone number. Twilio, OpenRouter, and OpenAI TTS are explicit opt-in seams with honest status reporting. Tests and the demo use mocks or fake transports; no paid API call or live service is required.

## Features / Highlights

- **Consent-first campaigns** — tenant-scoped contacts, consent records, suppression, calling windows, scheduling, retries, and an append-only audit trail.
- **Deterministic voice bridge** — Rust turn-taking, scripted dialog, WAV inspection, and a PyO3 seam for the Python orchestrator.
- **Intelligent turn detection** — energy VAD, adaptive endpointing, semantic waits, backchannel-aware barge-in, code-switch grace, and provider-failure resilience.
- **Squid-CRM adapter** — contact sync, webhooks, field discovery, retries, and capability-driven consent/suppression enrichment; Squid's missing capabilities are declared false, not simulated.
- **Audio Generation Studio** — mock-first generation, an asset library, non-destructive variants, long-form generation, CSV batches with a dedicated worker, and console views.
- **Operator console** — React + Vite + TypeScript with a generated OpenAPI client and overview, campaign, contact, call, Voice, and Audio tabs.
- **Honest provider status** — telephony, LLM, STT/TTS routing, and audio TTS status endpoints report what is implemented, configured, live, verified, and simulated.

## Who it's for / Use cases

- **MarTech and campaign operators** who need a consented outbound calling workflow with suppression, calling windows, and retries, without dialing a real number.
- **Voice-AI builders** who want a deterministic scripted bridge and a testable turn-detection engine before committing to a live media provider.
- **CDP and data teams** that need CRM contact sync and webhook replay against a source-verified Squid contract.
- **Content and operations teams** that need generated campaign audio assets, variants, long-form scripts, and CSV batch processing in a local-first console.
- **Maintainers and reviewers** who want provider seams that fail closed and clearly separate documented-contract support from live-server verification.

## What it is not

- **Not a telephony carrier, SIP trunk, or contact center.** The default runtime places no calls and carries no real-time media. SIP/RTP/WebSocket transport is future work behind a compliance review.
- **Not a CRM source of truth.** The Squid adapter syncs contacts and webhooks; consent and suppression enforcement stay in Voxora, and Squid's missing capabilities are never faked.
- **Not a general LLM framework.** It implements one agent-turn contract with a deterministic mock and an opt-in OpenRouter provider.
- **Not a live STT/TTS/VAD stack.** The provider routing table refuses unverified `(provider, modality, language)` combinations; audio generation's OpenAI TTS adapter is documented-contract only.
- **Not a paid-API test harness.** Tests never call live Twilio, OpenRouter, Squid, or OpenAI endpoints.

## Project status

Current release line: **0.2.0** (pre-1.0).

| Area | State |
|---|---|
| FastAPI orchestrator | Working; 468 pytest tests on SQLite + Postgres |
| Rust bridge and turn detection | Working in simulation; 134 cargo tests |
| Operator console | Working; clean production build |
| Squid-CRM adapter | Verified against client source contracts; no live-server test |
| Telephony, LLM, and audio TTS providers | Explicit opt-in seams; contract-verified, live-unverified |
| Audio Generation Studio | Mock-first; 26 batch tests; no paid API calls |

The authoritative detail is in [docs/implementation-audit.md](docs/implementation-audit.md); the remaining roadmap is in [docs/roadmap.md](docs/roadmap.md).

## Installation

Requires Python 3.9+. The Rust voice bridge builds from source and is never published to PyPI; a Rust toolchain is needed for that optional component. Node.js is needed only for the console.

```bash
# Published Python packages
pip install voxora-orchestrator voxora-ai-runtime

# Optional Rust bridge (source-built)
pip install ./crates/voice-runtime

# From a checkout with uv (recommended for development)
uv sync --all-packages
```

See [Packaging: pip and uv](#packaging-pip-and-uv) below for workspace and editable-install details.

## Quick start

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

Docker is also supported:

```bash
export VOXORA_API_TOKENS='{"dev-token": {"tenant_id": "tenant-a", "role": "admin", "name": "Admin"}}'
docker compose up -d
# console: http://localhost:8080   API: http://localhost:8000/api/v1/health
```

## Usage

The API is served under `/api/v1`; interactive OpenAPI docs are at `/docs` on the local server.

```bash
curl -H "Authorization: Bearer dev-token" \
  http://localhost:8000/api/v1/health
```

Main surfaces:

- `/api/v1/campaigns`, `/api/v1/contacts`, `/api/v1/consent`, `/api/v1/suppression` — campaign and eligibility workflows.
- `/api/v1/calls` and `/api/v1/voice-sessions` — call state, scripted sessions, and event replay.
- `/api/v1/voice/turn/*` — voice-turn engine diagnostics.
- `/api/v1/audio/*` — generation, assets, variants, long-form generation, and CSV batches.
- `/api/v1/system/info` — non-secret provider and runtime status.

Produced audio stays in the asset library; the console never injects it into a live call.

### Packaging: pip and uv

The repo is a uv workspace with three installable packages. The Rust extension `voxora_voice_runtime` always builds from source (it is never on PyPI); a Rust toolchain is required.

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

The orchestrator's `[voice]` extra declares the engine as an optional accelerator: `voxora.voice_turn_detection.TurnDetectionEngine` raises `TurnDetectionUnavailable` (and `POST /api/v1/voice/turn/engines` returns 503) instead of faking detection when it is missing.

### Intelligent turn detection (Rust engine)

`crates/voice-runtime/src/controller.rs` is the authoritative detection pipeline for one call: energy VAD with hysteresis, adaptive endpointing (fast/normal/deliberate profiles with speech-rate adaptation), a semantic wait heuristic (incomplete thoughts keep listening; timeouts are honestly labeled), backchannel-vs-genuine barge-in classification (an acknowledgement NEVER cancels playback), language code-switch grace, and a provider-degraded resilience mode. States and events are versioned (`schema_version = 1`, `td_events.rs`).

Mapped end to end:

- **Rust** — `TurnDetectionEngine` PyO3 class (`feed_audio`, `feed_transcript`, `feed_semantic`, `feed_lid`, `feed_agent_event`, `note_provider_failure/recovered`, `end_call`, `snapshot`).
- **Python wrapper** — `voxora.voice_turn_detection.TurnDetectionEngine` (validated args, monotonic seq, typed errors).
- **HTTP** — `/api/v1/voice/turn/*`: `GET config`, `POST engines`, `GET/DELETE engines/{id}`, and per-feature feeds `frames`, `transcript`, `semantic`, `lid`, `agent-events`, `provider-failure`, `provider-recovery`, plus `GET engines/{id}/events`. Tenant-scoped, FIFO-bounded registry.
- **Console** — the Voice tab: probe an engine, run a scripted turn, watch the detection event stream.

Config knob | Env | Default
---|---|---
endpoint profile | `VOXORA_TURN_ENDPOINT_PROFILE` | `normal`
semantic waits | `VOXORA_TURN_SEMANTIC_ENABLED` | `true`
word gate | `VOXORA_TURN_WORD_GATE_MIN_WORDS` | `3`
engine registry size | `VOXORA_TURN_DIAGNOSTICS_ENGINES` | `64`

### Audio Generation Studio

The M10 surface is deliberately separate from live calls. It generates assets with a deterministic mock TTS provider, stores them per tenant, produces non-destructive variants (trim, normalize, channel conversion), generates long-form audio in bounded chunks, accepts CSV batches through a dedicated worker, and exposes Generate / Batch / Library / Processing / Settings in the console. OpenAI TTS is an opt-in, documented-contract adapter and is live-unverified. Produced audio is never injected into a live call.

## Documentation

- [Implementation audit](docs/implementation-audit.md) — what shipped and how it was verified.
- [Architecture](docs/architecture.md) — components, data flow, and provider seams.
- [Roadmap](docs/roadmap.md) — M0–M10 status and deferred work.
- [Risks and assumptions](docs/risks-and-assumptions.md) — what could invalidate current claims.
- [Migration ADR](docs/migration/migration-adr.md) — boundaries with Squid CRM and PyInterviewBot.
- [Capability inventory](docs/migration/capability-inventory.md) — cross-repo ownership.
- [Voice intelligence DSP comparison](docs/voice-intelligence/dsp-engine-comparison.md) — Rust DSP design notes.
- [M10 specs and plans](docs/superpowers/) — audio generation, variants, long-form, and batch design records.

### Repository layout

```text
crates/voice-runtime/     Rust voice bridge + turn detection (vad, endpointing, semantic, controller) + PyO3 seam
services/orchestrator/    FastAPI control plane + voice/audio APIs + tests (SQLite/Postgres)
services/ai-runtime/      scripted agent backend + opt-in OpenRouter provider + tests
apps/console/             Operator console (generated API client, Voice + Audio Generation tabs)
docs/                     architecture · implementation-audit · roadmap · risks · M10 specs/plans
```

## Known issues and deferred work

**Blocked / failed:**

- Live Twilio calls require real credentials plus a compliance review; no live-server verification exists here.
- Live Squid instance verification requires a running server and credentials.
- Real STT/TTS/VAD verification requires observed field data for the quirks registry; the registry is intentionally empty.
- Multi-worker audio batch deployments need a shared lease; the current worker uses a local SQLite/Postgres claim loop.

**Rejected / deferred:**

- SIP/RTP/WebSocket media transport in the default runtime.
- Real calls from the console demo.
- Treating documented-contract or fake-transport tests as proof of live-provider behavior.
- Publishing `voxora_voice_runtime` to PyPI; it stays source-built.

Current verified gaps: [docs/implementation-audit.md](docs/implementation-audit.md). Roadmap detail: [docs/roadmap.md](docs/roadmap.md).

## Contributing

Use fakes and mocks for provider work. Do not add live calls, paid API calls, or live service tests to the default suite; provider status must continue to distinguish implemented, configured, live, verified, and simulated.

### Development

```bash
PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/pytest services/orchestrator/tests services/ai-runtime/tests -q
.venv/bin/ruff check services && PYTHONPATH=services/orchestrator/src .venv/bin/mypy --config-file services/orchestrator/pyproject.toml services/orchestrator/src/voxora
cargo test --workspace
make py-voice
cd apps/console && npm run build
```

`make verify` runs lint, typecheck, Python tests, the console build, and Rust tests in one command. After code changes, run `graphify update .` to refresh the committed code graph.

## Related projects

- [PyInterviewBot](../PyInterviewBot) — interview methodology and rubric scoring; Voxora owns telephony, call state, and agent runtime.
- [PyReverseETL](../PyReverseETL) — activation layer that pushes segments to CRM and marketing destinations.
- [PyCustomerHub](../PyCustomerHub) — CDP ingestion infrastructure that can feed Voxora's contact and audience imports.

[Browse all projects →](../Mullassery-profile)

## License

Licensed under the MIT License (workspace-wide).
