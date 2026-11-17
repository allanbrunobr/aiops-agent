pub mod analyzer;
pub mod bridge;
pub mod types;

pub use bridge::compute_ewma_slope;
pub use bridge::detect_cusum;
