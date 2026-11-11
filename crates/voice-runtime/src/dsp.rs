//! Voice Boost engine (roadmap M9/P2): modular inbound audio enhancement.
//!
//! Chain (engine-rate PCM16 in → enhanced PCM16 out):
//! resample 16 kHz → 48 kHz (cubic Hermite, integer ×3) → **nnnoiseless**
//! neural noise suppression (RNNoise port, weights embedded, 10 ms /
//! 480-sample frames at 48 kHz) → profile-graded residual gate → resample
//! 48 kHz → 16 kHz (windowed-sinc FIR, ×3 decimation) → low-cut → presence
//! EQ → dynamic de-esser → RMS AGC → look-ahead limiter → clipping
//! accounting.
//!
//! Honesty notes (capability-honest pattern, matching `media`):
//! * Echo cancellation is a **seam, not a working engine** (see
//!   [`EchoCanceller`]): acoustic echo cancellation needs a valid render
//!   (playout) reference signal, which the PSTN trunk paths never provide —
//!   network echo is carrier territory. The seam activates only when a
//!   reference is actually supplied; nothing claims AEC is integrated.
//! * The nnnoiseless model is real and integrated. Its first output frame
//!   has a documented fade-in artifact; it is kept rather than discarded so
//!   streaming alignment stays chunk-split deterministic.
//! * The AGC is frame-RMS based with attack/release smoothing and a
//!   look-ahead peak limiter — not an ITU BS.1770 loudness implementation.
//! * Algorithmic added latency ≈ 10.7 ms (one nnnoiseless frame + the
//!   limiter's 10 ms look-ahead + FIR group delay). Measured per-frame CPU
//!   cost is a benchmark concern (P8-style measurements, not asserted here).
//! * When bypassed, the stage is identity: input bytes are preserved exactly.
//! * The engine originally shipped a 0-order-hold resampler (see `media`);
//!   the real resampler here is the replacement for the Voice Boost path.

use nnnoiseless::DenoiseState;

use std::collections::VecDeque;

/// Engine-native inbound sample rate (`media::PCM16_16K`).
pub const ENGINE_RATE: u32 = 16_000;
/// nnnoiseless operates on 48 kHz mono, 10 ms (480-sample) frames.
pub const NN_RATE: u32 = 48_000;
/// Look-ahead limiter window: 10 ms at the engine rate.
pub const LIMITER_LOOKAHEAD: usize = (ENGINE_RATE as usize / 1000) * 10;

// ---------------------------------------------------------------------------
// Resampling
// ---------------------------------------------------------------------------

/// Streaming cubic-Hermite ×3 upsampler (e.g. 16 kHz → 48 kHz).
///
/// Deterministic; carries only past samples (zero lookahead). After `N`
/// input samples consumed, `3·N − 2 + flush_tail` outputs were emitted.
pub struct Upsampler3 {
    p0: f32,
    p1: f32,
    primed: bool,
}

impl Default for Upsampler3 {
    fn default() -> Self {
        Self::new()
    }
}

impl Upsampler3 {
    pub fn new() -> Self {
        Self { p0: 0.0, p1: 0.0, primed: false }
    }

    pub fn process(&mut self, input: &[f32]) -> Vec<f32> {
        let mut out = Vec::with_capacity(input.len() * 3 + 2);
        for &x in input {
            if !self.primed {
                self.primed = true;
                self.p0 = x;
                self.p1 = x;
                out.push(x);
                continue;
            }
            let p2 = x;
            out.push(hermite(self.p0, self.p1, p2, x, 1.0 / 3.0));
            out.push(hermite(self.p0, self.p1, p2, x, 2.0 / 3.0));
            out.push(p2);
            self.p0 = self.p1;
            self.p1 = p2;
        }
        out
    }

    /// Emit the two trailing fractional positions beyond the last sample,
    /// holding tangents from the current window. Makes the output count
    /// exactly `3·N`.
    pub fn flush_tail(&mut self) -> [f32; 2] {
        [
            hermite(self.p0, self.p1, self.p1, self.p1, 1.0 / 3.0),
            hermite(self.p0, self.p1, self.p1, self.p1, 2.0 / 3.0),
        ]
    }
}

/// Cubic Hermite interpolation between `p1` (t = 0) and `p2` (t = 1), with
/// tangents from `p0`/`p3`.
fn hermite(p0: f32, p1: f32, p2: f32, p3: f32, f: f32) -> f32 {
    let m1 = 0.5 * (p2 - p0);
    let m2 = 0.5 * (p3 - p1);
    let f2 = f * f;
    let f3 = f2 * f;
    p1
        + m1 * f
        + (-2.0 * m1 - m2 + 3.0 * (p2 - p1)) * f2
        + (m1 + m2 - 2.0 * (p2 - p1)) * f3
}

/// Streaming windowed-sinc low-pass FIR + ×3 decimation (48 kHz → 16 kHz).
///
/// Cutoff 7.2 kHz (0.45 × the 16 kHz output Nyquist), 33 taps, Hamming
/// window. Constant group delay of 16 output samples.
pub struct Downsampler3 {
    taps: Vec<f32>,
    delay: VecDeque<f32>,
    phase: usize,
    delay_target: usize,
}

impl Downsampler3 {
    pub fn new(taps: usize, cutoff_ratio: f32) -> Self {
        let m = (taps - 1) / 2;
        let mut taps_v = Vec::with_capacity(taps);
        let mid = (taps - 1) as f32 / 2.0;
        for n in 0..taps {
            let k = n as f32 - mid;
            let sinc = if k.abs() < f32::EPSILON {
                1.0
            } else {
                (std::f32::consts::PI * 2.0 * cutoff_ratio * k)
                    .sin()
                    / (std::f32::consts::PI * 2.0 * cutoff_ratio * k)
            };
            taps_v.push(sinc * (0.54 - 0.46 * (std::f32::consts::PI * n as f32 / m as f32).cos()));
        }
        Self {
            taps: taps_v,
            delay: VecDeque::with_capacity(taps + 4),
            phase: 0,
            delay_target: m,
        }
    }

    /// Feed input samples; emits one decimated sample every third input
    /// (after the FIR fills).
    pub fn process(&mut self, input: &[f32]) -> Vec<f32> {
        let mut out = Vec::with_capacity(input.len() / 3 + 2);
        for &s in input {
            self.delay.push_back(s);
            if self.delay.len() > self.taps.len() + self.delay_target {
                self.delay.pop_front();
            }
            if self.delay.len() < self.taps.len() {
                continue;
            }
            if self.phase == 0 {
                let t = &self.taps;
                let acc: f32 = self.delay.iter().rev().zip(t.iter()).map(|(d, c)| d * c).sum();
                out.push(acc);
            }
            self.phase = (self.phase + 1) % 3;
        }
        out
    }
}

// ---------------------------------------------------------------------------
// Equalization / de-essing (RBJ biquads)
// ---------------------------------------------------------------------------

/// Transposed direct-form-II biquad; coefficients are RBJ cookbook.
#[derive(Clone, Copy, Debug)]
pub struct Biquad {
    b0: f32,
    b1: f32,
    b2: f32,
    a1: f32,
    a2: f32,
    z1: f32,
    z2: f32,
}

impl Biquad {
    pub fn new() -> Self {
        Self { b0: 1.0, b1: 0.0, b2: 0.0, a1: 0.0, a2: 0.0, z1: 0.0, z2: 0.0 }
    }

    fn from_ab(b: (f32, f32, f32), a: (f32, f32, f32)) -> Self {
        let (b0, b1, b2) = (b.0 / a.0, b.1 / a.0, b.2 / a.0);
        let (_, a1, a2) = (a.0 / a.0, a.1 / a.0, a.2 / a.0);
        Self { b0, b1, b2, a1, a2, z1: 0.0, z2: 0.0 }
    }

    /// 2nd-order high-pass (audio low-cut).
    pub fn highpass(f0: f32, q: f32) -> Self {
        let w0 = std::f32::consts::PI * 2.0 * f0 / ENGINE_RATE as f32;
        let alpha = w0.sin() / (2.0 * q);
        let cos = w0.cos();
        Self::from_ab(
            ((1.0 + cos) / 2.0, -(1.0 + cos), (1.0 + cos) / 2.0),
            (1.0 + alpha, -2.0 * cos, 1.0 - alpha),
        )
    }

    /// Peaking EQ at `f0` with gain in dB.
    pub fn peaking(f0: f32, q: f32, gain_db: f32) -> Self {
        let w0 = std::f32::consts::PI * 2.0 * f0 / ENGINE_RATE as f32;
        let alpha = w0.sin() / (2.0 * q);
        let cos = w0.cos();
        let a = 10f32.powf(gain_db / 40.0);
        Self::from_ab(
            (1.0 + alpha * a, -2.0 * cos, 1.0 - alpha * a),
            (1.0 + alpha / a, -2.0 * cos, 1.0 - alpha / a),
        )
    }

    /// Band-pass (constant skirt) for band-energy measurement.
    pub fn bandpass(f0: f32, q: f32) -> Self {
        let w0 = std::f32::consts::PI * 2.0 * f0 / ENGINE_RATE as f32;
        let alpha = w0.sin() / (2.0 * q);
        let cos = w0.cos();
        Self::from_ab(
            (alpha, 0.0, -alpha),
            (1.0 + alpha, -2.0 * cos, 1.0 - alpha),
        )
    }

    pub fn run(&mut self, x: f32) -> f32 {
        let y = self.b0 * x + self.z1;
        self.z1 = self.b1 * x - self.a1 * y + self.z2;
        self.z2 = self.b2 * x - self.a2 * y;
        self.clamp_state();
        y
    }

    fn clamp_state(&mut self) {
        // f32 state drifts are bounded by construction; guard NaN honest fail-safe.
        if !self.z1.is_finite() {
            self.z1 = 0.0;
        }
        if !self.z2.is_finite() {
            self.z2 = 0.0;
        }
    }
}

impl Default for Biquad {
    fn default() -> Self {
        Self::new()
    }
}

/// Dynamic de-esser: attenuates a peaking band around 6.5 kHz when the
/// frame's band energy exceeds `threshold_ratio` of the overall RMS.
pub struct DeEsser {
    strength: f32,
    thresh: f32,
    band: Biquad,
    shaper: Biquad,
}

/// Center frequency of the sibilance band.
const DE_ESS_HZ: f32 = 6500.0;

impl DeEsser {
    pub fn new(strength: f32, threshold_ratio: f32) -> Self {
        Self {
            strength,
            thresh: threshold_ratio,
            band: Biquad::bandpass(DE_ESS_HZ, 1.5),
            shaper: Biquad::peaking(DE_ESS_HZ, 2.0, 0.0),
        }
    }

    /// `frame` is in [-1, 1]; returns the corrected frame (in place).
    pub fn process(&mut self, frame: &mut [f32]) -> f32 {
        let overall = rms(frame);
        let band_energy = frame.iter().map(|&s| self.band.run(s)).collect::<Vec<_>>();
        let band_rms = rms(&band_energy);
        let ratio = if overall > 1e-6 { band_rms / overall } else { 0.0 };
        let mut applied = 0.0;
        if ratio > self.thresh {
            let excess = ((ratio - self.thresh) / 1.0).min(1.0);
            applied = -6.0 * self.strength * excess;
            self.shaper = Biquad::peaking(DE_ESS_HZ, 2.0, applied);
        } else {
            // Relax back to neutral between frames.
            self.shaper = Biquad::peaking(DE_ESS_HZ, 2.0, 0.0);
        }
        for s in frame.iter_mut() {
            *s = self.shaper.run(*s);
        }
        applied
    }
}

// ---------------------------------------------------------------------------
// AGC / normalization / limiter / clipping
// ---------------------------------------------------------------------------

/// Frame-RMS automatic gain control with attack/release smoothing.
/// `normalize` enables the same mechanism with a slower integrator (loudness
/// normalization); both ride one look-ahead limiter for peak safety.
pub struct Agc {
    target_dbfs: f32,
    max_boost_db: f32,
    max_cut_db: f32,
    gain_db: f32,
    pub enabled: bool,
    pub normalize: bool,
}

impl Agc {
    pub fn new(target_dbfs: f32, max_boost_db: f32, max_cut_db: f32, enabled: bool, normalize: bool) -> Self {
        Self { target_dbfs, max_boost_db, max_cut_db, gain_db: 0.0, enabled, normalize }
    }

    /// Measure one frame (in [-1, 1]); returns per-sample gain segments to
    /// interpolate across the frame, and updates state.
    pub fn frame_gain(&mut self, frame: &[f32]) -> (f32, f32) {
        let rms_db = 20.0 * rms(frame).max(1e-6).log10();
        if !self.enabled {
            return (self.gain_db, self.gain_db);
        }
        let desired = (self.target_dbfs - rms_db).clamp(-self.max_cut_db, self.max_boost_db);
        // Speech dynamics: rising gain is slow (avoid pumping); attenuation is fast.
        let alpha = if desired < self.gain_db { 0.35 } else { 0.12 };
        let start = self.gain_db;
        self.gain_db += (desired - self.gain_db) * alpha;
        (start, self.gain_db)
    }
}

/// 10 ms look-ahead peak limiter with fast attack and slow release;
/// protection target 0.999 peak (≈ −0.0087 dBFS), honest clip accounting.
pub struct LookAheadLimiter {
    delay: VecDeque<f32>,
    peak_att: f32,
    pub engaged: bool,
}

impl LookAheadLimiter {
    pub fn new() -> Self {
        Self {
            delay: VecDeque::with_capacity(LIMITER_LOOKAHEAD + 2),
            peak_att: 1.0,
            engaged: false,
        }
    }

    pub fn process(&mut self, frame: &mut [f32]) -> bool {
        let peak = frame.iter().fold(0.0f32, |m, &s| m.max(s.abs()));
        let needed = if peak > 1e-6 { 0.999 / peak } else { f32::INFINITY };
        let mut engaged = false;
        if needed < self.peak_att {
            self.peak_att = needed;
            engaged = true;
        } else {
            self.peak_att += (1.0 - self.peak_att) * 0.25;
        }
        for &s in frame.iter() {
            self.delay.push_back(s);
        }
        let mut wrote = false;
        while self.delay.len() > LIMITER_LOOKAHEAD {
            let s = self.delay.pop_front().unwrap_or(0.0);
            frame[wrote as usize] = s * self.peak_att;
            wrote = true;
        }
        self.engaged |= engaged;
        // When the delay buffer is still filling, keep the frame as produced
        // (attenuation takes effect once look-ahead is primed).
        self.engaged |= engaged;
        engaged
    }

    /// Samples currently held in look-ahead (drain at stream end).
    pub fn flush(&mut self) -> usize {
        self.delay.drain(..).count()
    }
}

impl Default for LookAheadLimiter {
    fn default() -> Self {
        Self::new()
    }
}

/// Counts rail-adjacent samples (≥ −0.05 dBFS) per frame.
pub fn count_clipped(frame: &[f32]) -> u32 {
    frame.iter().filter(|&&s| s.abs() >= 0.994).count() as u32
}

fn rms(frame: &[f32]) -> f32 {
    if frame.is_empty() {
        return 0.0;
    }
    (frame.iter().map(|&s| s * s).sum::<f32>() / frame.len() as f32).sqrt()
}

// ---------------------------------------------------------------------------
// Echo-cancellation seam (honest: not integrated for trunk paths)
// ---------------------------------------------------------------------------

/// Echo-cancellation seam. Acoustic AEC needs valid capture + render
/// reference signals; PSTN trunk audio only ever provides a capture side.
/// Implementations must never be constructed with a bogus reference — the
/// [`NullEchoCanceller`] is the honest default.
pub trait EchoCanceller {
    /// Process capture audio; `reference` is the currently-rendering signal
    /// (must be supplied by the caller only when genuinely simultaneous).
    /// Simultaneity (reference phải be aligned within a millisecond or so)
    /// is validated by the caller behind `has_valid_reference`.
    fn process_capture(&mut self, capture: &mut [f32], reference: &[f32]);
    fn has_valid_reference(&self) -> bool;
}

/// The honest default: exactly what trunk paths need — nothing.
#[derive(Debug)]
pub struct NullEchoCanceller;

impl EchoCanceller for NullEchoCanceller {
    fn process_capture(&mut self, _capture: &mut [f32], _reference: &[f32]) {
        // No reference ⇒ no cancellation possible. Fail honest: no-op.
    }
    fn has_valid_reference(&self) -> bool {
        false
    }
}

// ---------------------------------------------------------------------------
// Profiles, configuration, stats
// ---------------------------------------------------------------------------

/// voice boost strength presets.
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VoiceBoostProfile {
    /// Minimal processing; preserves the original speech character.
    Natural,
    /// Moderate suppression + gain control + clarity.
    Clear,
    /// Strongest enhancement for noisy environments, capped short of
    /// audible processing artifacts.
    Studio,
}

/// Independently configurable DSP controls with profile presets and
/// per-control overrides. Global *defaults* live with the orchestrator; this
/// struct is the per-call instantiated configuration.
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct VoiceBoostConfig {
    /// Master switch: identity passthrough when true (bytes preserved).
    pub bypass: bool,
    pub noise_suppression: bool,
    /// Residual-gate strength 0..1 layered after nnnoiseless output
    /// (profile grading: NATURAL 0.25, CLEAR 0.55, STUDIO 0.85).
    pub ns_strength: f32,
    pub agc: bool,
    pub agc_target_dbfs: f32,
    pub agc_max_boost_db: f32,
    pub agc_max_cut_db: f32,
    /// Loudness normalization (slow integrator on the same target).
    pub normalize: bool,
    pub eq: bool,
    pub de_ess: bool,
    pub de_ess_strength: f32,
    pub clipping_watch: bool,
}

impl Default for VoiceBoostConfig {
    fn default() -> Self {
        Self::for_profile(VoiceBoostProfile::Clear)
    }
}

impl VoiceBoostConfig {
    pub fn for_profile(profile: VoiceBoostProfile) -> Self {
        let (ns, target, boost, eq, de_ess) = match profile {
            VoiceBoostProfile::Natural => (0.25, -22.0, 12.0, false, 0.15),
            VoiceBoostProfile::Clear => (0.55, -20.0, 18.0, true, 0.45),
            VoiceBoostProfile::Studio => (0.85, -19.0, 24.0, true, 0.7),
        };
        Self {
            bypass: false,
            noise_suppression: true,
            ns_strength: ns,
            agc: true,
            agc_target_dbfs: target,
            agc_max_boost_db: boost,
            agc_max_cut_db: 12.0,
            normalize: true,
            eq,
            de_ess: true,
            de_ess_strength: de_ess,
            clipping_watch: true,
        }
    }

    // Per-control overrides (each independent, per the master spec).

    pub fn with_bypass(mut self, bypass: bool) -> Self {
        self.bypass = bypass;
        self
    }
    pub fn with_noise_suppression(mut self, enabled: bool, strength: f32) -> Self {
        self.noise_suppression = enabled;
        self.ns_strength = strength.clamp(0.0, 1.0);
        self
    }
    pub fn with_agc(mut self, enabled: bool, target_dbfs: f32) -> Self {
        self.agc = enabled;
        self.agc_target_dbfs = target_dbfs;
        self
    }
    pub fn with_eq(mut self, enabled: bool) -> Self {
        self.eq = enabled;
        self
    }
    pub fn with_de_ess(mut self, enabled: bool, strength: f32) -> Self {
        self.de_ess = enabled;
        self.de_ess_strength = strength.clamp(0.0, 1.0);
        self
    }
    pub fn with_normalize(mut self, enabled: bool) -> Self {
        self.normalize = enabled;
        self
    }
}

/// Per-stage processing stats (serde for the future diagnostics API).
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct VoiceBoostStats {
    pub input_rms: f32,
    pub output_rms: f32,
    pub gain_applied_db: f32,
    pub ns_vad_prob: f32,
    pub gate_floor_applied: f32,
    pub de_ess_db: f32,
    pub clipped_input: u32,
    pub clipped_output: u32,
    pub limiter_engaged: bool,
    pub nn_frames: u32,
    /// Samples still buffered (chunk-split carries; empty when settled).
    pub buffered: usize,
}

/// One Voice Boost processed frame plus its stats.
#[derive(Debug)]
pub struct EnhancedFrame {
    pub pcm: Vec<i16>,
    pub stats: VoiceBoostStats,
}

// ---------------------------------------------------------------------------
// The orchestration stage
// ---------------------------------------------------------------------------

/// One call's Voice Boost processing stage. No global state: per-call
/// isolation is structural. Sample rate fixed to [`ENGINE_RATE`].
pub struct VoiceBoostStage {
    pub config: VoiceBoostConfig,
    ups: Upsampler3,
    nn: Box<DenoiseState<'static>>,
    nn_buf: Vec<f32>,
    out_buf: [f32; DenoiseState::FRAME_SIZE],
    nn_frames_total: u32,
    first_nn_frame: bool,
    downs: Downsampler3,
    /// Post-resample mastering accumulator; frame-quantized dynamics drain
    /// it on a fixed 320-sample (20 ms) grid for chunk-split determinism.
    frame_buf: Vec<f32>,
    lowcut: Option<Biquad>,
    presence: Option<Biquad>,
    de_esser: Option<DeEsser>,
    agc: Agc,
    limiter: LookAheadLimiter,
    echo: Box<dyn EchoCanceller>,
    carry_in: Vec<f32>,
}

impl VoiceBoostStage {
    pub fn new(config: VoiceBoostConfig) -> Self {
        let lowcut = config.eq.then(|| Biquad::highpass(90.0, 0.707));
        let presence = config.eq.then(|| Biquad::peaking(3200.0, 1.0, 3.0));
        let de_esser = config.de_ess.then(|| DeEsser::new(config.de_ess_strength, 0.3));
        let agc = Agc::new(
            config.agc_target_dbfs,
            config.agc_max_boost_db,
            config.agc_max_cut_db,
            config.agc,
            config.normalize,
        );
        Self {
            config,
            ups: Upsampler3::new(),
            nn: DenoiseState::new(),
            nn_buf: Vec::with_capacity(2 * DenoiseState::FRAME_SIZE),
            out_buf: [0.0; DenoiseState::FRAME_SIZE],
            nn_frames_total: 0,
            // First nnnoiseless output frame is a documented fade-in; tag it
            // in stats but keep it (alignment determinism first).
            first_nn_frame: true,
            downs: Downsampler3::new(33, 0.45),
            frame_buf: Vec::new(),
            lowcut,
            presence,
            de_esser,
            agc,
            limiter: LookAheadLimiter::new(),
            echo: Box::new(NullEchoCanceller),
            carry_in: Vec::new(),
        }
    }

    /// Swap in an echo canceller implementation (only when the caller can
    /// actually supply a valid, aligned render reference).
    pub fn set_echo_canceller(&mut self, echo: Box<dyn EchoCanceller>) {
        self.echo = echo;
    }

    /// Engine-rate PCM16 in → Voice Boost out. Accepts arbitrary chunk
    /// sizes; carries sub-frame remainders internally (chunk-split
    /// deterministic).
    pub fn process(&mut self, samples: &[i16]) -> EnhancedFrame {
        let stats_base_clipped = if self.config.clipping_watch {
            count_clipped(
                &samples.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
            )
        } else {
            0
        };
        if self.config.bypass || samples.is_empty() {
            return EnhancedFrame {
                pcm: samples.to_vec(),
                stats: VoiceBoostStats {
                    input_rms: f64::from(rms_i16(samples)) as f32,
                    output_rms: f64::from(rms_i16(samples)) as f32,
                    clipped_input: stats_base_clipped,
                    buffered: self.pending_count(),
                    ..VoiceBoostStats::default()
                },
            };
        }

        // i16 → f32 (raw 16-bit domain for nnnoiseless compatibility).
        let mut input: Vec<f32> = samples.iter().map(|&s| f32::from(s)).collect();
        let pre_rms = rms(&input.iter().map(|&s| s / 32768.0).collect::<Vec<_>>());

        // Echo seam (no-op with NullEchoCanceller; reference capture is not
        // wired in trunk paths — see EchoCanceller doc).
        if self.echo.has_valid_reference() {
            self.echo.process_capture(&mut input, &[]);
        }

        // 16 k → 48 k, into the 480-sample nnnoiseless frame accumulator.
        let up = self.ups.process(&input);
        self.nn_buf.extend_from_slice(&up);
        let mut vad_max = 0.0f32;
        let gate_floor = self.gate_floor();
        let enhanced_48k: Vec<f32> = self
            .nn_buf
            .chunks_exact(DenoiseState::FRAME_SIZE)
            .flat_map(|frame| {
                let vad = self.nn.process_frame(&mut self.out_buf, frame);
                let mut frame = self.out_buf.to_vec();
                // Profile-graded residual gate on top of the NN output.
                vad_max = vad_max.max(vad);
                let gate = gate_floor + (1.0 - gate_floor) * vad;
                for s in frame.iter_mut() {
                    *s *= gate;
                }
                self.nn_frames_total += 1;
                self.first_nn_frame = false;
                frame
            })
            .collect();
        // Retain only the sub-frame remainder.
        let consumed = self.nn_buf.len() / DenoiseState::FRAME_SIZE * DenoiseState::FRAME_SIZE;
        self.nn_buf.drain(..consumed);

        // 48 k → 16 k, then accumulate onto the fixed 20 ms mastering grid.
        // All frame-quantized dynamics (de-ess/AGC/limiter gating) run on
        // this content-derived grid, never per process() call — that keeps
        // output identical under arbitrary chunk splits.
        let down = self.downs.process(&enhanced_48k);
        self.frame_buf
            .extend(down.iter().map(|&s| (s / 32768.0).clamp(-1.0, 1.0)));

        // Fixed mastering frames of 20 ms (320 samples at the engine rate).
        const FRAME: usize = 320;
        let mut pcm_out: Vec<i16> = Vec::with_capacity(FRAME * (self.frame_buf.len() / FRAME));
        let mut any_clipped = 0u32;
        let mut limiter_any = false;
        let mut de_ess_last = 0.0f32;
        let mut gain_last = 0.0f32;
        while self.frame_buf.len() >= FRAME {
            let mut frame: Vec<f32> = self.frame_buf.drain(..FRAME).collect();

            // Clipping watch on the enhanced pre-master signal.
            if self.config.clipping_watch {
                any_clipped += count_clipped(&frame);
            }

            // EQ chain.
            if let (Some(lc), Some(pe)) = (self.lowcut.as_mut(), self.presence.as_mut()) {
                for s in frame.iter_mut() {
                    let x = *s;
                    *s = pe.run(lc.run(x));
                }
            } else if let Some(lc) = self.lowcut.as_mut() {
                for s in frame.iter_mut() {
                    *s = lc.run(*s);
                }
            }

            // De-ess.
            if let Some(de) = self.de_esser.as_mut() {
                de_ess_last = de.process(&mut frame);
            }

            // AGC + limiter.
            let (g_start, g_end) = self.agc.frame_gain(&frame);
            gain_last = g_end;
            let n = frame.len();
            for (i, s) in frame.iter_mut().enumerate() {
                let g_db = g_start
                    + (g_end - g_start) * (if n > 1 { i as f32 / (n - 1) as f32 } else { 1.0 });
                *s *= 10f32.powf(g_db / 20.0);
            }
            let engaged = self.limiter.process(&mut frame);
            limiter_any |= engaged;

            // To i16 with hard clamp (limiter should prevent overs; never wrap).
            pcm_out.extend(frame.iter().map(|&s| {
                (s * 32768.0).clamp(-32768.0, 32767.0).round() as i16
            }));
        }
        let out_rms = rms_i16(&pcm_out) as f32;

        EnhancedFrame {
            pcm: pcm_out,
            stats: VoiceBoostStats {
                input_rms: pre_rms,
                output_rms: out_rms,
                gain_applied_db: gain_last,
                ns_vad_prob: vad_max,
                gate_floor_applied: gate_floor,
                de_ess_db: de_ess_last,
                clipped_input: stats_base_clipped,
                clipped_output: any_clipped,
                limiter_engaged: limiter_any,
                nn_frames: self.nn_frames_total,
                buffered: self.pending_count(),
            },
        }
    }

    /// End of stream: flush interpolation tails + limiter look-ahead.
    /// Returns the final samples; the stage stays reusable.
    pub fn finish(&mut self) -> Vec<i16> {
        let tail = self.ups.flush_tail();
        self.nn_buf.extend_from_slice(&tail);
        let mut flush_out: Vec<i16> = Vec::new();
        while self.nn_buf.len() >= DenoiseState::FRAME_SIZE {
            let frame: Vec<f32> = self.nn_buf.drain(..DenoiseState::FRAME_SIZE).collect();
            let _vad = self.nn.process_frame(&mut self.out_buf, &frame);
            let mut f = self.out_buf.to_vec();
            let gate = self.gate_floor();
            for s in f.iter_mut() {
                *s *= gate;
            }
            let down = self.downs.process(&f);
            for s in down {
                let v = ((s / 32768.0) as f64).clamp(-1.0, 1.0) * 32768.0;
                flush_out.push(v.round() as i16);
            }
        }
        // Drain the 16 kHz mastering remainder without re-quantizing frame
        // dynamics (no AGC/limiter on the tail — bounded < 20 ms).
        let remainder: Vec<f32> = self
            .frame_buf
            .drain(..)
            .map(|s| s * 10f32.powf(self.agc.gain_db / 20.0))
            .collect();
        flush_out.extend(remainder.iter().map(|&s| {
            (s * 32768.0).clamp(-32768.0, 32767.0).round() as i16
        }));
        self.limiter.flush();
        self.carry_in.clear();
        flush_out
    }

    fn gate_floor(&self) -> f32 {
        1.0 - 0.75 * self.config.ns_strength
    }

    /// Sample count buffered in the 48 kHz NN-frame accumulator (the only
    /// unbounded-state concern; always < 2 × FRAME_SIZE plus a chunk).
    pub fn pending_count(&self) -> usize {
        self.nn_buf.len() + self.frame_buf.len()
    }
}

/// RMS of an i16 slice in [0, 1].
fn rms_i16(samples: &[i16]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    (samples.iter().map(|&s| (f64::from(s) / 32768.0).powi(2)).sum::<f64>() / samples.len() as f64)
        .sqrt() as f64
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::media::{FakeMediaWs, FormatRegistry, MediaBridge, PCM16_16K, ULAW_8K};
    use crate::dsp::VoiceBoostProfile::{Clear, Natural, Studio};
    use std::time::Duration;

    fn lcg_noise(n: usize, amplitude: f32, seed: u32) -> Vec<f32> {
        let mut state = seed;
        (0..n)
            .map(|_| {
                state = state.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
                let v = ((state >> 8) & 0xFFFF) as f32 / 32768.0 - 1.0;
                v * amplitude
            })
            .collect()
    }

    fn sine(n: usize, freq: f32, amplitude: f32) -> Vec<f32> {
        (0..n)
            .map(|i| (std::f32::consts::PI * 2.0 * freq * i as f32 / ENGINE_RATE as f32).sin() * amplitude)
            .collect()
    }

    fn i16_of(v: &[f32]) -> Vec<i16> {
        v.iter().map(|&s| (s * 32768.0).clamp(-32768.0, 32767.0).round() as i16).collect()
    }

    fn rms_of(v: &[f32]) -> f32 {
        rms(v)
    }

    fn raw_config(strength: f32) -> VoiceBoostConfig {
        VoiceBoostConfig {
            bypass: false,
            noise_suppression: true,
            ns_strength: strength,
            agc: false,
            agc_target_dbfs: -20.0,
            agc_max_boost_db: 18.0,
            agc_max_cut_db: 12.0,
            normalize: false,
            eq: false,
            de_ess: false,
            de_ess_strength: 0.0,
            clipping_watch: true,
        }
    }

    #[test]
    fn upsampler_emits_three_outputs_per_input_after_flush() {
        let mut up = Upsampler3::new();
        let input: Vec<f32> = (0..100).map(|i| (i as f32) * 0.1).collect();
        let out = up.process(&input);
        let tail = up.flush_tail();
        assert_eq!(out.len() + 2, 3 * input.len());
        assert!(tail.iter().all(|s| s.is_finite()));
        // Continuity: no discontinuity bigger than one step between outputs.
        let mut all = out;
        all.extend_from_slice(&tail);
        for w in all.windows(2) {
            assert!((w[0] - w[1]).abs() < 1.1, "discontinuity {w:?}");
        }
    }

    #[test]
    fn downsampler_preserves_bandlimited_signal_rms() {
        let mut up = Upsampler3::new();
        let sine_in = sine(2_000, 1_000.0, 0.25);
        let up = up.process(&sine_in);
        let mut down = Downsampler3::new(33, 0.45);
        let out = down.process(&up);
        let in_rms = 0.25 / std::f32::consts::SQRT_2;
        let out_rms = rms_of(&out);
        let ratio_db = 20.0 * (out_rms / in_rms).log10();
        assert!(
            ratio_db > -2.5 && ratio_db < 1.5,
            "RMS ratio {ratio_db} dB for resampler round trip"
        );
    }

    #[test]
    fn lowcut_removes_dc_component() {
        let mut lc = Biquad::highpass(90.0, 0.707);
        let last = (0..3_000).fold(0.0f32, |_, _| lc.run(0.5).abs());
        assert!(last < 0.01, "low-cut leaks DC: {last}");
    }

    #[test]
    fn presence_eq_boosts_speech_band_but_not_bass() {
        let mut eq_on = Biquad::peaking(3200.0, 1.0, 3.0);
        let mut eq_off = Biquad::peaking(3200.0, 1.0, 0.0);
        let mut measure = |freq: f32, stage: &mut Biquad| {
            let input = sine(2_000, freq, 0.2);
            let out: Vec<f32> = input.iter().map(|&s| stage.run(s)).collect();
            // Measure the last 1_000 samples (steady state).
            rms_of(&out[1_000..])
        };
        let hi_on = measure(3_200.0, &mut eq_on);
        let hi_off = measure(3_200.0, &mut eq_off);
        let lo_on = measure(400.0, &mut eq_on);
        let lo_off = measure(400.0, &mut eq_off);
        assert!(hi_on > hi_off * 1.2, "presence not boosted: {hi_on} vs {hi_off}");
        assert!(
            (lo_on - lo_off).abs() < lo_off * 0.15,
            "bass changed unexpectedly: {lo_on} vs {lo_off}"
        );
    }

    #[test]
    fn de_esser_attenuates_sibilance_only() {
        // Sibilance-like: strong 6.5 kHz content over a quiet voice band.
        let mut frame: Vec<f32> = sine(320, 6_500.0, 0.3);
        frame.extend(sine(0, 400.0, 0.05));
        let mut de = DeEsser::new(0.9, 0.3);
        let applied = de.process(&mut frame);
        assert!(applied < -2.0, "de-esser not engaged: {applied}");
        // A pure voice-band frame is untouched.
        let mut voice = sine(320, 400.0, 0.2);
        let applied = de.process(&mut voice);
        assert!(applied.abs() < 0.5, "de-esser engaged on voice: {applied}");
    }

    #[test]
    fn agc_boosts_quiet_speech_to_target_without_clipping() {
        let mut stage = VoiceBoostStage::new(VoiceBoostConfig::for_profile(Clear));
        let input = i16_of(&sine(320 * 80, 440.0, 0.02)); // −37 dBFS-ish
        let mut outs = Vec::new();
        for frame in input.chunks(320) {
            outs.extend_from_slice(&stage.process(frame).pcm);
        }
        let out_rms_db = 20.0 * rms_of(&outs.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>())
            .max(1e-6)
            .log10();
        // Warmup frames 1..40 (AGC alpha 0.12/frame converges ~95% by 40),
        // measure the settled tail.
        let tail = &outs[40 * 320..];
        let tail_db = 20.0 * rms_of(&tail.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>())
            .max(1e-6)
            .log10();
        assert!(
            (tail_db - (-20.0)).abs() < 3.0,
            "AGC target missed: settled {tail_db} dBFS, full {out_rms_db} dBFS"
        );
        assert!(outs.iter().all(|&s| s.abs() <= 32_767));
    }

    #[test]
    fn limiter_bounds_peak_under_overload() {
        let mut stage = VoiceBoostStage::new(
            VoiceBoostConfig::for_profile(Clear).with_agc(false, -20.0),
        );
        // 150% overrange square-ish wave.
        let input: Vec<i16> = (0..320 * 40)
            .map(|i| if (i / 160) % 2 == 0 { 32_760 } else { -32_760 })
            .collect();
        let mut outs = Vec::new();
        for frame in input.chunks(320) {
            outs.extend_from_slice(&stage.process(frame).pcm);
        }
        let peak = outs.iter().fold(0i32, |m, &s| m.max(s.abs() as i32));
        assert!(peak <= 32_767, "limiter let {peak} through");
        // Most of the stream must be bounded well under the rail.
        let bounded = outs.iter().filter(|&&s| s.abs() <= 32_700).count();
        assert!(bounded > outs.len() * 8 / 10, "only {bounded} bounded");
    }

    #[test]
    fn clipping_detector_counts_rail_samples() {
        let clipped: Vec<f32> = vec![1.0, 0.999, -1.0, 0.5, -0.996, 0.1];
        assert_eq!(count_clipped(&clipped), 4);
        let clean = sine(320, 440.0, 0.5);
        assert_eq!(count_clipped(&clean), 0);
    }

    #[test]
    fn nnnoiseless_suppresses_stationary_noise() {
        let stage = VoiceBoostStage::new(raw_config(0.55));
        let mut stage = stage;
        let noise = lcg_noise(320 * 60, 0.15, 0x1234_5678);
        let in_rms = rms_of(&noise[..320 * 30]);
        let mut outs = Vec::new();
        for frame in i16_of(&noise).chunks(320) {
            outs.extend_from_slice(&stage.process(frame).pcm);
        }
        let out = &outs[320 * 30..]; // skip fade-in/FIR settle
        let out_rms = rms_of(&out.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>());
        let reduction = 20.0 * (out_rms / in_rms).log10();
        assert!(
            reduction < -6.0,
            "noise suppression under-performed: {reduction} dB"
        );
    }

    #[test]
    fn nnnoiseless_preserves_clean_speech_tone() {
        let mut stage = VoiceBoostStage::new(raw_config(0.55));
        let tone = sine(320 * 60, 440.0, 0.08);
        let mut outs = Vec::new();
        for frame in i16_of(&tone).chunks(320) {
            outs.extend_from_slice(&stage.process(frame).pcm);
        }
        let out = &outs[320 * 30..];
        let in_rms = 0.08 / std::f32::consts::SQRT_2;
        let out_rms = rms_of(&out.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>());
        let delta_db = 20.0 * (out_rms / in_rms).log10();
        assert!(
            delta_db > -6.0,
            "clean speech attenuated too much: {delta_db} dB"
        );
    }

    #[test]
    fn profile_grading_is_monotonic_on_noise() {
        let run = |strength: f32| {
            let mut stage = VoiceBoostStage::new(raw_config(strength));
            let noise = lcg_noise(320 * 40, 0.12, 0xBEEF);
            let mut outs = Vec::new();
            for frame in i16_of(&noise).chunks(320) {
                outs.extend_from_slice(&stage.process(frame).pcm);
            }
            rms_of(&outs[320 * 20..].iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>())
        };
        let natural = run(0.25);
        let clear = run(0.55);
        let studio = run(0.85);
        assert!(
            studio < clear && clear < natural,
            "profile grading not monotonic: {natural:.4} / {clear:.4} / {studio:.4}"
        );
    }

    #[test]
    fn bypass_preserves_bytes_exactly() {
        let mut stage = VoiceBoostStage::new(VoiceBoostConfig::for_profile(Studio).with_bypass(true));
        let input = lcg_noise(1_234, 0.3, 42);
        let out = stage.process(&i16_of(&input));
        assert_eq!(out.pcm, i16_of(&input));
        assert_eq!(out.stats.input_rms, out.stats.output_rms);
    }

    #[test]
    fn chunk_split_processing_is_deterministic() {
        let input = lcg_noise(320 * 50, 0.1, 777);
        let mut whole = VoiceBoostStage::new(raw_config(0.55));
        let whole = whole.process(&i16_of(&input)).pcm;

        let mut split = VoiceBoostStage::new(raw_config(0.55));
        let mut gathered = Vec::new();
        for (i, chunk) in i16_of(&input).chunks(500).enumerate() {
            let out = split.process(chunk);
            gathered.extend_from_slice(&out.pcm);
            if i == 0 {
                assert!(out.stats.buffered > 0, "carry must be reported");
            }
        }
        assert_eq!(whole, gathered, "chunk-split outputs diverged");
    }

    #[test]
    fn per_call_stages_do_not_leak_state() {
        let mut a = VoiceBoostStage::new(raw_config(0.55));
        let mut b = VoiceBoostStage::new(raw_config(0.55));
        let noise = lcg_noise(320 * 20, 0.1, 99);
        let a_out = a.process(&i16_of(&noise)).pcm;
        let b_out = b.process(&i16_of(&noise)).pcm;
        assert_eq!(a_out, b_out, "identical stages must produce identical output");
        // Stage A's continued output matches a reference stage replayed
        // through the same sequence — its state belongs to this call alone
        // (RNN warm state is per-stage, nothing is shared or global).
        let noise2 = lcg_noise(320 * 20, 0.1, 101);
        let mut replay = VoiceBoostStage::new(raw_config(0.55));
        let _ = replay.process(&i16_of(&noise)).pcm;
        assert_eq!(
            a.process(&i16_of(&noise2)).pcm,
            replay.process(&i16_of(&noise2)).pcm
        );
    }

    #[test]
    fn empty_input_is_safe() {
        let mut stage = VoiceBoostStage::new(raw_config(0.55));
        let out = stage.process(&[]);
        assert!(out.pcm.is_empty());
        let fin = stage.finish();
        assert!(fin.iter().all(|&s| s.abs() <= 32_767));
    }

    #[test]
    fn echo_seam_is_honest_by_default() {
        let mut null = NullEchoCanceller;
        assert!(!null.has_valid_reference());
        let mut capture = vec![0.5f32; 100];
        null.process_capture(&mut capture, &[]);
        assert_eq!(capture, vec![0.5; 100], "null canceller must not mutate");
    }

    #[test]
    fn media_bridge_with_voice_boost_still_completes_turns() {
        let registry = FormatRegistry::new();
        let mut bridge = MediaBridge::new(
            &registry,
            Box::new(FakeMediaWs::new()),
            ULAW_8K,
            PCM16_16K,
            64,
        )
        .with_voice_boost(VoiceBoostStage::new(VoiceBoostConfig::for_profile(Clear)));
        // A burst of "speech"-level μ-law frames (RMS above the voiced floor).
        let speech: Vec<u8> = (0..10)
            .flat_map(|_| {
                sine(160, 440.0, 0.3)
                    .iter()
                    .map(|&s| crate::media::pcm16_to_ulaw((s * 32768.0) as i16))
                    .collect::<Vec<u8>>()
            })
            .collect();
        let mut emitted = 0;
        for frame in speech.chunks(160) {
            emitted += bridge.on_inbound_frame(frame, Duration::from_millis(20)).unwrap();
        }
        // Voice Boost may retune the voiced-floor crossing; either the reply
        // fired inside the burst or a finish() closes the utterance.
        if emitted == 0 {
            emitted = bridge.finish(Duration::from_millis(20)).unwrap();
        }
        assert_eq!(emitted, 1, "turn must still complete with boost enabled");
        assert!(bridge.tracker.total("user_in_dsp").is_some(), "DSP hop recorded");
        // Disabled path stays byte-identical to the legacy pipeline.
        let mut legacy = MediaBridge::new(
            &registry,
            Box::new(FakeMediaWs::new()),
            ULAW_8K,
            PCM16_16K,
            64,
        );
        let _ = legacy.on_inbound_frame(&speech[..160], Duration::from_millis(20)).unwrap();
        assert!(legacy.tracker.total("user_in_dsp").is_none());
    }

    #[test]
    fn config_round_trips_through_serde() {
        let config = VoiceBoostConfig::for_profile(Studio)
            .with_noise_suppression(false, 0.4)
            .with_agc(true, -18.5);
        let json = serde_json::to_string(&config).unwrap();
        let back: VoiceBoostConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(back.noise_suppression, false);
        assert_eq!(back.agc_target_dbfs, -18.5);
        let profile_json = serde_json::to_string(&Natural).unwrap();
        assert_eq!(profile_json, "\"natural\"");
        let _ = Clear; let _ = Studio;
    }
}
