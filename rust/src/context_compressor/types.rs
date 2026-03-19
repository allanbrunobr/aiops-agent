/// Structs proxy para transferência de dados Python → Rust através da fronteira PyO3.
/// Todos os campos são extraídos do Python enquanto o GIL está sendo mantido.

/// Proxy para pipeline.log_parser.LogTemplate
#[derive(Debug, Clone)]
pub struct LogTemplateProxy {
    pub template: String,
    pub level: String,
    pub count: u64,
    pub relevance_score: f64,
    pub services: Vec<String>,
}

/// Proxy para pipeline.metric_filter.MetricAnomaly
#[derive(Debug, Clone)]
pub struct MetricAnomalyProxy {
    pub service: String,
    pub metric_name: String,
    pub anomaly_type: String,
    pub value: f64,
    #[allow(dead_code)]
    pub mean: f64,
    pub deviation_sigma: f64,
}

/// Proxy para pipeline.trace_filter.SlowCallPath
/// attributes usa Vec para preservar ordem de inserção do dict Python
#[derive(Debug, Clone)]
pub struct SlowTraceProxy {
    #[allow(dead_code)]
    pub path: Vec<String>,
    pub total_duration_ms: f64,
    pub normal_duration_ms: f64,
    pub bottleneck_service: String,
    #[allow(dead_code)]
    pub attributes: Vec<(String, String)>,
}

/// Proxy para pipeline.aggregator.AggregatedReport
#[derive(Debug, Clone)]
pub struct AggregatedReportProxy {
    pub log_templates: Vec<LogTemplateProxy>,
    pub metric_anomalies: Vec<MetricAnomalyProxy>,
    pub slow_traces: Vec<SlowTraceProxy>,
    pub total_logs: u64,
    pub total_metrics: u64,
    pub total_traces: u64,
}

#[allow(dead_code)]
impl AggregatedReportProxy {
    /// Espelha AggregatedReport.severity (property Python)
    pub fn severity(&self) -> String {
        let error_count: u64 = self
            .log_templates
            .iter()
            .filter(|t| t.level == "ERROR" || t.level == "FATAL")
            .map(|t| t.count)
            .sum();
        let high_sigma = self.metric_anomalies.iter().any(|a| a.deviation_sigma > 5.0);
        if error_count > 1000 || high_sigma {
            "CRITICAL".to_string()
        } else if error_count > 100 {
            "HIGH".to_string()
        } else if error_count > 10 {
            "MEDIUM".to_string()
        } else {
            "LOW".to_string()
        }
    }

    /// Espelha AggregatedReport.affected_services (property Python), retorna Vec ordenado
    pub fn affected_services(&self) -> Vec<String> {
        let mut services: std::collections::HashSet<String> = std::collections::HashSet::new();
        for tpl in &self.log_templates {
            if tpl.level == "ERROR" || tpl.level == "FATAL" {
                for svc in &tpl.services {
                    services.insert(svc.clone());
                }
            }
        }
        for a in &self.metric_anomalies {
            services.insert(a.service.clone());
        }
        for t in &self.slow_traces {
            services.insert(t.bottleneck_service.clone());
        }
        let mut sorted: Vec<String> = services.into_iter().collect();
        sorted.sort();
        sorted
    }

    /// Espelha AggregatedReport._primary_suspect()
    pub fn primary_suspect(&self) -> String {
        let mut service_scores: std::collections::HashMap<String, f64> =
            std::collections::HashMap::new();
        for tpl in &self.log_templates {
            if tpl.level == "ERROR" || tpl.level == "FATAL" {
                for svc in &tpl.services {
                    *service_scores.entry(svc.clone()).or_insert(0.0) +=
                        tpl.count as f64 * tpl.relevance_score;
                }
            }
        }
        for a in &self.metric_anomalies {
            *service_scores.entry(a.service.clone()).or_insert(0.0) += a.deviation_sigma * 100.0;
        }
        if service_scores.is_empty() {
            return "unknown".to_string();
        }
        service_scores
            .into_iter()
            .max_by(|a, b| {
                a.1.partial_cmp(&b.1)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .map(|(k, _)| k)
            .unwrap_or_else(|| "unknown".to_string())
    }

    /// Conta templates de nível ERROR ou FATAL (para Summary section)
    pub fn error_template_count(&self) -> usize {
        self.log_templates
            .iter()
            .filter(|t| t.level == "ERROR" || t.level == "FATAL")
            .count()
    }
}

/// Estatísticas de compressão retornadas para Python como dict
#[derive(Debug, Clone)]
pub struct CompressionStatsProxy {
    pub original_tokens: usize,
    pub compressed_tokens: usize,
    pub chunks_kept: usize,
    pub chunks_removed: usize,
    pub critical_preserved: f64,
    pub compression_ratio: f64,
}
