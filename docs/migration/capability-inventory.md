# Ecosystem Migration — Capability Inventory (Phase 1)

Pipeline: **MudFish → QuillSift → Squid CRM → Voxora Studio → PyInterviewBot**
Migration branch: `ecosystem-integration` (baseline SHAs — Voxora `ebd1d04`, Squid `015a2e8`, PyInterviewBot `24e67ca`).
Rule of record: every product is independently installable, testable, deployable, maintainable. Integration only via documented HTTP APIs, signed webhooks, or portable file formats. **No cross-repository source-code imports; no shared databases.**

---

## 1. MudFish (only upstream crawler; keep its lane)

- Rust async web crawler + PyO3 Python bindings (`mudfish.crawl()`, `extract_fields()`); CLI artifacts: `CrawlResult` JSON / JSONL (`crates/core/src/types.rs`).
- **No outbound push** (no webhooks/HTTP POST): downstream is pull-based today.
- **Zero boundary violations** — no voice/interview/CRM code. No changes required; the JSON/JSONL artifact is the natural QuillSift ingest format (thin feeder adapter lives with the consumer, not in MudFish).

## 2. QuillSift (only document/knowledge processing; keep its lane)

- Rust HTML → Markdown/text/links + optional schema-validated LLM extraction; PyO3 (`_quillsift`), CLI (`fetch/crawl/extract`), MCP server.
- `parse_html(html, base_url)` is the no-network seam that consumes MudFish page bodies. No native `CrawlResult` reader — the feeder adapter belongs to the packaging/orchestration layer, **not QuillSift itself**.
- **No outbound writers, no webhooks**; zero voice/interview/CRM code. No changes required.

**Upstream rule:** MudFish and QuillSift never call Voxora/PyInterviewBot APIs and never receive voice webhooks. They terminate at producing structured content artifacts.

## 3. Squid CRM (owns contacts, segmentation, audiences, eligibility records; never executes calls)

Facts that shape the audience contract (paths verified in repo):

| Area | Reality |
|---|---|
| DB | PostgreSQL only (sqlx), shared `core` schema + per-workspace schema (`crates/squid-db/migrations/`) |
| Person | `workspace/0001_person.sql` + `0002_person_contract.sql`: `primary_email`, `additional_emails[]`, `phones` JSONB, `job_title`, `company_id` (no FK), soft delete |
| Filters | `FilterExpr` AST (Person + Company leaves, And/Or/Not) `crates/squid-core/src/lib.rs:378`; SQL compiler `crates/squid-db/src/lib.rs:238`; keyset pagination `(created_at,id)`; consumed by REST `parse_filter` (`crates/squid-api/src/rest.rs:3854`) and GraphQL `PersonFilterInput` (`crates/squid-api/src/graphql.rs:702`) |
| Audience primitive | **None exists** — no segment/list/audience/tag/campaign table; closest artifact is UI `views` (`core/0004_views.sql`), not reusable as a query |
| Consent/suppression | **None in product**; documented as adjacent-repo concern (`ECOSYSTEM_INTEGRATION.md`) — records will arrive from upstream organizers, Voxora enforces fail-closed at call time |
| Webhooks | Mature: HMAC-SHA256 signing, durable delivery + retry + replay (`core/0009/0012/0013_*.sql`, `crates/squid-api/src/rest.rs:2900-2963`) — the transport for audience events |
| Machine auth | Workspace API keys (`core.api_keys`, SHA-256 hash; `generateApiKeyToken` GraphQL) + JWT bearer → Voxora/PyInterviewBot will each use their own scoped key |
| Two filter engines | `FilterExpr`→SQL (persons/opportunity/task/note) vs in-memory `FilterNode` (`crates/squid-api/src/filtering.rs`, companies) — audience queries go through the SQL engine only |
| Python client | `python/src/squid_crm/SquidClient` (http helpers, graphql, list/create person) — thin; audience methods will be added here |
| Tests | `cargo test --workspace`; integration DB via `TEST_DATABASE_URL`; clippy `-D warnings` |

Planned Squid-side work (Phase 5): **`audience` entity** = named, persisted `FilterExpr` + snapshot counter; `POST/GET /api/v1/audiences`, `POST /api/v1/audiences/{id}/exports` (JSON/CSV rendering of a fresh keyset materialization, audience_version stamped), person-audience membership via the SAME compile path (`compile_filter`) so segments can never diverge from the query engine. No consent columns invented — eligibility travels as metadata + external consent references.

## 4. Voxora Studio (owns telephony, voice agents, OpenRouter, call state, campaigns; never interview methodology)

Current posture (this repo, mostly pre-verified Phase 1–7 work):

| Capability | Status |
|---|---|
| Telephony providers | Registry: `mock`, `voice-runtime` (PyO3 bridge), `twilio` (REST create/cancel, signed status webhooks, callback status map, dry-run isolation, live-gating via `VOXORA_ALLOW_LIVE_CALLS`) |
| Call state | Orchestrator SQLite/Postgres, sole-writer settle path (`settle_provider_call`), webhook replay dedupe tests |
| LLM | `voxora_ai.openrouter` (OpenAI-compatible chat completions, `usage.include:true` billing capture `cost`/tokens, retryable-status map, response_format fallback, AgentBrain turn hook, fail-closed provider selection `VOXORA_LLM_PROVIDER`) |
| Voice runtime | Rust `voxora-voice-runtime` + PyO3 bridge wheel (maturin), `ScriptLatency` camelCase export, 20 feature tests |
| CRM ingest | Squid adapter (physiques + company metadata → `custom_fields`), consent/suppression capability-driven sync, webhook replay harness |
| API | FastAPI `/api/v1/...`, HMAC webhook signatures, audit trail, campaign engine with idempotent campaign/call creation, retries |
| Missing for contracts | `POST /api/v1/campaigns/import-audience`, `voice-sessions` lifecycle endpoints, mid-call `instructions` interface, versioned webhook dispatcher to `external_application`, transcript/recording retrieval, OpenTelemetry Prometheus traces |

## 5. PyInterviewBot (owns interview methodology only; consumes Voxora, never telephony/LLM-provider infra)

Verified inventory (see recon detail in conversation record):

- **To extract → Voxora:** gateway `twilio.rs` (create/call/SMS/WhatsApp + HMAC verify, env config), `voice-runtime` crate (VoiceProvider trait, VoiceRuntime, studio, neural, mock), `gateway/calls.rs` call FSM store (rusqlite WAL, GATEWAY_CALLS_ENABLED), `scheduler.rs` + `reminders.rs` dispatch layers (telephony actions), `native_voice.rs` routes, `managed_call.rs` (Vapi-compatible client — becomes a Voxora **provider adapter', Vapi behind Voxora's TelephonyProvider), `providers/openrouter.py` (already refused: registry drops openrouter), `ai_client.rs` HTTP contract replaced by Voxora client.
- **To retain:** `agent.py` (evidence graph + question decision loop), `templates.py` (frozen snapshots + workflow pinning), `evidence.py`, `compliance.py` secrescreening (`_safe_question` — must stay intrare-domain), `report.py`/`reporting.py`, `workflow_*` lifecycle, `db.py` (SQLite, tenant isolation), `channels.py` external-id mapping (re-pointed to Voxora sessions), `providers/base.py` (LanguageModel protocol stays as the **simulated-provider** seam for local/mock), FastAPI main.py.
- **Turn interface today:** `agent.take_turn(session_id, transcript) -> TurnResponse(ai_text, is_complete)` (`agent.py:262`) ≡ the over-the-wire `next_turn`. Reduce-loss mapping: Voxora `POST /voice-sessions/{id}/instructions` ↔ PyInterviewBot issues next question; Voxora event webhooks feed transcripts back; template snapshot stays solely in ai-service DB (Voxora never caches it).
- **Standalone modes:** `local` (Ollama/faster-whisper/Piper), `mock` (CI), plus new `simulation` adapter for Voxora-missing operation. The three operating modes (local-integration / remote-integration / simulation) map to `VOXORA_BASE_URL` present vs absent.
- **Risks carried into Phase 4:** gateway is sole call-state writer today (Voxora must inherit sole-writer rule); compliance screening must not move; `speak_recovery` hardcoded fallback duplicates agent fallback text (trim at extraction); stale root Dockerfile (dead); duplicate FSMs are intentional but must be pruned of telephony states once calls move to Voxora.
