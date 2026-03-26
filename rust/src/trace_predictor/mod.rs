/// Módulo trace_predictor: port Rust de _percentile e detect_latency_drift.
///
/// Expõe detect_latency_drift() como função PyO3 registrada em lib.rs.
/// Usa #[forbid(unsafe_code)] em predictor.rs — sem código unsafe.
#[forbid(unsafe_code)]

pub mod bridge;
pub mod predictor;

pub use bridge::detect_latency_drift;
