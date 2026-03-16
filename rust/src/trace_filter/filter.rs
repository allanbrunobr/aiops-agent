/// Lógica de filtragem de traces por latência p95 em Rust puro.
///
/// Porta fiel do TraceFilter Python (pipeline/trace_filter.py):
///   - Agrupamento por trace_id
///   - p95 threshold por duração do root span
///   - Extração de call path via DFS (máx max_hops hops)
///   - Identificação de bottleneck (span de maior duração)
///   - Deduplicação por padrão de path (mantém o mais lento)

use std::collections::HashMap;
use super::types::{SlowCallPathData, SpanData};

pub fn filter_slow_traces(
    spans: Vec<SpanData>,
    percentile_threshold: f64,
    max_hops: usize,
) -> Vec<SlowCallPathData> {
    // Agrupar spans por trace_id
    let mut traces: HashMap<String, Vec<usize>> = HashMap::new();
    for (i, span) in spans.iter().enumerate() {
        traces.entry(span.trace_id.clone()).or_default().push(i);
    }

    // Duração por trace = duração do root span (parent_span_id == None)
    let mut trace_durations: HashMap<String, f64> = HashMap::new();
    for (trace_id, indices) in &traces {
        for &i in indices {
            if spans[i].parent_span_id.is_none() {
                trace_durations.insert(trace_id.clone(), spans[i].duration_ms);
                break;
            }
        }
    }

    if trace_durations.is_empty() {
        return vec![];
    }

    // Calcular p95 threshold
    let all_durations: Vec<f64> = trace_durations.values().copied().collect();
    let threshold = percentile(&all_durations, percentile_threshold);

    // Baseline mean = média das durações abaixo do threshold
    let below: Vec<f64> = all_durations.iter().copied().filter(|&d| d < threshold).collect();
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
    let mut path = Vec::new();
    let mut current_id = Some(spans[roots[0]].span_id.clone());
    let mut current_idx = roots[0];

    path.push(current_idx);

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

    for p in paths {
        let key = p.path.join(" → ");
        match best.get(&key) {
            Some(existing) if existing.total_duration_ms >= p.total_duration_ms => {}
            _ => {
                best.insert(key, p);
            }
        }
    }

    let mut result: Vec<SlowCallPathData> = best.into_values().collect();
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
