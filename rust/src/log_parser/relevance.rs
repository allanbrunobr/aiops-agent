/// Cálculo de relevância dos templates de log.
///
/// Replica exatamente o método _compute_relevance() do Python:
///   - Keyword match: INCIDENT_KEYWORDS × 1.5, cap 5.0
///   - Level boost: ERROR=3.0, FATAL=5.0, WARN=1.5
///   - Raridade inversa para INFO/WARN; bônus fixo para ERROR/FATAL
/// Keywords de incidente — corresponde exatamente ao INCIDENT_KEYWORDS do Python.
static INCIDENT_KEYWORDS: &[&str] = &[
    "error",
    "fatal",
    "crash",
    "timeout",
    "oom",
    "exception",
    "refused",
    "unavailable",
    "panic",
    "exhausted",
    "circuit",
    "breaker",
    "failed",
    "aborted",
    "timed out",
    "503",
    "500",
];

/// Calcula score de relevância para um único template.
pub fn compute_relevance_score(template: &str, level: &str, count: u64, total_logs: u64) -> f64 {
    let tpl_lower = template.to_lowercase();

    // Keyword match (0–5 pontos)
    let keyword_hits = INCIDENT_KEYWORDS
        .iter()
        .filter(|&&kw| tpl_lower.contains(kw))
        .count();
    let mut score = (keyword_hits as f64 * 1.5_f64).min(5.0);

    // Level boost
    score += match level {
        "ERROR" => 3.0,
        "FATAL" => 5.0,
        "WARN" => 1.5,
        _ => 0.0,
    };

    // Raridade: ERROR/FATAL ganham bônus fixo; outros ganham score por raridade
    if level == "ERROR" || level == "FATAL" {
        score += 2.0;
    } else {
        let frequency_ratio = if total_logs > 0 {
            count as f64 / total_logs as f64
        } else {
            0.0
        };
        score += (2.0 - frequency_ratio * 20.0).max(0.0);
    }

    (score * 100.0).round() / 100.0
}
