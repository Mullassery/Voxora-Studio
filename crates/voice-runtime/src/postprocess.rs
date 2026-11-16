//! Offline asset post-processing ops (roadmap M10 Phase C): normalize and
//! Voice Boost profile enhancement, dispatched from a JSON ops envelope.
//!
//! Trim-silence and channel conversion stay Python-side (stdlib `wave`
//! scanning/mixing); anything else arriving Rust-side is an error — the
//! listed-order contract lives in the orchestrator's `variants.py`, which
//! calls this seam once per Rust op so Python and Rust ops interleave exactly.
//!
//! voice_boost contract (builder-check calls 2–4, binding):
//! - The stage is 16 kHz-hardwired; non-16k input is transparently
//!   round-tripped through the real resampler (recorded in diagnostics).
//! - Mono only: the Rust chain is mono — stereo input is refused.
//! - Op-only resolution: `settings_json` comes from the op itself
//!   (`{"enabled": true, "profile": ..., "overrides": ...}`), never from
//!   tenant stored settings.

use crate::dsp::{count_clipped, config_from_settings_json, VoiceBoostStage, ENGINE_RATE};
use crate::resample::resample_i16;
use serde_json::Value;

/// Peak-normalize to `target_dbfs` with a pure gain, clip-guarded: if the
/// target gain would create new clipping, the gain is REDUCED to the largest
/// safe value (diagnostics report the actual gain and `target_reached`).
/// Never a limiter — normalize is a pure gain by design.
fn normalize_op(samples: &[i16], target_dbfs: f32) -> (Vec<i16>, Value) {
    let clipped_before = count_clipped(
        &samples.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
    );
    let peak = samples
        .iter()
        .fold(0.0f32, |m, &s| m.max((s as f32).abs())) / 32768.0;
    if peak <= f32::EPSILON {
        return (
            samples.to_vec(),
            serde_json::json!({
                "op": "normalize",
                "gain_db": 0.0,
                "target_reached": false,
                "clipped_before": clipped_before,
                "clipped_after": clipped_before,
                "reason": "silent input",
            }),
        );
    }
    let wanted_gain = 10f32.powf(target_dbfs / 20.0) / peak;
    let wanted_gain_db = 20.0 * wanted_gain.log10();
    let mut applied_gain = wanted_gain;
    let mut target_reached = true;
    // Clip guard: linear search down from the wanted gain for the largest
    // gain whose post-gain clip count does not exceed the input's.
    if count_clipped(&scale_to_f32(samples, wanted_gain)) > clipped_before {
        target_reached = false;
        let mut lo = 0.0f32;
        let mut hi = wanted_gain;
        for _ in 0..24 {
            let mid = 0.5 * (lo + hi);
            if count_clipped(&scale_to_f32(samples, mid)) > clipped_before {
                hi = mid;
            } else {
                lo = mid;
            }
        }
        applied_gain = lo;
    }
    let out: Vec<i16> = scale_to_i16(samples, applied_gain);
    let clipped_after = count_clipped(
        &out.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
    );
    (
        out,
        serde_json::json!({
            "op": "normalize",
            "gain_db": 20.0 * applied_gain.log10(),
            "target_reached": target_reached,
            "wanted_gain_db": wanted_gain_db,
            "clipped_before": clipped_before,
            "clipped_after": clipped_after,
        }),
    )
}

fn scale_to_f32(samples: &[i16], gain: f32) -> Vec<f32> {
    samples.iter().map(|&s| s as f32 / 32768.0 * gain).collect()
}

fn scale_to_i16(samples: &[i16], gain: f32) -> Vec<i16> {
    samples
        .iter()
        .map(|&s| {
            let v = s as f32 * gain;
            v.round().clamp(-32768.0, 32767.0) as i16
        })
        .collect()
}

/// Voice Boost enhancement at the stage's engine rate, transparently
/// round-tripping non-16k input through the real resampler.
fn voice_boost_op(samples: &[i16], rate: u32, settings_json: &str) -> Result<(Vec<i16>, Value), String> {
    if rate == 0 {
        return Err("invalid rate".to_string());
    }
    let config = config_from_settings_json(settings_json)?;
    let roundtrip = rate != ENGINE_RATE;
    let at_engine_rate: Vec<i16>;
    let out: Vec<i16>;
    if roundtrip {
        let up = resample_i16(samples, rate, ENGINE_RATE);
        let mut stage = VoiceBoostStage::new(config);
        let mut processed = Vec::new();
        for chunk in up.chunks(1600) {
            processed.extend_from_slice(&stage.process(chunk).pcm);
        }
        processed.extend_from_slice(&stage.finish());
        at_engine_rate = processed;
        out = resample_i16(&at_engine_rate, ENGINE_RATE, rate);
    } else {
        let mut stage = VoiceBoostStage::new(config);
        let mut processed = Vec::new();
        for chunk in samples.chunks(1600) {
            processed.extend_from_slice(&stage.process(chunk).pcm);
        }
        processed.extend_from_slice(&stage.finish());
        out = processed;
    }
    let clipped_after = count_clipped(
        &out.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
    );
    Ok((
        out,
        serde_json::json!({
            "op": "voice_boost",
            "internal_rate_roundtrip": roundtrip,
            "engine_rate": ENGINE_RATE,
            "asset_rate": rate,
            "clipped_after": clipped_after,
        }),
    ))
}

/// Dispatch the ops envelope `{"rate": u32, "ops": [...]}` over the samples,
/// applying ops in listed order. Diagnostics carry one record per op.
pub fn audio_postprocess_impl(
    samples: &[i16],
    rate: u32,
    ops_json: &str,
) -> Result<(Vec<i16>, Value), String> {
    let envelope: Value = serde_json::from_str(ops_json)
        .map_err(|e| format!("invalid ops envelope: {e}"))?;
    let rate = envelope
        .get("rate")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32)
        .unwrap_or(rate);
    if rate == 0 {
        return Err("invalid rate: 0".to_string());
    }
    let ops = envelope
        .get("ops")
        .and_then(|v| v.as_array())
        .ok_or("ops envelope missing an ops array")?;
    let mut current = samples.to_vec();
    let mut records: Vec<Value> = Vec::new();
    let mut roundtrip_any = false;
    for op in ops {
        let kind = op
            .get("type")
            .and_then(|v| v.as_str())
            .ok_or("op missing a type")?;
        match kind {
            "normalize" => {
                let target = op
                    .get("target_dbfs")
                    .and_then(|v| v.as_f64())
                    .map(|v| v as f32)
                    .unwrap_or(-16.0);
                let (next, record) = normalize_op(&current, target);
                current = next;
                records.push(record);
            }
            "voice_boost" => {
                let settings_json = op
                    .get("settings_json")
                    .and_then(|v| v.as_str())
                    .ok_or("voice_boost op missing settings_json")?;
                let (next, record) = voice_boost_op(&current, rate, settings_json)?;
                current = next;
                if record
                    .get("internal_rate_roundtrip")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false)
                {
                    roundtrip_any = true;
                }
                records.push(record);
            }
            other => {
                return Err(format!(
                    "unsupported op type: {other} (trim_silence/channels are Python-side)"
                ))
            }
        }
    }
    let clipped_after = count_clipped(
        &current.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
    );
    Ok((
        current,
        serde_json::json!({
            "ops": records,
            "clipped_after": clipped_after,
            "internal_rate_roundtrip": roundtrip_any,
        }),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine_i16(rate: u32, seconds: f64, freq: f64, amp: f64) -> Vec<i16> {
        let n = (rate as f64 * seconds) as usize;
        (0..n)
            .map(|i| {
                (amp * 32768.0
                    * (2.0 * std::f64::consts::PI * freq * i as f64 / rate as f64).sin())
                    .round() as i16
            })
            .collect()
    }

    fn peak_i16(samples: &[i16]) -> i16 {
        samples.iter().fold(0i16, |m, &s| m.max(s.abs()))
    }

    fn envelope(rate: u32, ops: Value) -> String {
        serde_json::json!({ "rate": rate, "ops": ops }).to_string()
    }

    #[test]
    fn unknown_op_type_is_rejected() {
        let result = audio_postprocess_impl(
            &sine_i16(16000, 0.1, 440.0, 0.5),
            16000,
            &envelope(16000, serde_json::json!([{ "type": "widen" }])),
        );
        let err = result.err().expect("should error");
        assert!(err.contains("unsupported op type"), "{err}");
        assert!(err.contains("Python-side"), "{err}");
    }

    #[test]
    fn normalize_raises_quiet_signal_to_target_without_new_clipping() {
        let input = sine_i16(16000, 0.5, 440.0, 0.02); // ~-34 dBFS peak
        let (out, diag) = audio_postprocess_impl(
            &input,
            16000,
            &envelope(16000, serde_json::json!([{ "type": "normalize", "target_dbfs": -16.0 }])),
        )
        .expect("normalize should succeed");
        let out_peak = peak_i16(&out) as f32 / 32768.0;
        let target_peak = 10f32.powf(-16.0 / 20.0);
        assert!(
            (out_peak - target_peak).abs() < 0.05 * target_peak,
            "peak {out_peak} vs target {target_peak}"
        );
        let record = &diag["ops"][0];
        assert_eq!(record["target_reached"], serde_json::json!(true));
        assert_eq!(diag["clipped_after"], serde_json::json!(0));
    }

    #[test]
    fn normalize_reduces_gain_when_target_would_clip() {
        // The API envelope caps target_dbfs at -6 (peak 0.501 — a pure gain
        // can never clip there). The guard exists for direct Rust use with
        // louder targets, so exercise normalize_op directly at +2 dBFS.
        let input = sine_i16(16000, 0.5, 440.0, 0.9);
        let clipped_before = count_clipped(
            &input.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
        );
        let (out, record) = normalize_op(&input, 2.0);
        assert_eq!(record["target_reached"], serde_json::json!(false));
        assert!(
            record["gain_db"].as_f64().unwrap()
                < record["wanted_gain_db"].as_f64().unwrap(),
            "applied gain must be reduced below the wanted gain"
        );
        assert!(
            record["clipped_after"].as_u64().unwrap() <= u64::from(clipped_before),
            "no new clipping"
        );
        assert!(!out.is_empty());
    }

    #[test]
    fn normalize_within_api_range_never_clips() {
        let input = sine_i16(16000, 0.5, 440.0, 0.05);
        let (_, record) = normalize_op(&input, -6.0);
        assert_eq!(record["clipped_after"], serde_json::json!(0));
        assert_eq!(record["target_reached"], serde_json::json!(true));
    }

    #[test]
    fn voice_boost_at_24k_round_trips_and_preserves_duration() {
        let input = sine_i16(24000, 0.5, 440.0, 0.3);
        let (out, diag) = audio_postprocess_impl(
            &input,
            24000,
            &envelope(
                24000,
                serde_json::json!([{
                    "type": "voice_boost",
                    "settings_json": r#"{"enabled": true, "profile": "clear", "overrides": {}}"#,
                }]),
            ),
        )
        .expect("voice_boost should succeed");
        assert_eq!(diag["internal_rate_roundtrip"], serde_json::json!(true));
        let ratio = out.len() as f64 / input.len() as f64;
        assert!(
            (ratio - 1.0).abs() < 0.01,
            "duration must survive: ratio {ratio}"
        );
        assert_ne!(out, input, "processing must change the audio");
    }

    #[test]
    fn voice_boost_rejects_invalid_settings_like_m9() {
        let result = audio_postprocess_impl(
            &sine_i16(16000, 0.1, 440.0, 0.3),
            16000,
            &envelope(
                16000,
                serde_json::json!([{
                    "type": "voice_boost",
                    "settings_json": r#"{"enabled": true, "profile": "magic", "overrides": {}}"#,
                }]),
            ),
        );
        assert!(result.is_err(), "unknown profile must be rejected");
    }

    #[test]
    fn empty_ops_returns_input_with_diagnostics() {
        let input = sine_i16(16000, 0.1, 440.0, 0.3);
        let (out, diag) = audio_postprocess_impl(&input, 16000, &envelope(16000, serde_json::json!([])))
            .expect("empty ops should succeed");
        assert_eq!(out, input);
        let expected_clipped = count_clipped(
            &input.iter().map(|&s| s as f32 / 32768.0).collect::<Vec<_>>(),
        );
        assert_eq!(diag["clipped_after"], serde_json::json!(expected_clipped));
        assert_eq!(diag["ops"], serde_json::json!([]));
    }

    #[test]
    fn ops_apply_in_listed_order() {
        let input = sine_i16(16000, 0.5, 440.0, 0.05);
        let ops = serde_json::json!([
            { "type": "normalize", "target_dbfs": -12.0 },
            { "type": "voice_boost",
              "settings_json": r#"{"enabled": true, "profile": "natural", "overrides": {}}"# },
        ]);
        let (out, diag) = audio_postprocess_impl(&input, 16000, &envelope(16000, ops))
            .expect("chained ops should succeed");
        assert_eq!(diag["ops"].as_array().unwrap().len(), 2);
        assert!(!out.is_empty());
    }

    #[test]
    fn rate_zero_is_rejected() {
        let result =
            audio_postprocess_impl(&sine_i16(16000, 0.1, 440.0, 0.3), 16000, &envelope(0, serde_json::json!([])));
        assert!(result.err().unwrap().contains("rate"));
    }
}
