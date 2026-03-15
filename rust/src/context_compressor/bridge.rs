/// Fronteira PyO3 — extrai dados Python → Rust, libera GIL, retorna resultado.
use pyo3::prelude::*;
use pyo3::types::PyDict;

use super::compressor::compress_inner;
use super::types::{
    AggregatedReportProxy, LogTemplateProxy, MetricAnomalyProxy, SlowTraceProxy,
};

/// Função registrada como _aiops_rust.compress_context(report) -> (str, dict)
///
/// Extrai campos do AggregatedReport Python enquanto mantém o GIL,
/// depois chama py.allow_threads() para executar a compressão sem GIL,
/// permitindo execução paralela de múltiplos incidentes.
#[pyfunction]
pub fn compress_context(py: Python<'_>, report: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    // Fase 1: extrai todos os campos enquanto o GIL está mantido
    let proxy = extract_report_proxy(report)?;

    // Fase 2: libera GIL — todo o trabalho CPU acontece aqui
    let (text, stats) = py.allow_threads(|| compress_inner(proxy));

    // Fase 3: converte resultado para objetos Python
    let dict = PyDict::new_bound(py);
    dict.set_item("original_tokens", stats.original_tokens)?;
    dict.set_item("compressed_tokens", stats.compressed_tokens)?;
    dict.set_item("chunks_kept", stats.chunks_kept)?;
    dict.set_item("chunks_removed", stats.chunks_removed)?;
    dict.set_item("critical_preserved", stats.critical_preserved)?;
    dict.set_item("compression_ratio", stats.compression_ratio)?;

    let tuple = pyo3::types::PyTuple::new_bound(py, [text.into_py(py), dict.into_py(py)]);
    Ok(tuple.into())
}

/// Extrai AggregatedReport Python para AggregatedReportProxy Rust (com GIL mantido)
fn extract_report_proxy(report: &Bound<'_, PyAny>) -> PyResult<AggregatedReportProxy> {
    // Log templates
    let log_templates_py = report.getattr("log_templates")?;
    let mut log_templates: Vec<LogTemplateProxy> = Vec::new();
    for item in log_templates_py.iter()? {
        let tpl = item?;
        let template: String = tpl.getattr("template")?.extract()?;
        let count: u64 = tpl.getattr("count")?.extract()?;
        let level: String = tpl.getattr("level")?.extract()?;
        let relevance_score: f64 = tpl.getattr("relevance_score")?.extract()?;

        // services é um set Python — iteramos para obter Vec<String>
        let services_py = tpl.getattr("services")?;
        let mut services: Vec<String> = Vec::new();
        for svc in services_py.iter()? {
            services.push(svc?.extract::<String>()?);
        }

        log_templates.push(LogTemplateProxy {
            template,
            count,
            level,
            relevance_score,
            services,
        });
    }

    // Metric anomalies
    let metric_anomalies_py = report.getattr("metric_anomalies")?;
    let mut metric_anomalies: Vec<MetricAnomalyProxy> = Vec::new();
    for item in metric_anomalies_py.iter()? {
        let a = item?;
        metric_anomalies.push(MetricAnomalyProxy {
            service: a.getattr("service")?.extract()?,
            metric_name: a.getattr("metric_name")?.extract()?,
            anomaly_type: a.getattr("anomaly_type")?.extract()?,
            value: a.getattr("value")?.extract()?,
            mean: a.getattr("mean")?.extract()?,
            deviation_sigma: a.getattr("deviation_sigma")?.extract()?,
        });
    }

    // Slow traces
    let slow_traces_py = report.getattr("slow_traces")?;
    let mut slow_traces: Vec<SlowTraceProxy> = Vec::new();
    for item in slow_traces_py.iter()? {
        let t = item?;
        let path: Vec<String> = t.getattr("path")?.extract()?;
        let total_duration_ms: f64 = t.getattr("total_duration_ms")?.extract()?;
        let normal_duration_ms: f64 = t.getattr("normal_duration_ms")?.extract()?;
        let bottleneck_service: String = t.getattr("bottleneck_service")?.extract()?;

        // attributes é dict Python — preserva ordem de inserção
        let attrs_py = t.getattr("attributes")?;
        let attrs_dict = attrs_py.downcast::<PyDict>()?;
        let mut attributes: Vec<(String, String)> = Vec::new();
        for (k, v) in attrs_dict.iter() {
            let k_str = k.str()?.to_str()?.to_string();
            let v_str = v.str()?.to_str()?.to_string();
            attributes.push((k_str, v_str));
        }

        slow_traces.push(SlowTraceProxy {
            path,
            total_duration_ms,
            normal_duration_ms,
            bottleneck_service,
            attributes,
        });
    }

    let total_logs: u64 = report.getattr("total_logs")?.extract()?;
    let total_metrics: u64 = report.getattr("total_metrics")?.extract()?;
    let total_traces: u64 = report.getattr("total_traces")?.extract()?;

    Ok(AggregatedReportProxy {
        log_templates,
        metric_anomalies,
        slow_traces,
        total_logs,
        total_metrics,
        total_traces,
    })
}
