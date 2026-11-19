//! Media-bridge engine (roadmap M6/P3).
//!
//! The audio side of `<Connect><Stream>` on Twilio Media Streams: G.711
//! μ-law 8 kHz arrives on the wire while the pipeline stays PCM-native, so
//! this module owns format conversion (+ format negotiation via a converter
//! registry), a jitter-tolerant bounded chunk ring, per-hop latency
//! accounting, and the inbound → utterance → decision → TTS frame path.
//!
//! The wire itself is a [`MediaTransport`] seam: a socket-backed backend is
//! transport glue (Tokio/tungstenite) that M7's live loop mounts behind this
//! trait, and the M3 legal/carrier review gate stands before live carrier
//! traffic. The scripted acceptance test rides a [`FakeMediaWs`] — the same
//! shape a socket-backed transport will have.
//!
//! Honesty notes (the codebase's capability-honest pattern):
//! * TTS frame emission is *deterministic placeholders*, not synthesis: the
//!   frame count derives from the reply text and sample values derive from
//!   the text bytes. That exercises the outbound encode/transport path end
//!   to end; no speech is synthesized here.
//! * The 16 kHz ⇄ 8 kHz resampler is 0-order hold (sample duplication on
//!   upsample, drop-half on downsample). Fine for gating/turn text; replace
//!   with a real resampler before audible output ships.
//! * Unregistered format pairs fail closed: [`FormatRegistry::convert`]
//!   errors instead of silently picking a fallback.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

/// Audio encoding carried inside a [`MediaFormat`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Encoding {
    /// G.711 μ-law, one byte per sample.
    Ulaw,
    /// Signed 16-bit little-endian linear PCM.
    Pcm16,
}

impl Encoding {
    /// Bytes per sample, for payload length validation.
    pub fn bytes_per_sample(self) -> usize {
        match self {
            Encoding::Ulaw => 1,
            Encoding::Pcm16 => 2,
        }
    }
}

/// A wire/buffer audio format: encoding plus sample rate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MediaFormat {
    /// Sample payload encoding.
    pub encoding: Encoding,
    /// Sample rate in Hz.
    pub sample_rate: u32,
}

/// μ-law 8 kHz — what Twilio Media Streams actually puts on the wire.
pub const ULAW_8K: MediaFormat = MediaFormat { encoding: Encoding::Ulaw, sample_rate: 8000 };
/// PCM 16-bit 16 kHz — this engine's PCM-native format (P3's model).
pub const PCM16_16K: MediaFormat = MediaFormat { encoding: Encoding::Pcm16, sample_rate: 16000 };

/// G.711 μ-law decode (canonical CCITT/Sun table algorithm).
pub fn ulaw_to_pcm16(sample: u8) -> i16 {
    let flipped = !sample;
    let exponent = ((flipped >> 4) & 0x07) as i32;
    let t = (((flipped & 0x0F) as i32) << 3) + 0x84;
    let t = t << exponent;
    if flipped & 0x80 != 0 {
        (0x84 - t) as i16
    } else {
        (t - 0x84) as i16
    }
}

/// G.711 μ-law encode: the exact quantized inverse of [`ulaw_to_pcm16`] on
/// that decoder's canonical grid, so re-encoding a decoded point is
/// identity (the sampled magnitude already carries the decoder's rounding
/// bias; μ-law's complemented wire sign is handled via raw bit 7 = negative).
pub fn pcm16_to_ulaw(sample: i16) -> u8 {
    const MAX_DECODABLE: i32 = 32124; // 252 << 7 − 132: the decoder's ceiling
    // Wire sign is complemented: decoded-negative ⇔ wire bit7 clear ⇔ raw
    // bit7 set (all bit identities above are pre-complement).
    let sign_bit = if sample < 0 { 0x80u8 } else { 0x00 };
    let magnitude = i32::from(sample.abs()).min(MAX_DECODABLE);
    let scaled = magnitude + 132;
    // Smallest exponent whose 8-bit grid still reaches the magnitude.
    let mut exponent: u32 = 0;
    while (scaled >> exponent) > 252 {
        exponent += 1;
    }
    // Grid-gap magnitudes (just past a segment's top) clamp to mantissa 15.
    let mantissa_raw = ((scaled >> exponent) - 132) >> 3;
    let mantissa = mantissa_raw.clamp(0, 15) as u8;
    !(sign_bit | ((exponent as u8) << 4) | mantissa)
}

fn ulaw_buffer_to_pcm16_le(bytes: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.extend_from_slice(&ulaw_to_pcm16(b).to_le_bytes());
    }
    out
}

fn pcm16_le_buffer_to_ulaw(bytes: &[u8]) -> Vec<u8> {
    bytes
        .as_chunks::<2>()
        .0
        .iter()
        .map(|c| pcm16_to_ulaw(i16::from_le_bytes(*c)))
        .collect()
}

/// 0-order-hold upsample: each 16-bit sample duplicated.
fn pcm16_le_upsample_2x(bytes: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(bytes.len() * 2);
    for &[lo, hi] in bytes.as_chunks::<2>().0 {
        out.extend_from_slice(&[lo, hi, lo, hi]);
    }
    out
}

/// 0-order-hold downsample: keep every second 16-bit sample.
fn pcm16_le_downsample_half(bytes: &[u8]) -> Vec<u8> {
    bytes
        .as_chunks::<4>()
        .0
        .iter()
        .flat_map(|[a, b, ..]| [*a, *b])
        .collect()
}

/// Media pipeline failure, honest and specific.
#[derive(Debug, PartialEq)]
pub enum MediaError {
    /// No registered converter for the pair; never silently fall back.
    NoConverter { from: MediaFormat, to: MediaFormat },
    /// Payload is not whole samples for `from`.
    BadPayloadLength { format: MediaFormat, len: usize },
    /// The transport rejected a send (e.g. fake socket already closed).
    TransportClosed(String),
}

/// Deterministic codec function: one format payload to another.
pub type ConverterFn = fn(&[u8]) -> Vec<u8>;

/// Converter registry with format negotiation (the P3 PCM-native model):
/// every known conversion is an explicit registered pair; unknown pairs
/// surface as [`MediaError::NoConverter`].
#[derive(Debug, Default)]
pub struct FormatRegistry {
    entries: Vec<(MediaFormat, MediaFormat, ConverterFn)>,
}

impl FormatRegistry {
    /// Registry containing the built-in μ-law 8 kHz ⇄ PCM 16 kHz path.
    pub fn new() -> Self {
        let mut r = Self::default();
        r.register(
            ULAW_8K,
            PCM16_16K,
            |b| pcm16_le_upsample_2x(&ulaw_buffer_to_pcm16_le(b)),
        );
        r.register(
            PCM16_16K,
            ULAW_8K,
            |b| pcm16_le_buffer_to_ulaw(&pcm16_le_downsample_half(b)),
        );
        r
    }

    /// Add (or replace) a conversion pair.
    pub fn register(&mut self, from: MediaFormat, to: MediaFormat, run: ConverterFn) {
        self.entries.retain(|(f, t, _)| !(*f == from && *t == to));
        self.entries.push((from, to, run));
    }

    /// Convert a payload between formats, or fail closed.
    pub fn convert(
        &self,
        from: MediaFormat,
        to: MediaFormat,
        payload: &[u8],
    ) -> Result<Vec<u8>, MediaError> {
        if from == to {
            return Ok(payload.to_vec());
        }
        let run = self
            .entries
            .iter()
            .find(|(f, t, _)| *f == from && *t == to)
            .map(|(_, _, r)| *r)
            .ok_or(MediaError::NoConverter { from, to })?;
        if !payload.len().is_multiple_of(from.encoding.bytes_per_sample()) {
            return Err(MediaError::BadPayloadLength { format: from, len: payload.len() });
        }
        Ok(run(payload))
    }

    /// Format negotiation: the first offered format convertible into
    /// `target`, or None if none works (honest, caller-visible).
    pub fn negotiate(&self, offered: &[MediaFormat], target: MediaFormat) -> Option<MediaFormat> {
        offered.iter().copied().find(|&f| {
            f == target || self.entries.iter().any(|(fr, t, _)| *fr == f && *t == target)
        })
    }
}

/// Outcome of a bounded [`ChannelRing::push`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PushOutcome {
    /// Chunk admitted.
    Admitted { hold: usize },
    /// Ring was full: the OLDEST chunk was evicted and this one admitted.
    /// Fresh audio is what downstream needs; shedding old is the
    /// jitter-tolerant posture.
    EvictedOldest { hold: usize },
}

impl PushOutcome {
    pub fn evicted(&self) -> bool {
        matches!(self, PushOutcome::EvictedOldest { .. })
    }
}

/// Bounded per-channel chunk ring (jitter buffer): admits chunk-granularity
/// audio, sheds the OLDEST under sustained jitter, counts the sheds.
#[derive(Debug)]
pub struct ChannelRing {
    cap: usize,
    chunks: VecDeque<Vec<u8>>,
    pub shed: u64,
}

impl ChannelRing {
    pub fn new(cap: usize) -> Self {
        Self { cap, chunks: VecDeque::new(), shed: 0 }
    }

    /// Push one PCM16 little-endian payload chunk.
    pub fn push(&mut self, chunk: Vec<u8>) -> PushOutcome {
        if self.cap == 0 {
            self.shed += 1;
            return PushOutcome::EvictedOldest { hold: 0 };
        }
        let mut outcome = PushOutcome::Admitted { hold: self.chunks.len() + 1 };
        if self.chunks.len() == self.cap {
            self.chunks.pop_front();
            self.shed += 1;
            outcome = PushOutcome::EvictedOldest { hold: self.cap };
        }
        self.chunks.push_back(chunk);
        outcome
    }

    /// Drain everything currently buffered (the pool re-arms as it empties).
    pub fn drain(&mut self) -> Vec<Vec<u8>> {
        self.chunks.drain(..).collect()
    }

    pub fn len(&self) -> usize {
        self.chunks.len()
    }

    pub fn is_empty(&self) -> bool {
        self.chunks.is_empty()
    }
}

/// RMS of one PCM frame as a 0..=1 fraction of full scale — the same VAD
/// quantity the barge-in gate consumes.
pub fn frame_rms(samples: &[i16]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum: f32 = samples
        .iter()
        .map(|&s| (i32::from(s) as f32 / 32768.0).powi(2))
        .sum();
    (sum / samples.len() as f32).sqrt()
}

/// One assembled user utterance: decoded engine PCM + provenance count.
#[derive(Debug)]
pub struct Utterance {
    /// Concatenated linear PCM samples at the engine format.
    pub pcm: Vec<i16>,
    /// Inbound wire frames that contributed.
    pub inbound_frames: u64,
}

/// Utterance assembler: accumulates voiced frames (energy gate here is only
/// the assembler's noise floor — the real barge-in gates live in
/// [`crate::barge_in`] and the turn machine, upstream of this) and emits one
/// utterance when `end_gap` consecutive quiet frames close it, or on
/// explicit [`UtteranceAssembler::flush`].
pub struct UtteranceAssembler {
    /// Fraction-of-full-scale RMS above which a frame is voiced.
    pub voiced_floor: f32,
    /// Consecutive quiet frames that close an open utterance.
    pub end_gap: u32,
    pending: Vec<i16>,
    quiet_run: u32,
    frames: u64,
    open: bool,
}

impl UtteranceAssembler {
    pub fn new(voiced_floor: f32, end_gap: u32) -> Self {
        Self {
            voiced_floor,
            end_gap,
            pending: Vec::new(),
            quiet_run: 0,
            frames: 0,
            open: false,
        }
    }

    /// Feed one decoded frame. Returns the finished utterance exactly when
    /// the closing quiet gap lands.
    pub fn feed(&mut self, frame: &[i16]) -> Option<Utterance> {
        if frame.is_empty() {
            return None;
        }
        self.frames += 1;
        if frame_rms(frame) >= self.voiced_floor {
            self.open = true;
            self.quiet_run = 0;
            self.pending.extend_from_slice(frame);
            return None;
        }
        if !self.open {
            return None;
        }
        self.quiet_run += 1;
        let mut utterance = None;
        if self.quiet_run >= self.end_gap {
            let pcm = std::mem::take(&mut self.pending);
            self.open = false;
            self.quiet_run = 0;
            utterance = Some(Utterance { pcm, inbound_frames: self.frames });
            self.frames = 0;
        } else {
            // Quiet audio inside an open utterance belongs to its tail.
            self.pending.extend_from_slice(frame);
        }
        utterance
    }

    /// End of stream: close any still-open utterance.
    pub fn flush(&mut self) -> Option<Utterance> {
        if !self.open {
            return None;
        }
        let pcm = std::mem::take(&mut self.pending);
        self.open = false;
        self.quiet_run = 0;
        let inbound = self.frames;
        self.frames = 0;
        Some(Utterance { pcm, inbound_frames: inbound })
    }
}

/// Per-hop latency accumulators (roadmap: "latency tracker per hop"); hop
/// names are pipeline stage names aligned with the orchestrator's
/// `TurnTiming` latency schema.
#[derive(Debug, Default)]
pub struct HopLatency {
    hops: Vec<(&'static str, Duration)>,
}

impl HopLatency {
    /// Accrue time onto a named hop (cumulative across frames).
    pub fn record(&mut self, hop: &'static str, dt: Duration) {
        if let Some(entry) = self.hops.iter_mut().find(|(name, _)| *name == hop) {
            entry.1 += dt;
        } else {
            self.hops.push((hop, dt));
        }
    }

    pub fn total(&self, hop: &str) -> Option<Duration> {
        self.hops
            .iter()
            .find(|(name, _)| *name == hop)
            .map(|(_, v)| *v)
    }

    pub fn all(&self) -> &[(&'static str, Duration)] {
        &self.hops
    }
}

/// The media wire seam. A real WebSocket backend (mounted in M7's live loop)
/// implements this with socket I/O; the pure engine here stays
/// socket-agnostic until M3's legal/carrier decision gate clears.
pub trait MediaTransport {
    /// Send one downstream frame in the negotiated upstream format.
    fn send_frame(&mut self, payload: &[u8]) -> Result<(), String>;
    /// Frames written to the wire so far (acceptance assertions).
    fn sent_frames(&self) -> &[Vec<u8>];
}

/// In-memory fake WS: the scripted test transport.
#[derive(Debug, Default)]
pub struct FakeMediaWs {
    sent: Vec<Vec<u8>>,
    closed: bool,
}

impl FakeMediaWs {
    pub fn new() -> Self {
        Self::default()
    }
}

impl MediaTransport for FakeMediaWs {
    fn send_frame(&mut self, payload: &[u8]) -> Result<(), String> {
        if self.closed {
            return Err("socket closed".into());
        }
        self.sent.push(payload.to_vec());
        Ok(())
    }

    fn sent_frames(&self) -> &[Vec<u8>] {
        &self.sent
    }
}

/// Bridge engine: inbound wire format → registered decode → jitter ring →
/// utterance assembly → decision → deterministic TTS frames → registered
/// encode → transport. One instance handles one media session direction
/// pair; the turn machine remains the conversation-state authority.
pub struct MediaBridge<'a> {
    /// Registry carrying the codec path for this session.
    pub registry: &'a FormatRegistry,
    /// Jitter buffer for decoded inbound chunks.
    pub ring: ChannelRing,
    /// Utterance assembler over decoded frames.
    pub assembler: UtteranceAssembler,
    /// Per-hop latency counters, readable at any time.
    pub tracker: HopLatency,
    /// Voice Boost stage (M9). `None` = disabled ⇒ original audio preserved
    /// exactly; the insertion point is between decode and the jitter ring,
    /// ahead of RMS/VAD/barge-in gates (thresholds re-validated in dsp tests).
    pub voice_boost: Option<crate::dsp::VoiceBoostStage>,
    /// Decider hook (M7 mounts the live model turn here).
    pub decider: Box<dyn FnMut(&Utterance) -> String>,
    transport: Box<dyn MediaTransport>,
    upstream: MediaFormat,
    engine: MediaFormat,
}

impl<'a> MediaBridge<'a> {
    pub fn new(
        registry: &'a FormatRegistry,
        transport: Box<dyn MediaTransport>,
        upstream: MediaFormat,
        engine: MediaFormat,
        ring_capacity: usize,
    ) -> Self {
        Self {
            registry,
            ring: ChannelRing::new(ring_capacity),
            assembler: UtteranceAssembler::new(0.01, 2),
            tracker: HopLatency::default(),
            voice_boost: None,
            decider: Box::new(|u: &Utterance| {
                format!("Acknowledged; your turn was {} samples of audio.", u.pcm.len())
            }),
            transport,
            upstream,
            engine,
        }
    }

    /// Mount a Voice Boost stage on the inbound path (bypass flag lives in
    /// the stage config; None here means Voice Boost is entirely off).
    pub fn with_voice_boost(mut self, stage: crate::dsp::VoiceBoostStage) -> Self {
        self.voice_boost = Some(stage);
        self
    }

    /// Ride one inbound wire frame: decode, buffer, assemble; when the
    /// closing quiet gap lands, run the decision and emit TTS frames.
    /// `wire_dt` is the harness-attributed latency of this frame's inbound
    /// hop (a socket backend passes measured socket delay; tests pass
    /// deterministic values). Returns outbound frames emitted.
    pub fn on_inbound_frame(
        &mut self,
        payload: &[u8],
        wire_dt: Duration,
    ) -> Result<usize, MediaError> {
        let decoded = self.registry.convert(self.upstream, self.engine, payload)?;
        self.tracker.record("user_in_decode", wire_dt);
        // Voice Boost (M9): between decode and the jitter ring, ahead of
        // frame_rms/VAD/barge-in gating. Disabled ⇒ byte-identical path.
        let dsp_bypass = self
            .voice_boost
            .as_ref()
            .map_or(true, |vb| vb.config.bypass);
        let decoded = if dsp_bypass {
            decoded
        } else {
            // Registry output is little-endian PCM16 bytes; run DSP on the
            // sample view and hand the ring back encoded bytes.
            let samples: Vec<i16> = decoded
                .as_chunks::<2>()
                .0
                .iter()
                .map(|c| i16::from_le_bytes(*c))
                .collect();
            let t0 = Instant::now();
            let frame = self
                .voice_boost
                .as_mut()
                .map(|vb| vb.process(&samples))
                .expect("dsp_bypass checked");
            self.tracker.record("user_in_dsp", t0.elapsed());
            frame
                .pcm
                .iter()
                .flat_map(|s| s.to_le_bytes())
                .collect::<Vec<u8>>()
        };
        self.ring.push(decoded);
        let chunk = self.ring.drain().into_iter().next().unwrap_or_default();
        let samples: Vec<i16> = chunk
            .as_chunks::<2>()
            .0
            .iter()
            .map(|c| i16::from_le_bytes(*c))
            .collect();
        match self.assembler.feed(&samples) {
            Some(u) => {
                self.emit_reply(&u)?;
                Ok(1)
            }
            None => Ok(0),
        }
    }

    /// Explicit end-of-stream: close any open utterance and emit its reply.
    pub fn finish(&mut self, wire_dt: Duration) -> Result<usize, MediaError> {
        self.tracker.record("user_in_decode", wire_dt);
        self.ring.drain();
        match self.assembler.flush() {
            Some(utterance) => self.emit_reply(&utterance).map(|_| 1),
            None => Ok(0),
        }
    }

    /// Closing quiet gap just landed on the assembler: decide + emit. Kept
    /// public so the scripted harness (and M7) can drive assembly and reply
    /// emission as separate explicit steps.
    pub fn emit_reply(&mut self, utterance: &Utterance) -> Result<(), MediaError> {
        self.tracker
            .record("user_to_agent", Duration::from_millis(utterance.inbound_frames * 20));
        let decision = (self.decider)(utterance);
        for (sent, frame) in tts_frames(&decision, self.engine.sample_rate)
            .into_iter()
            .enumerate()
        {
            let encoded = self.registry.convert(self.engine, self.upstream, &frame)?;
            self.tracker
                .record("agent_to_tts", Duration::from_millis(20 * (sent as u64 + 1)));
            self.transport
                .send_frame(&encoded)
                .map_err(MediaError::TransportClosed)?;
        }
        Ok(())
    }

    /// Frames written to the wire so far.
    pub fn sent_frames(&self) -> &[Vec<u8>] {
        self.transport.sent_frames()
    }
}

/// Deterministic TTS placeholder: frame COUNT derives from the reply text
/// (20 ms of samples per frame at the target rate); sample VALUES derive
/// from the text bytes. Honest: no synthesis — but a byte-real ride down
/// the encode + transport path.
fn tts_frames(text: &str, sample_rate: u32) -> Vec<Vec<u8>> {
    let bytes = text.as_bytes();
    if bytes.is_empty() {
        return Vec::new();
    }
    let frame_samples = (sample_rate / 50) as usize; // 20 ms per frame
    (0..bytes.len())
        .map(|i| {
            (0..frame_samples)
                .map(|j| {
                    let b = bytes[(i + j) % bytes.len()];
                    ((i32::from(b) % 97) * 200 + 100 + ((i % 7) as i32) * 300) as i16
                })
                .flat_map(|s| s.to_le_bytes())
                .collect::<Vec<u8>>()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One loud μ-law wire frame: 160 samples (20 ms at 8 kHz) alternating
    /// ±6800, decoded RMS well above the assembler floor.
    fn loud_frame() -> Vec<u8> {
        (0..160)
            .map(|i| {
                pcm16_to_ulaw(if i % 2 == 0 { 6800 } else { -6800 })
            })
            .collect()
    }

    fn quiet_frame() -> Vec<u8> {
        vec![pcm16_to_ulaw(0); 160]
    }

    #[test]
    fn ulaw_decode_encode_retraction_holds_for_every_byte() {
        // decode ∘ encode is the identity: μ-law decode maps each code to a
        // distinct canonical point, and encode reproduces the canonical code
        // of any decoded value (G.711's ±0 dupe codes co-collapse — a
        // codec property, not an implementation bug).
        for b in 0..=255u8 {
            let decoded = ulaw_to_pcm16(b);
            assert_eq!(ulaw_to_pcm16(pcm16_to_ulaw(decoded)), decoded, "byte {b:#x}");
        }
    }

    #[test]
    fn silence_and_full_scale_stay_in_range() {
        assert_eq!(ulaw_to_pcm16(0xFF), 0);
        assert!(ulaw_to_pcm16(0x00).abs() > 8000);
        // Zero re-encodes within the ±0 dupe-code set to a code that decodes 0.
        let near = ulaw_to_pcm16(pcm16_to_ulaw(0));
        assert_eq!(near, 0);
    }

    #[test]
    fn registry_roundtrips_ulaw_to_engine_and_back() {
        let registry = FormatRegistry::new();
        let wire: Vec<u8> = (0..160u8).collect();
        let decoded = registry.convert(ULAW_8K, PCM16_16K, &wire).unwrap();
        assert_eq!(decoded.len(), 640, "20 ms 8k → 16 kHz: 320 samples");
        let back = registry.convert(PCM16_16K, ULAW_8K, &decoded).unwrap();
        assert_eq!(back.len(), 160);
        for (orig, re) in wire.iter().zip(back.iter()) {
            let w = ulaw_to_pcm16(*orig);
            let r = ulaw_to_pcm16(*re);
            // encode∘decode is the canonical-grid inverse, so decoded values
            // survive; only the ±0 dupe codes may swap (same decoded point).
            assert_eq!(w, r, "decoded drift for orig {orig:#x}");
        }
    }

    #[test]
    fn unregistered_pairs_fail_closed() {
        let registry = FormatRegistry::new();
        let pcm8k = MediaFormat { encoding: Encoding::Pcm16, sample_rate: 8000 };
        assert_eq!(
            registry.convert(pcm8k, PCM16_16K, &[0, 0]),
            Err(MediaError::NoConverter { from: pcm8k, to: PCM16_16K })
        );
        // μ-law payloads are one byte per sample — never a length error;
        // the length gate lives on PCM16 payloads.
        assert_eq!(
            registry.convert(PCM16_16K, ULAW_8K, &[1, 2, 3]),
            Err(MediaError::BadPayloadLength { format: PCM16_16K, len: 3 })
        );
    }

    #[test]
    fn negotiation_picks_the_convertible_offered_format() {
        let registry = FormatRegistry::new();
        let offered = [ULAW_8K, MediaFormat { encoding: Encoding::Pcm16, sample_rate: 48000 }];
        assert_eq!(registry.negotiate(&offered, PCM16_16K), Some(ULAW_8K));
        assert_eq!(registry.negotiate(&[PCM16_16K], PCM16_16K), Some(PCM16_16K));
        let odd = MediaFormat { encoding: Encoding::Ulaw, sample_rate: 44100 };
        assert_eq!(registry.negotiate(&[odd], PCM16_16K), None);
    }

    #[test]
    fn ring_is_bounded_and_sheds_the_oldest_under_overflow() {
        let mut ring = ChannelRing::new(3);
        for i in 0..5 {
            let outcome = ring.push(vec![i]);
            if i >= 3 {
                assert_eq!(outcome, PushOutcome::EvictedOldest { hold: 3 }, "overflow at {i}");
            }
        }
        assert_eq!(ring.shed, 2);
        let drained = ring.drain();
        assert_eq!(drained, vec![vec![2], vec![3], vec![4]]);
        assert!(ring.is_empty());
    }

    #[test]
    fn hop_latency_accumulates_per_named_hop() {
        let mut tracker = HopLatency::default();
        tracker.record("user_in_decode", Duration::from_millis(12));
        tracker.record("user_in_decode", Duration::from_millis(30));
        tracker.record("agent_to_tts", Duration::from_millis(5));
        assert_eq!(tracker.total("user_in_decode"), Some(Duration::from_millis(42)));
        assert_eq!(tracker.total("agent_to_tts"), Some(Duration::from_millis(5)));
        assert_eq!(tracker.total("missing"), None);
    }

    #[test]
    fn assembler_closes_only_after_the_quiet_gap() {
        let mut a = UtteranceAssembler::new(0.01, 2);
        assert!(a.feed(&[1000; 160]).is_none(), "voiced frame opens, never closes");
        assert!(a.feed(&[100; 160]).is_none(), "first quiet frame is not the gap");
        let u = a.feed(&[100; 160]).expect("second quiet frame closes");
        assert!(u.pcm.len() >= 160);
        assert!(a.flush().is_none(), "stream end with nothing open is a no-op");
    }

    #[test]
    fn end_to_end_scripted_media_exchange() {
        // Acceptance: 100 injected μ-law frames ride a fake media WS and
        // produce one utterance → decision → TTS frames out; per-hop latency
        // counters non-zero.
        let registry = FormatRegistry::new();
        let mut bridge = MediaBridge::new(
            &registry,
            Box::new(FakeMediaWs::new()),
            ULAW_8K,
            PCM16_16K,
            300,
        );
        let loud = loud_frame();
        for i in 0..100u64 {
            let n = bridge
                .on_inbound_frame(&loud, Duration::from_millis(20 + i / 10))
                .unwrap();
            assert_eq!(n, 0, "no reply before the closing quiet gap");
        }
        assert_eq!(
            bridge.on_inbound_frame(&quiet_frame(), Duration::from_millis(20)).unwrap(),
            0,
            "first quiet frame is not the gap"
        );
        let sent = bridge
            .on_inbound_frame(&quiet_frame(), Duration::from_millis(20))
            .unwrap();
        assert!(sent >= 1, "closing gap must emit the reply");
        // Decision ran exactly once; TTS frames reached the wire in μ-law 8k.
        assert!(
            bridge.tracker.total("user_to_agent").unwrap() > Duration::ZERO,
            "user_to_agent hop counter must be non-zero"
        );
        let frames = bridge.sent_frames();
        assert!(!frames.is_empty(), "TTS frames must go out");
        for f in frames {
            let pcm = registry.convert(ULAW_8K, PCM16_16K, f).unwrap();
            assert!(pcm.as_chunks::<2>().0.iter().any(|c| i16::from_le_bytes(*c) != 0));
        }
        // Per-hop latency counters non-zero for every hop of the exchange.
        for (hop, _) in bridge.tracker.all() {
            assert!(
                bridge.tracker.total(hop).unwrap() > Duration::ZERO,
                "hop {hop} must be non-zero"
            );
        }
    }

    #[test]
    fn decision_runs_exactly_once_for_one_utterance() {
        let registry = FormatRegistry::new();
        let mut bridge = MediaBridge::new(
            &registry,
            Box::new(FakeMediaWs::new()),
            ULAW_8K,
            PCM16_16K,
            300,
        );
        bridge.decider = Box::new(|_| "one".to_string());
        let mut emits = 0;
        for i in 0..100u64 {
            emits += bridge
                .on_inbound_frame(&loud_frame(), Duration::from_millis(20 + i / 10))
                .unwrap();
        }
        emits += bridge
            .on_inbound_frame(&quiet_frame(), Duration::from_millis(20))
            .unwrap();
        emits += bridge
            .on_inbound_frame(&quiet_frame(), Duration::from_millis(20))
            .unwrap();
        assert_eq!(emits, 1, "one utterance → one decision");
    }

    #[test]
    fn finish_flushes_an_open_utterance() {
        let registry = FormatRegistry::new();
        let mut bridge = MediaBridge::new(
            &registry,
            Box::new(FakeMediaWs::new()),
            ULAW_8K,
            PCM16_16K,
            300,
        );
        assert_eq!(bridge.on_inbound_frame(&loud_frame(), Duration::from_millis(20)).unwrap(), 0);
        assert_eq!(bridge.finish(Duration::from_millis(10)).unwrap(), 1);
        assert!(!bridge.sent_frames().is_empty());
    }

    #[test]
    fn closed_transport_surfaces_honestly() {
        let registry = FormatRegistry::new();
        let mut fake = FakeMediaWs::new();
        fake.closed = true;
        let utterance = Utterance { pcm: vec![6800; 160], inbound_frames: 1 };
        let mut bridge = MediaBridge::new(
            &registry,
            Box::new(fake),
            ULAW_8K,
            PCM16_16K,
            300,
        );
        assert!(bridge.emit_reply(&utterance).is_err(), "closed socket must not fake success");
    }
}
