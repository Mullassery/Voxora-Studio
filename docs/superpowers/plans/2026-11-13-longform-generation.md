# Audio Generation Phase D (Long-form) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scripts beyond the provider's 4096-char limit produce ONE assembled asset via paragraph-preserving chunking, identical voice settings per chunk, per-chunk failure tracking, and retry-without-regeneration.

**Architecture:** A pure-Python chunker (`longform.py`) splits the script and stores a manifest (header entry + per-chunk entries) in `processing_history`; chunks are generated through the EXISTING `generate_asset`-equivalent provider path as separate files, assembled frame-exact with stdlib `wave`; `retry-failed` regenerates only failed chunks from the stored snapshot.

**Tech Stack:** Python/FastAPI orchestrator, stdlib `wave`, existing TTS provider abstraction, SQLite+Postgres parity (no schema change).

**Spec:** `docs/superpowers/specs/2026-11-13-longform-generation-design.md` (READ WITH THIS PLAN — the 6 settled builder-check calls at the bottom are binding).

## Global Constraints

- Chunk cap 64; `max_chunk_chars` default 4096, range 500–4096; hard-split at word boundary only as last resort.
- One settings set per run; retry rebuilds each chunk's request from the manifest header snapshot verbatim (`instructions` included).
- Long-form surface: `response_format: wav` (and `pcm`: raw concat, duration `None`); mp3/opus/aac/flac → 422 with the in-repo-decode reason.
- Manifest first entry is the header: `{"kind": "longform", "run_id", "settings": {...}, "max_chunk_chars"}` — long-form identification is `history[0].get("kind") == "longform"`.
- Row lifecycle: created immediately with `status: "processing"`, `file_size: 0`, `duration_seconds: None`, `storage_path` = final path. Chunk failures keep `processing`; assembly failures are terminal `failed` (sanitized error in history).
- Run lock: module-level set of asset_ids in `longform.py`; `retry-failed` while locked → 409 `run_in_flight`; unlocked `processing`/`failed` rows retryable (crash recovery).
- Download of a `processing` asset → 409 `still_processing`; variants on a non-`ready` asset → 422.
- DELETE removes the assembled file AND all `{asset_id}_chunk_*` files.
- Chunk files: `Path(settings.audio_storage_path) / f"{tenant}/{asset_id}_chunk_{i}.wav"` — ids only, never user text, in paths.
- Repo bar per commit: full suite green (SQLite + Postgres), ruff clean, mypy clean with `--config-file services/orchestrator/pyproject.toml`, conventional commits, ledger via task-start/task-done.
- Test commands (repo root): `PYTHONPATH=services/orchestrator/src:services/ai-runtime/src .venv/bin/python -m pytest services/orchestrator/tests services/ai-runtime/tests -q`.

## Review Focus

1. **Retry regenerates succeeded chunks** — a reasonable person expects only failed chunks re-run; deterministic mock makes byte-identity provable. Pin: fail-chunk-2-once test asserts chunk 1/3 audio bytes unchanged (Task 3).
2. **Retry uses different boundaries than the first run** — anti-goal; boundaries and chunk text are STORED and reused verbatim. Pin: retry test asserts manifest chunk texts unchanged after retry (Task 3).
3. **`ready` claimed with failed chunks in manifest** — status vocabulary must be honest. Pin: partial-failure run leaves `processing`, never `ready` (Task 2/3).
4. **Assembly size-cap dead end** — max legal input can exceed 50 MB assembled; row goes terminal `failed` with a message naming the remedy (shorter script), NOT an eternal `processing`. Pin: assembly-cap test with oversized blob (Task 2).
5. **Chunk text ≠ provider input** (off-by-one from boundary handling) — assembled audio must contain every paragraph; verbatim rule pinned by reconstructing the script from chunk texts (Task 1).

---

### Task 1: Chunker (`longform.py` part 1)

**Files:**
- Create: `services/orchestrator/src/voxora/longform.py`
- Test: `services/orchestrator/tests/test_longform.py`

**Interfaces:**
- Consumes: nothing (pure functions).
- Produces (for Tasks 2–3): `MAX_CHUNKS: int = 64`, `chunk_script(script: str, max_chunk_chars: int) -> List[str]` (raises `ValueError` when chunk count > `MAX_CHUNKS`, message includes the actual count; raises on empty/whitespace-only script), and `LONGFORM_HEADER_KEYS` not needed yet — header built in Task 2.

- [ ] **Step 1: Write failing tests** (exact assertions): short script (fits one chunk) → 1 chunk, text identical; paragraphs merged up to the limit (3 paragraphs of ~1500 chars → 1 chunk of ~4500 > limit → check: 4500 > 4096 → 2 chunks, boundaries BETWEEN paragraphs, no paragraph split); a paragraph > limit → split at sentence boundaries, sentences intact; a single sentence > limit → hard word-boundary split, concatenation of chunks == original text (verbatim, no chars lost or added); > 64 chunks → ValueError containing "64"; empty/whitespace script → ValueError; `max_chunk_chars=500` respected (no chunk > 500).

- [ ] **Step 2: Run — expect FAIL (module missing).**

- [ ] **Step 3: Implement `chunk_script`.** Paragraph split on `\n\s*\n`; greedy merge; sentence fallback splits on `.!?…` + whitespace; last-resort split at last space before the limit.

- [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** `feat(m10): long-form chunker — paragraph-preserving with sentence and word-boundary fallbacks`

### Task 2: Run engine — generate chunks, assemble, state machine (`longform.py` part 2)

**Files:**
- Modify: `services/orchestrator/src/voxora/longform.py`
- Modify: `services/orchestrator/src/voxora/audio_assets.py` (export a chunk-level generation helper OR refactor `generate_asset` to accept an explicit `asset_id` + `storage_path`; smallest change: add optional kwargs `asset_id: Optional[str] = None, storage_path_override: Optional[str] = None, status: str = "ready"`)
- Test: `services/orchestrator/tests/test_longform.py` (append)

**Interfaces:**
- Consumes: `chunk_script`; `generate_asset(db, settings, tenant, actor, payload, provider=None, ...)` (existing signature extended); `validate_audio_blob(audio, ext)`; `AudioFileStore`; `db.create_audio_asset/update_audio_asset` (allowed fields incl. `processing_history`); `MAX_ASSET_BYTES`.
- Produces (for Task 3): `generate_long_form(db, settings, tenant_id, actor, payload: Dict) -> Dict` — creates the row (header + pending chunks), generates each chunk via the provider path, assembles WAV (or raw pcm) frame-exact, flips `ready`, or leaves `processing` with failed chunks marked. `run_locks: Set[str]` module-level; `retry_long_form(db, settings, tenant_id, actor, asset: Dict, provider=None) -> Dict` — regenerates ONLY `failed`/missing chunks from the snapshot, re-assembles; raises `ValueError` (→422: non-long-form row, not processing/failed, format unsupported) or returns the updated asset. Chunk files named `{asset_id}_chunk_{i}.wav` under the tenant dir.

- [ ] **Step 1: Write failing tests** (mock provider injected via `provider=` param; `database` fixture; tmp store): happy path 3-chunk run → one `ready` asset, WAV parses, frame count == sum of chunk frame counts, history has header (`kind=="longform"`, settings snapshot present) + 3 chunk entries (`state=="ready"`) + `assembled` entry; chunk files exist mid-run and are REMOVED after successful assembly; provider failing chunk 2 (raises `TTSProviderError(502, "boom")` once — stateful fake) → asset stays `processing`, chunk 2 marked `failed` with sanitized error, chunks 1/3 `ready`; `retry_long_form` with a healthy provider → `ready`, manifest chunk texts unchanged (anti-goal pin), chunks 1/3 audio bytes IDENTICAL to first run (retry-without-regeneration pin — compare stored chunk file bytes before/after); retry of a normal (non-long-form) asset → ValueError; retry while locked → ValueError containing "in flight"; assembled blob > `MAX_ASSET_BYTES` (oversized sine chunks) → row `failed`, error mentions "50 MB"-equivalent message and "shorter script"; `response_format: "mp3"` long-form → ValueError mentioning in-repo decode; download/variant interaction NOT here (Task 4).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — `generate_long_form`: build header from payload settings; create row via `create_audio_asset` (explicit id, `status="processing"`, `file_size=0`, manifest `[header, ...pending]`); acquire run lock; per chunk: call the extended `generate_asset` with `asset_id=asset_id, storage_path_override=chunk_path, status="ready"` is NOT used for the ROW — chunk audio is written via `AudioFileStore.save` after a direct `provider.generate(TTSRequest(...))` + `validate_audio_blob` (chunk intermediates are NOT asset rows — approach C rejected); on success mark entry `ready`, on `TTSProviderError`/`AudioGenerationError` mark `failed` with `error.detail`; then `_assemble(...)`: read chunk files, assert identical rate/channels, concat frames, validate, write final file, update row (`ready`, real size/duration, manifest + assembled entry); any assembly failure → row `failed` (terminal, sanitized). `retry_long_form`: validate row (long-form header, status in {processing, failed}, lock), rebuild chunk requests from header snapshot, regenerate failed/missing chunks only, reassemble. Release lock in `finally`.

- [ ] **Step 4: Run — PASS; full suite green.**

- [ ] **Step 5: Commit** `feat(m10): long-form run engine — chunk generation, assembly, retry-without-regeneration`

### Task 3: API routes + models + row lifecycle

**Files:**
- Modify: `services/orchestrator/src/voxora/models.py` (add `LongFormGenerate` after `VariantOpsRequest`: same shape as `AudioAssetGenerate` but `script: str = Field(min_length=1, max_length=262144)`, plus `max_chunk_chars: int = Field(default=4096, ge=500, le=4096)`; no extra=forbid surprises — copy AudioAssetGenerate's constraints; `response_format` MUST be wav/pcm — validator refuses others with the in-repo-decode message)
- Modify: `services/orchestrator/src/voxora/api.py` (routes: `POST /api/v1/audio-assets/generate-long` writer-gated 201; `POST /api/v1/audio-assets/{asset_id}/retry-failed` writer-gated 200; error mapping ValueError→422 `longform_invalid`, lock-ValueError→409 `run_in_flight`)
- Modify: `services/orchestrator/src/voxora/api.py` `download_audio_asset` + `create_audio_variant`: download of `status=="processing"` → 409 `still_processing`; variant on non-`ready` → 422 "asset is not ready"
- Modify: `services/orchestrator/src/voxora/api.py` `delete_audio_asset`: also remove `{asset_id}_chunk_*` files (glob prefix in the tenant dir)
- Test: `services/orchestrator/tests/test_longform_api.py`

**Interfaces:**
- Consumes: Task 2 `generate_long_form`/`retry_long_form`/`run_locks`.
- Produces: HTTP surface only.

- [ ] **Step 1: Write failing tests**: generate-long happy path (mock provider, 2-paragraph script) → 201, `status=="ready"`, download works; partial failure → 201 with `status=="processing"` and failed chunk in body history (client decides; a 201 with honest body, NOT a 500); `retry-failed` on that asset → 200 `ready`; retry on a normal asset → 409 `longform_invalid`... (spec call: non-long-form → ValueError → map to 422 `longform_invalid` for generate-time, but retry-of-non-longform is a state conflict → 409 `longform_invalid`); retry while a lock is held (monkeypatch `run_locks.add(asset_id)`) → 409 `run_in_flight`; mp3 long-form → 422; script > 262144 → 422; `max_chunk_chars=100` → 422 (ge=500); download of a processing asset → 409 `still_processing`; variant of a processing asset → 422 "not ready"; delete of a long-form asset with chunk files → 200 and NO `{id}_chunk_*` files remain (write fake chunk files first); 403 for viewer role on both routes.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement models + routes + lifecycle guards.**

- [ ] **Step 4: Run — PASS; full suite + ruff + mypy clean.**

- [ ] **Step 5: Commit** `feat(m10): long-form HTTP surface — generate-long, retry-failed, processing-asset lifecycle`

### Task 4: Docs + roadmap

- [ ] **Step 1: Audit entry** (what shipped; honest boundaries: synchronous request bounded by 64-chunk cap, WAV/PCM assembly only, per-process run lock limitation, `instructions` survives via snapshot).
- [ ] **Step 2: Roadmap Phase D checkbox → `[x]`; commit** `docs(m10): Phase D long-form audit + roadmap`

## Self-review notes

- Spec coverage: chunker (T1), state machine + assembly + validation (T2), retry + lock + snapshot (T2/T3), lifecycle guards + delete cleanup (T3), caps (T1/T3). Approaches A chosen; B/C rejected in spec.
- Types consistent: `generate_long_form`/`retry_long_form`/`chunk_script`/`run_locks` names match across tasks; manifest header keys match spec call 3.
- Review Focus: all five pinned in T1/T2/T3 as marked.
