pub mod types;
pub mod analyzer;
pub mod bridge;

pub use bridge::detect_cusum;
pub use bridge::compute_ewma_slope;
