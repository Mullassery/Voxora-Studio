//! Barge-in decision functions (roadmap M6/P4 + P5).
//!
//! Pure, unit-testable gates that sit in front of the [`crate::turn`]
//! state machine's barge-in in-guards. The state machine keeps ownership of
//! conversation state; these gates decide whether a raw audio/trascript
//! signal is *worthy* of becoming a `UserSpeechStart` signal at all.
//!
//! * [`BargeInGate`] — echo-safe energy gate (P4): an above-threshold frame
//!   only counts when it cannot be playback echo (echo cooldown window after
//!   the last agent audio frame, minimum elapsed TTS time) and the caller
//!   needs N consecutive qualifying frames.
//! * [`WordInterruptGate`] — transcribed-word gate (P5): interruption is
//!   confirmed only after N transcribed words from STT, so door slams and
//!   music never kill a question. Verified competitor value N=3.
//! * [`InterruptClassifier`] — turns a confirmed interruption candidate
//!   into [`InterruptionKind`]: sustained non-backchannel speech is
//!   genuine; "yes"/"okay"-style acknowledgements are backchannels and must
//!   not terminate agent playback.

use crate::td_events::InterruptionKind;
use std::time::Duration;

/// Configuration for the echo-safe barge-in energy gate (P4).
#[derive(Debug, Clone)]
pub struct BargeInConfig {
    /// RMS threshold (linear scale, 0..=1 after μ-law decode). Frames below
    /// this are noise and reset the consecutive-frame counter.
    pub rms_threshold: f32,
    /// Consecutive above-threshold frames required (≥3 frames: a single
    /// transient must never interrupt).
    pub min_frames: u32,
    /// After agent audio was last seen, user energy this young is presumed
    /// playback echo and ignored.
    pub echo_cooldown: Duration,
    /// Agent playback younger than this is never interruptible (echo of the
    /// user's own channel right after the agent starts).
    pub min_tts_elapsed: Duration,
}

impl Default for BargeInConfig {
    fn default() -> Self {
        Self {
            rms_threshold: 0.05,
            min_frames: 3,
            echo_cooldown: Duration::from_millis(200),
            min_tts_elapsed: Duration::from_millis(500),
        }
    }
}

/// Evidence accompanying one audio frame handed to [`BargeInGate::evaluate`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FrameEvidence {
    /// Frame RMS on the user channel (linear 0..=1).
    pub rms: f32,
    /// Whether agent TTS audio is currently playing.
    pub agent_speaking: bool,
    /// Elapsed playback time of the agent's current utterance.
    pub tts_elapsed: Duration,
    /// Time since the last agent audio frame arrived on any channel.
    pub since_agent_audio: Duration,
}

/// Echo-safe barge-in energy gate (P4). Pure: holds only a counter.
#[derive(Debug)]
pub struct BargeInGate {
    config: BargeInConfig,
    consecutive_frames: u32,
}

impl BargeInGate {
    pub fn new(config: BargeInConfig) -> Self {
        Self { config, consecutive_frames: 0 }
    }

    pub fn config(&self) -> &BargeInConfig {
        &self.config
    }

    /// Evaluate one frame. Returns `true` exactly when the frame is the Nth
    /// consecutive qualifying frame; that is the moment to emit
    /// `Signal::UserSpeechStart` to the turn machine.
    pub fn evaluate(&mut self, frame: FrameEvidence) -> bool {
        let qualifies = frame.rms >= self.config.rms_threshold
            && !(frame.agent_speaking && frame.since_agent_audio < self.config.echo_cooldown)
            && (!frame.agent_speaking || frame.tts_elapsed >= self.config.min_tts_elapsed);
        if !qualifies {
            self.consecutive_frames = 0;
            return false;
        }
        self.consecutive_frames += 1;
        self.consecutive_frames >= self.config.min_frames
    }
}

/// Transcribed-word interruption gate (P5).
#[derive(Debug)]
pub struct WordInterruptGate {
    min_words: u32,
    words_seen: u32,
}

impl WordInterruptGate {
    /// `min_words` is the verified competitor value N=3 by default.
    pub fn new(min_words: u32) -> Self {
        assert!(min_words >= 1, "word gate requires at least one word");
        Self { min_words, words_seen: 0 }
    }

    /// Feed one partial STT transcript. Partial takes the shape of snapshots
    /// from streaming ASR: each call carries the full transcript so far, not a
    /// delta, so re-feeding a shorter or repeated partial can never regress or
    /// double-count (`words_seen` is monotone). Returns `true` exactly on the
    /// call that first crosses `min_words` words. Frames-only noise never
    /// reaches this gate, so it can never interrupt.
    pub fn on_partial_transcript(&mut self, text: &str) -> bool {
        let previous = self.words_seen;
        self.words_seen = self.words_seen.max(text.split_whitespace().count() as u32);
        self.words_seen >= self.min_words && previous < self.min_words
    }

    /// Words accumulated since the gate was armed.
    pub fn words_seen(&self) -> u32 {
        self.words_seen
    }
}

/// Classifies a confirmed interruption candidate by what the caller said.
///
/// Acknowledgements ("yes", "yeah", "okay", "hmm", "right"…) and their
/// common prefixes must not terminate agent speech — they carry no new
/// instruction. Sustained speech that does not resolve to a backchannel is
/// genuine; evidence that matches neither is ambiguous and the candidate
/// stays open while more transcript arrives (never falsely cancelled).
#[derive(Debug, Clone)]
pub struct InterruptClassifier {
    /// Case-insensitive backchannel lexicon (normalized, lowercase).
    backchannels: Vec<String>,
    /// Prefixes that a backchannel may start with ("yes and then" IS new).
    genuine_break: Vec<String>,
}

const DEFAULT_BACKCHANNELS: &[&str] = &[
    "yes", "yeah", "yep", "no", "nope", "okay", "ok", "alright", "hmm", "mm",
    "right", "sure", "fine", "got it", "understood", "uh-huh", "mhm", "oh",
];

impl InterruptClassifier {
    pub fn new(backchannels: Vec<String>) -> Self {
        let mut list = backchannels
            .into_iter()
            .map(|w| w.trim().to_lowercase())
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>();
        if list.is_empty() {
            list = DEFAULT_BACKCHANNELS
                .iter()
                .map(|w| w.to_string())
                .collect();
        }
        Self {
            backchannels: list,
            genuine_break: ["no", "wait", "actually", "stop", "hold on"]
                .iter()
                .map(|w| w.to_string())
                .collect(),
        }
    }

    pub fn default_lexicon() -> Self {
        Self::new(Vec::new())
    }

    pub fn lexicon(&self) -> &[String] {
        &self.backchannels
    }

    /// Classify the cumulative partial transcript snapshot the caller is
    /// producing during agent playback.
    pub fn classify(&self, text: &str) -> InterruptionKind {
        let normalized = text
            .split_whitespace()
            .map(|w| w.trim_matches(|c: char| !c.is_ascii_alphanumeric() && c != '-').to_lowercase())
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>();
        if normalized.is_empty() {
            // No words yet: energy-only evidence stays ambiguous rather than
            // fabricating an interruption kind.
            return InterruptionKind::Ambiguous;
        }
        let words = normalized.len() as u32;
        let first = normalized[0].as_str();
        // Break words reframe the utterance: "no, I said Tuesday" is
        // genuine even though "no" is also a backchannel token.
        if self.genuine_break.contains(&first.to_string()) {
            return InterruptionKind::Genuine;
        }
        if words == 1 {
            for channel in &self.backchannels {
                if channel.matches(' ').count() == 0 && channel == first {
                    return InterruptionKind::Backchannel;
                }
            }
            return InterruptionKind::Ambiguous;
        }
        // Multi-word candidates: an utterance fully covered by
        // acknowledgement entries ("yes yeah okay", "got it right") is a
        // backchannel — acknowledging callers never cancel playback. Any
        // token outside the lexicon (or a reframe prefix) makes it real
        // speech.
        if self.is_ack_covered(&normalized) {
            return InterruptionKind::Backchannel;
        }
        InterruptionKind::Genuine
    }

    /// Greedy left-to-right coverage check: every token position must be
    /// consumed either by a single-word lexicon entry or by a multi-word
    /// phrase entry ("got it") matched against consecutive tokens.
    fn is_ack_covered(&self, tokens: &[String]) -> bool {
        let mut i = 0usize;
        while i < tokens.len() {
            let mut matched = false;
            'phrase: for phrase in &self.backchannels {
                if !phrase.contains(' ') {
                    continue;
                }
                let toks: Vec<&str> = phrase.split(' ').collect();
                if i + toks.len() <= tokens.len()
                    && toks.iter().zip(&tokens[i..]).all(|(p, t)| *p == t.as_str())
                {
                    i += toks.len();
                    matched = true;
                    break 'phrase;
                }
            }
            if !matched {
                if self
                    .backchannels
                    .iter()
                    .any(|c| c.matches(' ').count() == 0 && *c == tokens[i])
                {
                    i += 1;
                } else {
                    return false;
                }
            }
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::td_events::InterruptionKind;

    fn evidence(rms: f32, agent_speaking: bool, tts_elapsed_ms: u64, since_agent_ms: u64) -> FrameEvidence {
        FrameEvidence {
            rms,
            agent_speaking,
            tts_elapsed: Duration::from_millis(tts_elapsed_ms),
            since_agent_audio: Duration::from_millis(since_agent_ms),
        }
    }

    #[test]
    fn playback_echo_cannot_self_trigger() {
        // Table: echo-shaped frames (loud, agent playing, agent audio just
        // seen or TTS just started) must never accumulate toward barge-in.
        let cases = [
            ("echo of agent audio", evidence(0.6, true, 2_000, 0)),
            ("echo within cooldown", evidence(0.6, true, 2_000, 150)),
            ("tts just started", evidence(0.6, true, 100, 10_000)),
        ];
        for (name, frame) in cases {
            let mut gate = BargeInGate::new(BargeInConfig::default());
            for i in 0..50 {
                assert!(!gate.evaluate(frame), "case {}: frame {} triggered", name, i);
            }
        }
    }

    #[test]
    fn candidate_speech_after_n_frames_triggers() {
        let mut gate = BargeInGate::new(BargeInConfig::default());
        // Quiet room first: below threshold resets.
        assert!(!gate.evaluate(evidence(0.01, false, 0, 10_000)));
        // Real user speech: agent audio long gone, TTS mature, loud frames.
        for i in 1..=3 {
            let fired = gate.evaluate(evidence(0.4, false, 0, 10_000));
            assert_eq!(fired, i == 3, "frame {i}");
        }
    }

    #[test]
    fn below_threshold_frame_resets_consecutive_count() {
        let mut gate = BargeInGate::new(BargeInConfig::default());
        assert!(!gate.evaluate(evidence(0.4, false, 0, 10_000)));
        assert!(!gate.evaluate(evidence(0.4, false, 0, 10_000)));
        // Door slam ends: one loud frame then silence — counter must reset.
        assert!(!gate.evaluate(evidence(0.01, false, 0, 10_000)));
        assert!(!gate.evaluate(evidence(0.4, false, 0, 10_000)));
        assert!(!gate.evaluate(evidence(0.4, false, 0, 10_000)));
        assert!(gate.evaluate(evidence(0.4, false, 0, 10_000))); // only 3rd after reset
    }

    #[test]
    fn mature_tts_with_distant_agent_audio_allows_barge_in() {
        // Agent still playing but audio long since buffered (network starved):
        // echo cooldown must not permanently block interruption.
        let mut gate = BargeInGate::new(BargeInConfig::default());
        assert!(!gate.evaluate(evidence(0.4, true, 2_000, 10_000)));
        assert!(!gate.evaluate(evidence(0.4, true, 2_000, 10_000)));
        assert!(gate.evaluate(evidence(0.4, true, 2_000, 10_000)));
    }

    #[test]
    fn word_gate_interrupts_only_after_n_words() {
        let mut gate = WordInterruptGate::new(3);
        assert!(!gate.on_partial_transcript("the"));
        assert!(!gate.on_partial_transcript("the answer"));
        // 3rd call: cumulative snapshot reaches N and fires exactly once.
        assert!(gate.on_partial_transcript("the answer is four"));
        assert!(!gate.on_partial_transcript("the answer is four"));
    }

    #[test]
    fn frames_only_noise_never_interrupts() {
        // The word gate only sees transcripts; no transcript, no interrupt.
        let mut gate = WordInterruptGate::new(3);
        assert!(!gate.on_partial_transcript(""));
        assert_eq!(gate.words_seen(), 0);
        assert!(!gate.on_partial_transcript("   "));
        assert_eq!(gate.words_seen(), 0);
    }

    #[test]
    fn word_gate_re_arms_per_utterance_by_reconstruction() {
        // Gates are cheap: a fresh gate per turn is the re-arm.
        let mut gate = WordInterruptGate::new(1);
        assert!(gate.on_partial_transcript("stop"));
        let mut gate = WordInterruptGate::new(3);
        assert!(!gate.on_partial_transcript("stop"));
    }

    #[test]
    fn acknowledgements_are_backchannels_playback_survives() {
        let c = InterruptClassifier::default_lexicon();
        assert_eq!(c.classify("yes"), InterruptionKind::Backchannel);
        assert_eq!(c.classify("Yeah"), InterruptionKind::Backchannel);
        assert_eq!(c.classify("okay!"), InterruptionKind::Backchannel);
        assert_eq!(c.classify("hmm"), InterruptionKind::Backchannel);
        assert_eq!(c.classify("got it"), InterruptionKind::Backchannel);
    }

    #[test]
    fn corrections_reframe_backchannel_tokens_as_genuine() {
        // "no" is in the lexicon, but "no, I said Tuesday!" is a real barge-in.
        let c = InterruptClassifier::default_lexicon();
        assert_eq!(c.classify("no I said Tuesday!"), InterruptionKind::Genuine);
        // And even bare "no" leading a correction cue is genuine.
        assert_eq!(c.classify("no wait"), InterruptionKind::Genuine);
    }

    #[test]
    fn extended_speech_is_genuine_even_when_flagged_as_positive() {
        let c = InterruptClassifier::default_lexicon();
        assert_eq!(c.classify("yes but I need Tuesday not Monday"), InterruptionKind::Genuine);
    }

    #[test]
    fn unknown_single_word_is_ambiguous_not_cancelled() {
        let c = InterruptClassifier::default_lexicon();
        assert_eq!(c.classify("Monday—"), InterruptionKind::Ambiguous);
    }

    #[test]
    fn wordless_evidence_stays_ambiguous() {
        let c = InterruptClassifier::default_lexicon();
        assert_eq!(c.classify(""), InterruptionKind::Ambiguous);
        assert_eq!(c.classify("!!!"), InterruptionKind::Ambiguous);
    }

    #[test]
    fn custom_lexicon_replaces_default() {
        let c = InterruptClassifier::new(vec!["ayyo".to_string(), "po po".to_string()]);
        assert_eq!(c.lexicon(), &["ayyo".to_string(), "po po".to_string()]);
        assert_eq!(c.classify("ayyo"), InterruptionKind::Backchannel);
        assert_eq!(c.classify("po po"), InterruptionKind::Backchannel);
        // Default tokens are gone when a custom lexicon is supplied.
        assert_eq!(c.classify("yes"), InterruptionKind::Ambiguous);
    }
}
