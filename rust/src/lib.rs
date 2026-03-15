use pyo3::prelude::*;

mod context_compressor;
// LogParser functions registered by wt3

/// Módulo nativo _aiops_rust — extensão PyO3 para aceleração do pipeline AIOps.
#[pymodule]
fn _aiops_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(context_compressor::compress_context, m)?)?;
    Ok(())
}
