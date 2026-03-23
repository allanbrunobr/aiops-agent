/// Fronteira PyO3: converte objetos Python ↔ Rust e orquestra o pipeline Drain3.
///
/// Fluxo de execução:
///   1. Extrai campos dos LogEntry Python enquanto segura o GIL
///   2. Libera o GIL para processar Drain3 em Rust puro
///   3. Usa Rayon para calcular relevance scores em paralelo
///   4. Re-adquire o GIL para construir objetos Python de retorno
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;

use super::drain3::Drain3;
use super::relevance::compute_relevance_score;
use super::types::{LogEntryData, LogTemplateData};

/// Processa uma lista de LogEntry Python e retorna lista de dicts com campos de LogTemplate.
///
/// Chamado por pipeline/log_parser.py quando _RUST_AVAILABLE = True.
/// Equivalente funcional ao _python_drain3_parse() sem cap de amostragem.
#[pyfunction]
pub fn parse_logs(py: Python<'_>, logs: &Bound<'_, PyList>) -> PyResult<Py<PyList>> {
    // ── Fase 1: extrair dados enquanto segura o GIL ───────────────────────────
    let entries = extract_log_entries(py, logs)?;

    // ── Fase 2: processar Drain3 sem GIL (libera GIL para concorrência) ───────
    let mut templates = py.allow_threads(|| run_drain3_pipeline(entries));

    // ── Fase 3: calcular relevância em paralelo com Rayon (ainda sem GIL) ─────
    let total_logs: u64 = templates.iter().map(|t| t.count).sum();
    templates.par_iter_mut().for_each(|tpl| {
        tpl.relevance_score =
            compute_relevance_score(&tpl.template, &tpl.level, tpl.count, total_logs);
    });

    // Ordenar por relevance_score decrescente; sort_by é estável, desempates preservam
    // a ordem de inserção (cluster_id crescente = ordem de aparecimento nos logs).
    templates.sort_by(|a, b| {
        b.relevance_score
            .partial_cmp(&a.relevance_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    // ── Fase 4: construir dicts Python enquanto segura o GIL ─────────────────
    build_python_result(py, templates)
}

/// Extrai campos de LogEntry enquanto o GIL está ativo.
/// Para volumes > 50K, aplica amostragem stride=4 via get_item para reduzir overhead GIL.
/// Drain3 converge com ~500 logs — amostrar 1:4 em volumes > 50K preserva todos templates.
fn extract_log_entries(
    _py: Python<'_>,
    logs: &Bound<'_, PyList>,
) -> PyResult<Vec<LogEntryData>> {
    let n = logs.len();
    let mut entries = Vec::with_capacity(n);

    // Processa todos os logs sem amostragem — Rust lida com volumes grandes sem cap
    for log in logs.iter() {
        let message: String = log.getattr("message")?.extract()?;
        let level: String = log.getattr("level")?.extract()?;
        let service: String = log.getattr("service")?.extract()?;
        let timestamp: f64 = log.getattr("timestamp")?.call_method0("timestamp")?.extract()?;
        let str_repr: String = log.str()?.to_str()?.to_owned();
        entries.push(LogEntryData { message, level, service, timestamp, str_repr });
    }

    Ok(entries)
}

/// Executa Drain3 sequencialmente e agrega resultados por cluster.
/// Chamado sem GIL — não pode acessar objetos Python.
/// Para volumes grandes, aplica amostragem stride (Drain3 converge com ~500 logs).
fn run_drain3_pipeline(entries: Vec<LogEntryData>) -> Vec<LogTemplateData> {
    let mut drain = Drain3::new(0.4);
    // Usar Vec de pares (cluster_id, template) para preservar ordem de inserção.
    // Isso garante desempate determinístico ao ordenar por relevance_score.
    let mut template_list: Vec<(usize, LogTemplateData)> = Vec::new();
    let mut cluster_index: std::collections::HashMap<usize, usize> =
        std::collections::HashMap::new();

    for entry in &entries {
        let (cluster_id, template) = drain.add_log_message(&entry.message);

        let idx = if let Some(&i) = cluster_index.get(&cluster_id) {
            // Cluster existente — atualizar template
            template_list[i].1.template = template;
            i
        } else {
            // Novo cluster
            let i = template_list.len();
            template_list.push((
                cluster_id,
                LogTemplateData {
                    template,
                    level: entry.level.clone(),
                    count: 0,
                    relevance_score: 0.0,
                    services: Vec::new(),
                    first_seen: None,
                    last_seen: None,
                    sample_messages: Vec::new(),
                },
            ));
            cluster_index.insert(cluster_id, i);
            i
        };

        let tpl = &mut template_list[idx].1;
        tpl.count += 1;

        // Serviço — deduplicação inline
        if !tpl.services.contains(&entry.service) {
            tpl.services.push(entry.service.clone());
        }

        // Rastrear timestamps first_seen / last_seen
        match tpl.first_seen {
            None => tpl.first_seen = Some(entry.timestamp),
            Some(fs) if entry.timestamp < fs => tpl.first_seen = Some(entry.timestamp),
            _ => {}
        }
        match tpl.last_seen {
            None => tpl.last_seen = Some(entry.timestamp),
            Some(ls) if entry.timestamp > ls => tpl.last_seen = Some(entry.timestamp),
            _ => {}
        }

        // Até 3 sample_messages por template (usa message como substituto de str_repr)
        if tpl.sample_messages.len() < 3 {
            tpl.sample_messages.push(entry.message.clone());
        }
    }

    template_list.into_iter().map(|(_, tpl)| tpl).collect()
}

/// Constrói lista Python de dicts com campos equivalentes ao LogTemplate.
fn build_python_result(py: Python<'_>, templates: Vec<LogTemplateData>) -> PyResult<Py<PyList>> {
    let result = PyList::empty_bound(py);
    let datetime_mod = py.import_bound("datetime")?;
    let datetime_cls = datetime_mod.getattr("datetime")?;

    for tpl in templates {
        let dict = PyDict::new_bound(py);
        dict.set_item("template", &tpl.template)?;
        dict.set_item("level", &tpl.level)?;
        dict.set_item("count", tpl.count)?;
        dict.set_item("relevance_score", tpl.relevance_score)?;

        // services ordenado para saída determinística
        let mut services = tpl.services.clone();
        services.sort();
        dict.set_item("services", services)?;

        // Converter timestamps f64 → datetime Python
        match tpl.first_seen {
            Some(ts) => {
                let dt = datetime_cls.call_method1("fromtimestamp", (ts,))?;
                dict.set_item("first_seen", dt)?;
            }
            None => {
                dict.set_item("first_seen", py.None())?;
            }
        }
        match tpl.last_seen {
            Some(ts) => {
                let dt = datetime_cls.call_method1("fromtimestamp", (ts,))?;
                dict.set_item("last_seen", dt)?;
            }
            None => {
                dict.set_item("last_seen", py.None())?;
            }
        }

        dict.set_item("sample_messages", &tpl.sample_messages)?;

        result.append(&dict)?;
    }

    Ok(result.into())
}
