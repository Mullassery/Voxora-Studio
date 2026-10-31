# Roadmap

Deliberately ordered so that every step ends in something runnable and testable.

## M0 — Local, simulation-only showcase (current build ✅)

- [x] FastAPI orchestrator: campaigns, contacts, consent, suppression, calling windows, retries, mock telephony, audit.
- [x] Squid-CRM adapter verified against verified client contracts (contact sync + webhooks).
- [x] Operator console (React) building against the orchestrator API.
- [x] Deterministic contract tests (59 Python tests), Rust voice-runtime tests (15), mypy + ruff clean.
- [x] Execute `docker compose up` end-to-end — both images built, health + contacts + full campaign lifecycle verified through the nginx proxy (see audit).

## M1 — Make the simulation trustworthy at scale

- [ ] Postgres storage backend beside SQLite (same `Database` seam) with a storage-compat test suite.
- [ ] Scheduling: campaign scheduling windows live in the worker loop (currently start-time only).
- [ ] Campaign templates + seasonal timezones in the console.
- [ ] OpenAPI snapshot + generated TypeScript client for the console (kill the hand-rolled `api.ts` DTOs).

## M2 — Real CRM breadth (Squid first, then one more)

- [ ] Live Squid instance integration test (needs an actual server; blocked on environment).
- [ ] Suppression + consent sync for a CRM that exposes such objects; Squid stays honestly incapable of these.
- [ ] Webhook replay testing harness (simulated out-of-order + duplicate delivery).
- [ ] Second CRM adapter (candidate: whatever exposes consent + suppression) to prove adapter portability.

## M3 — Real voice (the hard part)

- [ ] Wire `crates/voice-runtime` to the orchestrator over a prepared API seam (bridge events ↔ orchestrator call events).
- [ ] Real media backend choice: SIP/RTP gateway via provider SDK, or platform WebSocket media (e.g. provider media streams). Decision gate: legal + carrier compliance review first.
- [ ] Twilio (or equivalent) adapter implemented and tested against sandbox numbers only; `allow_live_calls` flips only with `TELEPHONY_PROVIDER != mock` and an explicit operator acknowledgment (per-call confirmation flag, not just env var).
- [ ] Streaming audio pipeline in the voice-runtime (jitter buffer, barge-in VAD) — the turn-taking machine is already written and tested for this.
- [ ] Real STT/TTS integration contract in `ai-runtime` (replace `real_llm: False` with a pluggable LLM client; scripted agent stays as the test default).

## M4 — Production hygiene

- [ ] Run CI on GitHub (workflow written; repo not pushed).
- [ ] Multi-worker safety: SQLite locks become Postgres advisory locks; campaign claims move to `SELECT … FOR UPDATE SKIP LOCKED`.
- [ ] Rate limiting + quota per tenant; token rotation endpoint; operator audit export.
- [ ] Observability: Prometheus metrics endpoint next to the JSON logs; call latency dashboards off the existing `call_latency` data.
