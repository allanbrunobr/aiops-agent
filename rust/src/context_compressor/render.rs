/// render_report() — renderiza chunks comprimidos em texto ultra-compacto para o LLM.
use super::classifier::Priority;
use super::compressor::CompressedChunkInternal;
use super::types::AggregatedReportProxy;

/// Renderiza em formato mínimo: menos tokens, mesma informação diagnóstica.
pub fn render_report(chunks: &[CompressedChunkInternal], report: &AggregatedReportProxy) -> String {
    let mut lines: Vec<String> = Vec::new();

    // Header compacto: "CRIT: svc1,svc2 | 503L 2160M 278T"
    let svcs = report.affected_services().join(",");
    lines.push(format!(
        "{}: {} | {}L {}M {}T",
        report.severity(),
        svcs,
        report.total_logs,
        report.total_metrics,
        report.total_traces,
    ));

    let log_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "log").collect();
    let metric_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "metric").collect();
    let trace_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "trace").collect();

    // Limita a no máximo 10 log chunks (estabiliza scale_consistency, mantém top CRITICAL)
    const MAX_LOG_DISPLAY: usize = 10;
    let log_display = if log_chunks.len() > MAX_LOG_DISPLAY {
        &log_chunks[..MAX_LOG_DISPLAY]
    } else {
        &log_chunks[..]
    };

    if !log_display.is_empty() {
        lines.push("L:".to_string());
        for c in log_display {
            let lvl = match c.priority {
                Priority::Critical => "!",
                Priority::Important => "W",
                _ => "-",
            };
            lines.push(format!("{} {}", lvl, c.text));
        }
    }

    // Limita a no máximo 6 anomalias métricas por escala (estabiliza scale_consistency)
    const MAX_METRIC_DISPLAY: usize = 6;
    let metric_display = if metric_chunks.len() > MAX_METRIC_DISPLAY {
        &metric_chunks[..MAX_METRIC_DISPLAY]
    } else {
        &metric_chunks[..]
    };

    if !metric_display.is_empty() {
        lines.push("M:".to_string());
        for c in metric_display {
            lines.push(c.text.clone());
        }
    }

    if !trace_chunks.is_empty() {
        lines.push("T:".to_string());
        for c in &trace_chunks {
            lines.push(c.text.clone());
        }
    }

    lines.join("\n")
}
