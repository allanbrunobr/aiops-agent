/// Detecção de taint em Rust puro usando regex compilados.
///
/// Porta do _detect_taint() Python com suporte a batch paralelo via Rayon.
/// Performance esperada: muito superior ao re.compile Python (regex crate PCRE-like).

use rayon::prelude::*;
use super::patterns::{adversarial_patterns, suspicious_patterns, tainted_field_patterns};

/// Nível de taint — espelha TaintLevel do Python.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaintLevel {
    Clean,
    Suspicious,
    Tainted,
    Adversarial,
}

impl TaintLevel {
    pub fn as_str(&self) -> &'static str {
        match self {
            TaintLevel::Clean => "clean",
            TaintLevel::Suspicious => "suspicious",
            TaintLevel::Tainted => "tainted",
            TaintLevel::Adversarial => "adversarial",
        }
    }
}

/// Detecta o nível de taint de um único texto.
///
/// Hierarquia:
///   1. Padrões adversariais → ADVERSARIAL
///   2. Conteúdo suspeito embutido → SUSPICIOUS
///   3. Campos user-generated → TAINTED
///   4. Sem padrão → CLEAN
pub fn detect_taint(text: &str) -> TaintLevel {
    if text.is_empty() {
        return TaintLevel::Clean;
    }

    // 1. Adversarial reward-hacking patterns
    for pattern in adversarial_patterns() {
        if pattern.is_match(text) {
            return TaintLevel::Adversarial;
        }
    }

    // 2. Suspicious embedded content
    for pattern in suspicious_patterns() {
        if pattern.is_match(text) {
            return TaintLevel::Suspicious;
        }
    }

    // 3. Tainted field indicators (field_name = value)
    for pattern in tainted_field_patterns() {
        if pattern.is_match(text) {
            return TaintLevel::Tainted;
        }
    }

    TaintLevel::Clean
}

/// Detecta taint em batch usando Rayon (paralelo).
/// Processa todos os textos em paralelo — útil para lotes grandes de logs.
pub fn detect_taint_batch_parallel(texts: Vec<String>) -> Vec<TaintLevel> {
    texts.par_iter().map(|t| detect_taint(t)).collect()
}
