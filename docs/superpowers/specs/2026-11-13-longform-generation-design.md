# Audio Generation Phase D — Long-form Generation (Design)

Date: 2026-11-13
Status: DRAFT (pending human approval)
Repo: Voxoro-Studio, branch `feature/voice-intelligence`
Parent roadmap: `docs/roadmap.md` — M10 Phase D; part of the M9+M10 one-pipeline mission.

## Intent and why

The provider caps a single TTS request at 4096 characters (documented contract,
verified in Phase A). Today a longer script is a hard 422 — honest, but it caps
the PRODUCE half at ~2 minutes of audio. Phase D adds long-form generation:
the orchestrator chunks the script on natural boundaries, generates every
chunk with IDENTICAL voice/model/speed settings (consistency is the whole
point of one asset), assembles the chunks sequentially into one file, and
tracks per-chunk state so a mid-run provider failure never wastes the chunks
that succeeded — retry regenerates ONLY the failed chunks.

## Goals

- Scripts far beyond 4096 chars produce ONE ready asset, assembled in order,
  with identical voice settings across every chunk.
- Paragraph preservation: chunks break at paragraph boundaries (blank lines)
  first, sentence boundaries second; a mid-word hard split is the last resort
  (longest paragraph > chunk limit only).
- Per-chunk failure tracking with retry-without-regeneration: a failed chunk
  blocks assembly but succeeded chunks are kept and reused verbatim.
- Assembled-file validation: the final WAV must parse, have the expected
  frame count (sum of chunk frames), and pass the existing blob validation
  (size cap, duration) before the asset goes `ready`.
- Every chunk's parameters and outcome are recorded in the asset's
  `processing_history`.

## Non-goals

- No cross-request caching or dedup of identical chunk text.
- No background/async processing (that is Phase E's worker loop; long-form
  runs synchronously like `generate` — a long script is a long request, and
  the cap on chunk count bounds it).
- No overlap/cross-fade at chunk joins: OpenAI TTS chunks are prosodically
  independent by design; paragraph-boundary chunking keeps joins at natural
  pauses. (Silence padding between chunks: NONE — paragraphs carry prosody;
  added padding would be fabricating pause timing.)
- No voice cloning or per-chunk voice variation (one request = one voice).
- No destructive edits to chunk files while retrying.

## Anti-goals (failure even if it technically works)

- Silent re-chunking with different boundaries between a failed attempt and
  its retry (a retry must use the SAME stored chunk boundaries).
- Assembling chunks from different voice settings into one asset.
- Claiming `ready` with a chunk manifest that contains failed chunks.
- Losing succeeded chunk audio on failure (regenerating everything because
  one chunk failed is the exact waste this phase exists to prevent).

## Constraints

- Chunk generation uses the SAME `generate_asset` machinery (provider
  abstraction, validation, capability honesty) — no parallel path.
- Assembly is WAV/PCM only (stdlib `wave`): `response_format: wav` is the
  long-form surface; `pcm` allowed (headerless concat, duration honestly
  `None`); mp3/opus/aac/flac → 422 with a clear reason (no in-repo decoder —
  same ruling as Phase C variants).
- Chunks inherit ONE set of voice settings from the request; a mismatch
  between a stored chunk and the retry's settings is a 422, not a blend.
- Both backends (SQLite + Postgres) stay green; no schema change required
  (`processing_history` carries the manifest).
- Provider errors sanitized (Phase A-B ruling stands).

## Approaches considered

### A. Chunk manifest on the asset row + chunk files + assembly (CHOSEN)

`POST /api/v1/audio-assets/generate-long` creates an asset row immediately
with `status: "processing"` and a chunk manifest (one `processing_history`
entry per chunk: index, text, boundaries, file, state). Each chunk is a real
`generate_asset`-equivalent call stored as its own file
(`{tenant}/{asset_id}_chunk_{i}.wav`); after all chunks succeed, they are
concatenated (frame-exact, same rate/channels enforced), validated, and the
row flips `ready` with a final `assembled` history entry. `POST
.../audio-assets/{id}/retry-failed` regenerates ONLY failed/missing chunks
using the STORED manifest text and boundaries, then re-attempts assembly.

Trade-off: synchronous request holds the connection for the full run (bounded
by chunk cap: 64 chunks ≈ 64 × 4096 ≈ 260k chars); retries are cheap and
state lives on the row.

### B. Immediate failure = full abort, no partial state (rejected)

One failed chunk discards the run. Rejected: it directly violates the
roadmap's "retry-without-regeneration" requirement.

### C. Chunk rows as first-class assets (rejected)

Each chunk becomes its own `audio_assets` row linked via `parent_asset_id`.
Rejected: it pollutes the operator's asset library with 60 synthetic rows per
long-form run; chunks are intermediates, not assets. The manifest-on-row
approach keeps the library clean while remaining auditable.

## The how (decided)

### Chunking (`longform.py`)

- Split on paragraph boundaries (`\n\s*\n`); merge consecutive paragraphs
  into chunks ≤ 4096 chars (never splitting a paragraph that fits); a single
  paragraph > 4096 is split at sentence boundaries (`.!?…` + space); a single
  sentence > 4096 is hard-split at the last word boundary (documented as a
  last resort, rare).
- Chunk cap: 64 (422 beyond, with the actual count in the message). Empty
  script → 422 (existing rule).
- Chunk text is EXACTLY what the provider receives — stored verbatim in the
  manifest so retries are byte-identical.

### Assembly

- Parse every chunk with stdlib `wave`; rate and channels must be identical
  across chunks (they are — one settings set), else the mismatch is a hard
  error (defense in depth).
- Concatenate frames in manifest order; write ONE WAV 16-bit; final blob
  validated (Phase B `validate_audio_blob`); frame count asserted = sum of
  chunk frame counts (assembled-file validation).
- `pcm` format: raw concat, duration `None` (honest), same validation minus
  duration.

### State machine on the asset row

- `processing` → (all chunks ok) → `ready`; `processing` → (any chunk
  failed) → stays `processing` with failed chunks marked in the manifest;
  `retry-failed` regenerates failed/missing chunks and retries assembly.
- Manifest entries: `{chunk: i, chars: n, state: "ready"|"failed"|"pending",
  error?: sanitized, file: path, at}`.
- A `ready` long-form asset is downloaded/variant-processed exactly like any
  other asset (no special cases downstream).

### API

- `POST /api/v1/audio-assets/generate-long` (writer-gated, 201 when the run
  completes OR when chunks partially fail and the row is `processing` — the
  response body carries per-chunk status either way). Body: same fields as
  `AudioAssetGenerate` minus the 4096 cap (`script` up to 260k), plus
  `max_chunk_chars: int = 4096` (range 500–4096, for testing/consistency).
- `POST /api/v1/audio-assets/{id}/retry-failed` (writer-gated, 200; 409 if
  the asset is not a long-form row in `processing` state).
- `GET /api/v1/audio-assets/{id}` already exposes `status` +
  `processing_history` — no new read endpoint.
- `generate_asset`'s 4096 script cap STAYS for the normal route (one-request
  surface unchanged); `generate-long` is the explicit long-form surface.

### Verification plan

- Unit: chunker (paragraph merge, sentence fallback, hard-split last resort,
  verbatim text, cap), state transitions, retry-only-failed (mock provider
  that fails chunk 2 once, then succeeds — succeeded chunks NOT regenerated,
  provable by deterministic mock audio byte-identity).
- API: e2e long-form run (mock provider, multi-chunk script) → one `ready`
  asset, frame count = sum; partial failure → `processing` + failed marker →
  retry → `ready`; 409 retrying a non-long-form asset; compressed format →
  422; both backends parametrized.
- Repo bar: full suite + ruff + mypy per commit.

## Builder-check calls (settled 2026-11-13; these are MY calls, recorded for review)

1. **Run-in-flight vs partially failed**: an in-process module-level lock
   (set of asset_ids) marks a live run; `retry-failed` while the lock is
   held → 409 `run_in_flight` (no double-generation, no racing the assembly
   write). A `processing` row with NO lock held is an abandoned run —
   retryable (crash recovery). Honest limitation: the lock is per-process;
   multi-worker deployments could double-generate (this deployment is
   single-worker; documented, not hidden).
2. **Assembly failure**: distinct terminal state `failed` on the row (chunk
   failures keep the row `processing` — they are retryable; assembly
   failures — size cap exceeded, malformed chunk, disk error — are terminal
   with a sanitized error in history). `retry-failed` accepts `processing`
   AND `failed` rows: it regenerates failed/missing chunks (none when all
   are ready) and re-attempts assembly. A deterministic size-cap failure
   will re-fail on reassembly with the same clear message — the honest
   remedy is a shorter script (the error says so).
3. **Settings snapshot**: the manifest's FIRST entry (written at row
   creation) is a header: `{kind: "longform", run_id, settings: {voice,
   model, speed, response_format, instructions, language, title},
   max_chunk_chars}`. Retries rebuild each chunk's provider request from
   this snapshot verbatim — `instructions` survives, nothing blends.
4. **Row lifecycle during processing**: `storage_path` is the FINAL path
   from creation (known upfront); `file_size: 0`, `duration_seconds: None`,
   `status: "processing"`. Download of a `processing` asset → 409
   `still_processing` (never the misleading 404 `file_missing`); variants on
   a non-`ready` asset → 422 "asset is not ready". `processing`/`failed`
   are first-class status values in listings.
5. **Long-form identification**: the history header entry above is the
   marker — `history[0].get("kind") == "longform"`; no schema change, no
   sniffing ambiguity.
6. **DELETE chunk cleanup**: in scope — the delete path removes the
   assembled file AND all `{asset_id}_chunk_*` files in the tenant dir
   (prefix match on the asset id; no orphaned intermediates).

## What's left to the builder (unchanged)

- Exact regex for sentence boundaries (keep it conservative; period-only
  abbreviations are out of scope — conservative means a rare early split,
  never a lost paragraph).
- Manifest entry shape details beyond the fields above.
- Whether `retry-failed` accepts an optional fresh provider config (default:
  reuse the stored request settings — recommended).
