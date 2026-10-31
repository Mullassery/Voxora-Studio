# Voxora Studio — Architecture

**Status: honest.** This document describes what is built and how it fits
together, including what is explicitly *not* built.

## Goal

An open-source, self-contained campaign telephony platform: tenants run
outbound calling campaigns against contact lists, with CRM imports, consent
and suppression gating, call scheduling windows, retries, per-call latency
telemetry, and an operator console. **Every call in this build is simulated**
— there is no live telephony and no credential path that can dial a real
phone number.

## Layout

```
Voxora-Studio/
├── Cargo.toml                      # Rust workspace
├── crates/voice-runtime/           # pure-logic voice pipeline (Rust)
│   └── src/{turn,bridge,wav}.rs    #   turn-taking, scripted bridge, WAV inspection
├── services/
│   ├── orchestrator/               # FastAPI control plane (the working core)
│   │   ├── src/voxora/             #   config, security, state, db, eligibility,
│   │   │                           #   telephony/, crm/, campaign, worker, api
│   │   └── tests/                  #   52 tests
│   └── ai-runtime/                 # deterministic mock agent backend
├── apps/console/                   # React + Vite operator console
├── docker-compose.yml              # orchestrator + console, simulation-only
└── .github/workflows/ci.yml        # python + rust + node CI
```

## Orchestrator (FastAPI)

- **Durable state**: SQLite in WAL mode (`data/voxora.db`). Every mutation is
  a transaction; inflight work is recovered with `recover_inflight()` on
  startup, so a restart re-queues stale in-progress recipients.
- **State machines on disk** (src `voxora/state.py`): explicit CAMPAIGN
  (`draft → validated → running → paused/completed/cancelled`…) and CALL
  transition maps. Illegal transitions raise `InvalidTransition`; the API
  surfaces them as 409.
- **Eligibility gate** (`voxora/eligibility.py`): consent (granted only),
  suppression (list + contact-level), calling window with per-contact
  timezone via `zoneinfo`, retry policy with exponential capped backoff.
  Ineligible recipients get a deterministic status
  (`blocked_consent`, `blocked_suppression`, `outside_calling_window` with a
  computed `retry_at`, etc.).
- **Campaign engine** (`campaign.py`): claim → pre-dial recheck → dial →
  record, with thread-pool concurrency, `dry_run` default, retryable vs
  permanent failure classification, audit trail on lifecycle events.
- **Telephony** (`telephony/`): `mock` adapter that is deterministic
  (sha256 bucketing of recipient+call id → outcome), records simulated
  latency stages, and marks every call `simulated: true`. The mock is the
  decorated default; a `Twilio` adapter is **declared but explicitly
  unimplemented** — selecting it raises at build time rather than
  pretending to work.
- **CRM** (`crm/`): adapter contract (`HttpTransport` injection point),
  credentials sealed with Fernet (`SecretBox`), webhook ingestion with
  HMAC-verification and replay dedupe.
- **Auth**: API bearer tokens mapped to tenant principals with
  admin/operator/viewer roles; per-request scoping by tenant id; constant-time
  token compare.

## CRM integration — Squid (first integration, verified against source)

The Squid adapter (`crm/sqib…squid.py`) was written against verified client
contracts from `Squid-CRM` (commit `015a2e8`):

- REST envelopes `data.people`/`totalCount`/`pageInfo`.
- API key auth as `Authorization: Bearer api_…`.
- `/rest/me` rejects API keys (403), so connection tests use
  `GET /rest/people?limit=1`.
- Webhooks signed `x-squid-signature: sha256=<HMAC-SHA256(raw body)>` on the
  raw bytes, plus `x-squid-event`, `x-squid-event-id`,
  `x-squid-schema-version`, `x-squid-attempt` headers; verified + deduped by
  event id before processing.
- Person fields map `.name.firstName/.lastName`, `.emails.primaryEmail`,
  `.phones.primaryPhoneNumber/primaryPhoneCallingCode`.
- Honest capability declaration: Squid exposes no consent, suppression, or
  call-activity objects, so the adapter reports `consent_supported=False`,
  `suppression_supported=False`, `call_activity_supported=False`. Field
  sync of those objects returns an explicit `NotSupportedError` instead of
  failing silently.
- Redirect flows: not implemented (returned as unsupported in the catalog).

## Voice runtime (Rust)

`crates/voice-runtime` is deliberately zero-dependency pure logic:
turn-taking with barge-in guarding, a scripted bridge that emits the same
event vocabulary and latency stage names as the Python mock, and a minimal
PCM16 WAV header inspection. It is **not** wired to the orchestrator yet; the
seam where a real media backend will plug in is the `DialogBackend` trait.
This keeps the pipeline semantics testable without carrying an audio stack.

## AI runtime (Python)

`services/ai-runtime` implements the agent-turn contract: a deterministic,
script-driven `MockAgent` that honestly reports `real_llm: False` in its
capabilities. No real LLM client exists in this build.

## Console (React)

Single-page operator console (Vite + TS). Token → tenant from
`VOXORA_API_TOKENS`; tabs for overview, campaigns (lifecycle actions),
contacts, and calls. `/api` is proxied to the orchestrator in dev (Vite) and
prod (nginx).

## Enforcement of honesty

- `VOXORA_ALLOW_LIVE_CALLS` defaults to `false` and config validation
  rejects enabling it while the provider is `mock`.
- `GET /api/v1/telephony/providers` reports what is wired vs not.
- Claims in docs/tests refer to executed commands only (see
  `docs/implementation-audit.md`).
