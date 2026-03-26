use pyo3::prelude::*;
use pyo3::types::PyDict;

mod context_compressor;
mod cusum_ewma;
mod log_parser;
mod metric_filter;
mod telemetry_sanitizer;
mod trace_filter;
mod trace_predictor;

/// Detecta ponto de mudança CUSUM em série temporal.
/// Fallback direto em lib.rs para garantir registro no módulo.
#[pyfunction]
fn detect_cusum(py: Python<'_>, values: Vec<f64>, k: f64, h: f64) -> PyResult<PyObject> {
    let (detected, change_index) = py.allow_threads(|| {
        let mut s: f64 = 0.0;
        for (i, &x) in values.iter().enumerate() {
            s = (s + x - k).max(0.0);
            if s > h {
                return (true, Some(i));
            }
        }
        (false, None)
    });
    let dict = PyDict::new_bound(py);
    dict.set_item("change_point_detected", detected)?;
    dict.set_item("change_index", change_index.map(|i| i as i64))?;
    Ok(dict.into())
}

/// Calcula slope EWMA de uma série temporal.
/// Fallback direto em lib.rs para garantir registro no módulo.
#[pyfunction]
fn compute_ewma_slope(py: Python<'_>, values: Vec<f64>, span: usize) -> PyResult<PyObject> {
    let slope = py.allow_threads(|| {
        if values.len() < 2 { return 0.0; }
        let alpha = 2.0 / (span as f64 + 1.0);
        let mut prev = values[0];
        let mut last = prev;
        for &v in &values[1..] {
            prev = last;
            last = alpha * v + (1.0 - alpha) * prev;
        }
        last - prev
    });
    let dict = PyDict::new_bound(py);
    dict.set_item("slope", slope)?;
    dict.set_item("eta_minutes", py.None())?;
    Ok(dict.into())
}

/// Módulo nativo _aiops_rust — extensão PyO3 para aceleração do pipeline AIOps.
#[pymodule]
fn _aiops_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ContextCompressor (Story 1.2)
    m.add_function(wrap_pyfunction!(context_compressor::compress_context, m)?)?;
    // LogParser / Drain3 sem cap de amostragem (Story 1.3)
    m.add_function(wrap_pyfunction!(log_parser::parse_logs, m)?)?;
    // MetricFilter / 3-sigma anomaly detection
    m.add_function(wrap_pyfunction!(metric_filter::filter_metrics, m)?)?;
    // TraceFilter / p95 latency filtering
    m.add_function(wrap_pyfunction!(trace_filter::filter_traces, m)?)?;
    // TelemetrySanitizer / AIOpsShield (batch regex via Rayon)
    m.add_function(wrap_pyfunction!(telemetry_sanitizer::detect_taint_batch, m)?)?;
    m.add_function(wrap_pyfunction!(telemetry_sanitizer::detect_taint_single, m)?)?;
    // CUSUM change-point detection e EWMA slope (Story 1.2)
    m.add_function(wrap_pyfunction!(detect_cusum, m)?)?;
    m.add_function(wrap_pyfunction!(compute_ewma_slope, m)?)?;
    // TracePredictor: _percentile + detect_latency_drift (Story 6.1)
    m.add_function(wrap_pyfunction!(trace_predictor::detect_latency_drift, m)?)?;
    Ok(())
}
