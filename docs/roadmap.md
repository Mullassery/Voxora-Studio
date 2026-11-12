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
- [x] Second CRM adapter — LeadConnector (GoHighLevel) with documented call-suppression read (`dndSettings.Call.status`, contract verified against marketplace.gohighlevel.com docs retrieved 2026-11-12, live-unverified), adapter-portability proven end-to-end through the sync engine (suppression applied per record, config-in-db `location_id`). Consent-read remains False honestly (no documented consent object); provider-supplied consent proven via the capability-driven contract tests.

## M3 — Real voice (the hard part)

- [x] Wire `crates/voice-runtime` to the orchestrator: PyO3 extension `voxora_voice_runtime` (`make py-voice`) exposes the scripted bridge JSON; `VoiceRuntimeTelephonyAdapter` maps it onto `TelephonyResult` (simulation-only; seam tested with the real module). *Still open: real media transport in the bridge itself.*
- [ ] Real media backend choice: SIP/RTP gateway via provider SDK, or platform WebSocket media (e.g. provider media streams). Decision gate: legal + carrier compliance review first.
- [x] Twilio adapter implemented against the documented REST contract with fake-transport tests; async settlement wired through signed webhooks. *Still open: live-server verification (needs credentials).* `allow_live_calls` flips only with a real provider configured and an explicit operator env flag.
- [x] Streaming audio pipeline in the voice-runtime — the bounded jitter-tolerant chunk ring + per-hop latency tracking + barge-in gates shipped with the M6 media engine (f276a43); real-time integration tests ride the fake media WS.
- [ ] Real STT/TTS integration contract in `ai-runtime` (replace `real_llm: False` with a pluggable LLM client; scripted agent stays as the test default).

## M4 — Production hygiene

- [x] Run CI on GitHub (green on `ubuntu-24.04` pinned Python job; runs 41192274198, 41259238748).
- [x] Multi-worker safety: Postgres claims use an advisory transaction lock + `SELECT … FOR UPDATE SKIP LOCKED` (plus a shared rowcount guard), proven by a two-worker race test on both backends; SQLite keeps `BEGIN IMMEDIATE` serialization.
- [ ] Rate limiting + quota per tenant; token rotation endpoint; operator audit export.
- [x] Observability: Prometheus `/metrics` text exposition (cross-tenant status counters, queue/active gauges, per-stage latency p50/p95 gauges from `call_latency`, bearer-token scrape auth). *Still open: console latency dashboards (the JSON log and metric surfaces exist).*

## M5 — Make the integration contracts real (P0) — COMPLETE 2026-11-04

Shipped: `bf40ed7` (defects V1–V3 + /ai/decide route tests), `e4c81c1` (PyReverseETL transport + production dispatch wiring), `72f69d9` (recording callbacks). Requires PyReverseETL >= 3.6.0 (PyPI: extra_headers + WebhookConnector).

Every ADR-0003 promise must have a production call path, not just a test
harness. Pattern provenance (P-ids) is held in the internal ecosystem research
dossier (kept separately from both product repos); nothing below names other
products.

- [x] **Webhook delivery moves to the PyReverseETL transport (P1).**
  `dispatch_session_events`/`deliver_webhook` today run bounded single-pass
  (max 3 attempts, no backoff, no dead-letter, no replay) and are invoked by
  nothing outside tests. Per the ecosystem ownership rule, queue buffering,
  backpressure, dead-lettering, and replay are **delegated to PyReverseETL**
  — its webhook destination (real auth), retry-with-backoff, idempotency
  ledger for crash-mid-batch recovery, and lineage edges are exactly this
  contract; do not rebuild a dead-letter state machine in-repo. Voxora
  enqueues the signed envelope (HMAC signing stays Voxora-side: the signing
  secret never leaves this repo), PyReverseETL owns retry / backoff /
  dead-letter / replay and records delivery lineage; `voice_session_deliveries`
  rows remain the Voxora-side audit trail of enqueue + terminal state.
  Acceptance: kill the receiver mid-batch; PyReverseETL retries with backoff,
  dead-letters terminal failures, and replays them; every enqueue/replay
  appends a delivery row and a lineage edge; replays carry a byte-identical
  signature. Tests: enqueue idempotence + signature stability across replay
  in-repo; transport behavior verified against a running PyReverseETL sync
  (live, not mocked).
- [x] **Ship the three verified defects** (audit 2026-11-04) that block
  contract consumers: (a) `OpenRouterAgent`/`OpenRouterLLM` construct
  `httpx.AsyncClient` but call it synchronously → `/api/v1/ai/decide`
  500s under `VOXORA_LLM_PROVIDER=openrouter` (reproduce in test, fix to
  injectable sync `httpx.Client` default); (b) voice-session `transcript`
  stored as TEXT outside `JSON_COLUMNS` → transcript endpoint returns a JSON
  *string*, not an array (add strict `entries` array assertion to
  `test_voice_sessions.py`); (c) structured-log formatter drops standard
  `extra=` fields (`observability.py` promotes only `_`-prefixed keys) —
  fix promotion, assert log content in a test.
- [x] **`/api/v1/ai/decide` route tests** (zero coverage today): mock path
  determinism + audit row, openrouter-unconfigured 501, openrouter-unavailable
  503, usage in response.
- [x] **Recording callbacks (P2, differentiator: no reference implements
  telephony at all).** Twilio `RecordingUrl`/`CallEnded` recording callbacks →
  store recording reference on the call; emit `recording.ready`; downstream
  report consumers read recordings via the outbound contract only — the
  media path stays closed to them (reference only).
  Acceptance: seeded dry-run recording callback settles call with recording
  pointer + event; unsigned callback rejected.

## M6 — Real-time media bridge (P1, extends M3's open media item)

The turn machine exists; this milestone gives it real audio, adopting four
verified patterns from competing browser-voice implementations (P3–P6):

- [x] **Twilio Media Streams WebSocket backend in `voice-runtime` (Rust):
  audio engine delivered (f276a43).** `<Connect><Stream>` bidirectional audio;
  μ-law 8kHz ↔ 16k PCM converter
  registry + format negotiation modeled on a competing implementation's
  PCM-native engine (P3): ring buffer, jitter-tolerant chunk pool, latency
  tracker per hop. Acceptance: scripted end-to-end test rides a fake media WS:
  100 injected μ-law frames produce one utterance ⭢ decision ⭢ TTS frames out;
  per-hop latency counters non-zero (fake transport per acceptances; the
  socket-backed transport mounts behind the `MediaTransport` seam in M7).
  Legal/carrier review gate first (M3 decision gate stands).
- [x] **Echo-safe barge-in decision function (P4).** Adopt the pure,
  unit-testable decision curve (echo cooldown window + RMS threshold across
  N frames + minimum elapsed TTS time) as the Rust-side barge-in gate instead
  of raw VAD energy only; keep the existing barge-in in-guards as the state
  machine layer. Acceptance: table-driven tests proving playback echo cannot
  self-trigger and candidate speech ≥ threshold with ≥3 frames does.
- [x] **Transcribed-word-gated interruption (P5).** Barge-in confirmed only
  after N transcribed words from STT (not bare VAD onset), so door slams and
  music don't kill a question — competing implementation verified value N=3.
  Acceptance: frames-only noise → no interruption; partial ASR words →
  interrupt after N.
- [x] **In-flight session checkpointing + restart recovery (P6; 8e8b05c).**
  Off the
  turn-critical path, flush a transcript checkpoint every ~20 s (bounded
  queue; ≥2.5× heartbeat while dialing); on worker restart, recover answers
  the agent failed to persist by re-reading the verbatim transcript
  snapshot with a substance gate (short junk discarded, terminally-recovered
  answers marked `source: "recovered"`). Acceptance: kill -9 the worker
  mid-session; restarted session shows ≤1 flush interval of loss, recovered
  answers present and scored, provenance tagged. Tests cover corruption,
  empty and boundary cases.
- [x] **Session cost guards (77e7b65).** Hard per-session ceilings (config: max
  duration, max turns, token budget) enforced in the voice session engine;
  breach ⇒ orderly terminate with `voice_session.terminated` reason
  `budget_exhausted`; usage persisted (see M7). Matches competing 20-min/
  80-turn guardrails but implemented Voxora-side where sole-writer state lives.

## M7 — Live turn loop + model routing (P1)

Closes the audit finding that the AI layer currently never participates in a
live call (TwiML `<Say>` from the static prompt only).

- [x] **Real STT/TTS chain inside the live session** (M3 open item): the
  `LiveTurnEngine` loop is mounted over HTTP for `voice-runtime` sessions —
  `POST /voice-sessions` opens the live session (agent opening line spoken,
  session stays in-flight) and `POST /voice-sessions/{id}/turns` drives one
  turn from caller STT deltas to server-spoken segments; mid-call
  instructions are drained at the stream boundary and spoken ahead of the
  agent reply; `mid_call_instructions` stays True for the media backend only.
  *Still open: the Rust socket transport behind `MediaTransport` (M6 seam);
  the HTTP seam is exactly what it will call.*
- [x] **STT/TTS provider routing table (P7).** `voice_providers.py` chains +
  verified-quirks registry exposed at `GET /api/v1/voice/providers` with the
  literal `UNVERIFIED` label and `attemptable: false` for unverified
  combinations (`tests/test_voice_providers.py`). The registry stays empty
  by design until dated, provenanced field observations exist.
  *Still open: no real vendor adapters are configured yet (M9).*
- [x] **Real model_profile dispatch (P8).** `model_profiles` table is seeded
  config-in-db and actually dispatches: `/api/v1/ai/decide` resolves
  (profile, task) → model, unknown profile ⇒ 501 with the configured list;
  usage/cost rows persist per (tenant, profile, task, model) in `ai_usage`
  for both the decide route (analyze) and the live turn loop (turn).
- [x] **Server-authoritative TTS only (P9).** No client-controlled
  arbitrary-text synthesis endpoint exists or was added; spoken content
  comes exclusively from the agent or the instruction store — pinned by
  tests (`test_no_arbitrary_text_tts_endpoint`,
  `test_client_deltas_never_appear_in_spoken_content`).

## M8 — Integrity signals (P2)

- [x] **Speech-analytics metadata per call/report (P10).** Deterministic
  estimates (pause count via interior-silence runs ≥ 400 ms, audio-measured
  words-per-minute, filler-word counts) computed once per finalized
  transcript at settle, stored in `session_metadata.speech_analytics`, and
  exposed via the outbound contract (transcript.ready event data + session
  view). Stdlib WAV decoder (see the ruling in `voice_analytics.py`: ffmpeg
  absent in this environment/CI). Absent audio ⇒ field omitted, never
  zero-fabricated. *Still open: applying it to real recorded audio from the
  telephony callbacks (reference-only URLs today).*
- [ ] **Candidate-consent voice cloning / identity checks stay out of scope**
  unless record consents explicitly classify allowed models.

## M9 — Voice Intelligence Suite (in progress, branch `feature/voice-intelligence`)

Integrated voice intelligence: audio enhancement + open-source multilingual
transcription + observability, per the discovery-first master spec. Discovery
report (existing-state architecture audit) is complete; every item below keeps
the capability-honesty contract — no neural/integrated claim without a working
implementation and measurements.

- [ ] **DSP engine evaluation + selection.** Evaluate WebRTC APM, RNNoise,
  DeepFilterNet, SpeexDSP against licensing, Rust integration, CPU/memory,
  quality, latency, sample rates; select (do not install all indiscriminately).
  Report the comparison in docs.
- [ ] **Voice Boost engine (Rust, modular DSP).** Noise suppression, echo
  cancellation where valid render reference exists, AGC without clipping,
  speech clarity, dynamic EQ, de-essing, loudness normalization, silence/VAD
  awareness, clipping detection. Insertion point: between
  `FormatRegistry::convert` and `ChannelRing::push` on the inbound path
  (media.rs) — ahead of `frame_rms`/VAD/barge-in gates; thresholds
  re-validated against enhanced audio.
- [ ] **Voice Boost profiles.** NATURAL / CLEAR / STUDIO presets + independent
  per-control overrides, global defaults and per-call settings, master
  switchable off (bypass preserves original audio exactly).
- [ ] **Open-source multilingual transcription.** WhisperLiveKit + faster-whisper
  as primary engine (no paid cloud ASR); licenses verified before integration;
  model-size choice by benchmark on available hardware. Streaming + batch,
  auto/manual language, partial/final transcripts, timestamps, diarization
  where supported, optional separate translation (original never silently
  translated). Language priorities: English, Hindi, Malayalam, Kannada, Tamil,
  Telugu; code-switching (hi/mal/kn-English mixed) tested explicitly, not assumed.
- [ ] **Integrated audio pipeline.** Independent inbound (decode → Voice Boost
  → format adapter → ASR → normalizer → conversation engine) and outbound
  (agent → TTS → optional enhancement → encode → streams) paths; no mic-style
  suppression on synthesized speech; no blind AEC on TTS; bounded queues,
  async processing, per-call session isolation, safe DSP bypass that never
  terminates a call; correct μ-law/PCM rate handling, minimal conversions.
- [ ] **Rust audio gateway extensions.** Frame routing, codec adaptation, DSP
  engine management, transcription session management, per-call config, health
  monitoring, error recovery, backpressure. Provider-independent seams for
  enhancement and ASR — no coupling of the call lifecycle to WhisperLiveKit
  internals.
- [ ] **React Audio Intelligence dashboard.** Voice Intelligence section in the
  existing console: Voice Boost panel (profiles, per-control toggles, clipping
  indicators, engine status), Transcription panel (enable/disable, language +
  model selectors, live partial/final transcript, detected language, speaker
  labels, timestamps, translation toggle, latency, model health),
  Observability panel (call/activation counts, frame processed/dropped,
  DSP + transcription p50/p95/p99, CPU/RAM/GPU where available, errors,
  service health). Existing design conventions, no separate app.
- [ ] **Performance targets with measurements.** <20 ms p95 Voice Boost added
  latency per frame where achievable; transcription measured separately
  (time-to-first-partial, final latency, RTF, WER/CER, language-detection
  accuracy). Concurrency tested at 1/5/10/20 calls where hardware permits.
  No unbounded queues, no telephony-event-loop blocking, no repeated model
  loads. Report numbers, never target claims without measurements.
- [ ] **Docker + deployment.** Rust gateway + Python ASR service + DSP deps +
  model persistence in Docker/Compose; CPU-only mandatory, NVIDIA optional;
  health checks, resource limits, startup/shutdown handling.
- [ ] **Tests.** Voice Boost (noise suppression, AEC where applicable, gain,
  clipping, silence, frame boundaries, invalid input, codec conversions,
  bypass, DSP failure, per-call isolation); transcription (en/hi/ml/kn/ta/te,
  mixed speech, accents, telephone bandwidth, noise, partial/final, timestamps,
  diarization, reconnection, concurrent sessions); integration (boost on/off ×
  transcription on/off matrix, DSP failure with transcription continuing, ASR
  failure with call continuing, full Twilio→AI→TTS flow).
- [ ] **Security & privacy.** Self-hosted audio + transcripts; no raw caller
  audio in logs; transcript-retention configuration; per-call session
  isolation; protected internal WebSockets; documented data handling + consent
  requirements; secrets outside source control.
- [ ] **Documentation + final verification report.** Architecture, DSP engine
  comparison, transcription + language support matrix, pipeline diagrams, API
  docs, Docker deployment, benchmark reports, troubleshooting, security,
  licensing inventory; final engineering report with measured results and
  stated limitations.

## Explicit non-goals (patterns rejected from competitor adoption)

- Per-session question randomization as the *sole* adaptivity mechanism
  (temperature + random prompt "angles"): fine for candidate practice tools,
  contradicts cross-candidate comparability that registered screening
  requires; evidence templates + rubrics stay the spread-control mechanism.
- Video-avatar interviewer rendering: reference packaging exists but all
  avatar assets are placeholders 404ing by design; watch, don't build.
- In-browser practice-mode gamification: belongs to candidate-side products,
  not assessment infrastructure.
- PTT desktop-audio drivers (reference's unique but unmotivated piece given
  our PSTN-first posture).

