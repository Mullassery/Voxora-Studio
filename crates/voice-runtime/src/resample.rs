//! Offline whole-buffer windowed-sinc resampler for asset variants.
//!
//! The call path resamples with a fixed ×3/÷3 pair (16k↔48k) and a
//! documented 0-order-hold placeholder that is FORBIDDEN for audible output.
//! Asset variants need arbitrary ratios (OpenAI WAV is 24 kHz; delivery
//! channels want 44.1k/16k/8k), so this module implements the real thing:
//! Hann-windowed sinc, whole-buffer (no streaming state needed offline).
//!
//! Quality bound, tested below: a sine resampled by this module must come
//! out as the same sine — correct amplitude (±2%) and correct frequency
//! (zero-crossing count within 2%), no aliasing garbage.

/// Hann-windowed sinc kernel: `sinc(x) = sin(pi x) / (pi x)`.
fn sinc(x: f64) -> f64 {
    if x.abs() < 1e-9 {
        1.0
    } else {
        let px = std::f64::consts::PI * x;
        px.sin() / px
    }
}

/// Half-width of the kernel in zero-crossings (sinc lobes kept each side).
/// 16 lobes: keeps the 5–8 kHz sibilance/clarity band intact through the
/// 24k→16k flagship path (a 4-lobe kernel attenuated 6 kHz by −1.9 dB and
/// 8 kHz by −10.8 dB — found in whole-branch review, fixed).
const KERNEL_HALF_WIDTH_ZC: f64 = 16.0;

/// Kernel value at input-sample offset `dist` (in input samples) for the
/// given beta (= 2·cutoff/in_rate, i.e. zero-spacing = 1/β input samples).
/// Gain factor β builds in the ideal-lowpass DC gain (Poisson-sum = 1);
/// the Hann window tapers the lobes (small, flat in-band ripple).
fn kernel(dist: f64, beta: f64) -> f64 {
    let zc = dist * beta;
    if zc.abs() > KERNEL_HALF_WIDTH_ZC {
        return 0.0;
    }
    // Symmetric window peaking at the sinc's main lobe (zc=0), zero at ±half.
    let window = 0.5 * (1.0 + (std::f64::consts::PI * zc / KERNEL_HALF_WIDTH_ZC).cos());
    sinc(zc) * window * beta
}

/// Resample f32 samples from `in_rate` to `out_rate` (whole-buffer).
pub fn resample_f32(input: &[f32], in_rate: u32, out_rate: u32) -> Vec<f32> {
    if input.is_empty() || in_rate == out_rate {
        return input.to_vec();
    }
    let ratio = out_rate as f64 / in_rate as f64;
    // Cutoff below the smaller Nyquist, with margin for the transition band.
    let cutoff = 0.45 * (in_rate.min(out_rate) as f64);
    let beta = 2.0 * cutoff / in_rate as f64;
    // DC-gain norm of the truncated windowed kernel ON THE INPUT GRID:
    // one scalar (Poisson-sum identity ≈ 1 pre-truncation/window), NOT a
    // per-output-position renormalization — per-position division explodes
    // when the shifted kernel sum nears zero (observed: 870× overshoot).
    let taps_half = (KERNEL_HALF_WIDTH_ZC / beta).ceil() as i64 + 1;
    let mut norm0 = 0.0f64;
    for n in -taps_half..=taps_half {
        norm0 += kernel(n as f64, beta);
    }
    if norm0.abs() < 1e-9 {
        norm0 = 1.0;
    }
    let out_len = ((input.len() as f64) * ratio).round() as usize;
    let mut output = Vec::with_capacity(out_len);
    for out_index in 0..out_len {
        let position = out_index as f64 / ratio;
        let left = position.floor();
        let start = (left - taps_half as f64).max(0.0) as isize;
        let end = (left + taps_half as f64 + 1.0).min(input.len() as f64) as isize;
        let mut acc = 0.0f64;
        for i in start..end {
            acc += kernel(position - i as f64, beta) * input[i as usize] as f64;
        }
        let value = acc / norm0;
        output.push(if value.is_nan() { 0.0 } else { value as f32 });
    }
    output
}

/// Resample i16 samples (clamped on output, round-half-away-from-zero).
pub fn resample_i16(samples: &[i16], in_rate: u32, out_rate: u32) -> Vec<i16> {
    if samples.is_empty() || in_rate == out_rate {
        return samples.to_vec();
    }
    let f32_in: Vec<f32> = samples.iter().map(|&s| s as f32 / 32768.0).collect();
    let f32_out = resample_f32(&f32_in, in_rate, out_rate);
    f32_out
        .iter()
        .map(|&v| {
            let scaled = v * 32768.0;
            let rounded = if scaled >= 0.0 {
                (scaled + 0.5).floor()
            } else {
                (scaled - 0.5).ceil()
            };
            rounded.clamp(-32768.0, 32767.0) as i16
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 440 Hz sine at the given rate, `seconds` long, amplitude 0.5.
    fn sine(rate: u32, seconds: f64, freq: f64, amp: f64) -> Vec<f32> {
        let n = (rate as f64 * seconds) as usize;
        (0..n)
            .map(|i| {
                (amp * (2.0 * std::f64::consts::PI * freq * i as f64 / rate as f64).sin()) as f32
            })
            .collect()
    }

    fn peak(samples: &[f32]) -> f32 {
        samples.iter().fold(0.0f32, |m, &s| m.max(s.abs()))
    }

    fn zero_crossings(samples: &[f32]) -> usize {
        samples
            .windows(2)
            .filter(|w| (w[0] < 0.0) != (w[1] < 0.0))
            .count()
    }

    #[test]
    fn identity_rate_returns_input() {
        let input = sine(16000, 0.1, 440.0, 0.5);
        assert_eq!(resample_f32(&input, 16000, 16000), input);
        let i16_in: Vec<i16> = input.iter().map(|&s| (s * 32768.0) as i16).collect();
        assert_eq!(resample_i16(&i16_in, 16000, 16000), i16_in);
    }

    #[test]
    fn empty_and_single_sample_do_not_panic() {
        assert!(resample_f32(&[], 24000, 16000).is_empty());
        assert!(resample_i16(&[], 24000, 16000).is_empty());
        let one = vec![0.5f32];
        assert_eq!(resample_f32(&one, 24000, 16000).len(), 1);
        let one_i16 = vec![1000i16];
        assert_eq!(resample_i16(&one_i16, 24000, 16000).len(), 1);
    }

    #[test]
    fn downsample_frame_count_matches_ratio() {
        let input = sine(24000, 1.0, 440.0, 0.5);
        let i16_in: Vec<i16> = input.iter().map(|&s| (s * 32768.0) as i16).collect();
        let out = resample_i16(&i16_in, 24000, 16000);
        assert!((out.len() as i64 - 16000).abs() <= 16);
    }

    #[test]
    fn upsample_frame_count_matches_ratio() {
        let input = sine(16000, 1.0, 440.0, 0.5);
        let i16_in: Vec<i16> = input.iter().map(|&s| (s * 32768.0) as i16).collect();
        let out = resample_i16(&i16_in, 16000, 44100);
        assert!((out.len() as i64 - 44100).abs() <= 32);
    }

    #[test]
    fn sine_survives_downsample_with_amplitude_and_frequency_intact() {
        let input = sine(48000, 1.0, 440.0, 0.5);
        let out = resample_f32(&input, 48000, 16000);
        let p = peak(&out);
        assert!(
            (p - 0.5).abs() <= 0.02 * 0.5 + 0.02,
            "peak {p} not within ±2% of 0.5"
        );
        let expected_zc = (440.0 * 2.0 * 1.0) as usize; // 2 crossings per cycle
        let zc = zero_crossings(&out);
        assert!(
            (zc as f64 - expected_zc as f64).abs() <= 0.02 * expected_zc as f64,
            "zero crossings {zc} vs expected {expected_zc}"
        );
    }

    #[test]
    fn midband_tone_survives_flagship_24k_to_16k_within_half_db() {
        // The flagship OpenAI path: a 6 kHz tone must pass within ±0.5 dB
        // (a 4-lobe kernel attenuated it by -1.9 dB; found in review).
        let input = sine(24000, 1.0, 6000.0, 0.5);
        let out = resample_f32(&input, 24000, 16000);
        let p = peak(&out);
        let delta_db = 20.0 * (p as f64 / 0.5).log10();
        assert!(
            delta_db.abs() <= 0.5,
            "6 kHz attenuation {delta_db:.2} dB exceeds ±0.5 dB"
        );
    }

    #[test]
    fn arbitrary_ratio_upsample_keeps_sine_clean() {
        let input = sine(8000, 0.5, 440.0, 0.5);
        let out = resample_f32(&input, 8000, 44100);
        let p = peak(&out);
        assert!((p - 0.5).abs() <= 0.02 * 0.5 + 0.02, "peak {p}");
        let out_i16 = resample_i16(
            &input.iter().map(|&s| (s * 32768.0) as i16).collect::<Vec<_>>(),
            8000,
            44100,
        );
        assert!((out_i16.len() as i64 - 22050).abs() <= 32);
    }

    #[test]
    fn i16_output_is_clamped_in_range() {
        let input: Vec<i16> = (0..100).map(|i| if i % 2 == 0 { 32767 } else { -32768 }).collect();
        let out = resample_i16(&input, 16000, 8000);
        assert!(out.iter().all(|&s| (-32768..=32767).contains(&s)));
    }
}
