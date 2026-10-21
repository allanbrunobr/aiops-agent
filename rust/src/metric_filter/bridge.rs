/// Fronteira PyO3: converte MetricPoint Python → Rust → dicts Python.
///
/// Fluxo de execução:
///   1. Extrai campos dos MetricPoint Python enquanto segura o GIL
///   2. Libera o GIL para processar 3-sigma em Rust puro
///   3. Re-adquire o GIL para construir dicts Python de retorno
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use super::filter::detect_anomalies;
use super::types::MetricPointData;

/// Detecta anomalias 3-sigma em uma lista de MetricPoint Python.
///
/// Chamado por pipeline/metric_filter.py quando _RUST_AVAILABLE = True.
/// Retorna lista de dicts com campos equivalentes ao MetricAnomaly Python.
#[pyfunction]
pub fn filter_metrics(py: Python<'_>, metrics: &Bound<'_, PyList>) -> PyResult<Py<PyList>> {
    // ── Fase 1: extrair dados enquanto segura o GIL ───────────────────────────
    let points = extract_metric_points(py, metrics)?;

    // ── Fase 2: processar 3-sigma sem GIL ────────────────────────────────────
    let anomalies = py.detach(|| detect_anomalies(points, 3.0, 0.5));

    // ── Fase 3: construir dicts Python enquanto segura o GIL ─────────────────
    build_python_result(py, anomalies)
}

/// Extrai todos os campos de MetricPoint enquanto o GIL está ativo.
fn extract_metric_points(
    _py: Python<'_>,
    metrics: &Bound<'_, PyList>,
) -> PyResult<Vec<MetricPointData>> {
    let mut points = Vec::with_capacity(metrics.len());

    for m in metrics.iter() {
        let service: String = m.getattr("service")?.extract()?;
        let metric_name: String = m.getattr("metric_name")?.extract()?;
        let value: f64 = m.getattr("value")?.extract()?;

        // Extrair timestamp como epoch seconds via dt.timestamp()
        let timestamp: f64 = m.getattr("timestamp")?.call_method0("timestamp")?.extract()?;

        points.push(MetricPointData {
            timestamp,
            service,
            metric_name,
            value,
        });
    }

    Ok(points)
}

/// Constrói lista Python de dicts com campos equivalentes ao MetricAnomaly.
fn build_python_result(
    py: Python<'_>,
    anomalies: Vec<super::types::MetricAnomalyData>,
) -> PyResult<Py<PyList>> {
    let result = PyList::empty(py);

    for a in anomalies {
        let dict = PyDict::new(py);
        dict.set_item("service", &a.service)?;
        dict.set_item("metric_name", &a.metric_name)?;
        dict.set_item("anomaly_type", &a.anomaly_type)?;
        dict.set_item("timestamp", a.timestamp)?;
        dict.set_item("value", a.value)?;
        dict.set_item("mean", a.mean)?;
        dict.set_item("std", a.std)?;
        dict.set_item("deviation_sigma", a.deviation_sigma)?;
        result.append(&dict)?;
    }

    Ok(result.into())
}
