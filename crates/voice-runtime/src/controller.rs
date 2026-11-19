//! The authoritative turn detection controller.
//!
//! One controller instance owns the detection pipeline for exactly one
//! call; concurrent calls are isolated by construction (no shared state)
//! and the Python orchestrator owns the session that wires audio and STT
//! events into it. Transport owns time: state transitions are driven by
//! caller-supplied timestamps, so replay harnesses and tests are fully
//! deterministic.
//!
//! Detections flow through the per-call subsystems — VAD, adaptive
//! endpointing, semantic evaluation, barge-in gates — and every subsystem
//! decision becomes a versioned [`TurnEvent`] carrying call/turn identity,
//! confidence and provenance. The controller is also the corruption
//! firewall: input deduplication, stale-generation rejection, out-of-order
//! tolerance and a provider-failure mode (`Recovering`) that degrades
//! detection instead of killing the call.
//!
//! State machine (every transition emits `turn_state_changed`):
//!
//! ```text
//!   IDLE -> LISTENING -> SPEECH_DETECTED -> IN_TURN
//!   IN_TURN -> PENDING_END -> (TURN_COMPLETE | IN_TURN)
//!   TURN_COMPLETE -> AI_THINKING -> AI_SPEAKING -> (LISTENING | INTERRUPTED)
//!   AI_SPEAKING -> INTERRUPTION_CANDIDATE -> (INTERRUPTED | AI_SPEAKING)
//!   any -> RECOVERING (provider failure) -> LISTENING (recovered)
//!   any -> ENDED (terminal)
//! ```
//!
//! Policies:
//! * VAD answers "is speech happening now" — it never decides the turn
//!   itself; endpointing and semantics own the boundary.
//! * An endpoint candidate combines acoustic confidence with the semantic
//!   verdict. An Incomplete/Correction verdict withdraws the candidate
//!   (`turn_continued`) and defers completion to the timeout fallback, so
//!   a caller trailing off still completes deterministically.
//! * A language switch inside an open utterance (code-switching) grants
//!   silence grace instead of a boundary.
//! * Barge-in cancellation is word-confirmed; energy-only evidence stays a
//!   candidate. Backchannels ("yes", "okay"…) never terminate playback.
//!   The `energy_fallback_interrupt_ms` escape hatch keeps interruption
//!   possible when STT is down or silent.

use crate::barge_in::{
    BargeInConfig, BargeInGate, FrameEvidence, InterruptClassifier, WordInterruptGate,
};
use crate::endpointing::{AdaptiveEndpointer, EndpointConfig, EndpointDecision, EndpointProfile};
use crate::semantic::{
    HeuristicSemanticEvaluator, SemanticContext, SemanticEvaluation, SemanticTurnEvaluator,
};
use crate::td_events::{
    DetectionSource, InterruptionKind, SemanticVerdict, TurnDetectionState, TurnEvent,
    TurnEventType,
};
use crate::vad::{
    EnergyVad, SpeechActivityConfig, SpeechActivityTracker, VadDecision,
};

/// Controller configuration, one per call.
#[derive(Debug, Clone)]
pub struct ControllerConfig {
    pub call_id: String,
    pub profile: EndpointProfile,
    pub max_wait_ms: u64,
    pub confirm_confidence: f32,
    /// Semantic evaluation enabled; disabled falls back to acoustic-only
    /// endpointing with the timeout fallback.
    pub semantic_enabled: bool,
    pub vad: SpeechActivityConfig,
    pub barge_in: BargeInConfig,
    pub word_gate_min_words: u32,
    /// Backchannel lexicon; empty selects the default library set.
    pub backchannels: Vec<String>,
    /// Window extension when LID signals a language switch in the open
    /// utterance (code-switching is not a turn boundary).
    pub code_switch_grace_ms: u64,
    /// Escape hatch: how long an unresolved energy-only candidate during
    /// playback waits before it is treated as a genuine interruption.
    /// `None` disables the hatch (word-gating only).
    pub energy_fallback_interrupt_ms: Option<u64>,
    /// Deadline for an ambiguous interruption candidate to resolve without
    /// new evidence; expiry returns the caller to playback.
    pub interruption_resolve_timeout_ms: u64,
    /// Minimum semantic confidence for a complete verdict (external
    /// semantic VAD reports; local heuristic reports apply too).
    pub semantic_confidence_floor: f32,
    /// Fallback VAD threshold (raw RMS) for degraded mode.
    pub fallback_rms_threshold: f32,
}

impl ControllerConfig {
    pub fn from_profile(call_id: &str, profile: EndpointProfile) -> Self {
        Self {
            call_id: call_id.to_string(),
            profile,
            max_wait_ms: profile.max_silence_ms() * 3,
            confirm_confidence: 0.5,
            semantic_enabled: true,
            vad: SpeechActivityConfig::default(),
            barge_in: BargeInConfig::default(),
            word_gate_min_words: 3,
            backchannels: Vec::new(),
            code_switch_grace_ms: 400,
            energy_fallback_interrupt_ms: Some(900),
            interruption_resolve_timeout_ms: 700,
            semantic_confidence_floor: 0.5,
            fallback_rms_threshold: 0.05,
        }
    }
}

/// Agent lifecycle signals from the TTS layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentEvent {
    /// Response generation started (after a completed turn).
    ThinkingStarted,
    /// TTS playback began with the given generation.
    TtsStarted { generation: u64 },
    /// TTS playback finished cleanly with the given generation.
    TtsEnded { generation: u64 },
}

/// Externally supplied semantic verdict (provider-native semantic VAD).
#[derive(Debug, Clone, Copy)]
pub struct SemanticReport {
    pub verdict: SemanticVerdict,
    pub confidence: f32,
}

/// Per-call diagnostic readout, serializable from the Python seam.
#[derive(Debug, Clone)]
pub struct ControllerSnapshot {
    pub state: TurnDetectionState,
    pub turn_id: u64,
    pub generation: u64,
    pub rejected_inputs: u64,
    pub stale_agent_events: u64,
    pub reordered_inputs: u64,
    pub turns_completed: u64,
    pub interruptions: u64,
    pub provider_failures: u64,
    pub language: Option<String>,
    pub semantic_enabled: bool,
    pub last_partial: String,
}

#[derive(Debug, Clone, Copy)]
struct PendingBoundary {
    silence_ms: u64,
    semantic_verdict: Option<SemanticVerdict>,
}

/// The turn detection controller for one call. Owns all detection state;
/// never reads clocks, never blocks, and allocates at most transiently.
pub struct TurnDetectionController {
    config: ControllerConfig,
    call_id: String,
    state: TurnDetectionState,
    turn_id: u64,
    generation: u64,
    // input guards
    last_seq: u64,
    last_ts_ms: u64,
    rejected_inputs: u64,
    stale_agent_events: u64,
    reordered_inputs: u64,
    // outcome counters
    turns_completed: u64,
    interruptions: u64,
    provider_failures: u64,
    // detection subsystems
    vad: SpeechActivityTracker,
    endpointer: AdaptiveEndpointer,
    word_gate: WordInterruptGate,
    classifier: InterruptClassifier,
    energy_gate: BargeInGate,
    // utterance memory
    last_partial: String,
    last_partial_words: u32,
    // lexical identity
    language: Option<String>,
    code_switch_seen: bool,
    // boundary state
    pending: Option<PendingBoundary>,
    /// One withdrawal notice per silence episode (avoids event flood).
    withdraw_notified: bool,
    // barge-in bookkeeping
    agent_playing: bool,
    candidate_since_ms: Option<u64>,
    // degraded operation
    failure_active: bool,
}

impl TurnDetectionController {
    pub fn new(call_id: &str, config: ControllerConfig) -> Self {
        let max_wait = config.max_wait_ms;
        let confirm = config.confirm_confidence;
        let profile = config.profile;
        let vad_cfg = config.vad.clone();
        Self {
            call_id: call_id.to_string(),
            state: TurnDetectionState::Idle,
            turn_id: 0,
            generation: 0,
            last_seq: 0,
            last_ts_ms: 0,
            rejected_inputs: 0,
            stale_agent_events: 0,
            reordered_inputs: 0,
            turns_completed: 0,
            interruptions: 0,
            provider_failures: 0,
            vad: SpeechActivityTracker::new(EnergyVad::new(), vad_cfg),
            endpointer: AdaptiveEndpointer::new(EndpointConfig {
                profile,
                max_wait_ms: max_wait,
                confirm_confidence: confirm,
                reference_wpm: 140.0,
                grace_ms: 0,
            }),
            word_gate: WordInterruptGate::new(config.word_gate_min_words.max(1)),
            classifier: InterruptClassifier::new(config.backchannels.clone()),
            energy_gate: BargeInGate::new(config.barge_in.clone()),
            last_partial: String::new(),
            last_partial_words: 0,
            language: None,
            code_switch_seen: false,
            pending: None,
            withdraw_notified: false,
            agent_playing: false,
            candidate_since_ms: None,
            failure_active: false,
            config,
        }
    }

    /* ------------------------------- readouts --------------------------- */

    pub fn state(&self) -> TurnDetectionState {
        self.state
    }

    pub fn turn_id(&self) -> u64 {
        self.turn_id
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn language(&self) -> Option<&str> {
        self.language.as_deref()
    }

    pub fn last_partial(&self) -> &str {
        &self.last_partial
    }

    pub fn snapshot(&self) -> ControllerSnapshot {
        ControllerSnapshot {
            state: self.state,
            turn_id: self.turn_id,
            generation: self.generation,
            rejected_inputs: self.rejected_inputs,
            stale_agent_events: self.stale_agent_events,
            reordered_inputs: self.reordered_inputs,
            turns_completed: self.turns_completed,
            interruptions: self.interruptions,
            provider_failures: self.provider_failures,
            language: self.language.clone(),
            semantic_enabled: self.config.semantic_enabled,
            last_partial: self.last_partial.clone(),
        }
    }

    /* ------------------------------- plumbing --------------------------- */

    /// Sequence admission: duplicates never invent state; out-of-order
    /// timestamps are tolerated (frame jitter is normal) but counted;
   /// terminal state rejects silently.
    fn admission(&mut self, seq: u64, ts_ms: u64) -> bool {
        if self.ended() {
            return false;
        }
        if seq <= self.last_seq {
            self.rejected_inputs += 1;
            return false;
        }
        if ts_ms < self.last_ts_ms {
            self.reordered_inputs += 1;
        }
        self.last_seq = seq;
        self.last_ts_ms = self.last_ts_ms.max(ts_ms);
        true
    }

    fn ended(&self) -> bool {
        matches!(self.state, TurnDetectionState::Ended)
    }

    fn base(&self, ts_ms: u64, ty: TurnEventType, source: DetectionSource) -> TurnEvent {
        let mut ev = TurnEvent::new(
            &self.call_id,
            self.turn_id,
            self.generation,
            ts_ms,
            ty,
            self.state,
            source,
        );
        ev.metadata.profile = Some(match self.config.profile {
            EndpointProfile::Fast => "fast",
            EndpointProfile::Normal => "normal",
            EndpointProfile::Deliberate => "deliberate",
        }.to_owned());
        ev.metadata.language = self.language.clone();
        ev
    }

    fn emit(&self, out: &mut Vec<TurnEvent>, ev: TurnEvent) {
        out.push(ev);
    }

    fn transition(&mut self, next: TurnDetectionState, ts_ms: u64, out: &mut Vec<TurnEvent>) {
        if self.state == next || self.ended() {
            return;
        }
        self.state = next;
        let ev = self.base(ts_ms, TurnEventType::TurnStateChanged, DetectionSource::Controller);
        self.emit(out, ev);
    }

    /// Re-arm all per-utterance trackers: called at turn boundaries and
    /// interruptions so the next utterance starts from zero evidence.
    fn arm_next_utterance(&mut self) {
        self.word_gate = WordInterruptGate::new(self.config.word_gate_min_words.max(1));
        self.pending = None;
        self.withdraw_notified = false;
        self.energy_gate = BargeInGate::new(self.config.barge_in.clone());
        self.candidate_since_ms = None;
        self.code_switch_seen = false;
        self.endpointer.config_mut().grace_ms = 0;
        self.endpointer.reset();
        self.last_partial = String::new();
        self.last_partial_words = 0;
    }

    /* -------------------------------- frame path ------------------------ */

    /// Feed one frame of audio evidence. `rms` is the user-channel linear
    /// level 0..=1; agent flags come from the transport.
    pub fn feed_frame(
        &mut self,
        seq: u64,
        ts_ms: u64,
        rms: f32,
        agent_playing: bool,
        tts_elapsed_ms: u64,
        since_agent_audio_ms: u64,
    ) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if !self.admission(seq, ts_ms) {
            return out;
        }

        if agent_playing {
            return self.agent_frame(ts_ms, rms, tts_elapsed_ms, since_agent_audio_ms);
        }

        // User-channel frame.
        match self.state {
            TurnDetectionState::Idle => self.transition(TurnDetectionState::Listening, ts_ms, &mut out),
            TurnDetectionState::AiSpeaking => {
                self.agent_playing = false;
                self.transition(TurnDetectionState::Listening, ts_ms, &mut out);
            }
            TurnDetectionState::Interrupted => {
                // Playback was cancelled; proceed to listening for the
                // caller's replacement utterance.
                self.agent_playing = false;
                self.transition(TurnDetectionState::Listening, ts_ms, &mut out);
            }
            _ => {}
        }

        if self.ended() {
            return out;
        }

        if self.state == TurnDetectionState::Recovering {
            // Degraded mode: raw threshold keeps speech activity alive; no
            // hysteresis knowledge because the provider may be down.
            let active = rms >= self.config.fallback_rms_threshold;
            let decision = self.endpointer.observe_frame(active, self.config.vad.frame_ms);
            self.apply_endpoint_decision(decision, None, confidence_between(active), ts_ms, &mut out);
            return out;
        }

        let decision = self.vad.process_frame(&self.rms_sample(rms));
        self.annotate_speech_edges(&decision, ts_ms, &mut out);
        let endpoint =
            self.endpointer.observe_frame(decision.speech_active, self.config.vad.frame_ms);
        let semantic_now = self.evaluate_semantics();
        self.apply_endpoint_decision(endpoint, semantic_now, decision.probability, ts_ms, &mut out);
        out
    }

    /// Emit speech-start breadcrumbs from the tracker's hysteresis edges.
    fn annotate_speech_edges(
        &mut self,
        decision: &VadDecision,
        ts_ms: u64,
        out: &mut Vec<TurnEvent>,
    ) {
        if decision.probability >= self.config.vad.start_threshold {
            match self.state {
                TurnDetectionState::Listening => {
                    self.transition(TurnDetectionState::SpeechDetected, ts_ms, out);
                    let mut ev =
                        self.base(ts_ms, TurnEventType::SpeechStarted, DetectionSource::Vad);
                    ev.metadata.vad_probability = Some(decision.probability);
                    self.emit(out, ev);
                }
                TurnDetectionState::SpeechDetected if decision.speech_active => {
                    // Sustained window confirmed by the tracker: IN_TURN.
                    self.transition(TurnDetectionState::InTurn, ts_ms, out);
                }
                _ => {}
            }
        } else if self.state == TurnDetectionState::SpeechDetected
            && !decision.speech_active
            && self.vad.candidature_frames() == 0
        {
            // Candidature died out: short noise rejected.
            self.transition(TurnDetectionState::Listening, ts_ms, out);
        }
    }

    fn evaluate_semantics(&self) -> Option<SemanticEvaluation> {
        if self.last_partial.is_empty() {
            return None;
        }
        if !self.config.semantic_enabled {
            return None;
        }
        Some(
            HeuristicSemanticEvaluator.evaluate(&SemanticContext {
                partial_transcript: self.last_partial.clone(),
                word_count: self.last_partial_words,
                language: self.language.clone(),
                speech_ms: self.endpointer.speech_rate().speech_ms,
            }),
        )
    }

    fn apply_endpoint_decision(
        &mut self,
        decision: EndpointDecision,
        semantic: Option<SemanticEvaluation>,
        vad_probability: f32,
        ts_ms: u64,
        out: &mut Vec<TurnEvent>,
    ) {
        match decision {
            EndpointDecision::Speaking => {}
            EndpointDecision::Continued { silence_ms } => {
                self.withdraw_notified = false;
                if self.pending.is_some() {
                    self.pending = None;
                    let mut ev =
                        self.base(ts_ms, TurnEventType::TurnContinued, DetectionSource::Endpointing);
                    ev.metadata.silence_ms = Some(silence_ms);
                    ev.metadata.reason = Some("speech_resumed".into());
                    ev.metadata.vad_probability = Some(vad_probability);
                    self.transition(TurnDetectionState::InTurn, ts_ms, out);
                    self.emit(out, ev);
                }
            }
            EndpointDecision::Candidate { silence_ms, confidence, timed_out, .. } => {
                // No utterance in progress: silence-only candidates cannot
                // open or complete a turn (a call silent from its start
                // must never "complete" a turn that never happened).
                if self.pending.is_none() && self.endpointer.speech_evidence_ms() == 0 {
                    return;
                }
                let semantic_blocked = match &semantic {
                    Some(sem)
                        if !timed_out
                            && matches!(
                                sem.verdict,
                                SemanticVerdict::Incomplete | SemanticVerdict::Correction
                            )
                            && sem.confidence >= self.config.semantic_confidence_floor =>
                    {
                        true
                    }
                    _ => false,
                };
                if semantic_blocked {
                    if self.pending.is_some() || !self.withdraw_notified {
                        self.pending = None;
                        self.withdraw_notified = true;
                        let sem = semantic.expect("blocked with evaluation");
                        let mut ev =
                            self.base(ts_ms, TurnEventType::TurnContinued, DetectionSource::Semantic);
                        ev.metadata.semantic_verdict = Some(sem.verdict);
                        ev.metadata.reason = Some("semantic_wait".into());
                        ev.metadata.transcript_excerpt = Some(self.last_partial.clone());
                        ev.confidence = Some(sem.confidence);
                        self.transition(TurnDetectionState::InTurn, ts_ms, out);
                        self.emit(out, ev);
                    }
                    return;
                }

                let first = self.pending.is_none();
                if first {
                    self.transition(TurnDetectionState::PendingEnd, ts_ms, out);
                    self.pending = Some(PendingBoundary {
                        silence_ms,
                        semantic_verdict: semantic.as_ref().map(|s| s.verdict),
                    });
                    let mut ev = self.base(
                        ts_ms,
                        TurnEventType::EndpointCandidate,
                        DetectionSource::Endpointing,
                    );
                    ev.confidence = Some(confidence);
                    ev.metadata.silence_ms = Some(silence_ms);
                    ev.metadata.reason =
                        Some(if timed_out { "timeout" } else { "silence_window" }.into());
                    ev.metadata.semantic_verdict = semantic.as_ref().map(|s| s.verdict);
                    self.emit(out, ev);
                }
                if let Some(pending) = self.pending.as_mut() {
                    pending.silence_ms = pending.silence_ms.max(silence_ms);
                    if let Some(sem) = &semantic {
                        pending.semantic_verdict = Some(sem.verdict);
                    }
                }

                // Boundary condition: acoustic gate open (or timeout) plus a
                // complete-ish verdict (or acoustic-only mode). After a
                // detected code switch the English-suffix heuristic cannot
                // score the utterance reliably: only the timeout completes.
                let semantic_gate = timed_out
                    || if self.code_switch_seen {
                        false
                    } else {
                        match &semantic {
                            None => true,
                            Some(sem) => matches!(
                                sem.verdict,
                                SemanticVerdict::Complete | SemanticVerdict::Question
                            ),
                        }
                    };
                let acoustic_gate = timed_out || confidence >= self.config.confirm_confidence;
                if acoustic_gate && semantic_gate {
                    let verdict = semantic.as_ref().map(|s| s.verdict);
                    let reason = if timed_out { "timeout" } else { "endpoint_confirmed" };
                    self.complete_turn(
                        ts_ms,
                        confidence.min(match &semantic {
                            Some(sem) => sem.confidence,
                            None => 1.0,
                        }),
                        verdict,
                        reason,
                        silence_ms,
                        out,
                    );
                }
            }
        }
    }

    /* ------------------------------ speak path --------------------------- */

    fn agent_frame(
        &mut self,
        ts_ms: u64,
        rms: f32,
        tts_elapsed_ms: u64,
        since_agent_audio_ms: u64,
    ) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if self.ended() {
            return out;
        }
        self.agent_playing = true;
        if self.state == TurnDetectionState::Interrupted {
            // Playback was cancelled: frames still arriving are streaming
            // residue. They never re-open AiSpeaking; only a fresh
            // TtsStarted (via feed_agent) arms playback again.
            return out;
        }
        match self.state {
            TurnDetectionState::AiSpeaking | TurnDetectionState::InterruptionCandidate => {}
            _ => {
                self.transition(TurnDetectionState::AiSpeaking, ts_ms, &mut out);
                // Fresh playback run: gates re-arm (the P4/P5 gates are
                // cheap enough that re-arming per utterance is the reset).
                self.energy_gate = BargeInGate::new(self.config.barge_in.clone());
                self.candidate_since_ms = None;
            }
        }
        if self.state == TurnDetectionState::Recovering {
            return out;
        }

        let fired = self.energy_gate.evaluate(FrameEvidence {
            rms,
            agent_speaking: true,
            tts_elapsed: std::time::Duration::from_millis(tts_elapsed_ms),
            since_agent_audio: std::time::Duration::from_millis(since_agent_audio_ms),
        });
        if !fired {
            return out;
        }
        if self.candidate_since_ms.is_none() {
            self.transition(TurnDetectionState::InterruptionCandidate, ts_ms, &mut out);
            self.energy_candidate_open(ts_ms);
            return out;
        }
        // Sustained energy without any transcript: the escape hatch
        // decides when evidence is strong enough to be an interruption.
        if let Some(fallback_ms) = self.config.energy_fallback_interrupt_ms {
            let open_for = ts_ms.saturating_sub(self.candidate_since_ms.unwrap_or(ts_ms));
            if open_for >= fallback_ms {
                self.complete_interruption(
                    ts_ms,
                    InterruptionKind::Genuine,
                    0,
                    Some("energy_fallback"),
                    &mut out,
                );
            }
        }
        out
    }

    fn energy_candidate_open(&mut self, ts_ms: u64) {
        if self.candidate_since_ms.is_none() {
            self.candidate_since_ms = Some(ts_ms);
        }
    }

    /* ------------------------------ lexical path ------------------------- */

    /// Feed one STT transcript snapshot. `is_final` marks a final result.
    /// Shares the sequence space with `feed_frame` for deduplication.
    pub fn feed_lexical(
        &mut self,
        seq: u64,
        ts_ms: u64,
        text: &str,
        is_final: bool,
    ) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if !self.admission(seq, ts_ms) {
            return out;
        }
        let words = u32::try_from(text.split_whitespace().count()).unwrap_or(0);
        self.last_partial = text.to_string();
        self.last_partial_words = self.last_partial_words.max(words);
        self.endpointer
            .note_transcript(self.last_partial_words, self.endpointer.speech_rate().speech_ms);
        // speech minutes update comes from the frame path; word evidence is
        // what matters here.

        if is_final {
            let mut ev = self
                .base(ts_ms, TurnEventType::TranscriptionFinalized, DetectionSource::Semantic);
            ev.metadata.transcript_excerpt = Some(text.chars().take(200).collect());
            ev.metadata.word_count = Some(words);
            self.emit(&mut out, ev);
            // A final transcript can settle an open boundary immediately.
            if let Some(pending) = self.pending {
                if let Some(sem) = self.evaluate_semantics() {
                    if matches!(sem.verdict, SemanticVerdict::Complete | SemanticVerdict::Question)
                        && sem.confidence >= self.config.semantic_confidence_floor
                    {
                        self.complete_turn(
                            ts_ms,
                            sem.confidence,
                            Some(sem.verdict),
                            "semantic_final",
                            pending.silence_ms,
                            &mut out,
                        );
                    }
                }
            }
        }

        if self.state == TurnDetectionState::AiSpeaking
            || self.state == TurnDetectionState::InterruptionCandidate
        {
            self.lexical_during_speech(text, words, ts_ms, &mut out);
        }
        out
    }

    fn lexical_during_speech(
        &mut self,
        text: &str,
        words: u32,
        ts_ms: u64,
        out: &mut Vec<TurnEvent>,
    ) {
        if self.state == TurnDetectionState::InterruptionCandidate {
            // Re-classify the cumulative snapshot: the candidate keeps
            // its evidence open instead of requiring a fresh gate fire.
            let kind = self.classifier.classify(text);
            match kind {
                InterruptionKind::Genuine => {
                    self.complete_interruption(ts_ms, kind, words, None, out);
                }
                InterruptionKind::Backchannel => {
                    // The acknowledgement dissolved the candidate;
                    // playback continues.
                    let mut ev =
                        self.base(ts_ms, TurnEventType::SpeechStarted, DetectionSource::BargeIn);
                    ev.metadata.interruption_kind = Some(InterruptionKind::Backchannel);
                    ev.metadata.agent_audio_cancelled = Some(false);
                    ev.metadata.word_count = Some(words);
                    ev.metadata.transcript_excerpt = Some(text.chars().take(64).collect());
                    self.emit(out, ev);
                    self.dissolve_candidate(ts_ms, out);
                }
                InterruptionKind::Ambiguous => {}
            }
            return;
        }

        let fired = self.word_gate.on_partial_transcript(text);
        if !fired {
            return;
        }
        match self.classifier.classify(text) {
            InterruptionKind::Backchannel => {
                // Acknowledgements never terminate agent playback.
                let mut ev =
                    self.base(ts_ms, TurnEventType::SpeechStarted, DetectionSource::BargeIn);
                ev.metadata.interruption_kind = Some(InterruptionKind::Backchannel);
                ev.metadata.agent_audio_cancelled = Some(false);
                ev.metadata.word_count = Some(words);
                self.emit(out, ev);
            }
            InterruptionKind::Ambiguous => {
                self.transition(TurnDetectionState::InterruptionCandidate, ts_ms, out);
                self.energy_candidate_open(ts_ms);
            }
            InterruptionKind::Genuine => {
                self.complete_interruption(ts_ms, InterruptionKind::Genuine, words, None, out);
            }
        }
    }

    fn dissolve_candidate(&mut self, ts_ms: u64, out: &mut Vec<TurnEvent>) {
        self.candidate_since_ms = None;
        self.energy_gate = BargeInGate::new(self.config.barge_in.clone());
        self.word_gate = WordInterruptGate::new(self.config.word_gate_min_words.max(1));
        self.transition(TurnDetectionState::AiSpeaking, ts_ms, out);
    }

    /// Provider-supplied semantic verdict for the open utterance.
    pub fn feed_semantic(
        &mut self,
        seq: u64,
        ts_ms: u64,
        report: SemanticReport,
    ) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if !self.admission(seq, ts_ms) {
            return out;
        }
        if let Some(pending) = self.pending {
            let mut ud = pending;
            ud.semantic_verdict = Some(report.verdict);
            self.pending = Some(ud);
            if matches!(report.verdict, SemanticVerdict::Complete | SemanticVerdict::Question)
                && report.confidence >= self.config.semantic_confidence_floor
            {
                self.complete_turn(
                    ts_ms,
                    report.confidence,
                    Some(report.verdict),
                    "semantic_report",
                    pending.silence_ms,
                    &mut out,
                );
            }
        }
        out
    }

    /// Language identification output. `switch` marks a language change
    /// inside the open utterance: it grants silence grace, never a turn.
    pub fn feed_lid(
        &mut self,
        seq: u64,
        ts_ms: u64,
        language: &str,
        switch: bool,
    ) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if !self.admission(seq, ts_ms) {
            return out;
        }
        let lang = language.trim().to_string();
        if lang.is_empty() {
            return out;
        }
        let switched = switch && self.language.as_deref() != Some(lang.as_str());
        self.language = Some(lang);
        if switched && matches!(
            self.state,
            TurnDetectionState::InTurn | TurnDetectionState::PendingEnd
        ) {
            self.code_switch_seen = true;
            // Give the speaker room to finish the code-switched thought:
            // every later endpointing decision now uses the extended
            // window, whether or not a candidate was already open.
            let extended = self.config.code_switch_grace_ms;
            self.endpointer.config_mut().grace_ms = extended;
            let mut ev = self.base(
                ts_ms,
                TurnEventType::TurnContinued,
                DetectionSource::Semantic,
            );
            ev.metadata.language = self.language.clone();
            ev.metadata.reason = Some("code_switch_grace".into());
            ev.metadata.wait_ms = Some(extended);
            if self.pending.is_some() {
                self.pending = None;
                self.transition(TurnDetectionState::InTurn, ts_ms, &mut out);
            }
            self.emit(&mut out, ev);
        }
        out
    }

    /* ------------------------------- agent path -------------------------- */

    pub fn feed_agent(&mut self, ts_ms: u64, event: AgentEvent) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if self.ended() {
            return out;
        }
        match event {
            AgentEvent::ThinkingStarted => {
                if self.state == TurnDetectionState::TurnComplete {
                    self.transition(TurnDetectionState::AiThinking, ts_ms, &mut out);
                } else {
                    self.stale_agent_events += 1;
                }
            }
            AgentEvent::TtsStarted { generation } => {
                if generation < self.generation {
                    self.stale_agent_events += 1;
                    return out;
                }
                match self.state {
                    TurnDetectionState::AiThinking | TurnDetectionState::TurnComplete => {
                        self.transition(TurnDetectionState::AiSpeaking, ts_ms, &mut out);
                        self.agent_playing = true;
                        self.energy_gate = BargeInGate::new(self.config.barge_in.clone());
                        self.candidate_since_ms = None;
                    }
                    _ => {
                        self.stale_agent_events += 1;
                    }
                }
            }
            AgentEvent::TtsEnded { generation } => {
                if generation < self.generation {
                    // Playback of a cancelled generation finishing after the
                    // prompt was invalidated: stale by design.
                    self.stale_agent_events += 1;
                    return out;
                }
                if self.state == TurnDetectionState::AiSpeaking {
                    self.agent_playing = false;
                    self.transition(TurnDetectionState::Listening, ts_ms, &mut out);
                    self.energy_gate = BargeInGate::new(self.config.barge_in.clone());
                    self.candidate_since_ms = None;
                } else {
                    self.stale_agent_events += 1;
                }
            }
        }
        out
    }

    /* ------------------------------ failure path ------------------------- */

    /// A provider (VAD/STT/semantic) failed: degrade instead of dying.
    /// In `Recovering`, detection uses raw threshold fallbacks and the
    /// escape hatch so callers are never permanently locked out.
    pub fn note_provider_failure(&mut self, ts_ms: u64, hint: &str) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if self.ended() {
            return out;
        }
        self.provider_failures += 1;
        let mut ev = self.base(ts_ms, TurnEventType::ProviderFailed, DetectionSource::Controller);
        ev.metadata.provider_error = Some(hint.to_string());
        self.emit(&mut out, ev);
        self.failure_active = true;
        self.transition(TurnDetectionState::Recovering, ts_ms, &mut out);
        out
    }

    pub fn note_provider_recovered(&mut self, ts_ms: u64) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if !self.failure_active {
            // Restart without a live failure: stale recovery, ignore.
            return out;
        }
        self.failure_active = false;
        self.vad = SpeechActivityTracker::new(EnergyVad::new(), self.config.vad.clone());
        let mut ev = self
            .base(ts_ms, TurnEventType::Recovered, DetectionSource::Controller);
        ev.metadata.reason = Some("provider_recovered".into());
        self.emit(&mut out, ev);
        let next = if self.agent_playing {
            TurnDetectionState::AiSpeaking
        } else {
            TurnDetectionState::Listening
        };
        self.transition(next, ts_ms, &mut out);
        out
    }

    /* ------------------------------ termination -------------------------- */

    pub fn end_call(&mut self, ts_ms: u64) -> Vec<TurnEvent> {
        let mut out = Vec::new();
        if self.ended() {
            return out;
        }
        self.transition(TurnDetectionState::Ended, ts_ms, &mut out);
        out
    }

    /* ------------------------------- internals --------------------------- */

    fn complete_interruption(
        &mut self,
        ts_ms: u64,
        kind: InterruptionKind,
        words: u32,
        reason: Option<&str>,
        out: &mut Vec<TurnEvent>,
    ) {
        let cancelled = matches!(kind, InterruptionKind::Genuine);
        if cancelled {
            self.interruptions += 1;
            self.generation += 1;
        }
        let mut ev = self
            .base(ts_ms, TurnEventType::InterruptionDetected, DetectionSource::BargeIn);
        ev.metadata.interruption_kind = Some(kind);
        if cancelled {
            ev.metadata.agent_audio_cancelled = Some(true);
        }
        ev.metadata.word_count = Some(words);
        if let Some(chunk) = reason {
            ev.metadata.reason = Some(chunk.to_string());
        }
        self.emit(out, ev);
        if cancelled {
            let mut cancel_ev = self.base(ts_ms, TurnEventType::TtsCancelled, DetectionSource::BargeIn);
            cancel_ev.metadata.agent_audio_cancelled = Some(true);
            self.emit(out, cancel_ev);
            self.transition(TurnDetectionState::Interrupted, ts_ms, out);
            self.agent_playing = false;
            self.arm_next_utterance();
        }
    }

    fn complete_turn(
        &mut self,
        ts_ms: u64,
        confidence: f32,
        verdict: Option<SemanticVerdict>,
        reason: &str,
        silence_ms: u64,
        out: &mut Vec<TurnEvent>,
    ) {
        self.turn_id += 1;
        self.turns_completed += 1;
        let speech_ms = self.endpointer.speech_rate().speech_ms;
        self.state = TurnDetectionState::TurnComplete;
        let mut ev = self
            .base(ts_ms, TurnEventType::TurnCompleted, DetectionSource::Endpointing);
        ev.confidence = Some(confidence.clamp(0.0, 1.0));
        ev.metadata.silence_ms = Some(silence_ms);
        ev.metadata.speech_ms = Some(speech_ms);
        ev.metadata.semantic_verdict = verdict;
        ev.metadata.reason = Some(reason.to_string());
        self.emit(out, ev);
        self.arm_next_utterance();
    }

    /// Constant-amplitude sample vector whose `frame_rms` equals `rms`, so
    /// the Python seam feeds the reference VAD through the unmodified
    /// real math path.
    fn rms_sample(&self, rms: f32) -> Vec<i16> {
        let clamped = rms.clamp(0.0, 1.0);
        let s = (clamped * 32767.0).round() as i16;
        let len = (self.config.vad.frame_ms * 16) as usize;
        vec![s; len]
    }
}

#[inline]
fn confidence_between(active: bool) -> f32 {
    if active { 0.9 } else { 0.0 }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::td_events::TD_EVENT_SCHEMA_VERSION;

    pub(super) const SPEECH_RMS: f32 = 0.4;
    pub(super) const SILENCE_RMS: f32 = 0.005;
    #[allow(dead_code)]
    const NOISE_RMS: f32 = 0.12;

    /// Juwaa feed helper: a clock keeps monotonic time as we add frames.
    struct Harness {
        c: TurnDetectionController,
        seq: u64,
        ts: u64,
    }

    impl Harness {
        fn normal(profile: EndpointProfile) -> Self {
            Self::with(call_cfg(profile))
        }

        fn with(config: ControllerConfig) -> Self {
            let c = TurnDetectionController::new("call-1", config);
            Self { c, seq: 0, ts: 0 }
        }

        fn frame(&mut self, rms: f32, agent: bool, tts_ms: u64, since: u64) -> Vec<TurnEvent> {
            self.seq += 1;
            self.ts += 20;
            self.c.feed_frame(self.seq, self.ts, rms, agent, tts_ms, since)
        }

        fn speak(&mut self, frames: u32) -> Vec<TurnEvent> {
            (0..frames).flat_map(|_| self.frame(SPEECH_RMS, false, 0, 9_999)).collect()
        }

        fn silence(&mut self, frames: u32) -> Vec<TurnEvent> {
            (0..frames).flat_map(|_| self.frame(SILENCE_RMS, false, 0, 9_999)).collect()
        }

        fn partial(&mut self, text: &str) -> Vec<TurnEvent> {
            self.seq += 1;
            self.ts += 20;
            self.c.feed_lexical(self.seq, self.ts, text, false)
        }

        fn final_transcript(&mut self, text: &str) -> Vec<TurnEvent> {
            self.seq += 1;
            self.ts += 20;
            self.c.feed_lexical(self.seq, self.ts, text, true)
        }

        fn agent(&mut self, ev: AgentEvent) -> Vec<TurnEvent> {
            self.c.feed_agent(self.ts, ev)
        }

        #[allow(dead_code)]
        fn events_of<'a>(events: impl IntoIterator<Item = &'a TurnEvent>, ty: TurnEventType) -> Vec<&'a TurnEvent> {
            events.into_iter().filter(|e| e.event_type == ty).collect()
        }
    }

    pub(super) fn call_cfg(profile: EndpointProfile) -> ControllerConfig {
        ControllerConfig::from_profile("call-1", profile)
    }

    #[test]
    fn schema_version_is_carried_on_every_event() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        h.partial("I need an appointment for Monday");
        let _ = h.speak(8);
        let stream = h.silence(45);
        assert!(stream.iter().any(|e| e.event_type == TurnEventType::TurnCompleted));
        for ev in &stream {
            assert_eq!(ev.schema_version, TD_EVENT_SCHEMA_VERSION);
            assert_eq!(ev.call_id, "call-1");
        }
    }

    #[test]
    fn completed_utterance_reaches_turn_complete() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        h.partial("I need an appointment for Monday");
        let utt = h.speak(10);
        let quie = h.silence(45);
        assert!(
            utt.iter().chain(quie.iter()).any(|e| e.event_type == TurnEventType::TurnCompleted),
            "turn should complete after endpoint: {:?}",
            quie
        );
        assert_eq!(h.c.state(), TurnDetectionState::TurnComplete);
        assert_eq!(h.c.turn_id(), 1);
        assert_eq!(h.c.snapshot().turns_completed, 1);
    }

    #[test]
    fn silence_does_not_end_turn_while_speech_continues() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        h.partial("and the price is");
        h.speak(10);
        h.silence(8);
        h.speak(10);
        h.partial("and the price is very high");
        h.speak(10);
        h.silence(45);
        assert!(matches!(h.c.state(), TurnDetectionState::TurnComplete | TurnDetectionState::AiThinking));
        // A (inter-turn) pause must not count as +1 turn before endpoint.
        assert_eq!(h.c.snapshot().turns_completed, 1, "mid-turn pause is not a boundary");
    }

    #[test]
    fn incomplete_thought_waits_through_silence_then_completes_on_timeout() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        h.partial("I need to book an appointment for");
        h.speak(10);
        // Silence: candidate opens as soon as the window allows (or at
        // timeout); the semantic verdict says WAIT.
        let frag = (0..60)
            .flat_map(|_| h.silence(1))
            .collect::<Vec<_>>();
        assert!(
            frag.iter().any(|e| e.event_type == TurnEventType::TurnContinued),
            "semantic wait must withdraw candidate"
        );
        assert!(matches!(h.c.state(), TurnDetectionState::InTurn | TurnDetectionState::PendingEnd));
        // Keep silent past max_wait (2700ms + opened silence): fallback
        // must complete the turn, honestly marked as a timeout.
        let tail = (0..200).flat_map(|_| h.silence(1)).collect::<Vec<_>>();
        assert!(
            tail.iter().any(|e| e.event_type == TurnEventType::TurnCompleted),
            "timeout fallback must complete an abandoned utterance"
        );
        assert!(tail.iter().any(|e| e.metadata.reason.as_deref() == Some("timeout")));
    }

    #[test]
    fn question_completes_after_endpoint_confirmation() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        h.partial("Can you tell me the price?");
        h.speak(10);
        let tail = h.silence(45);
        assert!(
            tail.iter().any(|e| e.event_type == TurnEventType::TurnCompleted),
            "question completes on endpoint confirmation"
        );
    }

    #[test]
    fn genuine_barge_in_cancels_playback_and_bumps_generation() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        // Prime a full turn cycle into AI_SPEAKING.
        let _ = h.final_transcript("Hello there");
        let _ = h.speak(10);
        let boundary = h.silence(45);
        assert!(boundary.iter().any(|e| e.event_type == TurnEventType::TurnCompleted));
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let tts = h.agent(AgentEvent::TtsStarted { generation: 0 });
        assert!(tts.iter().any(|e| e.metadata.agent_audio_cancelled.is_none()));
        assert_eq!(h.c.state(), TurnDetectionState::AiSpeaking);

        // Backend silence, mature TTS: caller says "no, I said Tuesday!".
        let _ = h.partial("");
        let _ = h.partial("no");
        let _ = h.partial("no, I");
        let fired = h.partial("no, I said Tuesday!");

        // The word gate fires on the 3rd word (default N=3).
        let detected: Vec<_> = fired
            .iter()
            .filter(|e| e.event_type == TurnEventType::InterruptionDetected)
            .collect();
        assert_eq!(detected.len(), 1, "exactly one interruption event {:?}", fired);
        assert_eq!(
            h.c.state(),
            TurnDetectionState::Interrupted,
            "fired events: {:?}",
            fired
        );
        assert_eq!(h.c.generation(), 1, "generation bumps on interrupt");
        assert_eq!(h.c.snapshot().interruptions, 1);
    }

    #[test]
    fn backchannel_acknowledgement_does_not_cancel_playback() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        let _ = h.final_transcript("Hello there");
        let _ = h.speak(10);
        let _ = h.silence(45);
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let _ = h.agent(AgentEvent::TtsStarted { generation: 0 });

        let _ = h.partial("yes");
        let _ = h.partial("yes okay");
        // The word gate fires on the cumulative snapshot (default N=3); a
        // pure-acknowledgement snapshot records backchannel speech, never
        // an interrupt.
        let firing = h.partial("yes okay hmm");
        let detected: Vec<_> = firing
            .iter()
            .filter(|e| e.event_type == TurnEventType::SpeechStarted)
            .collect();
        assert_eq!(
            detected.len(),
            1,
            "backchannel recorded as speech, not as interrupt: {:?}",
            firing
        );
        assert_eq!(
            detected[0].metadata.interruption_kind,
            Some(InterruptionKind::Backchannel)
        );
        assert!(firing.iter().all(|e| e.metadata.agent_audio_cancelled != Some(true)));
        assert_eq!(h.c.state(), TurnDetectionState::AiSpeaking);
        assert_eq!(h.c.snapshot().interruptions, 0);
        // Extended backchannel flow: partials of pure acknowledgements still
        // never interrupt.
        let _ = h.partial("yes okay");
        let more = h.partial("yes okay hmm");
        assert!(!more.iter().any(|e| e.metadata.agent_audio_cancelled == Some(true)));
        assert_eq!(h.c.state(), TurnDetectionState::AiSpeaking);
    }

    #[test]
    fn wordless_toggle_candidate_resolves_to_playback_after_deadline() {
        // Energy-only candidate without transcript: not a real interruption
        // until the fallback window elapses. With the fallback disabled
        // (`None`), frames-only noise can never cancel, per the P4/P5
        // verified policy.
        let mut config = call_cfg(EndpointProfile::Normal);
        config.energy_fallback_interrupt_ms = None;
        let mut h = Harness::with(config);
        let _ = h.final_transcript("Hello there");
        let _ = h.speak(10);
        let _ = h.silence(45);
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let _ = h.agent(AgentEvent::TtsStarted { generation: 0 });
        // Loud frames past the energy gate (>=3 consecutive, mature TTS).
        for _ in 0..4 {
            let _ = h.frame(0.4, true, 4_000, 30_000);
        }
        assert_eq!(h.c.state(), TurnDetectionState::InterruptionCandidate);
        assert_eq!(h.c.snapshot().interruptions, 0);
        // No transcript resolution: no cancel, ever.
        for _ in 0..20 {
            let _ = h.frame(0.4, true, 4_020, 30_000);
        }
        assert_eq!(h.c.snapshot().interruptions, 0);
    }

    #[test]
    fn energy_fallback_hatch_allows_interrupt_without_transcript() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        let _ = h.final_transcript("Hello there");
        let _ = h.speak(10);
        let _ = h.silence(45);
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let _ = h.agent(AgentEvent::TtsStarted { generation: 0 });
        // Mature TTS, loud user channel, no transcript ever arrives.
        // ~900ms past the candidate opening crosses the hatch window.
        for _ in 0..56 {
            let _ = h.frame(0.4, true, 4_000, 30_000);
        }
        assert!(matches!(h.c.state(), TurnDetectionState::Interrupted));
        assert_eq!(h.c.snapshot().interruptions, 1);
    }

    #[test]
    fn stale_agent_events_do_not_permanently_desync() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        let _ = h.final_transcript("Hello");
        let _ = h.speak(10);
        let _ = h.silence(45);
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let _ = h.agent(AgentEvent::TtsStarted { generation: 0 });
        // A stale finished generation-0 TTS end after cancel arrives late.
        let _ = h.final_transcript("no I said Tuesday!");
        let _ = h.frame(SPEECH_RMS, true, 1_000, 30_000);
        let _ = h.frame(SPEECH_RMS, true, 1_020, 30_000);
        let _ = h.frame(SPEECH_RMS, true, 1_040, 30_000);
        assert_eq!(h.c.generation(), 1, "interrupt bumped generation");
        let late = h.agent(AgentEvent::TtsEnded { generation: 0 });
        // TtsEnded for the cancelled generation is stale: no state change.
        assert!(!late.iter().any(|e| e.event_type == TurnEventType::TurnStateChanged));
        assert_eq!(h.c.state(), TurnDetectionState::Interrupted);
        // The interrupted correction utterance completes a fresh turn and
        // the next agent generation advances the pipeline.
        let _ = h.frame(SPEECH_RMS, false, 0, 9_999);
        let _ = h.speak(8);
        let _ = h.silence(45);
        assert_eq!(h.c.turn_id(), 2, "interrupted correction is its own turn");
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let fresh = h.agent(AgentEvent::TtsStarted { generation: 1 });
        assert!(fresh.iter().any(|e| e.event_type == TurnEventType::TurnStateChanged));
        assert_eq!(h.c.state(), TurnDetectionState::AiSpeaking);
    }

    #[test]
    fn duplicate_and_out_of_order_inputs_are_isolated() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        let _ = h.frame(SILENCE_RMS, false, 0, 9_999);
        // Exact duplicate of the previous frame is rejected.
        let _ = h.c.feed_frame(h.seq, h.ts, SILENCE_RMS, false, 0, 9_999);
        assert_eq!(h.c.snapshot().rejected_inputs, 1);
        // Out-of-order timestamp within the stream is tolerated but counted.
        h.seq += 10;
        let _ = h.c.feed_frame(h.seq, h.ts + 400, SPEECH_RMS, false, 0, 9_999);
        let snapshot = h.c.snapshot();
        assert_eq!(snapshot.reordered_inputs, 0);
        let _ = h.c.feed_frame(h.seq - 5, h.ts, SPEECH_RMS, false, 0, 9_999);
        assert_eq!(h.c.snapshot().rejected_inputs, 2);
    }

    #[test]
    fn provider_failure_degrades_and_recovers() {
        let mut config = call_cfg(EndpointProfile::Normal);
        config.semantic_enabled = false;
        let mut h = Harness::with(config);
        h.speak(10);
        // STT died: no transcripts, frames only, we still endpoint on
        // acoustic evidence alone.
        let failed = h.c.note_provider_failure(h.ts, "stt_down");
        assert!(failed.iter().any(|e| e.event_type == TurnEventType::ProviderFailed));
        assert_eq!(h.c.state(), TurnDetectionState::Recovering);
        // Acoustic-only proceeds in the degraded path (fallback threshold).
        for _ in 0..20 {
            let _ = h.frame(SPEECH_RMS, false, 0, 9_999);
        }
        let tail = (0..300).flat_map(|_| h.silence(1)).collect::<Vec<_>>();
        // Acoustic fallback endpointing must still close the turn.
        assert!(
            tail.iter().any(|e| e.event_type == TurnEventType::TurnCompleted),
            "fallback must still endpoint"
        );
        h.c.note_provider_recovered(h.ts);
        assert_eq!(h.c.state(), TurnDetectionState::Listening);
        assert_eq!(h.c.snapshot().provider_failures, 1);
    }

    #[test]
    fn concurrent_calls_are_isolated() {
        let mut a = TurnDetectionController::new("call-a", call_cfg(EndpointProfile::Normal));
        let mut b = TurnDetectionController::new("call-b", call_cfg(EndpointProfile::Fast));
        // Call A completes 3 turns; call B stays silent forever.
        let mut seq_a = 0;
        let mut ts_a = 0;
        for _ in 0..3 {
            for _ in 0..10 {
                seq_a += 1;
                ts_a += 20;
                let _ = a.feed_frame(seq_a, ts_a, SPEECH_RMS, false, 0, 9_999);
            }
            for _ in 0..45 {
                seq_a += 1;
                ts_a += 20;
                let _ = a.feed_frame(seq_a, ts_a, SILENCE_RMS, false, 0, 9_999);
            }
        }
        for i in 0..30 {
            let _ = b.feed_frame(i + 1, i * 20, SILENCE_RMS, false, 0, 9_999);
        }
        assert_eq!(a.snapshot().turns_completed, 3);
        assert_eq!(b.snapshot().turns_completed, 0);
        assert_ne!(
            a.snapshot().turns_completed, b.snapshot().turns_completed
        );
    }

    #[test]
    fn tts_cancel_from_interrupt_race_is_safe() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        let _ = h.final_transcript("Hello");
        let _ = h.speak(10);
        let _ = h.silence(45);
        let _ = h.agent(AgentEvent::ThinkingStarted);
        let _ = h.agent(AgentEvent::TtsStarted { generation: 0 });
        // Interruption while playback streaming.
        h.partial("no");
        h.partial("no, I said");
        let _ = h.partial("no, I said wait!");
        let evs = h.c.feed_agent(h.ts, AgentEvent::TtsEnded { generation: 0 });
        // Old-generation cancel-late playback end: stale, no transition.
        assert!(evs.is_empty());
        assert_eq!(h.c.state(), TurnDetectionState::Interrupted);
        // end_call works even while interrupted state pending.
        let end = h.c.end_call(h.ts);
        let last = end.last().map(|e| e.state).unwrap();
        assert_eq!(last, TurnDetectionState::Ended);
        // Terminal state accepts nothing new.
        let after_end = h.c.feed_frame(h.seq + 1, h.ts + 20, SPEECH_RMS, false, 0, 9_999);
        assert!(after_end.is_empty());
    }

    #[test]
    fn code_switching_grants_silence_grace_not_a_boundary() {
        let mut h = Harness::normal(EndpointProfile::Normal);
        h.partial("I need an appointment tomorrow");
        h.speak(10);
        // Language switch mid-utterance (Malayalam code-switch).
        let lid = h.c.feed_lid(h.seq + 1, h.ts, "ml-IN", true);
        h.seq += 1;
        h.ts += 20;
        assert!(
            lid.iter().any(|e| e.metadata.reason.as_deref() == Some("code_switch_grace")),
            "code switch grants grace: {:?}",
            lid
        );
        h.partial("Tuesday venam");
        h.speak(10);
        // The candidate must not fire at the plain normal window
        // (~600ms); grace extends it. 35 frames = 700ms of silence: under
        // grace the caller is still in-turn.
        h.silence(35);
        assert!(
            !matches!(h.c.state(), TurnDetectionState::PendingEnd),
            "sweet silence with grace stays in turn: {:?}",
            h.c.snapshot()
        );
        // Well past profile max (900) + no fallback yet — pending appears
        // only once elapsed silence reaches window+grace.
        h.silence(26);
        assert_eq!(h.c.state(), TurnDetectionState::PendingEnd);
    }
}
