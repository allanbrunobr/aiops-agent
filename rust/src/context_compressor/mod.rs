/// Módulo context_compressor — implementação Rust do ContextCompressor com release do GIL.
pub mod types;
pub mod classifier;
pub mod compressor;
pub mod render;
pub mod bridge;

pub use bridge::compress_context;
