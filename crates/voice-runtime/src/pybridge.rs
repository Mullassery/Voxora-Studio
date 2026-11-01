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

use crate::bridge::{run_with_timing, DialogBackend, TurnLatency, Utterance};

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

    #[pymodule]
    #[pyo3(name = "voxora_voice_runtime")]
    fn voxora_voice_runtime(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(run_scripted_call_json, m)?)?;
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