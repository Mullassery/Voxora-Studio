# DSP engine evaluation (Voice Boost, M9)

Evaluated against: open-source licensing, Rust integration, CPU, memory,
audio quality, latency, supported sample rates. Selection policy from the
master spec: do not install all engines indiscriminately; do not claim a
neural engine is integrated unless it actually works; Rust for
latency-sensitive processing where practical.

## Candidates

### 1. WebRTC Audio Processing Module (via `webrtc-audio-processing` 2.1.0)

- **License**: BSD-3 (wrapper + PulseAudio repackaging of WebRTC APM).
- **Rust integration**: C++ bundled build via cc/cmake; docs.rs currently
  fails to build it; OpenSSF scorecard 4/10 with "maintained 0/10"
  (1 commit/90 days as of 2026-08). Large transitive dep tree (82 total,
  includes unmaintained crates flagged by RUSTSEC advisories).
- **Capabilities**: the full stack — AEC3, noise suppression, AGC, VAD,
  high-pass filter, deinterleaved 10 ms frames, 16/32/48 kHz.
- **Latency**: AEC3 adds order-of-30 ms block delay; NS/AGC ~10 ms.
- **Verdict: rejected for now.** AEC3 dominates compile time and the dep
  tree, maintenance is stalled, and the pipeline has no acoustic
  capture/render microphone pair to feed — Twilio Media Streams is
  network-echo-only (handled by carriers), not acoustic echo. Revisit if
  a browser/softphone capture+render path exists.

### 2. RNNoise (via `nnnoiseless` 0.5.2)

- **License**: BSD-3 (pure-Rust port of Xiph RNNoise; weights embedded).
- **Rust integration**: pure Rust, safe code; model weights embedded
  (`RnnModel::from_static_bytes`) — no model download at runtime. Well
  maintained (0.5.2, 2025-12), >1.1M downloads, 27 dependents.
- **Capabilities**: neural noise suppression with VAD + band SNR
  estimates; operates on 48 kHz mono f32 in 10 ms (480-sample) frames.
  No AGC/echo/EQ. Essentially knob-free (custom post-filters possible).
- **CPU/memory**: tiny (RNNNoise-class ~= single-digit % of one core);
  state is a few hundred KB.
- **Latency**: 10 ms algorithmic + FFT/lookahead internal to frame.
- **Verdict: SELECTED as the noise-suppression engine.** Works on real
  audio today, small audit surface, licensed cleanly, embedded weights,
  per-call 10 ms frames match our frame path.

### 3. DeepFilterNet3 (via `deep-filter-rt` 0.1.1)

- **License**: MIT/Apache-2.0 (code and model weights dual-licensed).
- **Rust integration**: pure Rust on tract (embedded DFN3 model —
  nothing to download). BUT it is a third-party inference-only
  repackaging: the official `deep_filter` crate is unmaintained
  (last crates.io release 2022-07; usable from git only), and
  `deep-filter-rt` is maintained by an unrelated party.
- **Capabilities**: high-quality NS for full-band 48 kHz, 10 ms frames,
  `atten_lim` (attenuation limit in dB) maps directly to profile
  strength; ~2.5% of one core in release; 30 ms delay (2 lookahead
  frames + overlap); emits local-SNR estimates.
- **Verdict: evaluated, deferred.** The `atten_lim` knob is exactly the
  profile-strength control we want, and quality is top of the evaluated
  set, but the only crate shipping an embeddable working build is an
  untrusted repackaging. Revisit when an official release exists.
  Our own residual-spectral gate + post-processing achieves graded
  profiles on top of nnnoiseless in the meantime.

### 4. SpeexDSP (`speexdsp` rust-av bindings / `aec-rs` vendored C)

- **License**: BSD-3.
- **Rust integration**: resampler/jitter pieces have pure-Rust paths;
  AEC and preprocess (AGC/denoise) require the C library (sys crate or
  vendored builds like `aec-rs`).
- **Capabilities**: AEC, AGC, spectral denoise, VAD, resampler at
  8/16/32 kHz; frame sizes ~10/20 ms.
- **Quality**: modest; AGC is i16-domain and simplistic; AEC is
  conception-telephony-grade, not AEC3-grade.
- **Verdict: rejected as the echo-cancellation engine.** Acoustic echo
  cancellation is a capture/render problem (speaker re-entering a mic).
  Our inbound path is a PSTN trunk: there is no render reference to
  cancel against, and network echo is the carrier's termination problem.
  Installing a C build for an engine we cannot honestly exercise fails
  the no-unverified-claims rule.

## Selection

| Capability | Engine | Notes |
|---|---|---|
| Noise suppression | `nnnoiseless` (RNNoise port) | embedded weights, 48 kHz/10 ms frames |
| Noise-suppression profile grading | in-repo residual spectral gate | strength knob per profile, layered after NN output |
| AGC / loudness normalization / look-ahead limiter | in-repo pure Rust | RMS-target AGC with attack/release + peak limiter, clipping counters |
| Dynamic EQ (speech presence) + de-essing | in-repo pure Rust biquads | mild presence band + dynamic 5–8 kHz de-ess |
| Clipping detection | in-repo pure Rust | rail-adjacent-sample counting per frame |
| Resampling (8/16 kHz path + ASR input) | in-repo real resampler | replaces the 0-order-hold documented in media.rs |
| Echo cancellation | pluggable seam, off by default | activates only when a valid render reference is supplied; N/A on Twilio trunk paths (documented honestly) |

Dependency impact: `crates/voice-runtime` stops being strictly
zero-dependency except serde — `nnnoiseless` is added behind nothing
(configured with default-features off for the library-only build).
Everything else stays in-repo, testable, and honest.
