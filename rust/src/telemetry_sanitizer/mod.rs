pub mod bridge;
pub mod detector;
pub mod patterns;

pub use bridge::{detect_taint_batch, detect_taint_single};
