//! Minimal PCM16 WAV inspection (std-only).
//!
//! Enough of RIFF parsing to validate an inbound audio channel artifact:
//! sample rate, channels, bit depth, and duration. Not a full WAV library —
//! intentionally rejects non-PCM formats rather than guessing.

/// Parse errors with honest messages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WavError {
    TooShort,
    NotRiff,
    NotWave,
    MissingFmtChunk,
    MissingDataChunk,
    UnsupportedBits(u16),
    UnsupportedFormatTag(u16),
}

impl std::fmt::Display for WavError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WavError::TooShort => write!(f, "input shorter than a RIFF header"),
            WavError::NotRiff => write!(f, "missing RIFF magic"),
            WavError::NotWave => write!(f, "RIFF container is not WAVE"),
            WavError::MissingFmtChunk => write!(f, "no fmt chunk"),
            WavError::MissingDataChunk => write!(f, "no data chunk"),
            WavError::UnsupportedBits(n) => write!(f, "unsupported bit depth {n} (PCM16 only)"),
            WavError::UnsupportedFormatTag(n) => write!(f, "unsupported format tag {n} (PCM only)"),
        }
    }
}

impl std::error::Error for WavError {}

/// Inspected header facts about a PCM16 WAV byte buffer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WavInfo {
    pub channels: u16,
    pub sample_rate: u32,
    pub bits_per_sample: u16,
    /// Size of the data chunk in bytes (PCM frames * channels * 2).
    pub data_bytes: u64,
}

impl WavInfo {
    pub fn duration_ms(&self) -> u64 {
        if self.channels == 0 || self.sample_rate == 0 {
            return 0;
        }
        let bytes_per_frame = u64::from(self.channels) * 2;
        let frames = self.data_bytes / bytes_per_frame;
        frames * 1000 / u64::from(self.sample_rate)
    }
}

fn le_u16(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}

fn le_u32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// Inspect a PCM16 WAV buffer. Walks chunks until both `fmt ` and `data`
/// are found; offsets in the header (e.g. `LIST` metadata) are tolerated.
pub fn inspect(bytes: &[u8]) -> Result<WavInfo, WavError> {
    if bytes.len() < 12 {
        return Err(WavError::TooShort);
    }
    if &bytes[0..4] != b"RIFF" {
        return Err(WavError::NotRiff);
    }
    if &bytes[8..12] != b"WAVE" {
        return Err(WavError::NotWave);
    }

    let mut info: Option<WavInfo> = None;
    let mut data_bytes: Option<u64> = None;
    let mut pos = 12usize;
    while pos + 8 <= bytes.len() {
        let id = &bytes[pos..pos + 4];
        let size = le_u32(bytes, pos + 4) as usize;
        let body = pos + 8;
        let end = body.saturating_add(size).min(bytes.len());
        if id == b"fmt " {
            if end - body < 14 {
                return Err(WavError::MissingFmtChunk);
            }
            let format_tag = le_u16(bytes, body);
            if format_tag != 1 {
                return Err(WavError::UnsupportedFormatTag(format_tag));
            }
            let channels = le_u16(bytes, body + 2);
            let sample_rate = le_u32(bytes, body + 4);
            let bits = le_u16(bytes, body + 14);
            if bits != 16 {
                return Err(WavError::UnsupportedBits(bits));
            }
            info = Some(WavInfo {
                channels,
                sample_rate,
                bits_per_sample: bits,
                data_bytes: 0,
            });
        } else if id == b"data" {
            data_bytes = Some((end - body) as u64);
        }
        pos = if size > 0 && body + size <= bytes.len() {
            body + size
        } else {
            end
        };
        if pos + 8 > bytes.len() {
            break;
        }
    }

    let mut info = info.ok_or(WavError::MissingFmtChunk)?;
    let data = data_bytes.ok_or(WavError::MissingDataChunk)?;
    info.data_bytes = data;
    // Sanity: data chunk cannot exceed what the buffer actually holds.
    info.data_bytes = info.data_bytes.min(bytes.len() as u64);
    Ok(info)
}

/// Serialize a canonical PCM16 mono WAV with the given samples at `rate`.
/// Used by tests and tooling to produce fixtures without scaffolding files.
pub fn encode_pcm16_mono(samples: &[i16], rate: u32) -> Vec<u8> {
    let data_len = samples.len() * 2;
    let mut out = Vec::with_capacity(44 + data_len);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&((36 + data_len) as u32).to_le_bytes());
    out.extend_from_slice(b"WAVE");
    out.extend_from_slice(b"fmt ");
    out.extend_from_slice(&16u32.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes()); // PCM
    out.extend_from_slice(&1u16.to_le_bytes()); // mono
    out.extend_from_slice(&rate.to_le_bytes());
    out.extend_from_slice(&((rate * 2) as u32).to_le_bytes()); // byte rate
    out.extend_from_slice(&2u16.to_le_bytes()); // block align
    out.extend_from_slice(&16u16.to_le_bytes()); // bits
    out.extend_from_slice(b"data");
    out.extend_from_slice(&(data_len as u32).to_le_bytes());
    for s in samples {
        out.extend_from_slice(&s.to_le_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_encode_inspect() {
        let samples = vec![0i16; 16_000]; // 1 second at 16 kHz mono
        let bytes = encode_pcm16_mono(&samples, 16_000);
        let info = inspect(&bytes).unwrap();
        assert_eq!(info.channels, 1);
        assert_eq!(info.sample_rate, 16_000);
        assert_eq!(info.bits_per_sample, 16);
        assert_eq!(info.duration_ms(), 1000);
    }

    #[test]
    fn duration_at_8khz() {
        let bytes = encode_pcm16_mono(&vec![0i16; 4_000], 8_000);
        assert_eq!(inspect(&bytes).unwrap().duration_ms(), 500);
    }

    #[test]
    fn rejects_garbage() {
        assert_eq!(inspect(b"no").unwrap_err(), WavError::TooShort);
        assert_eq!(inspect(b"JUNKJUNKJUNK").unwrap_err(), WavError::NotRiff);
        let bytes = encode_pcm16_mono(&[0i16; 8], 8_000);
        let mut not_wave = bytes.clone();
        not_wave[8..12].copy_from_slice(b"AVI ");
        assert_eq!(inspect(&not_wave).unwrap_err(), WavError::NotWave);
    }

    #[test]
    fn rejects_non_pcm16() {
        let mut bytes = encode_pcm16_mono(&[0i16; 8], 8_000);
        // flip bit depth to 8
        bytes[34] = 8;
        assert_eq!(inspect(&bytes).unwrap_err(), WavError::UnsupportedBits(8));
    }

    #[test]
    fn custom_header_goofiness_detected() {
        // Replace the fmt chunk id (12..16) so parsing fails honestly.
        let bytes = encode_pcm16_mono(&[0i16; 8], 8_000);
        let mut broken = bytes.clone();
        broken[12..16].copy_from_slice(b"fmxx");
        assert_eq!(inspect(&broken).unwrap_err(), WavError::MissingFmtChunk);
    }
}
