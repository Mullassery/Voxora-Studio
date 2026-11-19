# Audio Generation Phase C — Formats + Non-Destructive Variants (Design)

Date: 2026-11-13
Status: DRAFT (pending human approval)
Repo: Voxora-Studio, branch `feature/voice-intelligence`
Parent roadmap: `docs/roadmap.md` — M10 Phase C; part of the M9+M10 one-pipeline mission.

## Intent and why

Generated audio assets today are single-shot: whatever the provider returned is
the only version. Phase C adds the **PRODUCE-half finishing pass**: operators
derive optional, non-destructive variants from a generated asset — loudness
normalization, silence trimming, sample-rate/channel conversion, and Voice
Boost profile enhancement — reusing the M9 DSP components so ONE DSP layer
serves both directions of the pipeline (calls enhanced on the way in,
assets enhanced on the way out).

The why: a TTS asset straight off the wire is rarely publish-ready (level
varies by voice/model, scripts often start with a breath of silence, delivery
channels need specific rates/formats). The M9 machinery already exists and is
proven; pointing it at generated assets closes the loop without a second DSP
stack and without ffmpeg.

## Goals

- Variants are always NEW assets: new id, `parent_asset_id` set,
  `processing_history` extended; the original is preserved **byte-identical**
  (pinned by test).
- Post-processing reuses the Rust DSP (`crates/voice-runtime/src/dsp.rs`) via
  the real extension wheel — the M9 seam pattern (fake-free: tests drive the
  actual compiled engine through `make py-voice`).
- Provider-native formats pass through untouched. Conversion exists ONLY where
  implementable in-repo with stdlib + the Rust resampler; it is NEVER done by
  renaming extensions.
- Capability honesty: unsupported operations/formats are refused with a clear
  422, never silently skipped or defaulted.
- Every variant records what was done: op name, parameters, and diagnostics
  (gain applied, samples trimmed, clipped-sample count post-op) in
  `processing_history`.

## Non-goals

- No ffmpeg, no external audio binaries (ruling stands unless a deployment
  need reopens it).
- No destructive edits, no in-place re-render of the parent.
- No destructive processing of COMPRESSED parents this phase: mp3/opus/aac/
  flac parents are refused (decode-in-repo is not implementable with stdlib).
  WAV 16-bit PCM parents are the first-class surface.
- No batch/background processing (Phase E).
- No client-facing call-path integration (the M7/P9 boundary stands: `/say`
  stays 404).

## Anti-goals (failure even if it technically works)

- Renaming `asset.mp3` to `asset.wav` and calling it converted.
- A variant flow that mutates the parent file or overwrites its row.
- A "Voice Boost profile" variant that bypasses the Rust engine (Python
  re-implementation of AGC/limiter — duplication is the failure).
- Faked success on unsupported input (e.g., 24-bit WAV "processed" by
  misreading bytes).

## Constraints

- Rust changes must keep `cargo test` green AND `cargo check
  --features pyo3-bindings` compiling (linking is impossible in this env —
  documented limitation; behavior verified through the maturin-built wheel).
- Cross-backend parity: variant rows land through the existing
  `create_audio_asset` seam — both SQLite and Postgres stay green (schema from
  Phase A-B already has `parent_asset_id`/`processing_history`).
- Provider errors are sanitized; the orchestrator never echoes provider
  internals to clients.
- Repo conventions: TDD RED→GREEN, conventional commits, full suite + ruff +
  mypy before each commit.

## Approaches considered

### A. Offline DSP seam in Rust + variant engine in Python (CHOSEN)

Add one Rust pyfunction, `audio_postprocess(samples: Vec<i16>, ops_json:
&str) -> (Vec<i16>, String)` — samples in/out as i16 vectors (direct pyo3
extraction; ops envelope and diagnostics as JSON strings, matching the
bridge's JSON-string convention for structured data; no numpy dependency).
Inside: the ops pipeline dispatches to the EXISTING `VoiceBoostProcessor`
(profile/overrides config resolved via `voice_boost_config_from_json`),
`FirResampler` (real resampler), and `count_clipped`. Python (`variants.py`)
owns WAV I/O (stdlib `wave`), trim-silence detection (amplitude scan over
samples — cheap and honest in Python), channel conversion (mono↔stereo
mixing with clip guard), row creation, and the API.

Trade-off: rebuilds the wheel (`make py-voice`) — already the established
verification path. Sample transport via `Vec<i16>` is fast for the sizes
involved (a 4096-char script is ~2 min of 16 kHz mono = ~2M samples; fine).

### B. Pure-Python post-processing (rejected)

No Rust surface changes; normalize/trim/resample in Python. Rejected: it
duplicates the real resampler (a linear-interp resampler in Python is exactly
the "fake DSP" the roadmap forbids) and violates the one-DSP-layer pipeline
connection. Only viable as a spike, not as the feature.

### C. Whole variant engine in Rust (rejected)

WAV I/O + row semantics in Rust too. Rejected: storage/orchestration belongs
to the Python service per repo pattern; Rust I/O would duplicate stdlib `wave`
and drag file-policy questions (paths, tenant scoping) across the boundary.

## The how (decided)

### Operations (first cut, applied in listed order, cap 6 ops/request)

| Op | Parameters | Engine | Diagnostics |
|----|-----------|--------|-------------|
| `normalize` | `target_dbfs` (default -16, range [-24, -6]) | Rust: peak-scan + pure gain, clip-guard | actual gain_db applied (may be reduced), `target_reached`, clipped_before/after |
| `trim_silence` | `threshold_dbfs` (default -40, range [-60, -20]), `min_silence_ms` (default 120) | Python scan → bounds; pure sample slicing | trimmed_head/tail samples |
| `resample` | `sample_rate` (8k–48k) | Rust offline windowed-sinc resampler (NEW, real DSP — see calls below) | in_rate/out_rate |
| `channels` | `to`: "mono"\|"stereo" | Python mix/split with clip guard (mix = mean, hard-clip guard + count) | clipped mix count |
| `voice_boost` | `profile` + optional `overrides` (same 11-control schema as M9) | Rust `VoiceBoostProcessor` via resolved config | clipped_after, applied profile, internal 16k round-trip flag |

- Pydantic envelope `VariantOpsRequest` (extra=forbid; unknown op → 422).
- A `voice_boost` op MAY combine with others; profile resolution reuses the
  M9 settings-resolution surface (`resolve_voice_boost_settings` semantics —
  explicit op parameters win over stored defaults ONLY when the op specifies
  them; otherwise the builtin profile config is used as-is).
- Empty result guard: trim that removes everything, or resample that would
  emit zero frames → 422, not an empty asset.

### WAV contract

- Parents must be WAV/PCM (`format` in {wav, pcm}); `pcm` (headerless) is
  refused this phase — stdlib `wave` cannot parse it and honest refusal beats
  guessing parameters. So: **WAV only, 16-bit, mono or stereo**; anything else
  → 422 with the reason.
- Variants are ALWAYS written as 16-bit WAV at their resulting rate/channels,
  regardless of parent. (mp3/opus outputs from variants = deferred until an
  in-repo encoder exists; never by renaming.)
- Duration recomputed from actual frames; `file_size` actual bytes.

### Persistence + API

- New rows via `create_audio_asset` (same tenant scoping, same table):
  `parent_asset_id = parent id`, `processing_history = parent.history +
  [{op, params, diagnostics, at, by}]`, title = parent title + " (variant)".
- `POST /api/v1/audio-assets/{id}/variants` (writer-gated, 201, audited
  `audio.variant_created`). Parent missing → 404; unsupported → 422 with
  machine-readable code; DSP/extension unavailable → 503 (honest), never a
  fake pass-through.
- `GET /api/v1/audio-assets/{id}/variants` — list direct children (new DB
  method `list_audio_asset_variants(parent_id)`), same `_page` shape.
- Download of variants reuses the existing download route unchanged.
- Variant-of-variant chains allowed; history accumulates (each entry names its
  op only — the chain is the parent_id traversal).

### Verification plan (per repo bar)

- Rust: unit tests for the new dispatch + resampler/profile paths (`cargo
  test`), `cargo check --features pyo3-bindings` compiles.
- Seam tests through the REAL wheel (`make py-voice`): normalize actually
  raises a quiet WAV's peak to target; resample changes frame count by the
  expected ratio; voice_boost variant differs from input and is clipped-safe;
  config rejection matches M9 rejection semantics.
- Orchestrator: variant flow e2e (mock provider parent → variant → new row →
  parent bytes unchanged → download works), 422/404/503 pins, cross-backend
  parametrized, full suite + ruff + mypy green per commit.

### Builder-check calls (settled 2026-11-13; these are MY calls, recorded for review)

1. **Resampler**: the design's `FirResampler` does not exist — what exists is a
   fixed ×3/÷3 pair (16k↔48k) and a documented 0-order-hold placeholder
   (explicitly forbidden for audible output). Decision: implement a REAL
   offline windowed-sinc resampler in Rust (whole-buffer, cutoff ≈
   0.45×min(in,out) rate, ~32 taps per zero-crossing, Hann window), verified
   by sine-sweep tests asserting SNR/aliasing bounds. Any target rate in
   8k–48k works; the 0-order hold is never used. Bounded scope, no fake DSP.
2. **voice_boost at non-16k rates**: `VoiceBoostStage` is 16 kHz-hardwired and
   OpenAI WAV is typically 24 kHz. Decision: transparent internal round-trip —
   resample to 16k (real resampler), process, resample back to the asset's
   rate; diagnostics record `internal_rate_roundtrip: true`. No pitch
   corruption, no 422 for the most common parent.
3. **voice_boost on stereo**: refused with 422
   (`voice_boost_stereo_unsupported`) — the Rust chain is mono-only; per-channel
   AGC doubles cost/nuance for no first-cut need. Operators convert to mono
   explicitly. Combination `channels→stereo` + `voice_boost` in one request is
   rejected at validation time.
4. **voice_boost resolution for variants**: op-ONLY resolution — explicit op
   params over the builtin `VoiceBoostConfig::for_profile(profile)`. Tenant
   stored settings are NOT consulted (an `enabled: false` stored row silently
   no-opping an explicit op would be a fake success). Diagnostics record the
   applied profile + effective config.
5. **normalize clip-guard**: if the target gain would create clipping, gain is
   REDUCED to the largest safe value; success semantics stay "same content,
   louder, no new clipping"; diagnostics report the actual gain and
   `target_reached: false`. Never a 422, never limiter-transformation
   (normalize is a pure gain; the limiter belongs to the voice_boost path).

### What's left to the builder (unchanged)

- Exact Rust function/module layout and error mapping (follow existing
  pybridge patterns).
- Trim-silence algorithm details (hysteresis, min-gap merging) — sensible
  defaults, documented in code comments/tests.
- Whether `normalize` uses peak or RMS-based gain for the first cut (either is
  defensible; peak is simpler and clip-safe with the limiter guard — default
  to peak unless review argues otherwise).
- Resampler tap/cutoff choices (reuse `FirResampler` defaults used by the call
  path).
