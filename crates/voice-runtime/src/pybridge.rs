//! PyO3 seam: the Rust voice bridge exposed to the Python orchestrator.
//!
//! Built into the `voxora_voice_runtime` extension module by maturin with
//! `--features pyo3-bindings` (see `pyproject.toml` and the CI step). Kept
//! behind a feature flag so plain `cargo build/test` never links
//! `extension-module`.
//!
//! The boundary is a string (canonical JSON). The Python side
//! (`voxora.telephony.voice_runtime`) owns parsing and mapping onto
//! `TelephonyResult`; the bridge error vocabulary surfaces as Python
//! `ValueError` messages (`EmptyDialog`, `DialogNeverEnded`).

#[cfg(feature = "pyo3-bindings")]
mod _py {

    use crate::bridge::{run_with_timing, DialogBackend, TurnLatency, Utterance};
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;

    #[derive(serde::Deserialize, Debug)]
    pub struct ScriptTurn {
        pub text: String,
        #[serde(default)]
        #[serde(rename = "latencyMs")]
        pub latency_ms: ScriptLatency,
        #[serde(default)]
        #[serde(rename = "speakMs")]
        pub speak_ms: u64,
    }

    #[derive(serde::Deserialize, Debug, Default)]
    #[serde(rename_all = "camelCase")]
    pub struct ScriptLatency {
        #[serde(default)]
        pub audio_received: f64,
        #[serde(default)]
        pub speech_endpoint: f64,
        #[serde(default)]
        pub stt_result: f64,
        #[serde(default)]
        pub agent_response: f64,
        #[serde(default)]
        pub tts_first_audio: f64,
        #[serde(default)]
        pub first_audio_transmitted: f64,
    }

    impl From<&ScriptLatency> for TurnLatency {
        fn from(value: &ScriptLatency) -> Self {
            TurnLatency {
                audio_received: value.audio_received,
                speech_endpoint: value.speech_endpoint,
                stt_result: value.stt_result,
                agent_response: value.agent_response,
                tts_first_audio: value.tts_first_audio,
                first_audio_transmitted: value.first_audio_transmitted,
            }
        }
    }

    #[derive(serde::Deserialize, Debug)]
    pub struct CallScript {
        #[serde(default)]
        pub turns: Vec<ScriptTurn>,
        #[serde(default)]
        pub turn_cap_ms: Option<u64>,
    }

    struct JsonScriptBackend {
        turns: Vec<ScriptTurn>,
        cursor: usize,
    }

    impl DialogBackend for JsonScriptBackend {
        fn respond(&mut self, turn_index: u32) -> Option<Utterance> {
            let index = turn_index as usize;
            if index >= self.turns.len() {
                return None;
            }
            let turn = &self.turns[index];
            self.cursor += 1;
            Some(Utterance {
                text: turn.text.clone(),
                latency: (&turn.latency_ms).into(),
                speak_ms: turn.speak_ms,
            })
        }
    }

    /// Serialize summary + per-turn timing into the Python-facing shape:
    /// `{"schemaVersion": 1,
    ///   "summary": {"totalMs", "turns", "meanTurnLatencyMs", "events":
    ///               [{"atMs", "kind", "detail"}, …]},
    ///   "turns": [{"index", "text", "latencyMs": {…}, "speakMs", "startedAtMs"}]}`.
    pub fn to_python_json(
        summary: &crate::bridge::BridgeSummary,
        timings: &[crate::bridge::TurnTiming],
    ) -> String {
        let body = serde_json::json!({
            "schemaVersion": crate::bridge::SUMMARY_JSON_VERSION,
            "summary": {
                "totalMs": summary.total_ms,
                "turns": summary.turns,
                "meanTurnLatencyMs": summary.mean_turn_latency_ms,
                "events": summary.events,
            },
            "turns": timings,
        });
        serde_json::to_string(&body).expect("structured summary serializes")
    }

    #[pyfunction]
    #[pyo3(signature = (script_json))]
    fn run_scripted_call_json(script_json: &str) -> PyResult<String> {
        let script: CallScript = serde_json::from_str(script_json)
            .map_err(|e| PyValueError::new_err(format!("invalid call script: {e}")))?;
        let mut backend =
            JsonScriptBackend { turns: script.turns, cursor: 0 };
        let turn_cap = script.turn_cap_ms.unwrap_or(u64::MAX);
        let result = run_with_timing(&mut backend, 16, turn_cap).map_err(|e| {
            PyValueError::new_err(match e {
                crate::bridge::BridgeError::EmptyDialog => {
                    "EmptyDialog: scripted dialog has no first turn".to_string()
                }
                crate::bridge::BridgeError::DialogNeverEnded => {
                    "DialogNeverEnded: dialog produced no completed turn".to_string()
                }
            })
        })?;
        Ok(to_python_json(&result.0, &result.1))
    }

    /* ---------------------- turn detection engine ---------------------- */

    /// Live turn-detection seam: one Rust `TurnDetectionController` per
    /// Python-owned call. The caller keeps its own monotonic `seq` and
    /// transport `timestampMs`; every feed returns the serialized event
    /// batch (JSON array) produced by that call.
    #[pyclass]
    struct TurnDetectionEngine {
        controller: crate::controller::TurnDetectionController,
    }

    fn profile_from_str(profile: &str) -> PyResult<crate::endpointing::EndpointProfile> {
        match profile.trim().to_lowercase().as_str() {
            "fast" => Ok(crate::endpointing::EndpointProfile::Fast),
            "normal" => Ok(crate::endpointing::EndpointProfile::Normal),
            "deliberate" => Ok(crate::endpointing::EndpointProfile::Deliberate),
            other => Err(PyValueError::new_err(format!(
                "unknown endpoint profile: {other} (fast|normal|deliberate)"
            ))),
        }
    }

    fn events_to_json(events: &[crate::td_events::TurnEvent]) -> String {
        serde_json::to_string(events).expect("turn events serialize")
    }

    #[pymethods]
    impl TurnDetectionEngine {
        /// Build the engine for one call. `profile` is fast|normal|
        /// deliberate; `semantic` toggles the heuristic semantic evaluator;
        /// `word_gate_min_words` is the confirmed interruption word gate.
        #[new]
        #[pyo3(signature = (call_id, profile="normal", semantic=true, word_gate_min_words=3))]
        fn new(
            call_id: &str,
            profile: &str,
            semantic: bool,
            word_gate_min_words: u32,
        ) -> PyResult<Self> {
            let mut cfg =
                crate::controller::ControllerConfig::from_profile(call_id, profile_from_str(profile)?);
            cfg.semantic_enabled = semantic;
            cfg.word_gate_min_words = word_gate_min_words.max(1);
            Ok(Self {
                controller: crate::controller::TurnDetectionController::new(call_id, cfg),
            })
        }

        /// Feed one frame of user/agent audio evidence. `agentPlaying`,
        /// `ttsElapsedMs` and `sinceAgentAudioMs` describe playback from the
        /// transport; for user-channel frames pass agent=false.
        #[pyo3(signature = (seq, timestamp_ms, rms, agent_playing=false, tts_elapsed_ms=0, since_agent_audio_ms=0))]
        fn feed_audio(
            &mut self,
            seq: u64,
            timestamp_ms: u64,
            rms: f64,
            agent_playing: bool,
            tts_elapsed_ms: u64,
            since_agent_audio_ms: u64,
        ) -> String {
            let events = self.controller.feed_frame(
                seq,
                timestamp_ms,
                rms as f32,
                agent_playing,
                tts_elapsed_ms,
                since_agent_audio_ms,
            );
            events_to_json(&events)
        }

        /// Feed an STT transcript snapshot (`text` is the cumulative
        /// partial, or the finalized utterance when `is_final`).
        #[pyo3(signature = (seq, timestamp_ms, text, is_final=false))]
        fn feed_transcript(&mut self, seq: u64, timestamp_ms: u64, text: &str, is_final: bool) -> String {
            let events = self
                .controller
                .feed_lexical(seq, timestamp_ms, text, is_final);
            events_to_json(&events)
        }

        /// Feed an external semantic verdict (`verdict` is
        /// incomplete|complete|question|correction).
        fn feed_semantic(
            &mut self,
            seq: u64,
            timestamp_ms: u64,
            verdict: &str,
            confidence: f64,
        ) -> PyResult<String> {
            let report = crate::controller::SemanticReport {
                verdict: parse_verdict(verdict)?,
                confidence: confidence.clamp(0.0, 1.0) as f32,
            };
            let events = self
                .controller
                .feed_semantic(seq, timestamp_ms, report);
            Ok(events_to_json(&events))
        }

        /// Feed a language-ID routing result; `switch` marks a language
        /// change inside the open turn (grants the code-switch grace).
        #[pyo3(signature = (seq, timestamp_ms, language, switch=false))]
        fn feed_lid(
            &mut self,
            seq: u64,
            timestamp_ms: u64,
            language: &str,
            switch: bool,
        ) -> String {
            let events = self.controller.feed_lid(seq, timestamp_ms, language, switch);
            events_to_json(&events)
        }

        /// Feed one agent pipeline event: `kind` is thinking_started |
        /// tts_started | tts_ended; `generation` is the TTS epoch.
        fn feed_agent_event(
            &mut self,
            timestamp_ms: u64,
            kind: &str,
            generation: u64,
        ) -> PyResult<String> {
            let event = match kind.trim().to_lowercase().as_str() {
                "thinking_started" => crate::controller::AgentEvent::ThinkingStarted,
                "tts_started" => crate::controller::AgentEvent::TtsStarted { generation },
                "tts_ended" => crate::controller::AgentEvent::TtsEnded { generation },
                other => {
                    return Err(PyValueError::new_err(format!(
                        "unknown agent event kind: {other} \
                         (thinking_started|tts_started|tts_ended)"
                    )))
                }
            };
            let events = self.controller.feed_agent(timestamp_ms, event);
            Ok(events_to_json(&events))
        }

        /// Signal a provider failure (stt/vad down): the controller resiles
        /// into degraded acoustic-only mode while the hatch stays armed.
        fn note_provider_failure(&mut self, timestamp_ms: u64, hint: &str) -> String {
            let events = self
                .controller
                .note_provider_failure(timestamp_ms, hint);
            events_to_json(&events)
        }

        /// Signal provider recovery; re-arms full detection state.
        fn note_provider_recovered(&mut self, timestamp_ms: u64) -> String {
            let events = self.controller.note_provider_recovered(timestamp_ms);
            events_to_json(&events)
        }

        /// End the call: terminal state, nothing else is accepted.
        fn end_call(&mut self, timestamp_ms: u64) -> String {
            events_to_json(&self.controller.end_call(timestamp_ms))
        }

        /// Diagnostic snapshot (JSON object): counters, state, partial.
        fn snapshot(&self) -> String {
            let snap = self.controller.snapshot();
            serde_json::to_string(&serde_json::json!({
                "state": snap.state,
                "turnId": snap.turn_id,
                "generation": snap.generation,
                "rejectedInputs": snap.rejected_inputs,
                "staleAgentEvents": snap.stale_agent_events,
                "reorderedInputs": snap.reordered_inputs,
                "turnsCompleted": snap.turns_completed,
                "interruptions": snap.interruptions,
                "providerFailures": snap.provider_failures,
                "language": snap.language,
                "semanticEnabled": snap.semantic_enabled,
                "lastPartial": snap.last_partial,
            }))
            .expect("snapshot serializes")
        }

        /// Current state name (convenience for fast Python branching).
        fn state(&self) -> String {
            let name = serde_json::to_string(&self.controller.state())
                .expect("state serializes");
            name.trim_matches('"').to_string()
        }

        /// Current turn ordinal.
        fn turn_id(&self) -> u64 {
            self.controller.turn_id()
        }

        /// Current pipeline generation (bumped on interruption).
        fn generation(&self) -> u64 {
            self.controller.generation()
        }
    }

    fn parse_verdict(verdict: &str) -> PyResult<crate::td_events::SemanticVerdict> {
        match verdict.trim().to_lowercase().as_str() {
            "incomplete" => Ok(crate::td_events::SemanticVerdict::Incomplete),
            "complete" => Ok(crate::td_events::SemanticVerdict::Complete),
            "question" => Ok(crate::td_events::SemanticVerdict::Question),
            "correction" => Ok(crate::td_events::SemanticVerdict::Correction),
            other => Err(PyValueError::new_err(format!(
                "unknown semantic verdict: {other} \
                 (incomplete|complete|question|correction)"
            ))),
        }
    }

    #[pymodule]
    #[pyo3(name = "voxora_voice_runtime")]
    fn voxora_voice_runtime(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(run_scripted_call_json, m)?)?;
        m.add_class::<TurnDetectionEngine>()?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn script_backend_drives_bridge_from_json() {
            let script_value = serde_json::json!({
                "turns": [
                    {"text": "hello", "latencyMs": {"audioReceived": 50.0,
                     "speechEndpoint": 200.0, "sttResult": 100.0,
                     "agentResponse": 300.0, "ttsFirstAudio": 150.0,
                     "firstAudioTransmitted": 20.0}, "speakMs": 1000}
                ]
            });
            let script: CallScript =
                serde_json::from_value(script_value).expect("valid scripted call JSON");
            assert_eq!(script.turns.len(), 1);
            let mut backend = JsonScriptBackend { turns: script.turns, cursor: 0 };
            let (summary, timings) =
                run_with_timing(&mut backend, 16, u64::MAX).expect("bridged call");
            assert_eq!(summary.turns, 1);
            assert_eq!(timings[0].text, "hello");
        }

        #[test]
        fn python_json_shape_is_canonical() {
            let script_value = serde_json::json!({
                "turns": [
                    {"text": "one", "latencyMs": {"audioReceived": 10.0,
                     "speechEndpoint": 20.0, "sttResult": 30.0,
                     "agentResponse": 40.0, "ttsFirstAudio": 50.0,
                     "firstAudioTransmitted": 60.0}, "speakMs": 500}
                ]
            });
            let script: CallScript = serde_json::from_value(script_value).unwrap();
            let mut backend = JsonScriptBackend { turns: script.turns, cursor: 0 };
            let (summary, timings) =
                run_with_timing(&mut backend, 16, u64::MAX).unwrap();
            let parsed: serde_json::Value =
                serde_json::from_str(&to_python_json(&summary, &timings)).unwrap();
            assert_eq!(parsed["schemaVersion"], 1);
            let events = parsed["summary"]["events"].as_array().unwrap();
            assert_eq!(events[0]["kind"], "dialing");
            assert!(parsed["turns"][0]["latency"]["audioReceived"].is_number());
            // Stage values must survive deserialization (not silently default to 0).
            assert_eq!(parsed["turns"][0]["latency"]["audioReceived"], 10.0);
            assert_eq!(parsed["summary"]["meanTurnLatencyMs"], 10.0 + 20.0 + 30.0 + 40.0 + 50.0 + 60.0);
            assert!(parsed["turns"][0]["startedAtMs"].is_number());
        }

        #[test]
        fn empty_script_maps_to_bridge_error() {
            let script_value = serde_json::json!({"turns": []});
            let script: CallScript = serde_json::from_value(script_value).unwrap();
            let mut backend = JsonScriptBackend { turns: script.turns, cursor: 0 };
            let result = run_with_timing(&mut backend, 16, u64::MAX);
            assert_eq!(result.unwrap_err(), crate::bridge::BridgeError::EmptyDialog);
        }
    }
}