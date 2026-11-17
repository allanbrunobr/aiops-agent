/// Lógica de classificação de prioridade — espelha Python Priority enum + métodos _classify_*.
use super::types::{LogTemplateProxy, MetricAnomalyProxy, SlowTraceProxy};

/// Espelha pipeline.context_compressor.Priority
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Priority {
    Noise = 0,
    Context = 1,
    Important = 2,
    Critical = 3,
}

/// Espelha ContextCompressor._classify_log_priority()
pub fn classify_log_priority(tpl: &LogTemplateProxy) -> Priority {
    if tpl.level == "FATAL" {
        return Priority::Critical;
    }
    if tpl.level == "ERROR" && tpl.relevance_score >= 5.0 {
        return Priority::Critical;
    }
    if tpl.level == "ERROR" {
        return Priority::Important;
    }
    if tpl.level == "WARN" && tpl.relevance_score >= 3.0 {
        return Priority::Important;
    }
    if tpl.level == "WARN" {
        return Priority::Context;
    }
    if tpl.level == "INFO" && tpl.relevance_score >= 2.0 {
        return Priority::Context;
    }
    Priority::Noise
}

/// Espelha ContextCompressor._classify_metric_priority()
pub fn classify_metric_priority(anomaly: &MetricAnomalyProxy) -> Priority {
    if anomaly.deviation_sigma >= 8.0 {
        Priority::Critical
    } else if anomaly.deviation_sigma >= 5.0 {
        Priority::Important
    } else if anomaly.deviation_sigma >= 3.0 {
        Priority::Context
    } else {
        Priority::Noise
    }
}

/// Espelha ContextCompressor._classify_trace_priority()
pub fn classify_trace_priority(trace: &SlowTraceProxy) -> Priority {
    let slowdown_ratio = trace.total_duration_ms / trace.normal_duration_ms.max(1.0);
    if slowdown_ratio >= 20.0 {
        Priority::Critical
    } else if slowdown_ratio >= 5.0 {
        Priority::Important
    } else {
        Priority::Context
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tpl(level: &str, score: f64) -> LogTemplateProxy {
        LogTemplateProxy {
            template: "t".into(),
            level: level.into(),
            count: 1,
            relevance_score: score,
            services: vec!["svc".into()],
        }
    }

    #[test]
    fn log_priority_mirrors_python_rules() {
        assert_eq!(
            classify_log_priority(&tpl("FATAL", 0.0)),
            Priority::Critical
        );
        assert_eq!(
            classify_log_priority(&tpl("ERROR", 5.0)),
            Priority::Critical
        );
        assert_eq!(
            classify_log_priority(&tpl("ERROR", 4.9)),
            Priority::Important
        );
        assert_eq!(
            classify_log_priority(&tpl("WARN", 3.0)),
            Priority::Important
        );
        assert_eq!(classify_log_priority(&tpl("WARN", 2.9)), Priority::Context);
        assert_eq!(classify_log_priority(&tpl("INFO", 2.0)), Priority::Context);
        assert_eq!(classify_log_priority(&tpl("INFO", 1.9)), Priority::Noise);
        assert_eq!(classify_log_priority(&tpl("DEBUG", 9.0)), Priority::Noise);
    }

    #[test]
    fn metric_priority_by_sigma() {
        let m = |sigma: f64| MetricAnomalyProxy {
            service: "s".into(),
            metric_name: "m".into(),
            anomaly_type: "spike".into(),
            value: 0.0,
            mean: 0.0,
            deviation_sigma: sigma,
        };
        assert_eq!(classify_metric_priority(&m(8.0)), Priority::Critical);
        assert_eq!(classify_metric_priority(&m(5.0)), Priority::Important);
        assert_eq!(classify_metric_priority(&m(3.0)), Priority::Context);
        assert_eq!(classify_metric_priority(&m(2.9)), Priority::Noise);
    }

    #[test]
    fn trace_priority_by_slowdown_ratio_with_zero_normal_guard() {
        let t = |total: f64, normal: f64| SlowTraceProxy {
            path: vec![],
            total_duration_ms: total,
            normal_duration_ms: normal,
            bottleneck_service: "b".into(),
            attributes: vec![],
        };
        assert_eq!(
            classify_trace_priority(&t(2000.0, 100.0)),
            Priority::Critical
        ); // 20x
        assert_eq!(
            classify_trace_priority(&t(500.0, 100.0)),
            Priority::Important
        ); // 5x
        assert_eq!(classify_trace_priority(&t(200.0, 100.0)), Priority::Context);
        // normal 0 → divisor vira 1.0, não NaN
        assert_eq!(classify_trace_priority(&t(50.0, 0.0)), Priority::Critical);
    }

    #[test]
    fn priority_is_totally_ordered() {
        assert!(Priority::Noise < Priority::Context);
        assert!(Priority::Context < Priority::Important);
        assert!(Priority::Important < Priority::Critical);
    }
}
