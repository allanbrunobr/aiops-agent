/// Fronteira PyO3: expõe detect_cusum e compute_ewma_slope ao Python.
///
/// Fluxo de execução:
///   1. Recebe dados Python enquanto segura o GIL
///   2. Libera o GIL para processar CUSUM/EWMA em Rust puro
///   3. Re-adquire o GIL para construir dicts Python de retorno

use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::analyzer::{cusum_detect, ewma_slope};

/// Detecta ponto de mudança CUSUM em série temporal de floats.
///
/// Retorna dict: {"change_point_detected": bool, "change_index": int | None}
#[pyfunction]
pub fn detect_cusum(
    py: Python<'_>,
    values: Vec<f64>,
    k: f64,
    h: f64,
) -> PyResult<Py<PyAny>> {
    // Libera GIL para computação CPU-bound
    let result = py.detach(|| cusum_detect(&values, k, h));

    let dict = PyDict::new(py);
    dict.set_item("change_point_detected", result.change_point_detected)?;
    match result.change_index {
        Some(idx) => dict.set_item("change_index", idx)?,
        None => dict.set_item("change_index", py.None())?,
    }

    Ok(dict.into())
}

/// Calcula slope EWMA em série temporal de floats.
///
/// Retorna dict: {"slope": float, "eta_minutes": float | None}
/// Nota: eta_minutes é calculado no Python wrapper com o threshold.
#[pyfunction]
pub fn compute_ewma_slope(
    py: Python<'_>,
    values: Vec<f64>,
    span: usize,
) -> PyResult<Py<PyAny>> {
    // Libera GIL para computação CPU-bound
    let result = py.detach(|| ewma_slope(&values, span));

    let dict = PyDict::new(py);
    dict.set_item("slope", result.slope)?;
    dict.set_item("eta_minutes", py.None())?;

    Ok(dict.into())
}
