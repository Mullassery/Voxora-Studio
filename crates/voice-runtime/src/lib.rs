//! Voxora Studio voice runtime: deterministic voice-pipeline logic.
//!
//! Scope is honestly limited to pure logic (`turn`, `bridge`, `wav`, and M6's
//! scripted media engine in `media`/`barge_in`) plus the gated PyO3 seam
//! (`pybridge`) that hands the scripted bridge to the Python orchestrator;
//! the socket-backed media backend behind `media`'s `MediaTransport` seam,
//! and the live turn loop that mounts it, is M7 work tracked in
//! `docs/roadmap.md`.

pub mod barge_in;
pub mod bridge;
pub mod controller;
pub mod endpointing;
pub mod media;
pub mod semantic;
pub mod td_events;
pub mod turn;
pub mod vad;
pub mod wav;

#[cfg(feature = "pyo3-bindings")]
pub mod pybridge;
