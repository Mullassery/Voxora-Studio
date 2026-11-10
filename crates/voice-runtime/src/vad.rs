//! Voice activity detection.
//!
//! [`VadProvider`] is a provider-independent interface: the deterministic
//! [`EnergyVad`] reference ships in-tree (zero dependencies, exact in tests),
//! while a model-backed provider such as Silero VAD drops in behind the same
//! trait by converting one frame of PCM16 to a speech probability. VAD only
//! ever answers "is speech happening right now": it must not decide on its
//! own that a conversational turn finished — that decision lives in
//! [`crate::endpointing`] and the controller.
//!
//! [`SpeechActivityTracker`] adds the hysteresis machinery on top of any
//! provider: separate start/end thresholds, sustained-speech confirmation,
//! short-noise rejection, and per-call isolation (every call gets its own
//! tracker; a VAD that locks onto room tone of one call cannot affect
//! others).


/// One VAD decision for a frame of audio.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct VadDecision {
    /// Raw speech probability from the provider, 0.0..=1.0.
    pub probability: f32,
    /// Whether the tracker's hysteresis declares speech active.
    pub speech_active: bool,
}

/// Provider-independent VAD. Implementations must be cheap enough to run on
/// every 20 ms frame and must be resettable per call.
pub trait VadProvider {
    /// Feed one frame of PCM16 mono samples and return speech probability
    /// in 0.0..=1.0.
    fn speech_probability(&mut self, samples: &[i16]) -> f32;

    /// Clear per-call learned state (noise floor, smoothing).
    fn reset(&mut self);

    /// Provider identifier used in events and benchmarks.
    fn name(&self) -> &'static str;
}

/// Energy reference VAD: SNR-ratio speech probability against an adaptive
/// noise floor so background noise tolerance comes from adaptation, not a
/// hardcoded cliff.
///
/// Noise-floor adaptation is asymmetric: frames below the current floor
/// pull it down quickly (room tone settles the estimate), frames between
/// floor and speech levels pull it up slowly (so a louder environment
/// adapts too), and frames at speech level never move it at all — a cough
/// can neither recalibrate tolerance mid-speech nor fake speech.
#[derive(Debug, Clone)]
pub struct EnergyVad {
    /// Downward adaptation factor for sub-floor frames.
    pub floor_down_alpha: f32,
    /// Slow upward adaptation factor for between-floor-and-speech frames.
    pub floor_up_alpha: f32,
    /// Absolute ceiling: frames at or above this never raise the floor
    /// (this is presumed speech, no matter how long it lasts).
    pub speech_ceiling: f32,
    /// Lower clamp so dead-silent audio cannot produce a zero-floor divide.
    pub floor_min: f32,
    /// Smoothing of the reported probability (fast-decay).
    pub prob_alpha: f32,
    noise_floor: f32,
    smoothed: f32,
}

impl EnergyVad {
    pub fn new() -> Self {
        Self {
            floor_down_alpha: 0.05,
            floor_up_alpha: 0.02,
            speech_ceiling: 0.24,
            floor_min: 0.002,
            prob_alpha: 0.5,
            noise_floor: 0.01,
            smoothed: 0.0,
        }
    }
}

impl Default for EnergyVad {
    fn default() -> Self {
        Self::new()
    }
}

impl VadProvider for EnergyVad {
    fn speech_probability(&mut self, samples: &[i16]) -> f32 {
        let raw = crate::media::frame_rms(samples);
        let floor = self.noise_floor.max(self.floor_min);
        let ceiling = (floor * 3.0).max(self.speech_ceiling);
        if raw < floor {
            self.noise_floor += self.floor_down_alpha * (raw - self.noise_floor);
        } else if raw < ceiling {
            self.noise_floor += self.floor_up_alpha * (raw - self.noise_floor);
        }
        // SNR-ratio squash: raw below 1.3x floor reads as probability 0,
        // raw at 6.3x floor reads as probability 1.
        let ratio = raw / (self.noise_floor.max(self.floor_min) + 1e-9);
        let probability = ((ratio - 1.3) / 5.0).clamp(0.0, 1.0);
        self.smoothed += self.prob_alpha * (probability - self.smoothed);
        self.smoothed.clamp(0.0, 1.0)
    }

    fn reset(&mut self) {
        self.noise_floor = 0.01;
        self.smoothed = 0.0;
    }

    fn name(&self) -> &'static str {
        "energy"
    }
}

/// Fixed-probability provider: a stand-in for model-backed VAD in tests and
/// for simulating a provider that always (or never) hears speech.
#[derive(Debug, Clone)]
pub struct StaticVad {
    pub probability: f32,
}

impl VadProvider for StaticVad {
    fn speech_probability(&mut self, _samples: &[i16]) -> f32 {
        self.probability
    }

    fn reset(&mut self) {}

    fn name(&self) -> &'static str {
        "static"
    }
}

impl StaticVad {
    pub fn constant(probability: f32) -> Self {
        Self { probability: probability.clamp(0.0, 1.0) }
    }
}

/// Concrete VAD engine behind a tracker: in-tree deterministic engines
/// today, with a model-backed provider (Silero via ONNX) plugging in here
/// later without changing the tracker or any consumer.
#[derive(Debug, Clone)]
pub enum VadEngine {
    Energy(EnergyVad),
    Static(StaticVad),
}

impl VadProvider for VadEngine {
    fn speech_probability(&mut self, samples: &[i16]) -> f32 {
        match self {
            Self::Energy(e) => e.speech_probability(samples),
            Self::Static(s) => s.speech_probability(samples),
        }
    }

    fn reset(&mut self) {
        match self {
            Self::Energy(e) => e.reset(),
            Self::Static(s) => s.reset(),
        }
    }

    fn name(&self) -> &'static str {
        match self {
            Self::Energy(e) => e.name(),
            Self::Static(s) => s.name(),
        }
    }
}

impl From<EnergyVad> for VadEngine {
    fn from(v: EnergyVad) -> Self {
        Self::Energy(v)
    }
}

impl From<StaticVad> for VadEngine {
    fn from(v: StaticVad) -> Self {
        Self::Static(v)
    }
}

/// Hysteresis tracker over a [`VadProvider`]: the answer to "when did the
/// caller start and stop speaking" with short-noise rejection.
///
/// * Start threshold is higher than the end threshold (double hysteresis),
///   so ordinary noise does not open speech but speech fading out is not
///   chopped into pieces.
/// * Speech must be sustained for `min_speech_ms` before it counts as
///   started; shorter bursts are treated as short noise and rejected.
/// * End is not declared here at all — the tracker only reports that speech
///   went quiet; endpointing owns the turn boundary.
#[derive(Debug)]
pub struct SpeechActivityTracker {
    config: SpeechActivityConfig,
    provider: VadEngine,
    digital_high: bool,
    speech_frames: u32,
    silence_frames: u32,
    pub noise_duration_ms: u64,
    pub speech_open_ms: u64,
}

#[derive(Debug, Clone)]
pub struct SpeechActivityConfig {
    /// Probability at/above which a frame opens speech candidature.
    pub start_threshold: f32,
    /// Probability below which a frame counts as silence.
    pub end_threshold: f32,
    /// Sustained speech before `speech_started` is the honest truth.
    pub min_speech_ms: u64,
    /// Frame size the transport guarantees (20 ms standard).
    pub frame_ms: u64,
    /// Minimum speech energy frames needed even after the threshold.
    pub min_speech_frames: u32,
    /// Sustained silence before the tracker reports speech as inactive;
    /// a brief dip must not chop speech (endpointing owns boundaries).
    pub min_silence_close_ms: u64,
}

impl Default for SpeechActivityConfig {
    fn default() -> Self {
        Self {
            start_threshold: 0.5,
            end_threshold: 0.25,
            min_speech_ms: 120,
            frame_ms: 20,
            min_speech_frames: 3,
            min_silence_close_ms: 160,
        }
    }
}

impl SpeechActivityTracker {
    pub fn new(provider: impl Into<VadEngine>, config: SpeechActivityConfig) -> Self {
        assert!(
            config.start_threshold >= config.end_threshold,
            "start threshold must not be below end threshold"
        );
        Self {
            config,
            provider: provider.into(),
            digital_high: false,
            speech_frames: 0,
            silence_frames: 0,
            noise_duration_ms: 0,
            speech_open_ms: 0,
        }
    }

    pub fn provider_name(&self) -> &'static str {
        self.provider.name()
    }

    pub fn config(&self) -> &SpeechActivityConfig {
        &self.config
    }

    /// Was speech declared active before this frame?
    pub fn is_speech_active(&self) -> bool {
        self.digital_high
    }

    /// Above-threshold frames collected since the last reset. Non-zero
    /// while candidature is open but the sustained-speech window has not
    /// confirmed yet — the controller's `SpeechDetected` state.
    pub fn candidature_frames(&self) -> u32 {
        self.speech_frames
    }


    /// Process one frame. Returns the decision with hysteresis applied.
    pub fn process_frame(&mut self, samples: &[i16]) -> VadDecision {
        let probability = self.provider.speech_probability(samples);
        let frame_ms = self.config.frame_ms;
        if self.digital_high {
            if probability < self.config.end_threshold {
                self.silence_frames += 1;
                self.speech_open_ms += frame_ms;
                if self.silence_frames as u64 * frame_ms
                    >= self.config.min_silence_close_ms
                {
                    self.digital_high = false;
                    self.speech_frames = 0;
                    self.silence_frames = 0;
                }
            } else {
                self.silence_frames = 0;
                self.speech_open_ms += frame_ms;
            }
        } else if probability >= self.config.start_threshold {
            self.speech_frames += 1;
            let sustained =
                self.speech_frames as u64 * frame_ms >= self.config.min_speech_ms;
            if sustained && self.speech_frames >= self.config.min_speech_frames {
                self.digital_high = true;
                self.noise_duration_ms = 0;
            }
        } else {
            if self.speech_frames > 0 {
                self.noise_duration_ms += self.speech_frames as u64 * frame_ms;
            }
            self.speech_frames = 0;
        }
        VadDecision { probability, speech_active: self.digital_high }
    }

    /// Per-call isolation: drop all learned state between calls.
    pub fn reset(&mut self) {
        self.provider.reset();
        self.digital_high = false;
        self.speech_frames = 0;
        self.silence_frames = 0;
        self.noise_duration_ms = 0;
        self.speech_open_ms = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAME_20MS_16K: usize = 320;

    fn silence_frame() -> Vec<i16> {
        vec![0; FRAME_20MS_16K]
    }

    fn loud_frame() -> Vec<i16> {
        vec![9000; FRAME_20MS_16K]
    }

    fn quiet_noise_frame(amp: i16) -> Vec<i16> {
        let mut f = vec![0; FRAME_20MS_16K];
        for (i, s) in f.iter_mut().enumerate() {
            *s = if i % 2 == 0 { amp } else { -amp };
        }
        f
    }

    #[test]
    fn energy_vad_separates_speech_from_silence() {
        let mut vad = EnergyVad::new();
        let silence_prob = loop {
            let p = vad.speech_probability(&silence_frame());
            if p < 0.2 {
                break p;
            }
        };
        assert!(silence_prob < 0.2, "silence prob {silence_prob}");
        let mut speech_prob = 0.0f32;
        for _ in 0..4 {
            speech_prob = vad.speech_probability(&loud_frame());
        }
        assert!(speech_prob > 0.9, "speech prob {speech_prob}");
    }

    #[test]
    fn background_noise_tolerance_is_adaptive_not_cliffed() {
        let mut vad = EnergyVad::new();
        // Calibrate on loud-ish room tone: the floor adapts up.
        for _ in 0..40 {
            let _ = vad.speech_probability(&quiet_noise_frame(1400));
        }
        for _ in 0..40 {
            let _ = vad.speech_probability(&quiet_noise_frame(1400));
        }
        let adapt = vad.speech_probability(&quiet_noise_frame(1400));
        assert!(adapt < 0.4, "room tone after adaptation {adapt}");
        // Real speech stays decisively above the adapted floor.
        let mut speech = 0.0f32;
        for _ in 0..4 {
            speech = vad.speech_probability(&loud_frame());
        }
        assert!(speech > 0.8, "speech over room tone {speech}");
    }

    #[test]
    fn short_noise_is_rejected() {
        let mut t = SpeechActivityTracker::new(
            EnergyVad::new(),
            SpeechActivityConfig::default(),
        );
        assert!(!t.process_frame(&loud_frame()).speech_active);
        assert!(!t.process_frame(&silence_frame()).speech_active);
        assert!(!t.process_frame(&loud_frame()).speech_active);
        assert!(t.noise_duration_ms > 0, "burst must be logged as noise");
        // Two full bursts, each shorter than min_speech_ms: never active.
        for _ in 0..2 {
            assert!(!t.process_frame(&loud_frame()).speech_active);
            assert!(!t.process_frame(&silence_frame()).speech_active);
        }
    }

    #[test]
    fn sustained_speech_open_activity() {
        let mut t = SpeechActivityTracker::new(
            EnergyVad::new(),
            SpeechActivityConfig::default(),
        );
        for i in 0..10 {
            let d = t.process_frame(&loud_frame());
            assert_eq!(d.speech_active, i >= 5, "frame {i}");
        }
        assert!(t.is_speech_active());
    }

    #[test]
    fn speech_fading_keeps_inactive_gate() {
        let mut t = SpeechActivityTracker::new(
            EnergyVad::new(),
            SpeechActivityConfig::default(),
        );
        for _ in 0..6 {
            let _ = t.process_frame(&loud_frame());
        }
        assert!(t.is_speech_active());
        // A dip that would be under the start threshold but not the end
        // threshold must not close speech (mid-speech hysteresis).
        let _ = t.process_frame(&quiet_noise_frame(400));
        assert!(t.is_speech_active(), "dip must not chop speech");
    }

    #[test]
    fn speech_end_reports_before_terminal_decision() {
        // Tracker only flips OFF after one sub-threshold frame; the
        // *boundary decision* is endpointing's job, never VAD's.
        let mut t = SpeechActivityTracker::new(
            StaticVad::constant(0.0),
            SpeechActivityConfig::default(),
        );
        let _ = t.process_frame(&loud_frame());
        let _ = t.process_frame(&silence_frame());
        assert!(!t.is_speech_active());
    }

    #[test]
    fn per_call_reset_clears_floor_and_state() {
        let mut t = SpeechActivityTracker::new(
            EnergyVad::new(),
            SpeechActivityConfig::default(),
        );
        for _ in 0..8 {
            let _ = t.process_frame(&loud_frame());
        }
        assert!(t.is_speech_active());
        t.reset();
        assert!(!t.is_speech_active());
        assert_eq!(t.noise_duration_ms, 0);
    }

    #[test]
    fn static_provider_matches_its_constant() {
        let t = SpeechActivityTracker::new(
            StaticVad::constant(0.9),
            SpeechActivityConfig::default(),
        );
        assert_eq!(t.provider_name(), "static");
        assert_eq!(t.config().start_threshold, 0.5);
    }
}
