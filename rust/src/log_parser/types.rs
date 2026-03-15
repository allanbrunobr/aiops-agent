/// Dados extraídos de um LogEntry Python para processamento puro em Rust.
/// Elimina referências ao GIL após a fase de extração.
pub struct LogEntryData {
    pub message: String,
    pub level: String,
    pub service: String,
    pub timestamp: f64,   // Unix epoch seconds
    pub str_repr: String, // str(log) pré-computado para sample_messages
}

/// Template de log com dados agregados — resultado do pipeline Drain3 Rust.
pub struct LogTemplateData {
    pub template: String,
    pub level: String,
    pub count: u64,
    pub relevance_score: f64,
    pub services: Vec<String>,
    pub first_seen: Option<f64>,
    pub last_seen: Option<f64>,
    pub sample_messages: Vec<String>,
}
