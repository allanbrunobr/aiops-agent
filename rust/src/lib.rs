use pyo3::prelude::*;

// ContextCompressor functions registered by wt2
// LogParser functions registered by wt3

/// Módulo nativo _aiops_rust — extensão PyO3 para aceleração do pipeline AIOps.
#[pymodule]
fn _aiops_rust(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
