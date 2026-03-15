/// Módulo log_parser: migração do Drain3 para Rust sem cap de amostragem.
///
/// Expõe parse_logs() como função PyO3 registrada em lib.rs.
pub mod bridge;
pub mod drain3;
pub mod relevance;
pub mod types;

pub use bridge::parse_logs;
