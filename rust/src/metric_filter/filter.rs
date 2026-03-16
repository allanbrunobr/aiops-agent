/// Lógica de detecção de anomalias 3-sigma em Rust puro.
///
/// Porta fiel do MetricFilter Python (pipeline/metric_filter.py):
///   - Agrupamento por (service, metric_name)
///   - Baseline = primeiros 50% dos pontos ordenados por timestamp
///   - Detecção: |value - mean| / std >= sigma_threshold
///   - Classificação de forma: spike / level_shift / steady_increase
///   - Deduplicação: mantém o ponto de maior desvio por (service, metric)

use std::collections::HashMap;
use super::types::{MetricPointData, MetricAnomalyData};

pub fn detect_anomalies(
    metrics: Vec<MetricPointData>,
    sigma_threshold: f64,
    baseline_ratio: f64,
) -> Vec<MetricAnomalyData> {
    // Agrupar por (service, metric_name)
    let mut grouped: HashMap<(String, String), Vec<MetricPointData>> = HashMap::new();
    for m in metrics {
        let key = (m.service.clone(), m.metric_name.clone());
        grouped.entry(key).or_default().push(m);
    }

    let mut anomalies: Vec<MetricAnomalyData> = Vec::new();

    for ((service, metric_name), mut points) in grouped {
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

    for a in anomalies {
        let key = (a.service.clone(), a.metric_name.clone());
        match best.get(&key) {
            Some(existing) if existing.deviation_sigma >= a.deviation_sigma => {}
            _ => {
                best.insert(key, a);
            }
        }
    }

    // Ordenar por deviation_sigma decrescente (igual ao Python)
    let mut result: Vec<MetricAnomalyData> = best.into_values().collect();
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
