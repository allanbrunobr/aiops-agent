/// Tipos de dados internos para o módulo metric_filter.
///
/// MetricPointData: entrada (equivalente ao MetricPoint Python)
/// MetricAnomalyData: saída (equivalente ao MetricAnomaly Python)

pub struct MetricPointData {
    pub timestamp: f64,    // epoch seconds
    pub service: String,
    pub metric_name: String,
    pub value: f64,
}

pub struct MetricAnomalyData {
    pub service: String,
    pub metric_name: String,
    pub anomaly_type: String,  // "spike" | "level_shift" | "steady_increase"
    pub timestamp: f64,        // epoch seconds
    pub value: f64,            // rounded to 2 decimals
    pub mean: f64,             // rounded to 2 decimals
    pub std: f64,              // rounded to 2 decimals
    pub deviation_sigma: f64,  // rounded to 1 decimal
}
