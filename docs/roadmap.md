# Roadmap

Deliberately ordered so that every step ends in something runnable and testable.

## M0 — Local, simulation-only showcase (current build ✅)

- [x] FastAPI orchestrator: campaigns, contacts, consent, suppression, calling windows, retries, mock telephony, audit.
- [x] Squid-CRM adapter verified against verified client contracts (contact sync + webhooks).
- [x] Operator console (React) building against the orchestrator API.
- [x] Deterministic contract tests, Rust voice-runtime tests, mypy + ruff clean (test counts below grew per milestone).
- [x] Execute `docker compose up` end-to-end — both images built, health + contacts + full campaign lifecycle verified through the nginx proxy (see audit).

## M1 — Make the simulation trustworthy at scale (✅ 2026-11-01)

- [x] Postgres storage backend beside SQLite (same `Database` seam) with a storage-compat test suite (24 tests, parametrized over both backends; `psycopg` is a hard orchestrator dep).
- [x] Scheduling: `Database.promote_due_campaigns()` flips scheduled→running in the worker loop when `schedule_at` arrives (audited as `campaign.due_promoted`).
- [x] Campaign templates in the console (Welcome / Win-back / Reminder presets). *Still open: seasonal dialing-timezone presets.*
- [x] OpenAPI-driven generated TypeScript client for the console (`services/orchestrator/scripts/generate_ts_client.py` → `apps/console/src/generated/`; response envelopes still hand-typed).

## M2 — Real CRM breadth (Squid first, then one more)

- [ ] Live Squid instance integration test (needs an actual server; blocked on environment).
- [x] Consent + suppression sync for a CRM that exposes such objects — capability-driven enrichment in `crm/sync.py` with contract tests (memory capable-adapter + Squid stays honestly incapable).
- [x] Webhook replay testing harness: duplicate idempotence, tampered-replay rejection, no-resurrect-on-replay, last-accepted-out-of-order semantics (`tests/test_webhook_replay.py`).
- [ ] Second CRM adapter (candidate: whatever exposes consent + suppression) to prove adapter portability.

## M3 — Real voice (the hard part)

- [x] Wire `crates/voice-runtime` to the orchestrator: PyO3 extension `voxora_voice_runtime` (`make py-voice`) exposes the scripted bridge JSON; `VoiceRuntimeTelephonyAdapter` maps it onto `TelephonyResult` (simulation-only; seam tested with the real module). *Still open: real media transport in the bridge itself.*
- [ ] Real media backend choice: SIP/RTP gateway via provider SDK, or platform WebSocket media (e.g. provider media streams). Decision gate: legal + carrier compliance review first.
- [x] Twilio adapter implemented against the documented REST contract with fake-transport tests; async settlement wired through signed webhooks. *Still open: live-server verification (needs credentials).* `allow_live_calls` flips only with a real provider configured and an explicit operator env flag.
- [ ] Streaming audio pipeline in the voice-runtime (jitter buffer, barge-in VAD) — the turn-taking machine is already written and tested for this.
- [ ] Real STT/TTS integration contract in `ai-runtime` (replace `real_llm: False` with a pluggable LLM client; scripted agent stays as the test default).

## M4 — Production hygiene

- [x] Run CI on GitHub (green on `ubuntu-24.04` pinned Python job; runs 41192274198, 41259238748).
- [ ] Multi-worker safety: SQLite locks become Postgres advisory locks; campaign claims move to `SELECT … FOR UPDATE SKIP LOCKED`.
- [ ] Rate limiting + quota per tenant; token rotation endpoint; operator audit export.
- [ ] Observability: Prometheus metrics endpoint next to the JSON logs; call latency dashboards off the existing `call_latency` data.
