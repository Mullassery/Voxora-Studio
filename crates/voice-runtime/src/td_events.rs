//! Versioned turn-detection event contracts.
//!
//! Every event one well-defined detection decision produces carries a stable
//! schema version along with the identifiers, timestamps, state context,
//! confidence and detection source that downstream Python services and the
//! console need. Schemas here are additive-only: fields may gain variants,
//! but existing names never change meaning, so the orchestrator can gate on
//! the version and stay backward compatible.

use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Current event schema version. Bumped when an event's meaning changes.
pub const TD_EVENT_SCHEMA_VERSION: u32 = 1;

/// The states of the high-level turn detection controller.
///
/// Distinct from [`crate::turn::State`]: that machine models conversation
/// turn-taking (who is talking); this one models the *detection* pipeline
/// that decides when a caller's utterance is actually finished, when an
/// interruption is real, and when the pipeline has recovered from failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnDetectionState {
    /// No session activity and no agent speech yet.
    Idle,
    /// Armed: audio is streaming, waiting for speech to start.
    Listening,
    /// VAD reported speech start, still waiting for it to be sustained.
    SpeechDetected,
    /// Sustained caller speech: an open turn is being captured.
    InTurn,
    /// Silence reached and endpointing is weighing a turn boundary.
    PendingEnd,
    /// A turn boundary is confirmed and handed to the agent.
    TurnComplete,
    /// Response generation is in flight.
    AiThinking,
    /// TTS playback is streaming and barge-in is armed.
    AiSpeaking,
    /// Energy/word evidence suggests the caller may be interrupting.
    InterruptionCandidate,
    /// A genuine interruption: playback cancelled, agent invalidated.
    Interrupted,
    /// A provider (VAD/STT/semantic) failed; detection is degraded.
    Recovering,
    /// Session ended; the controller is terminal and inert.
    Ended,
}

/// Which detector produced the event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetectionSource {
    /// Voice activity detection (energy reference provider by default).
    Vad,
    /// Adaptive endpointing (timing + continuation evidence).
    Endpointing,
    /// Semantic evaluation of partial/final transcripts.
    Semantic,
    /// Barge-in energy/word gates (interruption path only).
    BargeIn,
    /// Controller-internal bookkeeping (timeouts, recovery, cancellation).
    Controller,
}

/// The event vocabulary of the turn detection pipeline.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnEventType {
    /// Caller audio became active (`frame_evidence` carries VAD stats).
    SpeechStarted,
    /// Caller audio went back to silence.
    SpeechEnded,
    /// Endpointing surfaced a candidate turn boundary (`PendingEnd` state).
    EndpointCandidate,
    /// A turn boundary is confirmed; the caller's utterance may be answered.
    TurnCompleted,
    /// An endpoint candidate was withdrawn; the caller kept speaking.
    TurnContinued,
    /// A probable genuine interruption was classified during agent playback.
    InterruptionDetected,
    /// Agent playback was cancelled in response to an interruption.
    TtsCancelled,
    /// A final transcript for the completed turn is available.
    TranscriptionFinalized,
    /// The controller changed state (auditable breadcrumb for every move).
    TurnStateChanged,
    /// A fallback timer fired (max wait / stale session recovery).
    TurnTimeout,
    /// A provider failure was recorded and detection entered `Recovering`.
    ProviderFailed,
    /// Detection returned to `Listening` after a failure was cleared.
    Recovered,
    /// Stale or duplicate input was rejected (diagnostic only).
    Rejected,
}

/// One timestamped, versioned turn-detection event.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TurnEvent {
    /// Event contract version, see [`TD_EVENT_SCHEMA_VERSION`].
    pub schema_version: u32,
    /// Call/session identifier this event belongs to.
    pub call_id: String,
    /// Zero-based turn ordinal; incremented when a turn boundary confirms.
    pub turn_id: u64,
    /// Detection pipeline generation: bumped on interruption/recovery so
    /// consumers can discard events that predate a state reset.
    pub generation: u64,
    /// Wall-clock milliseconds supplied by the caller (transport time).
    pub timestamp_ms: u64,
    /// The event that occurred.
    pub event_type: TurnEventType,
    /// Controller state after the event.
    pub state: TurnDetectionState,
    /// Standardized 0.0..=1.0 confidence of the underlying decision,
    /// `None` where no probabilistic decision was made.
    pub confidence: Option<f32>,
    /// Which detector produced the event.
    pub source: DetectionSource,
    /// Event-specific payload (VAD stats, transcript snippet, profiles…).
    pub metadata: TurnEventMetadata,
}

/// Payload details keyed by [`TurnEvent::event_type`]. Optional fields are
/// honest: they are `None` when the detector had no evidence for them.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TurnEventMetadata {
    /// Sustained speech probability from VAD at the event instant.
    pub vad_probability: Option<f32>,
    /// Silence duration that drove an endpointing decision.
    pub silence_ms: Option<u64>,
    /// Speech duration of the open turn at the event instant.
    pub speech_ms: Option<u64>,
    /// Endpoint profile name in effect (fast/normal/deliberate/…).
    pub profile: Option<String>,
    /// Reason a boundary was decided or withdrawn (semantic/veto/timeout…).
    pub reason: Option<String>,
    /// Caller text excerpt involved in the event (transcript-derived ones).
    pub transcript_excerpt: Option<String>,
    /// Words counted by the transcript gates at the event instant.
    pub word_count: Option<u32>,
    /// Interruption classification when one was weighed.
    pub interruption_kind: Option<InterruptionKind>,
    /// `true` when the event invalidated agent work in flight.
    pub agent_audio_cancelled: Option<bool>,
    /// The error hint carried by provider failure events.
    pub provider_error: Option<String>,
    /// TTF-bounded segment of the semantic evaluator's verdict.
    pub semantic_verdict: Option<SemanticVerdict>,
    /// Routed language for the utterance, when LID provided one.
    pub language: Option<String>,
    /// Profile/latency hint: silence observed at the moment of a timeout.
    pub wait_ms: Option<u64>,
}

/// Classification of caller speech during agent playback.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InterruptionKind {
    /// Sustained non-agent speech after word confirmation: real barge-in.
    Genuine,
    /// A short acknowledgement ("yes", "okay"…): playback must survive.
    Backchannel,
    /// Evidence exists but is not conclusive; hold the candidate open.
    Ambiguous,
}

/// What a semantic evaluator says about the open utterance.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SemanticVerdict {
    /// The utterance is a complete, answerable turn.
    Complete,
    /// The caller is mid-sentence or mid-list; keep listening.
    Incomplete,
    /// The utterance asks a question: complete once the endpoint confirms.
    Question,
    /// The caller is correcting or restating; previous content is stale.
    Correction,
    /// Only filler so far ("um", "uh"): not yet a turn.
    Filler,
}

/// Sensor frame fed to the controller: one audio chunk worth of evidence.
///
/// Transport owns real time; the controller never samples a clock, which is
/// what makes replay harnesses and fixtures fully deterministic.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FrameEvidence {
    /// Frame RMS on the user channel (linear 0..=1).
    pub rms: f32,
    /// Whether agent TTS audio is currently playing.
    pub agent_playing: bool,
    /// Elapsed playback time of the agent's current utterance.
    pub tts_elapsed_ms: u64,
    /// Time since the last agent audio frame arrived on any channel.
    pub since_agent_audio_ms: u64,
}

/// A timestamped controller input. Carries a caller-supplied sequence
/// number so the controller can reject duplicates and out-of-order input.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Timed<T> {
    pub seq: u64,
    pub timestamp_ms: u64,
    pub value: T,
}

impl TurnEvent {
    /// Build a right-typed event with the current schema version.
    pub fn new(
        call_id: &str,
        turn_id: u64,
        generation: u64,
        timestamp_ms: u64,
        event_type: TurnEventType,
        state: TurnDetectionState,
        source: DetectionSource,
    ) -> Self {
        Self {
            schema_version: TD_EVENT_SCHEMA_VERSION,
            call_id: call_id.to_string(),
            turn_id,
            generation,
            timestamp_ms,
            event_type,
            state,
            confidence: None,
            source,
            metadata: TurnEventMetadata::default(),
        }
    }

    /// Human-shape age of the event against a later timestamp; zero when in
    /// the future rather than a negative number (timestamps are unsigned).
    pub fn age_at(&self, now_ms: u64) -> Duration {
        Duration::from_millis(now_ms.saturating_sub(self.timestamp_ms))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_carries_schema_version_and_full_identity() {
        let event = TurnEvent::new(
            "call-1",
            3,
            2,
            12_345,
            TurnEventType::TurnCompleted,
            TurnDetectionState::TurnComplete,
            DetectionSource::Endpointing,
        );
        assert_eq!(event.schema_version, TD_EVENT_SCHEMA_VERSION);
        assert_eq!(event.call_id, "call-1");
        assert_eq!(event.turn_id, 3);
        assert_eq!(event.generation, 2);
        assert_eq!(event.confidence, None);
        serde_json::to_string(&event).expect("event must serialize");
    }

    #[test]
    fn serialization_is_snake_case_stable() {
        let event = TurnEvent::new(
            "call-1",
            0,
            0,
            0,
            TurnEventType::InterruptionDetected,
            TurnDetectionState::InterruptionCandidate,
            DetectionSource::BargeIn,
        );
        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains("\"interruption_detected\""), "{json}");
        assert!(json.contains("\"interruption_candidate\""), "{json}");
        assert!(json.contains("\"barge_in\""), "{json}");
        // Round-trip keeps all fields.
        let parsed: TurnEvent = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.event_type, TurnEventType::InterruptionDetected);
    }

    #[test]
    fn age_never_goes_negative() {
        let event = TurnEvent::new(
            "c",
            0,
            0,
            100,
            TurnEventType::SpeechStarted,
            TurnDetectionState::SpeechDetected,
            DetectionSource::Vad,
        );
        assert_eq!(event.age_at(150).as_millis(), 50);
        assert_eq!(event.age_at(50).as_millis(), 0);
    }

    #[test]
    fn metadata_rejects_unknown_fields() {
        let body = r#"{"not_a_field": 1}"#;
        assert!(serde_json::from_str::<TurnEventMetadata>(body).is_err());
    }

    #[test]
    fn timed_carries_seq_and_time() {
        let frame = Timed { seq: 9, timestamp_ms: 400, value: FrameEvidence { rms: 0.2, agent_playing: false, tts_elapsed_ms: 0, since_agent_audio_ms: 400 } };
        assert_eq!(frame.seq, 9);
        assert_eq!(frame.value.rms, 0.2);
    }
}
