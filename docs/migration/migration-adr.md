# Ecosystem Migration — Architecture Decision Records (Phase 2)

Status: accepted for baseline planning on branch `ecosystem-integration`. Production-code changes follow the sequence in §7 only.

## ADR-0001 — Ownership boundaries (non-negotiable)

| Product | Owns | Never owns |
|---|---|---|
| MudFish | crawling, page fetch/parse, field extraction | voice, interviews, CRM writes |
| QuillSift | document/content extraction to Markdown/text/schema JSON, LLM extraction orchestration | voice, interviews, voice webhooks |
| Squid CRM | contacts, **audiences/segments (query-qualified subsets)**, eligibility/suppression record storage, exports | call execution, telephony, interview methodology, consent *enforcement* |
| Voxora Studio | telephony providers (incl. Twilio, Vapi-compatible adapter), OpenRouter connectivity + model routing, voice-agent runtime, call/session state (sole-writer), recordings/transcripts storage, retries/scheduling/campaign execution, import of audiences | interview methodology, question selection, scoring, transcripts-of-interview interpretation |
| PyInterviewBot | interview templates/stages/competencies, question selection + adaptive depth, evidence graphs, rubrics/scoring, reports | Twilio calls, separate voice-agent runtime, OpenRouter API keys, call scheduling |

Consent model: consent/suppression **records originate in the CRM layer** (Squid or ingestion); **enforcement is Voxora, fail-closed, at session creation** (no consent record ⇒ no dial). PyInterviewBot performs eligibility-agnostic interview work only.

## ADR-0002 — Independence and the no-source-import rule (supersedes any in-process-import idea)

1. No repository imports another repository's source code, database, or internal modules — not even Python-to-Python via path tricks. The Voxora `AgentBrain` protocol exists **for embedding inside a single Voxora deployment only**; cross-product conversation control is the HTTP `instructions` interface (ADR-0003-D).
2. Each product builds/tests/deploys alone (`docker-compose.yml` per product; no shared DB credentials).
3. Cross-product e2e coverage lives in the **dedicated `ecosystem-integration-tests` harness** (separate repo-level dir, Docker Compose, pinned SHAs, mock Twilio + mock LLM, never touches production data). Each repo keeps its own contract tests inline.
4. PyInterviewBot operating modes (all first-class):
   - **Local integration** — `VOXORA_BASE_URL=local instance`
   - **Remote integration** — deployed Voxora + scoped API key
   - **Simulation** — built-in simulation adapter; zero external services; must NOT re-implement the Voxoro voice engine (it replays recorded turn sequences / drives the interview engine directly)

## ADR-0003 — Integration contracts (v1, all versioned envelopes)

### A. Squid CRM → Voxora (audience)
- Squid: `POST /api/v1/audiences` `{name, filter}` (persisted FilterExpr; audited), `GET /api/v1/audiences/{id}` (definition + current version), `POST /api/v1/audiences/{id}/exports` `{format: json|csv}` → export document with `audience_version`, generated via the person SQL compile path; machine auth = Squid API key (Bearer); CSV/JSON files are the portable fallback for offline import.
- Voxora: `POST /api/v1/campaigns/import-audience` per target spec — preserves `external_contact_id`, records `source`, `audience_id`, `audience_version` (hot-wheel campaign provenance), validates `phone` (E.164), checks consent/suppression, idempotent dedupe on (campaign, external_contact_id), per-record reject/error capture with reasons rejected list in the response; consent/suppression never silently overridden (fail-closed rejects, reported).

### B. Voxora → PyInterviewBot (voice sessions)
- `POST /api/v1/voice-sessions` (opaque `external_application=pyinterviewbot`, `external_session_id`, contact ref + phone, `agent.model_profile`, `session_metadata` passthrough incl. `interview_template_id` — opaque to Voxora), `GET /voice-sessions/{id}`, `POST /voice-sessions/{id}/terminate`, `GET /voice-sessions/{id}/transcript`, `GET /voice-sessions/{id}/recording`.
- Conversation steering: `POST /voice-sessions/{id}/instructions {instruction_id, type:"speak", text, priority}` — **capability-honest**: providers that cannot inject mid-call (contract-documented Matrix in Voxora `telephony/registry.py`) return `409` with the provider capability reason; ordered/idempotent by instruction_id.

### C. Voxora events (versioned webhooks out)
Envelope exactly as specified (`event_id`, `event_type: voice_session.*|transcript.ready|recording.ready`, `event_version:"1.0"`, `occurred_at`, `session_id`, `external_application`, `external_session_id`, `data`). Delivery: HMAC-signed, at-least-once, retry + dead-letter + replay, per-dispatcher audit. Consumers (PyInterviewBot) must process idempotently and tolerate out-of-order/late arrival.

### D. PyInterviewBot → Squid CRM (post-interview outcomes)
`POST {squid}/api/v1/outcomes/...` (or audited webhook, whichever Squid surfaces first) storing **references + permitted summary only** (`report_reference`, rubric aggregate scores) — full transcripts and rubric internals stay in PyInterviewBot. Auth: PyInterviewBot-specific scoped API key.

## ADR-0004 — Migration discipline

- Branch-per-repo `ecosystem-integration`, recorded SHAs, small commits, never push to default; rollback = inverse branch.
- Extraction sequence per capability (identify→callers→classify→extract→client→redirect→regression test→remove) before any module deletion; removal list + replacement location documented per module in `docs/migration/extraction-plan.md` (Phase 4 work).
- Working functionality is never deleted before replacement tests pass (Twilio/OpenRouter/live edits included).
- OpenAPI specs per contract land in each producing repo (`docs/openapi/...`) and contract tests are pinned against them in the ecosystem harness; breaking changes require version bump, not silent replacement.
- Live provider truth: mock/contract tests are never cited as proof that Twilio/OpenRouter live calls work; live verification is a separate, credentialed, explicitly-labeled milestone.

## ADR-0005 — Voxora internal modules to introduce (Phase 3)

`API Gateway · Campaign Manager · Audience Importer · Agent Manager · Model Router (existing openrouter.ts → llmproviders) · Conversation Runtime (exists as Rust bridge + AgentBrain hook) · Telephony Gateway (exists) · Session Manager (extends calls) · Call State Engine (exists, sole-writer preserved) · Media Pipeline (Twilio streaming phase) · Transcription Service (stub honest) · Recording Manager (stub honest) · Webhook Dispatcher (new, reuses OrchestratorHMAC + retry patterns) · Analytics (extends reports) · Audit Service (exists)`.

Provider interfaces: `TelephonyProvider {Twilio, Vapi, Mock}`, `LLMProvider {OpenRouter, Mock}`, `TranscriptionProvider {Mock}` — implemented only where code or practical need exists today (Vapi adapter mirrors PyInterviewBot `managed_call.rs` semantics to preserve compatibility).

Phased status: Phase 1 recon ✅ (capability-inventory.md), Phase 2 ✅ (this file). Phases 3–8 are sequenced next; no production code has been modified for the ecosystem integration yet.
