use pyo3::prelude::*;

mod context_compressor;
mod log_parser;
mod metric_filter;
mod trace_filter;

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
    Ok(())
}
