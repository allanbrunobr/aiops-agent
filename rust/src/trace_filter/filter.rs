use super::types::{SlowCallPathData, SpanData};
/// Lógica de filtragem de traces por latência p95 em Rust puro.
///
/// Porta fiel do TraceFilter Python (pipeline/trace_filter.py):
///   - Agrupamento por trace_id
///   - p95 threshold por duração do root span
///   - Extração de call path via DFS (máx max_hops hops)
///   - Identificação de bottleneck (span de maior duração)
///   - Deduplicação por padrão de path (mantém o mais lento)
use std::collections::HashMap;

pub fn filter_slow_traces(
    spans: Vec<SpanData>,
    percentile_threshold: f64,
    max_hops: usize,
) -> Vec<SlowCallPathData> {
    // Agrupar spans por trace_id, na ordem de primeira aparição (como o dict
    // do Python). Iterar o HashMap dava ordem aleatória por processo: empates
    // de duração escolhiam trace diferente na deduplicação a cada execução.
    let mut traces: HashMap<String, Vec<usize>> = HashMap::new();
    let mut trace_order: Vec<String> = Vec::new();
    for (i, span) in spans.iter().enumerate() {
        if !traces.contains_key(&span.trace_id) {
            trace_order.push(span.trace_id.clone());
        }
        traces.entry(span.trace_id.clone()).or_default().push(i);
    }

    // Duração por trace = duração do root span (parent_span_id == None)
    let mut trace_durations: Vec<(String, f64)> = Vec::new();
    for trace_id in &trace_order {
        for &i in &traces[trace_id] {
            if spans[i].parent_span_id.is_none() {
                trace_durations.push((trace_id.clone(), spans[i].duration_ms));
                break;
            }
        }
    }

    if trace_durations.is_empty() {
        return vec![];
    }

    // Calcular p95 threshold
    let all_durations: Vec<f64> = trace_durations.iter().map(|(_, d)| *d).collect();
    let threshold = percentile(&all_durations, percentile_threshold);

    // Baseline mean = média das durações abaixo do threshold
    let below: Vec<f64> = all_durations
        .iter()
        .copied()
        .filter(|&d| d < threshold)
        .collect();
    let baseline_mean = if below.is_empty() {
        threshold
    } else {
        below.iter().sum::<f64>() / below.len() as f64
    };

    // Processar traces lentos
    let mut slow_paths: Vec<SlowCallPathData> = Vec::new();

    for (trace_id, duration) in &trace_durations {
        if *duration < threshold {
            continue;
        }

        let indices = &traces[trace_id];

        // Ordenar spans por start_time
        let mut sorted_indices = indices.clone();
        sorted_indices.sort_by(|&a, &b| {
            spans[a]
                .start_time
                .partial_cmp(&spans[b].start_time)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        // Extrair call path via DFS (máx max_hops)
        let path_indices = extract_call_path(&spans, &sorted_indices, max_hops);
        if path_indices.is_empty() {
            continue;
        }

        // Bottleneck = span de maior duração
        let bottleneck_idx = *sorted_indices
            .iter()
            .max_by(|&&a, &&b| {
                spans[a]
                    .duration_ms
                    .partial_cmp(&spans[b].duration_ms)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .unwrap_or(&sorted_indices[0]);

        let path: Vec<String> = path_indices
            .iter()
            .map(|&i| spans[i].service.clone())
            .collect();
        let operations: Vec<String> = path_indices
            .iter()
            .map(|&i| spans[i].operation.clone())
            .collect();
        let timestamp = spans[sorted_indices[0]].start_time;

        slow_paths.push(SlowCallPathData {
            trace_id: trace_id.clone(),
            path,
            operations,
            total_duration_ms: *duration,
            bottleneck_service: spans[bottleneck_idx].service.clone(),
            bottleneck_duration_ms: spans[bottleneck_idx].duration_ms,
            normal_duration_ms: (baseline_mean * 10.0).round() / 10.0,
            timestamp,
            attributes: spans[bottleneck_idx].attributes.clone(),
        });
    }

    deduplicate_paths(slow_paths)
}

/// DFS para extrair call path (até max_hops hops) a partir do root.
fn extract_call_path(spans: &[SpanData], sorted_indices: &[usize], max_hops: usize) -> Vec<usize> {
    if sorted_indices.is_empty() {
        return vec![];
    }

    // Build parent → children map
    let mut children: HashMap<Option<String>, Vec<usize>> = HashMap::new();
    for &i in sorted_indices {
        children
            .entry(spans[i].parent_span_id.clone())
            .or_default()
            .push(i);
    }

    // Raízes = spans sem parent
    let roots = children.get(&None).cloned().unwrap_or_default();
    if roots.is_empty() {
        // Fallback: primeiros max_hops spans ordenados
        return sorted_indices.iter().take(max_hops).copied().collect();
    }

    // DFS linear (sempre primeiro filho)
    let mut current_id = Some(spans[roots[0]].span_id.clone());
    let mut current_idx = roots[0];
    let mut path = vec![current_idx];

    while path.len() < max_hops {
        let kids = children.get(&current_id).cloned().unwrap_or_default();
        if kids.is_empty() {
            break;
        }
        current_idx = kids[0];
        current_id = Some(spans[current_idx].span_id.clone());
        path.push(current_idx);
    }

    path
}

/// Deduplicação: mantém o trace mais lento de cada padrão de path.
fn deduplicate_paths(paths: Vec<SlowCallPathData>) -> Vec<SlowCallPathData> {
    let mut best: HashMap<String, SlowCallPathData> = HashMap::new();
    let mut order: Vec<String> = Vec::new();

    for p in paths {
        let key = p.path.join(" → ");
        if !best.contains_key(&key) {
            order.push(key.clone());
        }
        match best.get(&key) {
            Some(existing) if existing.total_duration_ms >= p.total_duration_ms => {}
            _ => {
                best.insert(key, p);
            }
        }
    }

    let mut result: Vec<SlowCallPathData> = order.iter().filter_map(|k| best.remove(k)).collect();
    result.sort_by(|a, b| {
        b.total_duration_ms
            .partial_cmp(&a.total_duration_ms)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    result
}

/// Calcula o percentil usando interpolação linear (igual ao numpy.percentile).
fn percentile(values: &[f64], p: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let n = sorted.len();
    if n == 1 {
        return sorted[0];
    }

    let idx = p / 100.0 * (n as f64 - 1.0);
    let lower = idx.floor() as usize;
    let upper = idx.ceil() as usize;

    if lower == upper {
        sorted[lower]
    } else {
        let frac = idx - lower as f64;
        sorted[lower] + frac * (sorted[upper] - sorted[lower])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn span(
        trace: &str,
        id: &str,
        parent: Option<&str>,
        svc: &str,
        start: f64,
        dur: f64,
    ) -> SpanData {
        SpanData {
            trace_id: trace.to_string(),
            span_id: id.to_string(),
            parent_span_id: parent.map(str::to_string),
            service: svc.to_string(),
            operation: format!("op-{svc}"),
            start_time: start,
            duration_ms: dur,
            status: "ok".to_string(),
            attributes: vec![("k".to_string(), "v".to_string())],
        }
    }

    /// 10 traces: 9 rápidos e 1 lento → só o lento passa o p95.
    #[test]
    fn selects_only_traces_above_percentile() {
        let mut spans = Vec::new();
        for i in 0..9 {
            spans.push(span(&format!("t{i}"), "r", None, "gw", 0.0, 100.0));
        }
        spans.push(span("slow", "r", None, "gw", 0.0, 5000.0));
        let out = filter_slow_traces(spans, 95.0, 3);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].trace_id, "slow");
        assert_eq!(out[0].total_duration_ms, 5000.0);
        assert_eq!(out[0].normal_duration_ms, 100.0);
    }

    /// Call path segue parent→child (DFS), não a ordem temporal dos spans.
    #[test]
    fn call_path_follows_parent_child_chain() {
        let spans = vec![
            // filho começa ANTES do pai no relógio para provar que a ordem é causal
            span("t", "c", Some("b"), "postgres", 0.5, 900.0),
            span("t", "a", None, "gateway", 0.0, 1000.0),
            span("t", "b", Some("a"), "payment", 0.2, 950.0),
            span("fast", "x", None, "gateway", 0.0, 10.0),
        ];
        let out = filter_slow_traces(spans, 50.0, 3);
        let slow = out
            .iter()
            .find(|p| p.trace_id == "t")
            .expect("trace lento presente");
        assert_eq!(slow.path, vec!["gateway", "payment", "postgres"]);
        assert_eq!(slow.bottleneck_service, "gateway"); // span mais longo
        assert_eq!(slow.attributes, vec![("k".to_string(), "v".to_string())]);
    }

    /// max_hops limita o comprimento do path.
    #[test]
    fn max_hops_truncates_path() {
        let spans = vec![
            span("t", "a", None, "s1", 0.0, 1000.0),
            span("t", "b", Some("a"), "s2", 0.1, 900.0),
            span("t", "c", Some("b"), "s3", 0.2, 800.0),
            span("fast", "x", None, "s1", 0.0, 1.0),
        ];
        let out = filter_slow_traces(spans, 50.0, 2);
        let slow = out.iter().find(|p| p.trace_id == "t").unwrap();
        assert_eq!(slow.path.len(), 2);
    }

    /// Mesmo padrão de path: mantém só o mais lento.
    #[test]
    fn deduplicates_by_path_keeping_slowest() {
        let mut spans = vec![
            span("t1", "a", None, "gw", 0.0, 3000.0),
            span("t2", "a", None, "gw", 0.0, 4000.0),
        ];
        for i in 0..8 {
            spans.push(span(&format!("f{i}"), "a", None, "other", 0.0, 10.0));
        }
        let out = filter_slow_traces(spans, 50.0, 3);
        let gw: Vec<_> = out.iter().filter(|p| p.path == vec!["gw"]).collect();
        assert_eq!(gw.len(), 1);
        assert_eq!(gw[0].trace_id, "t2");
    }

    /// Traces sem root span não têm duração → nada é retornado.
    #[test]
    fn no_root_spans_returns_empty() {
        let spans = vec![span("t", "b", Some("missing"), "s", 0.0, 100.0)];
        assert!(filter_slow_traces(spans, 95.0, 3).is_empty());
    }

    /// percentile = interpolação linear como numpy.percentile.
    #[test]
    fn percentile_matches_numpy_linear_interpolation() {
        let v = [1.0, 2.0, 3.0, 4.0];
        assert!((percentile(&v, 50.0) - 2.5).abs() < 1e-12);
        assert!((percentile(&v, 100.0) - 4.0).abs() < 1e-12);
        assert!((percentile(&v, 0.0) - 1.0).abs() < 1e-12);
        assert_eq!(percentile(&[7.0], 95.0), 7.0);
        assert_eq!(percentile(&[], 95.0), 0.0);
    }
}
