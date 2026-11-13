# Audio Generation Phase A–B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Script → downloadable audio asset: provider abstraction (mock + OpenAI), asset table + file store, dedicated `/api/v1/audio/*` routes with the existing auth/audit conventions.

**Architecture:** Additive modules beside the existing ones: `tts_generation.py` (provider protocol + registry), `audio_assets.py` (file store + validation), one `audio_assets` DB table, routes in `api.py`. Existing code is not modified except additive hooks (SCHEMA, JSON_COLUMNS, conftest, Settings).

**Tech Stack:** FastAPI + pydantic, httpx (already a dependency), stdlib `wave` for WAV parsing, SQLite/Postgres via the `Database` seam.

**Spec:** `docs/superpowers/specs/2026-11-13-audio-generation-ab-design.md`

## Global Constraints

- Existing work rules: full suite green (both backends) before each commit; ruff + mypy clean; conventional commits.
- Provider contract (OpenAI, docs verified 2026-11-13): POST https://api.openai.com/v1/audio/speech, Bearer auth, JSON body `{model, input, voice, response_format?, speed?, instructions?}`, response binary. Voices: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse, marin, cedar. Formats: mp3 (default), opus, aac, flac, wav, pcm. Speed 0.25–4.0. Input ≤ 4096 chars. `instructions` only with `gpt-4o-mini-tts`. Live-unverified: no key in this environment — fake-transport tests only.
- Honesty: never claim a voice/language/format the provider does not expose; OpenAI exposes NO language-selection parameter — language is recorded metadata only.
- File store paths built only from internal ids; no user input in paths. Size cap 50 MB; script cap 4096 chars (Phase D lifts it with chunking).
- The M7/P9 boundary stands: no synthesized text into live calls via these routes; `/voice-sessions/{id}/say` stays 404.

## Review Focus

- Path traversal via title/filename → download Content-Disposition must be sanitized; storage path only ever `{tenant}/{asset_id}.{ext}`. Test: Task 4 (`test_download_sanitizes_filename`).
- Cross-tenant access to assets → every query tenant-scoped; download of another tenant's asset id → 404. Test: Task 4 (`test_asset_isolation_across_tenants`).
- Provider failure mid-generation → 502 with sanitized reason, no partial file/row left behind. Test: Task 4 (`test_provider_failure_leaves_no_partial_state`).
- Oversized script (>4096) → 422 naming the limit and the Phase D plan. Test: Task 4 (`test_script_length_limit`).
- Unconfigured OpenAI provider → 501 with configuration guidance, no crash. Test: Task 4 (`test_openai_unconfigured_501`).

---

### Task 1: audio_assets table + Settings storage path

**Files:**
- Modify: `services/orchestrator/src/voxora/db.py` (SCHEMA after `voice_boost_settings`; methods after voice-boost block; `JSON_COLUMNS` + `"processing_history"`)
- Modify: `services/orchestrator/src/voxora/config.py` (`audio_tts_provider: str = "mock"`, `audio_storage_path: str = "data/audio_assets"`, env `VOXORA_AUDIO_TTS_PROVIDER`/`VOXORA_AUDIO_STORAGE_PATH`, expose in `to_public_dict` if a pattern exists)
- Modify: `services/orchestrator/tests/conftest.py` (`_PG_TABLES` + `"audio_assets"`)
- Test: `services/orchestrator/tests/test_audio_assets.py` (create; `database` fixture)

**Interfaces:**
- Produces: `audio_assets` table `id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL, script TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, voice TEXT NOT NULL, language TEXT, speed REAL NOT NULL DEFAULT 1.0, format TEXT NOT NULL, duration_seconds REAL, file_size INTEGER NOT NULL, status TEXT NOT NULL, storage_path TEXT NOT NULL, parent_asset_id TEXT, processing_history TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL` (+ index on tenant). Methods: `create_audio_asset(tenant_id, values) -> Dict`, `get_audio_asset(tenant_id, asset_id) -> Optional[Dict]`, `list_audio_assets(tenant_id, limit, offset, search) -> (List[Dict], int)`, `update_audio_asset(tenant_id, asset_id, **fields) -> Optional[Dict]` (allowed: title, status, duration_seconds, file_size, processing_history), `delete_audio_asset(tenant_id, asset_id) -> bool`, `count_audio_assets_by_status(tenant_id) -> Dict[str, int]` (metrics reuse).

- [ ] **Step 1: Write failing storage tests** — create→get roundtrip (decoded `processing_history`), list paging + search on title, update title, delete returns True then get is None, count-by-status.
- [ ] **Step 2: RED** — `pytest tests/test_audio_assets.py` fails on missing methods.
- [ ] **Step 3: Implement** SCHEMA + methods (bound params; JSON-encode `processing_history` on write).
- [ ] **Step 4: GREEN** + full suite + ruff/mypy.
- [ ] **Step 5: Commit** — `feat(m10): audio_assets table + storage-path settings (both backends)`

### Task 2: TTS provider abstraction — mock + OpenAI

**Files:**
- Create: `services/orchestrator/src/voxora/tts_generation.py`
- Test: `services/orchestrator/tests/test_tts_generation.py`

**Interfaces:**
- Produces:
  - `TTSRequest` dataclass: `script: str, voice: str, model: str = "tts-1", speed: float = 1.0, response_format: str = "mp3", instructions: Optional[str] = None` → `payload()` for the wire body (drops `instructions` unless model is gpt-4o-mini-tts).
  - `TTSResult` dataclass: `audio: bytes, content_type: str, model: str, voice: str, format: str`.
  - `TTSProvider` protocol: `id: str`, `generate(request) -> TTSResult`, `status() -> Dict[str, Any]` (capability-honest: implemented/configured/live labels).
  - `MockTTSProvider` — deterministic mono 16-bit 16 kHz sine WAV (valid RIFF header, ~1 s per 20 chars so duration tests work), `simulated: True`.
  - `OpenAITTSProvider(transport)` — transport injectable (httpx.Client-compatible duck type; default builds `httpx.Client` with base_url https://api.openai.com/v1, timeout 60, Bearer key from settings). Post `/audio/speech`; non-200 → `TTSProviderError(status_code, sanitized detail)` (never leaks the key); empty body → error.
  - `build_tts_provider(settings)` — registry by `settings.audio_tts_provider`; `openai` without key raises `ValueError` (the 501 path).
- Constants: `OPENAI_VOICES` tuple, `OPENAI_FORMATS` tuple, `SPEED_MIN=0.25`, `SPEED_MAX=4.0`, `MAX_SCRIPT_CHARS=4096`.

- [ ] **Step 1: Failing tests** — mock provider returns parseable WAV (stdlib `wave` opens it, nchannels==1, framerate==16000, duration>0); OpenAI request shape (URL, headers WITHOUT the key value in any error, body drops `instructions` for tts-1 and keeps it for gpt-4o-mini-tts, response_format passthrough); 200→bytes result; 401/429→`TTSProviderError` with sanitized detail; empty body→error; `build_tts_provider("openai")` without key → ValueError; unknown provider → ValueError.
- [ ] **Step 2: RED** — module missing.
- [ ] **Step 3: Implement** — fake transport is a small class in the test file (`post(url, headers, json)` returning a stub response object with `.status_code`, `.text`, `.content`); provider uses only those attributes.
- [ ] **Step 4: GREEN** + suite + ruff/mypy.
- [ ] **Step 5: Commit** — `feat(m10): TTS provider abstraction — mock + OpenAI documented-contract provider (fake-transport tests)`

### Task 3: audio_assets.py — file store + validation

**Files:**
- Create: `services/orchestrator/src/voxora/audio_assets.py`
- Test: appended to `tests/test_audio_assets.py`

**Interfaces:**
- Produces: `MAX_ASSET_BYTES = 50 * 1024 * 1024`; `validate_audio_blob(audio: bytes, ext: str) -> Tuple[Optional[float], int]` — non-empty, size cap; WAV/PCM: parse duration via stdlib `wave` (frames/framerate; PCM raw headerless handled as 16 kHz mono f32/i16? — pcm is raw samples at 24 kHz for OpenAI: document honestly, duration None); compressed: `(None, size)`.
  `AudioFileStore(root: Path)` — `path_for(tenant_id, asset_id, ext) -> Path` (root/tenant/asset.ext, ids pre-validated `[a-zA-Z0-9_-]+`), `save(path, audio)`, `open_for_download(path, title, ext) -> (Path, str)` returning sanitized `filename*`/`filename` header value (strip control chars, quotes, path separators; fallback "audio.<ext>"), `delete(path)` (guard: refuse if resolved path not under root).
  `generate_asset(db, settings, tenant_id, actor, payload: AudioGenerateRequest) -> Dict` — orchestrates: validate script length/speed/format/voice against provider constants, id = `new_id("aud")`, provider build (ValueError → ApiError 501 mapped by caller), generate, validate blob, save, DB row status `ready` with duration/size; on `TTSProviderError` raise `AudioGenerationError(status, detail)` with NO row/file left behind.

- [ ] **Step 1: Failing tests** — WAV validation returns duration>0 and size; mp3 blob returns None duration; >50MB rejected; store path under root; download header sanitization (`../../etc/passwd`-style and control-char titles); traversal guard on delete.
- [ ] **Step 2: RED**; **Step 3: implement**; **Step 4: GREEN** + suite; **Step 5: Commit** — `feat(m10): audio file store + blob validation with sanitized downloads`

### Task 4: API routes

**Files:**
- Modify: `services/orchestrator/src/voxora/api.py` (block after voice-boost routes) + `services/orchestrator/src/voxora/models.py` (request models)
- Test: `services/orchestrator/tests/test_audio_api.py` (new; reuse `client`/AUTH conventions)

**Interfaces:**
- Models (`models.py`, `extra="forbid"`): `AudioGenerateRequest` — `script: str (1..4096)`, `voice: str (1..80)`, `model: str = "tts-1"`, `speed: float = Field(1.0, ge=0.25, le=4.0)`, `response_format: Literal["mp3","opus","aac","flac","wav","pcm"] = "mp3"`, `instructions: Optional[str] = Field(None, max_length=4096)`, `language: Optional[str] = Field(None, max_length=32)` (metadata only), `title: Optional[str] = Field(None, max_length=200)`; `AudioAssetUpdate` — `title: str (1..200)` only.
- Routes: `POST /api/v1/audio/generate` (writer-gated, 201 asset meta; 501 unconfigured; 422 script cap; 502 `AudioGenerationError` sanitized); `GET /api/v1/audio/assets` (paged, `search` on title, `status`/`provider` filters); `GET /api/v1/audio/assets/{id}` (404 cross-tenant); `PATCH /api/v1/audio/assets/{id}` (rename, audited); `DELETE /api/v1/audio/assets/{id}` (writer-gated, deletes file + row, audited, 404 unknown); `GET /api/v1/audio/assets/{id}/download` (StreamingResponse or Response with bytes + Content-Type + sanitized Content-Disposition); `GET /api/v1/audio/providers` (capability-honest status: mock `simulated: true`, openai `implemented: true, configured: <key set>, live: false`, supported voices/formats/speed ranges verbatim).
- Asset meta view: id, title, provider, model, voice, language, speed, format, duration_seconds, file_size, status, created_at, updated_at, parent_asset_id, processing_history. NEVER the storage path or script (script available via `GET .../transcript`-style route? No — include `script` in the single-asset GET only, not in list views).

- [ ] **Step 1: Failing HTTP tests** — mock-provider happy path (generate → 201 → asset in list → download returns WAV bytes with sanitized header → rename → delete → 404 after); `test_download_sanitizes_filename`; `test_asset_isolation_across_tenants` (other-tenant token cannot get/download/delete); `test_provider_failure_leaves_no_partial_state` (monkeypatched provider raising `TTSProviderError(502, "boom")` → 502, no row, no file); `test_script_length_limit` (4097 chars → 422 mentioning 4096); `test_openai_unconfigured_501` (settings provider=openai, no key → 501); unknown asset 404s; viewer cannot generate/delete (403) but can list/download.
- [ ] **Step 2: RED** (404s); **Step 3: implement**; **Step 4: GREEN + full suite + ruff/mypy**; **Step 5: Commit** — `feat(m10): /api/v1/audio generation + asset library routes`
- [ ] **Step 6: P9 side-by-side pin** — assert in `test_audio_api.py` that `/api/v1/voice-sessions/{id}/say` still 404s while `/api/v1/audio/generate` works (one test, both boundaries). Include in Step 1's file.

### Task 5: roadmap + audit docs

**Files:** `docs/roadmap.md` (Phase A+B → [x] with honest open-item notes), `docs/implementation-audit.md` (dated section: verified test counts, live-unverified honesty for OpenAI, mock provider description).
- [ ] Commit — `docs: mark M10 Phase A-B complete with honest open-item notes`
