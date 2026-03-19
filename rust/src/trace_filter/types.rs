/// Tipos de dados internos para o módulo trace_filter.

pub struct SpanData {
    pub trace_id: String,
    pub span_id: String,
    pub parent_span_id: Option<String>,
    pub service: String,
    pub operation: String,
    pub start_time: f64,      // epoch seconds
    pub duration_ms: f64,
    #[allow(dead_code)]
    pub status: String,
    pub attributes: Vec<(String, String)>,
}

pub struct SlowCallPathData {
    pub trace_id: String,
    pub path: Vec<String>,
    pub operations: Vec<String>,
    pub total_duration_ms: f64,
    pub bottleneck_service: String,
    pub bottleneck_duration_ms: f64,
    pub normal_duration_ms: f64,
    pub timestamp: f64,       // epoch seconds
    pub attributes: Vec<(String, String)>,
}
