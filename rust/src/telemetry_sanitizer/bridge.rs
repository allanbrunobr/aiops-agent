/// Fronteira PyO3: expõe detecção de taint para Python.
///
/// Python chama detect_taint_batch(texts) → lista de strings de nível de taint.
/// O loop de sanitização e o gerenciamento de estado permanecem no Python.
use pyo3::prelude::*;
use pyo3::types::{PyList, PyString};

use super::detector::{detect_taint, detect_taint_batch_parallel};

/// Detecta taint em batch para uma lista de strings.
///
/// Usa Rayon internamente para processar em paralelo.
/// Retorna lista de strings: "clean" | "suspicious" | "tainted" | "adversarial".
#[pyfunction]
pub fn detect_taint_batch(py: Python<'_>, texts: &Bound<'_, PyList>) -> PyResult<Py<PyList>> {
    // Extrair strings enquanto segura o GIL
    let strings: Vec<String> = texts
        .iter()
        .map(|item| {
            item.cast::<PyString>()
                .map(|s| s.to_str().unwrap_or("").to_owned())
                .unwrap_or_default()
        })
        .collect();

    // Processar em paralelo sem GIL
    let levels = py.detach(|| detect_taint_batch_parallel(strings));

    // Construir resultado Python
    let result = PyList::empty(py);
    for level in levels {
        result.append(level.as_str())?;
    }

    Ok(result.into())
}

/// Detecta taint de um único texto (conveniência para casos não-batch).
#[pyfunction]
pub fn detect_taint_single(py: Python<'_>, text: &str) -> PyResult<String> {
    let result = py.detach(|| detect_taint(text));
    Ok(result.as_str().to_owned())
}
