# Audio Generation Studio (M10 Phase A–B) — design

Phase A (TTS provider abstraction + first provider) and Phase B (generation
API + asset library) of roadmap M10. Phases C–G (formats/processing, long-form,
batch, console, final docs) follow as separate slices.

## Intent

The PRODUCE half of the pipeline: operators turn scripts into reviewed,
downloadable audio assets ahead of a campaign, instead of synthesizing
unreviewed speech live. Reuses the orchestrator's existing conventions end to
end: bearer auth + writer gating + audit, config-in-db, the `Database` seam
(additive table), the Twilio-adapter provider pattern (documented contract,
fake-transport tests, honest live-unverified labels), and the
capability-honesty machinery from `voice_providers.py`.

## Approaches considered

1. **Cloud SDKs first (Azure/Google)**: rejected for now — heavy SDK installs
   with no integration need; the provider interface admits them later
   (spec: implement only feasible integrations, no unnecessary dependencies).
2. **OpenAI REST + in-repo mock provider** (chosen): OpenAI TTS against the
   documented REST contract (verified against developers.openai.com docs,
   2026-11-13: POST /v1/audio/speech; models `tts-1`, `tts-1-hd`,
   `gpt-4o-mini-tts`; 13 built-in voices; formats mp3/opus/aac/flac/wav/pcm;
   speed 0.25–4.0; input ≤ 4096 chars; `instructions` only on
   gpt-4o-mini-tts) with fake-transport contract tests — contract-verified,
   live-unverified. A deterministic in-repo mock provider (sine-wave WAV) keeps
   local/demo runs and the standard test suite free of paid API calls,
   matching the repo's simulation-first ethos.
3. **Separate database for assets**: rejected — the existing Database seam
   takes one additive table (`audio_assets`); metadata and tenant scoping ride
   the same backends and tests.

## Binding decisions

- **Provider selection**: `VOXORA_AUDIO_TTS_PROVIDER` = `mock` (default) |
  `openai`. Unconfigured OpenAI ⇒ 501 with an honest message (the
  `build_agent` pattern). Credentials: `OPENAI_API_KEY` via env only — never
  in db, never to the frontend.
- **Honesty**: voices/speed/formats/models are listed ONLY as the provider
  exposes them; `GET /api/v1/audio/providers` reports capability status with
  live-unverified labels (per `voice_providers.py`). Language is recorded as
  metadata only — OpenAI exposes no language-selection parameter (voices are
  "optimized for English" per the docs); unsupported language selection is
  refused, never silently defaulted.
- **Script limits**: Phase A–B enforces the provider's 4096-char limit
  honestly (422 pointing at Phase D chunking); longer scripts wait for the
  long-form phase.
- **Storage**: local file store under `VOXORA_AUDIO_STORAGE_PATH`
  (default `data/audio_assets`), paths built ONLY from internal ids
  (`{tenant_id}/{asset_id}.{ext}`) — no user input reaches a path (no
  traversal). Downloads carry a sanitized Content-Disposition title.
- **Validation**: assets are validated before becoming downloadable
  (non-empty, size cap 50 MB; WAV/PCM duration parsed from the header via
  the stdlib — compressed formats report `duration_seconds: None` honestly).
- **Isolation**: dedicated `/api/v1/audio/*` routes; the live call path is
  untouched and the M7/P9 boundary (server-authoritative TTS in calls,
  `/voice-sessions/{id}/say` 404) stays pinned by the existing tests. A new
  test pins both sides side by side.
- **Formats in Phase A–B**: provider-native passthrough (mp3/wav/opus/aac/
  flac/pcm as returned); conversion is Phase C. Never rename extensions.
- **Post-processing**: Phase C. `parent_asset_id` + `processing_history`
  columns exist from the start so variants land cleanly later.
- **Concurrency**: Phase A–B generates synchronously (seconds-scale calls);
  background jobs, batch CSV, and rate-limit handling are Phase E. A failed
  TTS request is a 502 with the provider reason sanitized — it can never
  crash the app or the worker loop.

## Left to the builder

Exact names/signatures per the plan
(`docs/superpowers/plans/2026-11-13-audio-generation-ab.md`).
