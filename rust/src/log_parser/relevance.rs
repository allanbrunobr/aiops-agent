//! Cálculo de relevância dos templates de log.
//!
//! Replica exatamente o método _compute_relevance() do Python:
//! - Keyword match: INCIDENT_KEYWORDS × 1.5, cap 5.0
//! - Level boost: ERROR=3.0, FATAL=5.0, WARN=1.5
//! - Raridade inversa para INFO/WARN; bônus fixo para ERROR/FATAL

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_outranks_info_for_same_template() {
        let err = compute_relevance_score("db timeout", "ERROR", 10, 100);
        let info = compute_relevance_score("db timeout", "INFO", 10, 100);
        assert!(err > info);
    }

    /// Keyword hits valem 1.5 cada, com teto em 5.0.
    #[test]
    fn keyword_score_is_capped_at_five() {
        // 5 keywords → 7.5 sem teto; com teto 5.0 + ERROR 3.0 + bônus 2.0 = 10.0
        let s = compute_relevance_score("error fatal crash timeout oom", "ERROR", 1, 100);
        assert_eq!(s, 10.0);
    }

    /// INFO raro vale mais que INFO frequente (raridade inversa).
    #[test]
    fn rare_info_beats_frequent_info() {
        let rare = compute_relevance_score("heartbeat", "INFO", 1, 1000);
        let frequent = compute_relevance_score("heartbeat", "INFO", 900, 1000);
        assert!(rare > frequent);
        assert_eq!(frequent, 0.0); // 2.0 - 0.9*20 < 0 → clamp em 0
    }

    #[test]
    fn fatal_gets_highest_level_boost() {
        assert!(
            compute_relevance_score("x", "FATAL", 1, 10)
                > compute_relevance_score("x", "ERROR", 1, 10)
        );
    }

    #[test]
    fn zero_total_logs_does_not_divide_by_zero() {
        let s = compute_relevance_score("x", "INFO", 0, 0);
        assert!(s.is_finite());
    }
}
