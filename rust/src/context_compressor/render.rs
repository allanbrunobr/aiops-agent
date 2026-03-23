/// render_report() — renderiza chunks comprimidos em texto ultra-compacto para o LLM.
use super::compressor::CompressedChunkInternal;
use super::types::AggregatedReportProxy;

/// Saída mínima: severidade do incidente (4 chars = 1 token).
/// Espelha Python: report.severity[:4] — diagnose usa AggregatedReport diretamente.
pub fn render_report(_chunks: &[CompressedChunkInternal], report: &AggregatedReportProxy) -> String {
    let sev = report.severity();
    sev.chars().take(4).collect()
}
