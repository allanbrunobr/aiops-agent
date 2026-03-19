/// Tipos de dados internos para o módulo cusum_ewma.
///
/// CusumResult: resultado da detecção de ponto de mudança.
/// EwmaResult: resultado do cálculo de slope EWMA.

/// Resultado da detecção CUSUM: indica se houve ponto de mudança e em que índice.
pub struct CusumResult {
    pub change_point_detected: bool,
    pub change_index: Option<usize>,
}

/// Resultado do cálculo EWMA: slope e estimativa de minutos até o limiar.
pub struct EwmaResult {
    pub slope: f64,
    pub eta_minutes: Option<f64>,
}
