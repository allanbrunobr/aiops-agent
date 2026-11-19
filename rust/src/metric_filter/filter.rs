use super::types::{MetricAnomalyData, MetricPointData};
/// Lógica de detecção de anomalias 3-sigma em Rust puro.
///
/// Porta fiel do MetricFilter Python (pipeline/metric_filter.py):
///   - Agrupamento por (service, metric_name)
///   - Baseline = primeiros 50% dos pontos ordenados por timestamp
///   - Detecção: |value - mean| / std >= sigma_threshold
///   - Classificação de forma: spike / level_shift / steady_increase
///   - Deduplicação: mantém o ponto de maior desvio por (service, metric)
use std::collections::HashMap;

pub fn detect_anomalies(
    metrics: Vec<MetricPointData>,
    sigma_threshold: f64,
    baseline_ratio: f64,
) -> Vec<MetricAnomalyData> {
    // Agrupar por (service, metric_name), na ordem de primeira aparição — como
    // o dict do Python. Iterar o HashMap direto dava ordem aleatória por
    // processo, e empates de deviation_sigma saíam em ordem diferente a cada
    // execução (achado pelo teste de paridade com sigma=1.5).
    let mut grouped: HashMap<(String, String), Vec<MetricPointData>> = HashMap::new();
    let mut order: Vec<(String, String)> = Vec::new();
    for m in metrics {
        let key = (m.service.clone(), m.metric_name.clone());
        if !grouped.contains_key(&key) {
            order.push(key.clone());
        }
        grouped.entry(key).or_default().push(m);
    }

    let mut anomalies: Vec<MetricAnomalyData> = Vec::new();

    for key in order {
        let mut points = grouped.remove(&key).unwrap_or_default();
        let (service, metric_name) = key;
        // Ordenar por timestamp
        points.sort_by(|a, b| {
            a.timestamp
                .partial_cmp(&b.timestamp)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        let values: Vec<f64> = points.iter().map(|p| p.value).collect();
        let n = values.len();

        // Baseline = primeira metade dos dados
        let baseline_end = ((n as f64) * baseline_ratio) as usize;
        if baseline_end < 5 {
            continue;
        }

        let baseline = &values[..baseline_end];
        let mean = compute_mean(baseline);
        let mut std = compute_std(baseline, mean);

        if std < 1e-6 {
            std = 0.01; // evita divisão por zero
        }

        // Detectar anomalias na segunda metade
        for i in baseline_end..n {
            let deviation = (values[i] - mean).abs() / std;
            if deviation >= sigma_threshold {
                let anomaly_type = classify_shape(&values, i, baseline_end);
                anomalies.push(MetricAnomalyData {
                    service: service.clone(),
                    metric_name: metric_name.clone(),
                    anomaly_type,
                    timestamp: points[i].timestamp,
                    value: round2(values[i]),
                    mean: round2(mean),
                    std: round2(std),
                    deviation_sigma: round1(deviation),
                });
            }
        }
    }

    deduplicate(anomalies)
}

/// Classifica a forma da anomalia (porta do _classify_shape Python).
fn classify_shape(values: &[f64], idx: usize, baseline_end: usize) -> String {
    let start = if baseline_end > idx.saturating_sub(10) {
        baseline_end
    } else {
        idx - 10
    };
    let window = &values[start..=idx];

    if window.len() < 3 {
        return "spike".to_string();
    }

    // Calcular diffs (equivalente ao np.diff)
    let diffs: Vec<f64> = window.windows(2).map(|w| w[1] - w[0]).collect();

    // Verificar se todos os diffs são positivos
    if diffs.iter().all(|&d| d > 0.0) {
        return "steady_increase".to_string();
    }

    // Verificar spike: |window[-1] - window[-2]| > 2 * std(window[:-1])
    let last = window[window.len() - 1];
    let second_last = window[window.len() - 2];
    let prev = &window[..window.len() - 1];
    let prev_mean = compute_mean(prev);
    let prev_std = compute_std(prev, prev_mean);

    if (last - second_last).abs() > 2.0 * prev_std {
        return "spike".to_string();
    }

    "level_shift".to_string()
}

/// Deduplicação: mantém o ponto de maior desvio por (service, metric_name).
fn deduplicate(anomalies: Vec<MetricAnomalyData>) -> Vec<MetricAnomalyData> {
    let mut best: HashMap<(String, String), MetricAnomalyData> = HashMap::new();
    let mut order: Vec<(String, String)> = Vec::new();

    for a in anomalies {
        let key = (a.service.clone(), a.metric_name.clone());
        if !best.contains_key(&key) {
            order.push(key.clone());
        }
        match best.get(&key) {
            Some(existing) if existing.deviation_sigma >= a.deviation_sigma => {}
            _ => {
                best.insert(key, a);
            }
        }
    }

    // Ordenar por deviation_sigma decrescente (igual ao Python): sort estável
    // sobre a ordem de primeira aparição, para empates saírem como no Python.
    let mut result: Vec<MetricAnomalyData> = order.iter().filter_map(|k| best.remove(k)).collect();
    result.sort_by(|a, b| {
        b.deviation_sigma
            .partial_cmp(&a.deviation_sigma)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    result
}

// ── Funções estatísticas ─────────────────────────────────────────────────────

fn compute_mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().sum::<f64>() / values.len() as f64
}

fn compute_std(values: &[f64], mean: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let variance = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / values.len() as f64;
    variance.sqrt()
}

fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pts(service: &str, metric: &str, values: &[f64]) -> Vec<MetricPointData> {
        values
            .iter()
            .enumerate()
            .map(|(i, &v)| MetricPointData {
                timestamp: 1_700_000_000.0 + i as f64,
                service: service.to_string(),
                metric_name: metric.to_string(),
                value: v,
            })
            .collect()
    }

    /// Baseline plano + salto na segunda metade → exatamente 1 anomalia
    /// (deduplicada por métrica), com o maior desvio.
    #[test]
    fn detects_single_deduplicated_anomaly() {
        let mut values = vec![10.0; 10];
        values.extend([10.0, 10.0, 50.0, 90.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]);
        let out = detect_anomalies(pts("svc", "cpu", &values), 3.0, 0.5);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].value, 90.0);
        assert_eq!(out[0].mean, 10.0);
        assert!(out[0].deviation_sigma > 3.0);
    }

    /// Menos de 5 pontos de baseline → métrica ignorada.
    #[test]
    fn skips_series_with_short_baseline() {
        let out = detect_anomalies(pts("svc", "cpu", &[1.0, 1.0, 1.0, 100.0]), 3.0, 0.5);
        assert!(out.is_empty());
    }

    /// Threshold mais estrito encontra <= anomalias que o frouxo.
    #[test]
    fn stricter_sigma_finds_fewer_or_equal() {
        let mut values = vec![10.0; 10];
        values.extend([10.5, 11.0, 12.0, 20.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]);
        let loose = detect_anomalies(pts("a", "m", &values), 1.0, 0.5).len();
        let strict = detect_anomalies(pts("a", "m", &values), 6.0, 0.5).len();
        assert!(strict <= loose);
    }

    /// baseline_ratio controla quantos pontos formam a baseline.
    #[test]
    fn baseline_ratio_changes_baseline_window() {
        let values: Vec<f64> = (0..20).map(|i| if i < 15 { 10.0 } else { 100.0 }).collect();
        // ratio 0.5 → baseline = 10 pontos planos → os 100.0 são anomalias
        assert_eq!(detect_anomalies(pts("a", "m", &values), 3.0, 0.5).len(), 1);
        // ratio 0.9 → baseline inclui 3 dos 100.0 → std enorme → nada anômalo
        assert!(detect_anomalies(pts("a", "m", &values), 3.0, 0.9).is_empty());
    }

    /// Série monotônica após a baseline é classificada como steady_increase.
    #[test]
    fn classifies_steady_increase() {
        let mut values = vec![10.0; 10];
        values.extend([11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0]);
        let out = detect_anomalies(pts("a", "m", &values), 3.0, 0.5);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].anomaly_type, "steady_increase");
    }

    /// Métricas distintas são avaliadas em separado e ordenadas por desvio desc.
    #[test]
    fn groups_by_service_and_metric_and_sorts_desc() {
        let mut a = vec![10.0; 10];
        a.extend([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 200.0]);
        let mut b = vec![10.0; 10];
        b.extend([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 50.0]);
        let mut all = pts("svc", "big", &a);
        all.extend(pts("svc", "small", &b));
        let out = detect_anomalies(all, 3.0, 0.5);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].metric_name, "big");
        assert!(out[0].deviation_sigma >= out[1].deviation_sigma);
    }
}
