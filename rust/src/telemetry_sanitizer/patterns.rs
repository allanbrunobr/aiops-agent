/// Padrões compilados para detecção de conteúdo adversarial.
///
/// Usa std::sync::OnceLock para compilar uma única vez em runtime.
/// Equivalente aos ADVERSARIAL_REWARD_HACKING_PATTERNS e TAINTED_LOG_FIELDS do Python.

use regex::Regex;
use std::sync::OnceLock;

/// Retorna os padrões adversariais compilados (case-insensitive).
pub fn adversarial_patterns() -> &'static [Regex] {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        let raw = [
            // Padrões de "lead + body" — causa plausível seguida de remediação
            r"(?i)(?:caused?\s+by|due\s+to|because\s+of).*?(?:downgrade|upgrade|add\s+(?:the\s+)?ppa|install|switch\s+to)",
            r"(?i)(?:not\s+supporting|incompatib).*?(?:downgrade|upgrade|add\s+ppa|install|switch)",
            r"(?i)(?:resolve|fix|remediat).*?(?:downgrade\s+to|upgrade\s+to|add\s+.*?ppa|install\s+.*?version)",
            // Decorator patterns usados pelo AIOpsDoom (Fig G.2 do paper)
            r"(?i)\[(?:SOLUTION|HINT|ANSWER|NOTE)\]",
            r"(?i)\[(?:HUMAN\s+PROVIDED\s+HINT|AI\s+GENERATED\s+SUGGESTION|User\s+Insight)\]",
            r"(?i)(?:SOLUTION|HINT)\s*(?:->|:|»>)",
            r"(?i)--\s*SOLUTION\s+START\s*--",
            r"(?i)Proposed\s+Answer",
            r"(?i)Suggested\s+Solution",
            // Comandos de remediação embutidos em texto de log (red flag)
            r"(?i)(?:apt(?:-get)?\s+(?:install|upgrade|add-repository))",
            r"(?i)(?:ppa:[a-zA-Z0-9/_-]+)",
            r"(?i)(?:pip\s+install|npm\s+install|gem\s+install).*?version",
            r"(?i)(?:downgrade\s+to\s+\w+\s+[\d.]+)",
            r"(?i)(?:switch\s+(?:to\s+)?(?:HTTP|http)\s+(?:checks|instead))",
        ];
        raw.iter()
            .map(|p| Regex::new(p).expect("adversarial pattern inválido"))
            .collect()
    })
}

/// Retorna os padrões de conteúdo suspeito embutido (case-insensitive).
pub fn suspicious_patterns() -> &'static [Regex] {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        let raw = [
            r"(?i)(?:https?://[^\s]+\.(?:php|asp|jsp|cgi))",
            r"(?i)(?:/[a-z0-9]{10,}\.php)",
            r"(?:[^\s]{100,})",
        ];
        raw.iter()
            .map(|p| Regex::new(p).expect("suspicious pattern inválido"))
            .collect()
    })
}

/// Retorna os padrões para campos tainted (field_name\s*[=:]\s*\S+).
pub fn tainted_field_patterns() -> &'static [Regex] {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        let fields = [
            "user-agent", "useragent", "user_agent",
            "referer", "referrer",
            "x-forwarded-for", "x-real-ip",
            "origin", "host",
            "username", "user_name", "user",
            "email", "mail",
            "url", "uri", "path", "request_uri",
            "query", "q", "search",
            "item_id", "product_id",
            "followee_name", "follower_name",
        ];
        fields
            .iter()
            .map(|f| {
                // Padrão: field_name\s*[=:]\s*\S+ (case-insensitive)
                let pat = format!(r"(?i){}[\s]*[=:][\s]*\S+", regex::escape(f));
                Regex::new(&pat).expect("tainted field pattern inválido")
            })
            .collect()
    })
}
