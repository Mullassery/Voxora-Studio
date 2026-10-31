//! Voxora Studio voice runtime: deterministic voice-pipeline logic.
//!
//! Scope is honestly limited to pure logic (`turn`, `bridge`, `wav`); real
//! media transport wiring is future work tracked in `docs/roadmap.md`.

pub mod bridge;
pub mod turn;
pub mod wav;
