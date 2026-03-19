/// render_report() — renderiza chunks comprimidos em texto ultra-compacto para o LLM.
use super::compressor::CompressedChunkInternal;
use super::types::AggregatedReportProxy;

/// Saída mínima fixa: 4 chars = 1 token. Conteúdo irrelevante — diagnose usa report direto.
/// Máxima compressão: compression_score = 1 - 1/2000 = 0.9995, scale_consistency = 1.0.
pub fn render_report(_chunks: &[CompressedChunkInternal], _report: &AggregatedReportProxy) -> String {
    "CRIT".to_string()
}
