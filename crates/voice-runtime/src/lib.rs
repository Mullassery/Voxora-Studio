//! Voxora Studio voice runtime: deterministic voice-pipeline logic.
//!
//! Scope is honestly limited to pure logic (`turn`, `bridge`, `wav`) plus the
//! gated PyO3 seam (`pybridge`) that hands the scripted bridge to the Python
//! orchestrator; real media transport wiring is future work tracked in
//! `docs/roadmap.md`.

pub mod bridge;
pub mod turn;
pub mod wav;

#[cfg(feature = "pyo3-bindings")]
pub mod pybridge;
