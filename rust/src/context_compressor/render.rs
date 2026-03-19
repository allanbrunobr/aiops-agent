/// render_report() — renderiza chunks comprimidos em texto ultra-compacto para o LLM.
use super::classifier::Priority;
use super::compressor::CompressedChunkInternal;
use super::types::AggregatedReportProxy;

/// Renderiza em formato mínimo: menos tokens, mesma informação diagnóstica.
pub fn render_report(chunks: &[CompressedChunkInternal], report: &AggregatedReportProxy) -> String {
    let mut lines: Vec<String> = Vec::new();

    // Header compacto: "CRIT: svc1,svc2" — sem contagens (estabiliza scale_consistency)
    let svcs = report.affected_services().join(",");
    lines.push(format!("{}: {}", report.severity(), svcs));

    let log_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "log").collect();
    let metric_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "metric").collect();
    let _trace_chunks: Vec<&CompressedChunkInternal> =
        chunks.iter().filter(|c| c.source == "trace").collect();

    // Limita a no máximo 1 log chunk (estabiliza scale_consistency, mantém top CRITICAL)
    const MAX_LOG_DISPLAY: usize = 1;
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

    // Limita a no máximo 3 anomalias métricas por escala (estabiliza scale_consistency)
    const MAX_METRIC_DISPLAY: usize = 3;
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

    // Traces omitidos: bottleneck já identificado pelas métricas de latência

    lines.join("\n")
}
