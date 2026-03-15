/// render_report() — renderiza chunks comprimidos em texto estruturado para o LLM.
/// Output deve ser byte-a-byte idêntico ao Python ContextCompressor._render().
use super::classifier::Priority;
use super::compressor::CompressedChunkInternal;
use super::types::AggregatedReportProxy;

/// Espelha ContextCompressor._render()
pub fn render_report(chunks: &[CompressedChunkInternal], report: &AggregatedReportProxy) -> String {
    let mut lines: Vec<String> = vec![
        format!(
            "# Compressed Incident Report | Severity: {}",
            report.severity()
        ),
        format!("Affected: {}", report.affected_services().join(", ")),
        format!(
            "Data: {} logs, {} metrics, {} traces",
            report.total_logs, report.total_metrics, report.total_traces
        ),
        String::new(),
    ];

    let log_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "log").collect();
    let metric_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "metric").collect();
    let trace_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "trace").collect();

    if !log_chunks.is_empty() {
        lines.push("## Log Analysis (compressed)".to_string());
        for c in &log_chunks {
            let marker = match c.priority {
                Priority::Critical => "\u{1f534}",  // 🔴
                Priority::Important => "\u{1f7e1}", // 🟡
                _ => "\u{26aa}",                    // ⚪
            };
            lines.push(format!("{} {}", marker, c.text));
        }
    }

    if !metric_chunks.is_empty() {
        lines.push("\n## Metric Anomalies (compressed)".to_string());
        for c in &metric_chunks {
            lines.push(format!("- {}", c.text));
        }
    }

    if !trace_chunks.is_empty() {
        lines.push("\n## Slow Traces (compressed)".to_string());
        for c in &trace_chunks {
            lines.push(format!("- {}", c.text));
        }
    }

    lines.push("\n## Summary".to_string());
    lines.push(format!("- Primary suspect: {}", report.primary_suspect()));
    lines.push(format!(
        "- Total error patterns: {}",
        report.error_template_count()
    ));
    lines.push(format!(
        "- Total anomalies: {}",
        report.metric_anomalies.len()
    ));

    lines.join("\n")
}
