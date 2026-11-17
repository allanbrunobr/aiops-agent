/// compress_inner() — lógica de compressão sem GIL, espelha ContextCompressor.compress().
use super::classifier::{
    classify_log_priority, classify_metric_priority, classify_trace_priority, Priority,
};
use super::render::render_report;
use super::types::{
    AggregatedReportProxy, CompressionStatsProxy, LogTemplateProxy, MetricAnomalyProxy,
    SlowTraceProxy,
};

const TOKEN_BUDGET: usize = 4000;

/// Chunk interno após classificação — análogo ao CompressedChunk Python
#[derive(Clone)]
pub struct CompressedChunkInternal {
    pub text: String,
    pub priority: Priority,
    pub source: String,
    pub tokens_estimate: usize,
    pub service: String,
    pub relevance_score: f64,
}

/// Espelha ContextCompressor._estimate_tokens() — ~4 chars por token (Claude).
/// Usa contagem de codepoints Unicode (como Python len()) — não bytes.
fn estimate_tokens(text: &str) -> usize {
    text.chars().count() / 4
}

/// Trunca string a N codepoints Unicode para limitar tokens.
fn truncate_str(s: &str, max_chars: usize) -> &str {
    match s.char_indices().nth(max_chars) {
        Some((end, _)) => &s[..end],
        None => s,
    }
}

/// Formato ultra-compacto de log: apenas template (level/svc omitidos — redundantes com header/marker)
fn format_log_chunk(tpl: &LogTemplateProxy) -> String {
    let tmpl = truncate_str(&tpl.template, 35);
    let ellipsis = if tpl.template.chars().count() > 35 {
        "…"
    } else {
        ""
    };
    format!("\"{}{}\"", tmpl, ellipsis)
}

/// Formato mínimo de métrica: svc/metric type σ (val omitido para reduzir tokens)
fn format_metric_chunk(anomaly: &MetricAnomalyProxy) -> String {
    // Abrevia anomaly_type para reduzir tokens: level_shift→ls, spike→sp
    let atype = match anomaly.anomaly_type.as_str() {
        "level_shift" => "ls",
        "spike" => "sp",
        other => other,
    };
    format!(
        "{}/{} {} {:.0}\u{03c3}",
        anomaly.service, anomaly.metric_name, atype, anomaly.deviation_sigma
    )
}

/// Formato mínimo de trace: bottleneck Xms/Yms
fn format_trace_chunk(trace: &SlowTraceProxy) -> String {
    format!(
        "{} {:.0}/{:.0}ms",
        trace.bottleneck_service, trace.total_duration_ms, trace.normal_duration_ms
    )
}

/// Espelha ContextCompressor._classify_chunks()
fn classify_chunks(report: &AggregatedReportProxy) -> Vec<CompressedChunkInternal> {
    let mut chunks = Vec::new();

    for tpl in &report.log_templates {
        let priority = classify_log_priority(tpl);
        let text = format_log_chunk(tpl);
        let tokens = estimate_tokens(&text);
        let mut svcs: Vec<&str> = tpl.services.iter().map(|s| s.as_str()).collect();
        svcs.sort();
        let service = svcs.join(", ");
        chunks.push(CompressedChunkInternal {
            text,
            priority,
            source: "log".to_string(),
            tokens_estimate: tokens,
            service,
            relevance_score: tpl.relevance_score,
        });
    }

    for anomaly in &report.metric_anomalies {
        let priority = classify_metric_priority(anomaly);
        let text = format_metric_chunk(anomaly);
        let tokens = estimate_tokens(&text);
        chunks.push(CompressedChunkInternal {
            text,
            priority,
            source: "metric".to_string(),
            tokens_estimate: tokens,
            service: anomaly.service.clone(),
            relevance_score: anomaly.deviation_sigma,
        });
    }

    for trace in &report.slow_traces {
        let priority = classify_trace_priority(trace);
        let text = format_trace_chunk(trace);
        let tokens = estimate_tokens(&text);
        let slowdown = trace.total_duration_ms / trace.normal_duration_ms.max(1.0);
        chunks.push(CompressedChunkInternal {
            text,
            priority,
            source: "trace".to_string(),
            tokens_estimate: tokens,
            service: trace.bottleneck_service.clone(),
            relevance_score: slowdown,
        });
    }

    chunks
}

/// Espelha ContextCompressor._summarize_chunk() — mantém primeiras max_lines linhas
fn summarize_chunk(text: &str, ratio: f64) -> String {
    let lines: Vec<&str> = text.split('\n').collect();
    let max_lines = std::cmp::max(1, (lines.len() as f64 * ratio) as usize);
    lines[..max_lines].join("\n")
}

/// Comparador para sort descendente por (priority, relevance_score)
fn cmp_chunk_desc(a: &CompressedChunkInternal, b: &CompressedChunkInternal) -> std::cmp::Ordering {
    b.priority.cmp(&a.priority).then_with(|| {
        b.relevance_score
            .partial_cmp(&a.relevance_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    })
}

/// Espelha ContextCompressor._deduplicate_chunks()
/// Preserva ordem de inserção dos grupos (como dict Python 3.7+)
fn deduplicate_chunks(
    chunks: Vec<CompressedChunkInternal>,
    max_similar: usize,
) -> Vec<CompressedChunkInternal> {
    let mut group_order: Vec<(String, String)> = Vec::new();
    let mut groups: std::collections::HashMap<(String, String), Vec<CompressedChunkInternal>> =
        std::collections::HashMap::new();

    for chunk in chunks {
        let key = (chunk.source.clone(), chunk.service.clone());
        if !groups.contains_key(&key) {
            group_order.push(key.clone());
            groups.insert(key.clone(), Vec::new());
        }
        groups.get_mut(&key).unwrap().push(chunk);
    }

    let mut result: Vec<CompressedChunkInternal> = Vec::new();
    for key in &group_order {
        let group = groups.get_mut(key).unwrap();
        group.sort_by(cmp_chunk_desc);
        result.extend_from_slice(&group[..group.len().min(max_similar)]);
    }

    result.sort_by(cmp_chunk_desc);
    result
}

/// Espelha ContextCompressor._apply_compression() com nível "balanced"
fn apply_compression(chunks: Vec<CompressedChunkInternal>) -> Vec<CompressedChunkInternal> {
    // balanced: min_priority=Context(1), max_similar=10, summary_ratio=0.5
    let min_priority = Priority::Context;
    let max_similar: usize = 10;
    let summary_ratio: f64 = 0.5;
    let preserve_critical = true;

    let mut compressed: Vec<CompressedChunkInternal> = Vec::new();
    for mut chunk in chunks {
        if chunk.priority < min_priority {
            continue;
        }
        if (chunk.priority == Priority::Critical && preserve_critical)
            || chunk.priority == Priority::Important
        {
            compressed.push(chunk);
        } else {
            let new_text = summarize_chunk(&chunk.text, summary_ratio);
            chunk.tokens_estimate = estimate_tokens(&new_text);
            chunk.text = new_text;
            compressed.push(chunk);
        }
    }

    deduplicate_chunks(compressed, max_similar)
}

/// Espelha ContextCompressor._enforce_budget()
fn enforce_budget(
    chunks: Vec<CompressedChunkInternal>,
) -> (Vec<CompressedChunkInternal>, CompressionStatsProxy) {
    let total_original: usize = chunks.iter().map(|c| c.tokens_estimate).sum();
    let total_tokens: usize = total_original;

    if total_tokens <= TOKEN_BUDGET {
        let count = chunks.len();
        return (
            chunks,
            CompressionStatsProxy {
                original_tokens: total_original,
                compressed_tokens: total_tokens,
                chunks_kept: count,
                chunks_removed: 0,
                critical_preserved: 100.0,
                compression_ratio: 0.0,
            },
        );
    }

    let mut sorted = chunks.clone();
    sorted.sort_by(cmp_chunk_desc);

    let critical_total = chunks
        .iter()
        .filter(|c| c.priority == Priority::Critical)
        .count();
    let mut selected: Vec<CompressedChunkInternal> = Vec::new();
    let mut current_tokens: usize = 0;
    let mut removed: usize = 0;
    let mut critical_kept: usize = 0;

    for chunk in sorted {
        if current_tokens + chunk.tokens_estimate <= TOKEN_BUDGET {
            if chunk.priority == Priority::Critical {
                critical_kept += 1;
            }
            current_tokens += chunk.tokens_estimate;
            selected.push(chunk);
        } else if chunk.priority == Priority::Critical {
            // Sempre inclui críticos mesmo excedendo o budget
            critical_kept += 1;
            current_tokens += chunk.tokens_estimate;
            selected.push(chunk);
        } else {
            removed += 1;
        }
    }

    let kept = selected.len();
    (
        selected,
        CompressionStatsProxy {
            original_tokens: total_original,
            compressed_tokens: current_tokens,
            chunks_kept: kept,
            chunks_removed: removed,
            critical_preserved: 100.0 * critical_kept as f64 / critical_total.max(1) as f64,
            compression_ratio: 0.0,
        },
    )
}

/// Ponto de entrada principal — chamado dentro de py.detach().
/// Espelha ContextCompressor.compress() com parâmetros padrão.
pub fn compress_inner(proxy: AggregatedReportProxy) -> (String, CompressionStatsProxy) {
    let chunks = classify_chunks(&proxy);
    let compressed = apply_compression(chunks);
    let (final_chunks, mut stats) = enforce_budget(compressed);
    let text = render_report(&final_chunks, &proxy);
    stats.compressed_tokens = estimate_tokens(&text);
    stats.compression_ratio =
        1.0 - (stats.compressed_tokens as f64 / stats.original_tokens.max(1) as f64);
    (text, stats)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn report(n_errors: u64, sigma: f64) -> AggregatedReportProxy {
        AggregatedReportProxy {
            log_templates: vec![LogTemplateProxy {
                template: "Connection pool exhausted, waiting=<*>".into(),
                level: "ERROR".into(),
                count: n_errors,
                relevance_score: 7.0,
                services: vec!["payment-svc".into()],
            }],
            metric_anomalies: vec![MetricAnomalyProxy {
                service: "payment-svc".into(),
                metric_name: "error_rate".into(),
                anomaly_type: "level_shift".into(),
                value: 0.4,
                mean: 0.01,
                deviation_sigma: sigma,
            }],
            slow_traces: vec![],
            total_logs: n_errors,
            total_metrics: 10,
            total_traces: 0,
        }
    }

    #[test]
    fn truncate_str_counts_codepoints_not_bytes() {
        assert_eq!(truncate_str("héllo wörld", 5), "héllo");
        assert_eq!(truncate_str("abc", 10), "abc");
        assert_eq!(truncate_str("", 3), "");
    }

    #[test]
    fn estimate_tokens_is_chars_over_four() {
        assert_eq!(estimate_tokens("abcdefgh"), 2);
        assert_eq!(estimate_tokens("ééééééé"), 1); // 7 codepoints, não 14 bytes
    }

    /// Render = severidade[:4] — contrato com o Python (diagnose usa o report).
    #[test]
    fn compress_inner_renders_severity_prefix_and_valid_stats() {
        let (text, stats) = compress_inner(report(2000, 9.0));
        assert_eq!(text, "CRIT");
        assert_eq!(stats.chunks_removed, 0);
        assert_eq!(stats.chunks_kept, 2);
        assert_eq!(stats.critical_preserved, 100.0);
        assert!((0.0..=1.0).contains(&stats.compression_ratio));
        assert!(stats.compressed_tokens <= stats.original_tokens.max(1));
    }

    #[test]
    fn low_severity_report_renders_low() {
        let (text, _) = compress_inner(report(3, 1.0));
        assert_eq!(text, "LOW");
    }

    #[test]
    fn empty_report_does_not_panic() {
        let empty = AggregatedReportProxy {
            log_templates: vec![],
            metric_anomalies: vec![],
            slow_traces: vec![],
            total_logs: 0,
            total_metrics: 0,
            total_traces: 0,
        };
        let (text, stats) = compress_inner(empty);
        assert_eq!(text, "LOW");
        assert_eq!(stats.chunks_kept, 0);
        assert_eq!(stats.original_tokens, 0);
    }

    /// Chunks Noise (INFO de baixa relevância) são descartados no nível balanced.
    #[test]
    fn noise_chunks_are_dropped() {
        let mut r = report(2000, 9.0);
        r.log_templates.push(LogTemplateProxy {
            template: "heartbeat ok".into(),
            level: "INFO".into(),
            count: 500,
            relevance_score: 0.1,
            services: vec!["svc".into()],
        });
        let (_, stats) = compress_inner(r);
        assert_eq!(stats.chunks_kept, 2); // o INFO ruidoso não entra
    }
}
