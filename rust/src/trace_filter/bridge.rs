/// Fronteira PyO3: converte Span Python → Rust → dicts Python.
///
/// Fluxo de execução:
///   1. Extrai campos dos Span Python enquanto segura o GIL
///   2. Libera o GIL para processar p95 + call paths em Rust puro
///   3. Re-adquire o GIL para construir dicts Python de retorno
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use super::filter::filter_slow_traces;
use super::types::SpanData;

/// Filtra traces lentos (>p95) e extrai call paths.
///
/// Chamado por pipeline/trace_filter.py quando _RUST_AVAILABLE = True.
/// Retorna lista de dicts com campos equivalentes ao SlowCallPath Python.
#[pyfunction]
pub fn filter_traces(py: Python<'_>, spans: &Bound<'_, PyList>) -> PyResult<Py<PyList>> {
    // ── Fase 1: extrair dados enquanto segura o GIL ───────────────────────────
    let span_data = extract_spans(py, spans)?;

    // ── Fase 2: processar p95 + DFS sem GIL ──────────────────────────────────
    let slow_paths = py.detach(|| filter_slow_traces(span_data, 95.0, 3));

    // ── Fase 3: construir dicts Python enquanto segura o GIL ─────────────────
    build_python_result(py, slow_paths)
}

/// Extrai todos os campos de Span enquanto o GIL está ativo.
fn extract_spans(_py: Python<'_>, spans: &Bound<'_, PyList>) -> PyResult<Vec<SpanData>> {
    let mut result = Vec::with_capacity(spans.len());

    for span in spans.iter() {
        let trace_id: String = span.getattr("trace_id")?.extract()?;
        let span_id: String = span.getattr("span_id")?.extract()?;
        let service: String = span.getattr("service")?.extract()?;
        let operation: String = span.getattr("operation")?.extract()?;
        let duration_ms: f64 = span.getattr("duration_ms")?.extract()?;
        let status: String = span.getattr("status")?.extract()?;

        // parent_span_id pode ser None
        let parent_span_id: Option<String> = span.getattr("parent_span_id")?.extract()?;

        // start_time como epoch seconds
        let start_time: f64 = span
            .getattr("start_time")?
            .call_method0("timestamp")?
            .extract()?;

        // attributes: dict Python → Vec<(String, String)>
        let attrs_obj = span.getattr("attributes")?;
        let attributes = extract_attributes(&attrs_obj)?;

        result.push(SpanData {
            trace_id,
            span_id,
            parent_span_id,
            service,
            operation,
            start_time,
            duration_ms,
            status,
            attributes,
        });
    }

    Ok(result)
}

/// Extrai atributos do dict Python como Vec de pares (String, String).
fn extract_attributes(attrs: &Bound<'_, PyAny>) -> PyResult<Vec<(String, String)>> {
    if attrs.is_none() {
        return Ok(vec![]);
    }

    // Tentar iterar como dict
    match attrs.cast::<PyDict>() {
        Ok(d) => {
            let mut result = Vec::new();
            for (k, v) in d.iter() {
                let key: String = k.str()?.to_str()?.to_owned();
                let val: String = v.str()?.to_str()?.to_owned();
                result.push((key, val));
            }
            Ok(result)
        }
        Err(_) => Ok(vec![]),
    }
}

/// Constrói lista Python de dicts com campos equivalentes ao SlowCallPath.
fn build_python_result(
    py: Python<'_>,
    paths: Vec<super::types::SlowCallPathData>,
) -> PyResult<Py<PyList>> {
    let result = PyList::empty(py);
    let datetime_mod = py.import("datetime")?;
    let datetime_cls = datetime_mod.getattr("datetime")?;

    for p in paths {
        let dict = PyDict::new(py);
        dict.set_item("trace_id", &p.trace_id)?;
        dict.set_item("path", &p.path)?;
        dict.set_item("operations", &p.operations)?;
        dict.set_item("total_duration_ms", p.total_duration_ms)?;
        dict.set_item("bottleneck_service", &p.bottleneck_service)?;
        dict.set_item("bottleneck_duration_ms", p.bottleneck_duration_ms)?;
        dict.set_item("normal_duration_ms", p.normal_duration_ms)?;

        // Converter timestamp → datetime Python
        let dt = datetime_cls.call_method1("fromtimestamp", (p.timestamp,))?;
        dict.set_item("timestamp", dt)?;

        // Reconstrói attributes como dict Python
        let attrs_dict = PyDict::new(py);
        for (k, v) in &p.attributes {
            attrs_dict.set_item(k, v)?;
        }
        dict.set_item("attributes", attrs_dict)?;

        result.append(&dict)?;
    }

    Ok(result.into())
}
