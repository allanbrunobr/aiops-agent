/// Fronteira PyO3: expõe detect_latency_drift como função Python.
///
/// Fluxo de execução:
///   1. Extrai Vec<f64> de baseline e current enquanto segura o GIL
///   2. Libera o GIL para executar a lógica pura em Rust (py.allow_threads)
///   3. Re-adquire o GIL para construir o dict Python de retorno
use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::predictor::detect_latency_drift_inner;

/// Detecta drift de latência comparando p95 e p99 entre duas janelas.
///
/// Equivalente ao `detect_latency_drift` Python, mas executa sem GIL.
/// Retorna dict com: drift_detected (bool), p95_ratio (float), p99_ratio (float).
#[pyfunction]
#[pyo3(signature = (baseline, current, threshold_pct = 0.20))]
pub fn detect_latency_drift(
    py: Python<'_>,
    baseline: Vec<f64>,
    current: Vec<f64>,
    threshold_pct: f64,
) -> PyResult<PyObject> {
    // Libera o GIL para o cálculo de percentis (CPU-bound)
    let result = py.allow_threads(|| detect_latency_drift_inner(&baseline, &current, threshold_pct));

    let dict = PyDict::new_bound(py);
    dict.set_item("drift_detected", result.drift_detected)?;
    dict.set_item("p95_ratio", result.p95_ratio)?;
    dict.set_item("p99_ratio", result.p99_ratio)?;
    Ok(dict.into())
}
