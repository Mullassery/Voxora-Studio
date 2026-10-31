//! Turn-taking state machine for a duplex voice conversation.
//!
//! The machine has four states and moves deterministically in response to
//! explicit signals (scripted, or later derived from real VAD output):
//!
//! ```text
//!   Idle -> UserSpeaking -> AgentThinking -> AgentSpeaking -> (loop | Ended)
//! ```
//!
//! End-of-turn signals advance the machine; an `end` signal terminates the
//! conversation from any state. This is intentionally pure logic with no
//! audio dependency so the state semantics can be tested exhaustively.

use std::time::Duration;

/// One participant turn signal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Signal {
    /// The user started speaking (barge-in cancels agent audio).
    UserSpeechStart,
    /// The user stopped speaking and a turn boundary was detected.
    UserEndOfTurn,
    /// The agent began streaming a reply.
    AgentSpeechStart,
    /// The agent finished streaming its reply.
    AgentSpeechEnd,
    /// The conversation should terminate.
    End,
}

/// Conversation state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Idle,
    UserSpeaking,
    AgentThinking,
    AgentSpeaking,
    Ended,
}

/// Error raised on a signal that is not valid for the current state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidSignal {
    pub state: State,
    pub signal: Signal,
}

/// Turn-taking machine. `turns_completed` counts full user+agent exchanges.
#[derive(Debug, Clone)]
pub struct TurnMachine {
    state: State,
    pub turns_completed: u32,
    /// Budget for barge-in protection: agent audio younger than this may
    /// not be interrupted (echo of the user's own channel).
    pub barge_in_guard: Duration,
    agent_speech_elapsed: Duration,
}

impl TurnMachine {
    pub fn new() -> Self {
        Self {
            state: State::Idle,
            turns_completed: 0,
            barge_in_guard: Duration::from_millis(300),
            agent_speech_elapsed: Duration::ZERO,
        }
    }

    pub fn state(&self) -> State {
        self.state
    }

    /// Apply one signal, returning the next state.
    pub fn apply(&mut self, signal: Signal) -> Result<State, InvalidSignal> {
        use Signal::*;
        use State::*;
        let next = match (self.state, signal) {
            (Idle, UserSpeechStart) => UserSpeaking,
            (Idle, AgentSpeechStart) => AgentSpeaking,
            (Idle, End) => Ended,
            (UserSpeaking, UserEndOfTurn) => AgentThinking,
            (UserSpeaking, End) => Ended,
            (AgentThinking, AgentSpeechStart) => AgentSpeaking,
            (AgentThinking, End) => Ended,
            (AgentSpeaking, AgentSpeechEnd) if self.agent_speech_elapsed < self.barge_in_guard => {
                AgentSpeaking
            }
            (AgentSpeaking, AgentSpeechEnd) => {
                self.turns_completed += 1;
                Idle
            }
            (AgentSpeaking, UserSpeechStart) if self.agent_speech_elapsed >= self.barge_in_guard => {
                UserSpeaking
            }
            (AgentSpeaking, End) => Ended,
            (Ended, End) => Ended,
            _ => return Err(InvalidSignal { state: self.state, signal }),
        };
        self.state = next;
        Ok(next)
    }

    /// Advance simulated playback time for the agent's current utterance.
    /// Used to model the barge-in guard via `elapsed`.
    pub fn tick_agent_speech(&mut self, elapsed: Duration) {
        if self.state == State::AgentSpeaking {
            self.agent_speech_elapsed += elapsed;
        }
    }
}

impl Default for TurnMachine {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use State::*;

    fn round_trip(machine: &mut TurnMachine) {
        machine.tick_agent_speech(Duration::from_secs(1));
        assert_eq!(machine.apply(Signal::AgentSpeechStart), Ok(AgentSpeaking));
        machine.tick_agent_speech(Duration::from_secs(1));
        assert_eq!(machine.apply(Signal::AgentSpeechEnd), Ok(Idle));
        assert_eq!(machine.turns_completed, 1);
    }

    #[test]
    fn full_conversation_lifecycle() {
        let mut m = TurnMachine::new();
        assert_eq!(m.state(), Idle);
        assert_eq!(m.apply(Signal::UserSpeechStart), Ok(UserSpeaking));
        assert_eq!(m.apply(Signal::UserEndOfTurn), Ok(AgentThinking));
        round_trip(&mut m);
        assert_eq!(m.apply(Signal::End), Ok(Ended));
        // Ended is terminal and idempotent.
        assert_eq!(m.apply(Signal::End), Ok(Ended));
    }

    #[test]
    fn barge_in_guard_blocks_early_interrupt() {
        let mut m = TurnMachine::new();
        let _ = m.apply(Signal::UserSpeechStart);
        let _ = m.apply(Signal::UserEndOfTurn);
        let _ = m.apply(Signal::AgentSpeechStart);
        // Guard elapsed time below threshold: user speech is ignored.
        m.tick_agent_speech(Duration::from_millis(100));
        let err = m.apply(Signal::UserSpeechStart).unwrap_err();
        assert_eq!(err.state, AgentSpeaking);
        assert_eq!(m.state(), AgentSpeaking);
    }

    #[test]
    fn barge_in_allowed_after_guard() {
        let mut m = TurnMachine::new();
        let _ = m.apply(Signal::UserSpeechStart);
        let _ = m.apply(Signal::UserEndOfTurn);
        let _ = m.apply(Signal::AgentSpeechStart);
        m.tick_agent_speech(m.barge_in_guard);
        assert_eq!(m.apply(Signal::UserSpeechStart), Ok(UserSpeaking));
    }

    #[test]
    fn invalid_transitions_rejected() {
        let mut m = TurnMachine::new();
        // Cannot end-of-turn before anyone speaks.
        let err = m.apply(Signal::UserEndOfTurn).unwrap_err();
        assert_eq!(err.state, Idle);
        // Cannot complete agent speech while thinking.
        let _ = m.apply(Signal::UserSpeechStart);
        let _ = m.apply(Signal::UserEndOfTurn);
        assert!(m.apply(Signal::AgentSpeechEnd).is_err());
    }

    #[test]
    fn many_turns_counted() {
        let mut m = TurnMachine::new();
        for _ in 0..7 {
            let _ = m.apply(Signal::UserSpeechStart).unwrap();
            let _ = m.apply(Signal::UserEndOfTurn).unwrap();
            let _ = m.apply(Signal::AgentSpeechStart).unwrap();
            m.tick_agent_speech(Duration::from_secs(2));
            let _ = m.apply(Signal::AgentSpeechEnd).unwrap();
        }
        assert_eq!(m.turns_completed, 7);
        assert_eq!(m.state(), Idle);
    }
}
