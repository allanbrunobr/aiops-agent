/// Lógica pura de detecção de drift de latência — sem dependências PyO3.
///
/// Implementa o mesmo algoritmo de `pipeline/trace_predictor.py`:
///   - `percentile`: índice linear interpolado sobre slice ordenado
///   - `detect_latency_drift_inner`: compara p95 e p99 entre duas janelas
#[forbid(unsafe_code)]

/// Resultado de detecção de drift retornado pela camada de lógica pura.
pub struct LatencyDriftResult {
    pub drift_detected: bool,
    pub p95_ratio: f64,
    pub p99_ratio: f64,
}

/// Calcula o percentil `p` (0–100) de `values` via interpolação linear.
///
/// Equivalente ao `_percentile` Python: ordena os valores e interpola entre
/// os índices vizinhos. Retorna 0.0 para lista vazia.
pub fn percentile(values: &[f64], p: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted: Vec<f64> = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let idx = (p / 100.0) * (sorted.len() as f64 - 1.0);
    let low = idx.floor() as usize;
    let frac = idx - low as f64;
    let high = low + 1;

    if high >= sorted.len() {
        return sorted[low];
    }
    sorted[low] + frac * (sorted[high] - sorted[low])
}

/// Detecta drift de latência comparando p95 e p99 entre baseline e current.
///
/// Parâmetros idênticos ao `detect_latency_drift` Python:
///   - `baseline`: durações em ms da janela de baseline (W1)
///   - `current`: durações em ms da janela corrente (W2)
///   - `threshold_pct`: limiar fracional; drift se ratio >= 1 + threshold_pct
pub fn detect_latency_drift_inner(
    baseline: &[f64],
    current: &[f64],
    threshold_pct: f64,
) -> LatencyDriftResult {
    let p95_baseline = percentile(baseline, 95.0);
    let p95_current = percentile(current, 95.0);
    let p99_baseline = percentile(baseline, 99.0);
    let p99_current = percentile(current, 99.0);

    let p95_ratio = p95_current / f64::max(p95_baseline, 0.001);
    let p99_ratio = p99_current / f64::max(p99_baseline, 0.001);

    let threshold = 1.0 + threshold_pct;
    let drift_detected = (p95_ratio >= threshold) || (p99_ratio >= threshold);

    LatencyDriftResult { drift_detected, p95_ratio, p99_ratio }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_percentile_empty() {
        assert_eq!(percentile(&[], 95.0), 0.0);
    }

    #[test]
    fn test_percentile_single() {
        assert_eq!(percentile(&[42.0], 50.0), 42.0);
        assert_eq!(percentile(&[42.0], 99.0), 42.0);
    }

    #[test]
    fn test_percentile_p0_e_p100() {
        let vals = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(percentile(&vals, 0.0), 1.0);
        assert_eq!(percentile(&vals, 100.0), 5.0);
    }

    #[test]
    fn test_percentile_p50_interpolado() {
        // [1,2,3,4] → p50 = índice 1.5 → 2.0 + 0.5*(3.0-2.0) = 2.5
        let vals = vec![1.0, 2.0, 3.0, 4.0];
        let p50 = percentile(&vals, 50.0);
        assert!((p50 - 2.5).abs() < 1e-9);
    }

    #[test]
    fn test_percentile_nao_altera_entrada() {
        // percentile recebe slice — original não é modificado
        let vals = vec![3.0, 1.0, 2.0];
        let _ = percentile(&vals, 50.0);
        assert_eq!(vals, vec![3.0, 1.0, 2.0]);
    }

    #[test]
    fn test_detect_drift_detectado() {
        let baseline: Vec<f64> = vec![100.0; 100];
        let current: Vec<f64> = vec![130.0; 100]; // 30% acima → drift
        let r = detect_latency_drift_inner(&baseline, &current, 0.20);
        assert!(r.drift_detected);
        assert!((r.p95_ratio - 1.3).abs() < 1e-6);
    }

    #[test]
    fn test_detect_drift_nao_detectado() {
        let baseline: Vec<f64> = vec![100.0; 100];
        let current: Vec<f64> = vec![110.0; 100]; // 10% acima < 20% limiar
        let r = detect_latency_drift_inner(&baseline, &current, 0.20);
        assert!(!r.drift_detected);
    }

    #[test]
    fn test_detect_drift_baseline_zero_nao_divide_por_zero() {
        let baseline: Vec<f64> = vec![0.0; 10];
        let current: Vec<f64> = vec![50.0; 10];
        let r = detect_latency_drift_inner(&baseline, &current, 0.20);
        // p95_baseline = 0.0 → max(0.0, 0.001) = 0.001 → ratio = 50/0.001 = 50000
        assert!(r.drift_detected);
    }

    #[test]
    fn test_percentile_equivalencia_python() {
        // Reproduz o cálculo exato do Python _percentile para n=5, p=95
        // sorted=[1,2,3,4,5]; idx=0.95*(5-1)=3.8; low=3; high=4; frac=0.8
        // resultado = 4.0 + 0.8*(5.0-4.0) = 4.8
        let vals = vec![3.0, 1.0, 5.0, 2.0, 4.0];
        let result = percentile(&vals, 95.0);
        assert!((result - 4.8).abs() < 1e-9);
    }
}
