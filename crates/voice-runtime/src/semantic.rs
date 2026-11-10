//! Semantic turn detection.
//!
//! Acoustic endpointing alone cannot tell "I need an appointment for…" from
//! "I need an appointment for Monday." This layer evaluates partial and
//! final transcripts and classifies the utterance's stance.
//!
//! Design rules honored here:
//!
//! * The local fallback needs **no LLM request** and runs in microseconds,
//!   so semantic evaluation can never block the audio path.
//! * Provider-native semantic VAD (where a STT vendor offers it) drops in
//!   behind [`SemanticTurnEvaluator`]; the controller consumes the same
//!   verdict either way.
//! * Evaluation is accumulated evidence, never a command: the controller
//!   treats an evaluator verdict as a *cofactor* of the endpoint decision,
//!   and silence can still veto it, so mis-transcribed punctuation or a
//!   vendor outage degrades gracefully rather than corrupting turn state.

use crate::td_events::SemanticVerdict;

/// Everything an evaluator may consult about the open utterance.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SemanticContext {
    /// The partial transcript so far (full snapshot, same shape ASR gives).
    pub partial_transcript: String,
    /// Caller words seen so far (monotone count from the transcript).
    pub word_count: u32,
    /// Language routed for this call, when LID routed it (`en-US`…).
    /// Passing it lets language-aware profiles bias confidence; it must
    /// **not** change the verdict alone — a language switch mid-utterance is
    /// never by itself evidence that the turn ended.
    pub language: Option<String>,
    /// Wall clock the open turn has been speaking for.
    pub speech_ms: u64,
}

/// An evaluator's verdict plus provenance.
#[derive(Debug, Clone, PartialEq)]
pub struct SemanticEvaluation {
    pub verdict: SemanticVerdict,
    pub confidence: f32,
    /// Which rule/source produced it (heuristic rule name, provider id…).
    pub source: String,
    /// Snippet that triggered the verdict (for events / audit).
    pub evidence_snippet: String,
}

/// Provider-independent semantic turn evaluation.
pub trait SemanticTurnEvaluator {
    fn evaluate(&self, context: &SemanticContext) -> SemanticEvaluation;

    /// Outages must degrade, not block: `false` here means the evaluator is
    /// unavailable and the controller falls back to acoustic-only
    /// endpointing (the pipeline stays functional).
    fn available(&self) -> bool {
        true
    }

    fn name(&self) -> &'static str;
}

/// Trailing words that mark an unfinished thought. Tail matching only, so
/// "and then I booked it" (a completed sentence) is not flagged while
/// "…and then" is.
const TRAILING_INCOMPLETE: &[&str] = &[
    "for", "to", "and", "or", "of", "on", "in", "with", "but", "the", "a", "an", "then",
    "is", "was", "that", "so", "because", "which", "via", "before", "after",
];

const QUESTION_LEADS: &[&str] = &[
    "what", "when", "where", "who", "why", "how", "which", "can", "could", "do", "does",
    "did", "is", "are", "will", "would", "should", "may", "might",
];

const CORRECTION_CUES: &[&str] = &[
    "actually", "make that", "i mean", "wait no", "no wait", "correction", "scratch that",
    "on second thought", "change of plans", "never mind",
];

const PURE_FILLERS: &[&str] = &["um", "uh", "hmm", "er", "ah", "hm", "mm", "huh", "eh"];

/// Local, dependency-free semantic fallback. Reads the tail of the
/// utterance; does not *require* punctuation but honors it when present.
#[derive(Debug, Clone, Copy, Default)]
pub struct HeuristicSemanticEvaluator;

impl HeuristicSemanticEvaluator {
    pub fn new() -> Self {
        Self
    }

    fn first_word(text: &str) -> Option<String> {
        text.split_whitespace()
            .next()
            .map(|w| w.trim_matches(|c: char| !c.is_ascii_alphanumeric()).to_ascii_lowercase())
    }

    fn tail_words(text: &str, count: usize) -> String {
        let all: Vec<&str> = text.split_whitespace().collect();
        let start = all.len().saturating_sub(count);
        all[start..].join(" ").to_ascii_lowercase()
    }

    fn matches_tail(lower_full: &str, needles: &[&str]) -> Option<String> {
        let words: Vec<&str> = lower_full.split_whitespace().collect();
        for needle in needles {
            let needle_words: Vec<&str> = needle.split(' ').collect();
            if words.ends_with(needle_words.as_slice()) {
                return Some(needle.to_string());
            }
        }
        None
    }

    fn correction_hit(lower_full: &str) -> Option<String> {
        CORRECTION_CUES
            .iter()
            .find(|cue| lower_full.contains(*cue))
            .copied()
            .map(str::to_string)
    }

    fn is_pure_filler(lower_full: &str) -> bool {
        !lower_full.is_empty()
            && lower_full.split_whitespace().all(|w| {
                PURE_FILLERS.contains(&w.trim_end_matches(['.', ',']).to_ascii_lowercase().as_str())
            })
    }

    fn question_evaluation(snippet: String, confidence: f32) -> SemanticEvaluation {
        SemanticEvaluation {
            verdict: SemanticVerdict::Question,
            confidence,
            source: "heuristic".into(),
            evidence_snippet: snippet,
        }
    }
}

impl SemanticTurnEvaluator for HeuristicSemanticEvaluator {
    fn evaluate(&self, context: &SemanticContext) -> SemanticEvaluation {
        let text = context.partial_transcript.trim().to_string();
        let words = if context.word_count > 0 {
            context.word_count
        } else {
            u32::try_from(text.split_whitespace().count()).unwrap_or(0)
        };
        if text.is_empty() || words == 0 || !text.chars().any(char::is_alphanumeric) {
            return SemanticEvaluation {
                verdict: SemanticVerdict::Filler,
                confidence: 0.9,
                source: "heuristic".into(),
                evidence_snippet: String::new(),
            };
        }
        let lower = text.to_lowercase();

        // 1. Correction cues restate earlier content: evidence is stale.
        if let Some(cue) = Self::correction_hit(&lower) {
            return SemanticEvaluation {
                verdict: SemanticVerdict::Correction,
                confidence: 0.85,
                source: "heuristic".into(),
                evidence_snippet: cue,
            };
        }

        let incomplete_tail = Self::matches_tail(&lower, TRAILING_INCOMPLETE);

        // 2. Unfinished thought: trailing connective/preposition. Worthless
        //    unless the speaker said something more than the connective.
        if let Some(ref needle) = incomplete_tail {
            if words > 1 {
                return SemanticEvaluation {
                    verdict: SemanticVerdict::Incomplete,
                    confidence: 0.85,
                    source: "heuristic".into(),
                    evidence_snippet: needle.clone(),
                };
            }
        }

        // 3. Questions: explicit mark is strong; a question-lead utterance
        //    whose phrasing finished naturally is also answerable.
        if text.ends_with('?') {
            return Self::question_evaluation("?".into(), 0.9);
        }
        if incomplete_tail.is_none() {
            if let Some(first) = Self::first_word(&text) {
                if QUESTION_LEADS.contains(&first.as_str()) && words >= 3 {
                    return Self::question_evaluation(
                        Self::tail_words(&text, 3),
                        0.75,
                    );
                }
            }
        }

        // 4. Pure fillers: no turn yet.
        if Self::is_pure_filler(&lower) {
            return SemanticEvaluation {
                verdict: SemanticVerdict::Filler,
                confidence: 0.8,
                source: "heuristic".into(),
                evidence_snippet: Self::tail_words(&text, 3),
            };
        }

        // 5. Default: utterance ends naturally. Substance boosts
        //    confidence; very short utterances are discounted (a two-word
        //    "complete" is more likely a backchannel or a false end).
        let complete_confidence = if words >= 4 { 0.7 } else { 0.45 };
        SemanticEvaluation {
            verdict: SemanticVerdict::Complete,
            confidence: complete_confidence,
            source: "heuristic".into(),
            evidence_snippet: Self::tail_words(&text, 6),
        }
    }

    fn name(&self) -> &'static str {
        "heuristic"
    }
}

/// Provider-disabled evaluator: models a semantic outage honestly so the
/// controller's degraded path is exercised in tests.
#[derive(Debug, Clone, Copy, Default)]
pub struct UnavailableSemanticEvaluator;

impl SemanticTurnEvaluator for UnavailableSemanticEvaluator {
    fn evaluate(&self, _context: &SemanticContext) -> SemanticEvaluation {
        SemanticEvaluation {
            verdict: SemanticVerdict::Complete,
            confidence: 0.0,
            source: "unavailable".into(),
            evidence_snippet: String::new(),
        }
    }

    fn available(&self) -> bool {
        false
    }

    fn name(&self) -> &'static str {
        "unavailable"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn eval(text: &str) -> SemanticEvaluation {
        HeuristicSemanticEvaluator.evaluate(&SemanticContext {
            partial_transcript: text.to_string(),
            ..Default::default()
        })
    }

    #[test]
    fn book_appointment_for_is_incomplete() {
        let e = eval("I need to book an appointment for");
        assert_eq!(e.verdict, SemanticVerdict::Incomplete);
        assert_eq!(e.evidence_snippet, "for");
    }

    #[test]
    fn book_appointment_for_monday_is_complete() {
        let e = eval("I need to book an appointment for Monday");
        assert_eq!(e.verdict, SemanticVerdict::Complete);
    }

    #[test]
    fn trailing_then_is_incomplete_trailing_then_i_booked_is_not() {
        assert_eq!(eval("so we were at the station and then").verdict, SemanticVerdict::Incomplete);
        assert_eq!(eval("and then I booked it").verdict, SemanticVerdict::Complete);
    }

    #[test]
    fn punctuated_question_is_classified_as_answerable() {
        let e = eval("Can you tell me the price?");
        assert_eq!(e.verdict, SemanticVerdict::Question);
        assert_eq!(e.source, "heuristic");
    }

    #[test]
    fn spoken_question_without_punctuation_is_answerable() {
        let e = eval("can you tell me the price");
        assert_eq!(e.verdict, SemanticVerdict::Question);
        assert_eq!(e.evidence_snippet, "me the price");
    }

    #[test]
    fn correction_cues_mark_stale_content() {
        assert_eq!(eval("Monday actually make that Tuesday").verdict, SemanticVerdict::Correction);
        assert_eq!(eval("no wait I said Tuesday").verdict, SemanticVerdict::Correction);
    }

    #[test]
    fn pure_fillers_are_not_a_turn() {
        assert_eq!(eval("um uh hmm").verdict, SemanticVerdict::Filler);
        assert_eq!(eval("um").verdict, SemanticVerdict::Filler);
    }

    #[test]
    fn empty_transcript_is_filler_not_complete() {
        assert_eq!(eval("").verdict, SemanticVerdict::Filler);
        assert_eq!(eval("   ").verdict, SemanticVerdict::Filler);
    }

    #[test]
    fn a_bare_question_mark_is_not_substance() {
        // Words==0 despite the punctuation: filler, never a complete turn.
        assert_eq!(eval("?").verdict, SemanticVerdict::Filler);
    }

    #[test]
    fn short_complete_utterance_is_discounted_but_complete() {
        let e = eval("yes please");
        assert_eq!(e.verdict, SemanticVerdict::Complete);
        assert!(e.confidence <= 0.5, "{}", e.confidence);
    }

    #[test]
    fn substantive_complete_utterance_is_confident() {
        let e = eval("I would like to book a table for four people");
        assert_eq!(e.verdict, SemanticVerdict::Complete);
        assert!(e.confidence >= 0.7);
    }

    #[test]
    fn language_is_passenger_not_decider() {
        // The same utterance evaluates the same in any routed language;
        // LID is recorded, never a turn signal on its own.
        let en = eval("I need an appointment");
        let multi = HeuristicSemanticEvaluator.evaluate(&SemanticContext {
            partial_transcript: "I need an appointment".into(),
            language: Some("ml-IN".into()),
            ..Default::default()
        });
        assert_eq!(en.verdict, multi.verdict);
    }

    #[test]
    fn unavailable_evaluator_reports_outage_honestly() {
        let evaluator = UnavailableSemanticEvaluator;
        let e = evaluator.evaluate(&SemanticContext {
            partial_transcript: "I need an appointment".into(),
            ..Default::default()
        });
        assert!(!evaluator.available());
        assert_eq!(e.source, "unavailable");
        assert_eq!(e.confidence, 0.0);
    }

    #[test]
    fn evaluator_identity_is_stable() {
        assert_eq!(HeuristicSemanticEvaluator.name(), "heuristic");
    }
}
