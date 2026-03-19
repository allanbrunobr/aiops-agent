/// Lógica de detecção CUSUM e cálculo EWMA em Rust puro.
///
/// CUSUM: S_t = max(0, S_{t-1} + x_t - k); dispara se S_t > h.
/// EWMA: alpha = 2/(span+1); slope = ewma[-1] - ewma[-2].

use super::types::{CusumResult, EwmaResult};

/// Detecta ponto de mudança em uma série usando o algoritmo CUSUM.
///
/// Parâmetros:
///   values — série de floats
///   k      — allowance (referência de drift permitido)
///   h      — threshold de disparo
pub fn cusum_detect(values: &[f64], k: f64, h: f64) -> CusumResult {
    if values.is_empty() {
        return CusumResult {
            change_point_detected: false,
            change_index: None,
        };
    }

    let mut s: f64 = 0.0;
    for (i, &x) in values.iter().enumerate() {
        s = (s + x - k).max(0.0);
        if s > h {
            return CusumResult {
                change_point_detected: true,
                change_index: Some(i),
            };
        }
    }

    CusumResult {
        change_point_detected: false,
        change_index: None,
    }
}

/// Calcula a inclinação EWMA de uma série de floats.
///
/// Retorna EwmaResult com slope = ewma[-1] - ewma[-2].
/// eta_minutes é sempre None aqui — calculado no lado Python com o threshold.
pub fn ewma_slope(values: &[f64], span: usize) -> EwmaResult {
    if values.len() < 2 {
        return EwmaResult {
            slope: 0.0,
            eta_minutes: None,
        };
    }

    let alpha = 2.0 / (span as f64 + 1.0);
    let mut ewma_prev = values[0];
    let mut ewma_last = ewma_prev;

    for &v in &values[1..] {
        ewma_prev = ewma_last;
        ewma_last = alpha * v + (1.0 - alpha) * ewma_prev;
    }

    EwmaResult {
        slope: ewma_last - ewma_prev,
        eta_minutes: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Série vazia não dispara CUSUM.
    #[test]
    fn test_cusum_empty_series() {
        let result = cusum_detect(&[], 0.5, 5.0);
        assert!(!result.change_point_detected);
        assert!(result.change_index.is_none());
    }

    /// Série monotonicamente crescente deve disparar CUSUM.
    #[test]
    fn test_cusum_monotonic_increase() {
        let values: Vec<f64> = (1..=20).map(|i| i as f64).collect();
        let result = cusum_detect(&values, 0.5, 5.0);
        assert!(result.change_point_detected);
        assert!(result.change_index.is_some());
    }

    /// Série com ruído pequeno em torno de zero não dispara CUSUM.
    #[test]
    fn test_cusum_noisy_flat() {
        let values: Vec<f64> = vec![0.1, -0.1, 0.05, -0.05, 0.0, 0.1, -0.1, 0.03, -0.03];
        let result = cusum_detect(&values, 1.0, 10.0);
        assert!(!result.change_point_detected);
    }

    /// Série constante retorna slope ≈ 0.
    #[test]
    fn test_ewma_flat_slope_near_zero() {
        let values: Vec<f64> = vec![5.0; 20];
        let result = ewma_slope(&values, 10);
        assert!(result.slope.abs() < 1e-9);
    }

    /// Série crescente retorna slope positivo.
    #[test]
    fn test_ewma_increasing_slope_positive() {
        let values: Vec<f64> = (1..=20).map(|i| i as f64).collect();
        let result = ewma_slope(&values, 10);
        assert!(result.slope > 0.0);
    }

    /// Série com menos de 2 pontos retorna slope 0.
    #[test]
    fn test_ewma_single_point() {
        let result = ewma_slope(&[42.0], 10);
        assert_eq!(result.slope, 0.0);
    }
}
