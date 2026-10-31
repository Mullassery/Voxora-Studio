//! Scripted call bridge producing ordered telephony-style events.
//!
//! The bridge drives a [`DialogBackend`] through a call and records events
//! with monotonic offsets, mirroring the event vocabulary used by the Python
//! orchestrator (`dialing`, `ringing`, `answered`, `turn_started`,
//! `turn_completed`, `ended`) and the latency stage names stored per call.
//!
//! This is the honest seam where a real RTP/WebSocket backend will plug in.

use std::time::Duration;

use crate::turn::{Signal, TurnMachine};

/// Latency stages measured per agent turn, matching the orchestrator schema.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TurnLatency {
    pub audio_received: f64,
    pub speech_endpoint: f64,
    pub stt_result: f64,
    pub agent_response: f64,
    pub tts_first_audio: f64,
    pub first_audio_transmitted: f64,
}

impl TurnLatency {
    /// Sum of all pipeline stages for one turn.
    pub fn turn_completed(&self) -> f64 {
        self.audio_received
            + self.speech_endpoint
            + self.stt_result
            + self.agent_response
            + self.tts_first_audio
            + self.first_audio_transmitted
    }
}

/// A point-in-time call event.
#[derive(Debug, Clone, PartialEq)]
pub struct BridgeEvent {
    /// Offset from call start, monotonically non-decreasing.
    pub at_ms: u64,
    pub kind: &'static str,
    pub detail: String,
}

/// Outcome of one scripted agent turn.
#[derive(Debug, Clone)]
pub struct Utterance {
    pub text: String,
    pub latency: TurnLatency,
    /// How long the agent speaks (playback), after `first_audio_transmitted`.
    pub speak_ms: u64,
}

/// Deterministic scripted dialog backend.
pub trait DialogBackend {
    /// Produce the agent utterance for the user's (unknown here) input.
    /// Implementations must be deterministic for a given turn index or carry
    /// their own state.
    fn respond(&mut self, turn_index: u32) -> Option<Utterance>;
}

/// Final summary of a bridged call.
#[derive(Debug, Clone, PartialEq)]
pub struct BridgeSummary {
    pub total_ms: u64,
    pub turns: u32,
    pub events: Vec<BridgeEvent>,
    /// Mean of `turn_completed` latency across turns.
    pub mean_turn_latency_ms: f64,
}

/// Errors the bridge can report.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeError {
    /// The backend produced no utterance on the first turn: nothing to play.
    EmptyDialog,
    /// The dialog never reached the turn cap (backend misbehaving).
    DialogNeverEnded,
}

/// Drive up to `max_turns` exchanges with `backend`, recording events.
///
/// Timing model (deterministic): dialing at 0, ringing at 400 ms, answered at
/// 1200 ms, then per turn the summed pipeline latencies plus speaking time.
pub fn run_scripted_call(
    backend: &mut dyn DialogBackend,
    max_turns: u32,
    turn_cap_ms: u64,
) -> Result<BridgeSummary, BridgeError> {
    let mut events = Vec::new();
    let mut at: u64 = 0;
    let push = |events: &mut Vec<BridgeEvent>, at: &mut u64, kind: &'static str, detail: String| {
        events.push(BridgeEvent { at_ms: *at, kind, detail });
        let _ = at;
    };
    push(&mut events, &mut at, "dialing", "scripted bridge".to_string());
    at += 400;
    push(&mut events, &mut at, "ringing", "simulated".to_string());
    at += 800;
    push(&mut events, &mut at, "answered", "scripted bridge".to_string());

    let mut machine = TurnMachine::new();
    let mut turn_index: u32 = 0;
    let mut latency_total = 0.0;
    let mut dialog_started = false;

    while turn_index < max_turns {
        let Some(utterance) = backend.respond(turn_index) else {
            if !dialog_started {
                return Err(BridgeError::EmptyDialog);
            }
            break;
        };
        dialog_started = true;
        let stage_total = utterance.latency.turn_completed();
        if at as f64 + stage_total > turn_cap_ms as f64 {
            break;
        }
        at += stage_total.ceil() as u64;
        push(&mut events, &mut at, "turn_started", format!("turn {turn_index}"));
        let _ = machine.apply(Signal::AgentSpeechStart);
        machine.tick_agent_speech(Duration::from_millis(utterance.speak_ms.max(1)));
        let _ = machine.apply(Signal::AgentSpeechEnd);
        at += utterance.speak_ms;
        push(
            &mut events,
            &mut at,
            "turn_completed",
            utterance.text.clone(),
        );
        latency_total += stage_total;
        turn_index += 1;
    }

    if turn_index == 0 {
        return Err(BridgeError::DialogNeverEnded);
    }
    push(&mut events, &mut at, "ended", format!("{turn_index} turns"));
    Ok(BridgeSummary {
        total_ms: at,
        turns: turn_index,
        mean_turn_latency_ms: latency_total / f64::from(turn_index),
        events,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Fixed script: replies "ok <n>" with fixed latency for `n` turns.
    struct FixedScript {
        turns: u32,
        speak_ms: u64,
    }

    impl DialogBackend for FixedScript {
        fn respond(&mut self, turn_index: u32) -> Option<Utterance> {
            if turn_index >= self.turns {
                return None;
            }
            Some(Utterance {
                text: format!("ok {turn_index}"),
                latency: TurnLatency {
                    audio_received: 50.0,
                    speech_endpoint: 200.0,
                    stt_result: 100.0,
                    agent_response: 300.0,
                    tts_first_audio: 150.0,
                    first_audio_transmitted: 20.0,
                },
                speak_ms: self.speak_ms,
            })
        }
    }

    fn backend(turns: u32) -> FixedScript {
        FixedScript { turns, speak_ms: 1000 }
    }

    #[test]
    fn event_order_is_monotonic_and_complete() {
        let summary = run_scripted_call(&mut backend(3), 10, u64::MAX).unwrap();
        let kinds: Vec<_> = summary.events.iter().map(|e| e.kind).collect();
        assert_eq!(kinds.first(), Some(&"dialing"));
        assert_eq!(kinds.last(), Some(&"ended"));
        for pair in summary.events.windows(2) {
            assert!(pair[0].at_ms <= pair[1].at_ms, "offsets must be monotonic");
        }
        assert_eq!(summary.turns, 3);
        assert_eq!(kinds.iter().filter(|k| **k == "turn_completed").count(), 3);
    }

    #[test]
    fn latency_matches_orchestrator_stage_names() {
        let summary = run_scripted_call(&mut backend(1), 10, u64::MAX).unwrap();
        // dialing 0 + ringing 400 + answered 800 = 1200 ms before first turn
        assert_eq!(summary.events[2].at_ms, 1200);
        // one turn adds 50+200+100+300+150+20 = 820 ms
        assert_eq!(summary.events[3].at_ms, 1200 + 820);
        assert_eq!(summary.mean_turn_latency_ms, 820.0);
    }

    #[test]
    fn turn_cap_limits_dialog_cleanly() {
        // Cap allows turn 1 (1200 + 820 stage + 1000 speak) but not turn 2.
        let summary = run_scripted_call(&mut backend(5), 10, 1200 + 820 + 1000 + 10).unwrap();
        assert_eq!(summary.turns, 1);
        assert_eq!(summary.events.last().unwrap().kind, "ended");
    }

    #[test]
    fn empty_dialog_is_reported_honestly() {
        let err = run_scripted_call(&mut backend(0), 10, u64::MAX).unwrap_err();
        assert_eq!(err, BridgeError::EmptyDialog);
    }

    #[test]
    fn max_turns_bounds_runaway_dialog() {
        struct Endless;
        impl DialogBackend for Endless {
            fn respond(&mut self, _turn_index: u32) -> Option<Utterance> {
                Some(Utterance { text: "hi".into(), latency: TurnLatency::default(), speak_ms: 0 })
            }
        }
        let summary = run_scripted_call(&mut Endless, 4, u64::MAX).unwrap();
        assert_eq!(summary.turns, 4);
    }
}
