//! Adaptive endpointing.
//!
//! Endpointing decides when a caller's silence actually means the utterance
//! is over. Fixed timers cannot do this honestly: a 400 ms gap may be a
//! breath inside a sentence for a fast talker and a completed turn for a
//! deliberate one. This engine treats the silence threshold as a function
//! of evidence:
//!
//! * **Profile** — the configured conversational pace (`fast` 350–500 ms,
//!   `normal` 600–900 ms, `deliberate` 1–1.5 s) sets the *initial* silence
//!   window.
//! * **Speech-rate adaptation** — observed speech tempo (words over wall
//!   time) scales the window: slow speakers get longer windows.
//! * **Continuation detection** — if speech resumes inside the window, the
//!   candidate is withdrawn and a subsequent gap starts its window again
//!   (a pause inside a turn is not the end of the turn).
//! * **Confidence scoring** — a boundary gets a 0..=1 score from how much
//!   silence elapsed relative to the profile window and whether semantic
//!   evidence agrees.
//! * **Timeout fallback** — `max_wait_ms` guarantees that a caller who
//!   trails off still produces a decision; the controller reports it as a
//!   timeout rather than an ordinary boundary.

use std::time::Duration;

/// Named endpointing profile with per-profile silence bounds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EndpointProfile {
    /// 350–500 ms.
    Fast,
    /// 600–900 ms.
    Normal,
    /// 1,000–1,500 ms.
    Deliberate,
}

/// Default profile: ordinary conversation pace.
impl Default for EndpointProfile {
    fn default() -> Self {
        Self::Normal
    }
}

impl EndpointProfile {
    pub fn min_silence_ms(&self) -> u64 {
        match self {
            Self::Fast => 350,
            Self::Normal => 600,
            Self::Deliberate => 1_000,
        }
    }

    pub fn max_silence_ms(&self) -> u64 {
        match self {
            Self::Fast => 500,
            Self::Normal => 900,
            Self::Deliberate => 1_500,
        }
    }

    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "fast" => Some(Self::Fast),
            "normal" => Some(Self::Normal),
            "deliberate" => Some(Self::Deliberate),
            _ => None,
        }
    }
}

/// What endpointing says about the current observation instant.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum EndpointDecision {
    /// Speech is open; no boundary yet.
    Speaking,
    /// Silence duration reached candidate territory.
    Candidate { silence_ms: u64, confidence: f32, profile_ms: u64, timed_out: bool },
    /// Speech resumed inside the candidate window: boundary withdrawn.
    Continued { silence_ms: u64 },
}

#[derive(Debug, Clone, Default)]
pub struct EndpointConfig {
    /// Profile in effect for this call.
    pub profile: EndpointProfile,
    /// Hard ceiling: decide anyway (reported as timeout) after this much
    /// trailing silence.
    pub max_wait_ms: u64,
    /// Confidence floor that turns a candidate into a confirmed boundary.
    pub confirm_confidence: f32,
    /// Speech rate (wpm) benchmarking point for the adaptive scale.
    pub reference_wpm: f32,
    /// Extra silence granted to the window before candidates fire. Set by
    /// the controller when a language switch occurred inside the open
    /// utterance: code-switching is continuation evidence, not a boundary.
    pub grace_ms: u64,
}

impl EndpointConfig {
    pub fn from_profile(profile: EndpointProfile) -> Self {
        Self {
            profile,
            max_wait_ms: profile.max_silence_ms() * 3,
            confirm_confidence: 0.5,
            reference_wpm: 140.0,
            grace_ms: 0,
        }
    }
}

/// Evidence endpointing uses per utterance, kept deliberately minimal so it
/// is portable to any transport.
#[derive(Debug, Clone, Default)]
pub struct SpeechRateEvidence {
    /// Words counted from streaming partial transcripts for the open turn.
    pub words: u32,
    /// Wall clock the speech was open for (silence excluded).
    pub speech_ms: u64,
}

impl SpeechRateEvidence {
    /// Observed words-per-minute; `None` when there is not enough speech
    /// yet (a caller may still be in their very first breath).
    pub fn words_per_minute(&self) -> Option<f32> {
        if self.words == 0 || self.speech_ms < 500 {
            return None;
        }
        Some(self.words as f32 * 60_000.0 / self.speech_ms as f32)
    }
}

/// Adaptive endpointing engine for one open turn. The controller feeds it
/// speech/silence transitions and transcript evidence; it answers boundary
/// questions for the current instant.
#[derive(Debug)]
pub struct AdaptiveEndpointer {
    config: EndpointConfig,
    /// Did a candidate window open (we are inside trailing silence)?
    candidate_open: bool,
    /// Silence accumulated since speech last went quiet.
    silence_ms: u64,
    /// Total speech duration of the open turn.
    speech_ms: u64,
    /// Whether speech ever resumed after a candidate opened (continuation).
    continuations: u32,
    speech_rate: SpeechRateEvidence,
}

impl AdaptiveEndpointer {
    pub fn new(config: EndpointConfig) -> Self {
        Self {
            config,
            candidate_open: false,
            silence_ms: 0,
            speech_ms: 0,
            continuations: 0,
            speech_rate: SpeechRateEvidence::default(),
        }
    }

    pub fn config(&self) -> &EndpointConfig {
        &self.config
    }

    /// Mutable configuration access used by the controller to apply
    /// code-switch grace extensions mid-utterance.
    pub fn config_mut(&mut self) -> &mut EndpointConfig {
        &mut self.config
    }

    pub fn speech_rate(&self) -> &SpeechRateEvidence {
        &self.speech_rate
    }

    /// Total speech-activity time observed on the open turn (per-turn,
    /// zeroed on reset).
    pub fn speech_evidence_ms(&self) -> u64 {
        self.speech_ms
    }

    pub fn continuations(&self) -> u32 {
        self.continuations
    }

    /// Arm for a new turn: zero all evidence.
    pub fn reset(&mut self) {
        *self = AdaptiveEndpointer::new(self.config.clone());
    }

    /// Report one transcript snapshot for the open turn.
    pub fn note_transcript(&mut self, word_count: u32, speech_ms: u64) {
        self.speech_rate.words = self.speech_rate.words.max(word_count);
        self.speech_rate.speech_ms = speech_ms.max(self.speech_rate.speech_ms);
    }

    /// Effective silence window after speech-rate adaptation.
    ///
    /// Slow speakers (below reference wpm) stretch the profile's midpoint
    /// window up to the profile maximum; fast speakers tighten it down to
    /// 70% of the profile minimum. Everything is clamped so adaptation can
    /// never produce absurd timings.
    pub fn effective_silence_ms(&self) -> u64 {
        let profile = self.config.profile;
        let profile_ms = profile.min_silence_ms();
        let adaptive = match self.speech_rate.words_per_minute() {
            None => profile_ms,
            Some(wpm) if wpm >= self.config.reference_wpm => {
                let fast = profile_ms as f32
                    * (self.config.reference_wpm / wpm).clamp(0.6, 1.0);
                fast.round() as u64
            }
            Some(wpm) => {
                let slow = profile_ms as f32
                    * (self.config.reference_wpm / wpm).clamp(1.0, 2.2);
                slow.round() as u64
            }
        };
        (adaptive + self.config.grace_ms)
            .clamp(profile.min_silence_ms() * 7 / 10, profile.max_silence_ms() + self.config.grace_ms)
    }

    /// Feed one instant of time. `speech_active` is the VAD verdict for the
    /// frame ending at `frame_ms`-granularity time; call this once per frame.
    pub fn observe_frame(&mut self, speech_active: bool, frame_ms: u64) -> EndpointDecision {
        if speech_active {
            self.speech_ms += frame_ms;
            if self.candidate_open {
                self.candidate_open = false;
                self.continuations += 1;
                let silence = self.silence_ms;
                self.silence_ms = 0;
                return EndpointDecision::Continued { silence_ms: silence };
            }
            self.silence_ms = 0;
            return EndpointDecision::Speaking;
        }
        self.silence_ms += frame_ms;
        let effective = self.effective_silence_ms();
        if self.silence_ms >= self.config.max_wait_ms {
            let confidence = self.timeout_confidence();
            self.candidate_open = true;
            return EndpointDecision::Candidate {
                silence_ms: self.silence_ms,
                confidence,
                profile_ms: effective,
                timed_out: true,
            };
        }
        if self.silence_ms >= effective {
            let confidence = self.confidence(effective);
            self.candidate_open = true;
            return EndpointDecision::Candidate {
                silence_ms: self.silence_ms,
                confidence,
                profile_ms: effective,
                timed_out: false,
            };
        }
        EndpointDecision::Speaking
    }

    /// Distance-based confidence: silence well past the effective window
    /// scores high; silence exactly at the boundary scores around confirm
    /// threshold. Short speech inside the turn discounts confidence, since
    /// a two-word utterance is more likely a backchannel.
    pub fn confidence(&self, effective_ms: u64) -> f32 {
        let overshoot = self.silence_ms as f32 / effective_ms.max(1) as f32;
        let base = (overshoot / 2.0).clamp(0.0, 1.0);
        let speech_discount = if self.speech_ms < 400 { 0.85 } else { 1.0 };
        (base * speech_discount).clamp(0.0, 1.0)
    }

    /// Timeout confidence is honest but capped below certainty: candidate
    /// decisions past `max_wait_ms` are stale candidates, still safer than
    /// hanging.
    fn timeout_confidence(&self) -> f32 {
        self.confidence(self.effective_silence_ms()).min(0.4)
    }
}

/// Classify an observed silence gap for diagnostics: not a turn decision—
/// only a label for what the gap most plausibly was.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PauseKind {
    /// Short conversational pause (well under the profile window).
    ShortPause,
    /// Breathing pause (roughly under half the profile window).
    Breathing,
    /// Sentence-level pause: silence about equal to the profile window.
    SentencePause,
    /// Long thinking pause: over the window but the caller resumed.
    Thinking,
    /// Turn-complete silence (confirmed boundary).
    Completed,
}

impl PauseKind {
    pub fn classify(silence: Duration, profile_ms: u64) -> Self {
        let ms = silence.as_millis() as u64;
        if ms < profile_ms / 3 {
            Self::ShortPause
        } else if ms < profile_ms / 2 {
            Self::Breathing
        } else if ms < profile_ms * 3 / 2 {
            Self::SentencePause
        } else {
            Self::Thinking
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ep(profile: EndpointProfile) -> AdaptiveEndpointer {
        AdaptiveEndpointer::new(EndpointConfig::from_profile(profile))
    }

    fn speak(mut e: AdaptiveEndpointer, ms: u64) -> AdaptiveEndpointer {
        let mut t = 0;
        while t < ms {
            assert_eq!(e.observe_frame(true, 20), EndpointDecision::Speaking);
            t += 20;
        }
        e
    }

    #[test]
    fn profile_bounds_are_configurable_and_parsed() {
        assert_eq!(EndpointProfile::Fast.min_silence_ms(), 350);
        assert_eq!(EndpointProfile::Fast.max_silence_ms(), 500);
        assert_eq!(EndpointProfile::Normal.min_silence_ms(), 600);
        assert_eq!(EndpointProfile::Normal.max_silence_ms(), 900);
        assert_eq!(EndpointProfile::Deliberate.min_silence_ms(), 1000);
        assert_eq!(EndpointProfile::Deliberate.max_silence_ms(), 1500);
        assert_eq!(EndpointProfile::parse("fast"), Some(EndpointProfile::Fast));
        assert_eq!(EndpointProfile::parse("lobster"), None);
    }

    #[test]
    fn short_pause_is_not_endpoint() {
        let mut e = ep(EndpointProfile::Normal);
        e = speak(e, 2000);
        for _ in 0..10 {
            assert_eq!(e.observe_frame(false, 20), EndpointDecision::Speaking);
        }
    }

    #[test]
    fn long_silence_in_normal_profile_becomes_candidate() {
        let mut e = ep(EndpointProfile::Normal);
        e = speak(e, 2000);
        let mut decision = EndpointDecision::Speaking;
        for _ in 0..45 {
            let d = e.observe_frame(false, 20);
            decision = d;
        }
        assert!(matches!(
            decision,
            EndpointDecision::Candidate { silence_ms: 900, timed_out: false, .. }
        ));
    }

    #[test]
    fn continuation_withdraws_candidate_and_does_not_close_the_turn() {
        let mut e = ep(EndpointProfile::Fast);
        e = speak(e, 1000);
        // Silence until the fast window produces a candidate.
        let mut candidate_at_ms = None;
        for _ in 0..25 {
            if let EndpointDecision::Candidate { silence_ms, .. } = e.observe_frame(false, 20) {
                candidate_at_ms = Some(silence_ms);
                break;
            }
        }
        let silence_ms_in_window = candidate_at_ms.expect("fast silence must reach the window");
        assert!(
            (350..=500).contains(&silence_ms_in_window),
            "candidate at {silence_ms_in_window}"
        );
        // Caller resumes mid-thought.
        let resumed = e.observe_frame(true, 20);
        assert!(
            matches!(
                resumed,
                EndpointDecision::Continued { silence_ms } if (350..=500).contains(&silence_ms)
            ),
            "{resumed:?}"
        );
        assert!(e.continuations() >= 1);
        // The turn is still open: a fresh silence window must start from 0.
        for _ in 0..10 {
            assert_eq!(e.observe_frame(false, 20), EndpointDecision::Speaking);
        }
    }

    #[test]
    fn speech_rate_stretches_window_for_slow_speakers() {
        let mut e = ep(EndpointProfile::Normal);
        // 60 wpm: half the reference pace, window must grow beyond the
        // static minimum but stay clamped inside the profile band.
        e.note_transcript(10, 10 * 60_000);
        let stretched = e.effective_silence_ms();
        let floor = EndpointProfile::Normal.min_silence_ms();
        assert!(stretched > floor, "slow speaker window {stretched} > {floor}");
        assert!(stretched <= EndpointProfile::Normal.max_silence_ms());
    }

    #[test]
    fn speech_rate_tightens_window_for_fast_speakers() {
        let mut e = ep(EndpointProfile::Normal);
        e.note_transcript(30, 500);
        let tight = e.effective_silence_ms();
        let floor = EndpointProfile::Normal.min_silence_ms() * 7 / 10;
        assert!(tight < EndpointProfile::Normal.min_silence_ms());
        assert!(tight >= floor, "tight window {tight} >= {floor}");
    }

    #[test]
    fn confidence_upgrades_with_overshoot() {
        let mut e = ep(EndpointProfile::Normal);
        e = speak(e, 1500);
        let mut first: Option<f32> = None;
        loop {
            match e.observe_frame(false, 20) {
                EndpointDecision::Candidate { confidence, .. } => {
                    first.get_or_insert(confidence);
                    break;
                }
                _ => continue,
            }
        }
        let at_boundary = first.unwrap();
        // Continue another second of silence; overshoot model must score
        // higher than the moment the window opened.
        let mut later: f32 = 0.0;
        for _ in 0..50 {
            if let EndpointDecision::Candidate { confidence, .. } = e.observe_frame(false, 20) {
                later = later.max(confidence);
            }
        }
        assert!(later > at_boundary, "later {later} > boundary {at_boundary}");
    }

    #[test]
    fn timeout_fallback_decides_before_hanging() {
        let profile = EndpointProfile::Normal;
        let mut e = AdaptiveEndpointer::new(EndpointConfig::from_profile(profile));
        e = speak(e, 2000);
        // Silence past max_wait (3x the profile max) must fire the timeout
        // fallback, flagged distinguishably from an ordinary candidate.
        let mut timed_out_candidate = None;
        for _ in 0..60 {
            if let EndpointDecision::Candidate { timed_out: true, confidence, .. } =
                e.observe_frame(false, 100)
            {
                timed_out_candidate = Some(confidence);
                break;
            }
        }
        let confidence = timed_out_candidate.expect("timeout fallback must fire");
        assert!(confidence <= 0.4, "timeout candidates capped: {confidence}");
    }

    #[test]
    fn pause_classes_distinguish_gap_kinds() {
        let profile = EndpointProfile::Normal;
        let window = profile.min_silence_ms();
        assert_eq!(PauseKind::classify(Duration::from_millis(150), profile.min_silence_ms()), PauseKind::ShortPause);
        assert_eq!(PauseKind::classify(Duration::from_millis(250), profile.min_silence_ms()), PauseKind::Breathing);
        assert_eq!(PauseKind::classify(Duration::from_millis(window), profile.min_silence_ms()), PauseKind::SentencePause);
        assert_eq!(PauseKind::classify(Duration::from_millis(window * 3), profile.min_silence_ms()), PauseKind::Thinking);
    }
}
