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
