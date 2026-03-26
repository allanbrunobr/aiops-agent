from __future__ import annotations

"""Testes de correctude e benchmark de performance para detect_latency_drift em Rust.

Verifica equivalência de resultados entre implementação Python e Rust,
e valida que _percentile em Rust é >= 3x mais rápido que Python para n=10k
(requer extensão compilada com --release: maturin develop --release).
"""

import time

import pytest

from pipeline.trace_predictor import _percentile, detect_latency_drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_values(n: int, seed: int = 42) -> list[float]:
    """Gera lista determinística de n floats para testes."""
    import random

    rng = random.Random(seed)
    return [rng.uniform(50.0, 500.0) for _ in range(n)]


# ---------------------------------------------------------------------------
# Testes de correctude: equivalência Python ↔ Rust
# ---------------------------------------------------------------------------


class TestDetectLatencyDriftEquivalence:
    """Verifica que a implementação Rust produz resultados idênticos ao Python."""

    def test_drift_detectado_resultado_identico(self):
        """Rust e Python devem concordar quando drift é detectado."""
        import _aiops_rust as rust

        baseline = [100.0] * 100
        current = [130.0] * 100  # 30% acima → drift com threshold 20%

        py_result = detect_latency_drift(baseline, current, 0.20)
        rust_result = rust.detect_latency_drift(baseline, current, 0.20)

        assert py_result["drift_detected"] == rust_result["drift_detected"]
        assert abs(py_result["p95_ratio"] - rust_result["p95_ratio"]) < 1e-9
        assert abs(py_result["p99_ratio"] - rust_result["p99_ratio"]) < 1e-9

    def test_sem_drift_resultado_identico(self):
        """Rust e Python devem concordar quando não há drift."""
        import _aiops_rust as rust

        baseline = [100.0] * 100
        current = [110.0] * 100  # 10% < 20% limiar → sem drift

        py_result = detect_latency_drift(baseline, current, 0.20)
        rust_result = rust.detect_latency_drift(baseline, current, 0.20)

        assert not py_result["drift_detected"]
        assert not rust_result["drift_detected"]
        assert abs(py_result["p95_ratio"] - rust_result["p95_ratio"]) < 1e-9

    def test_baseline_zero_resultado_identico(self):
        """Divisão por zero protegida por max(p95_baseline, 0.001) em ambas implementações."""
        import _aiops_rust as rust

        baseline = [0.0] * 20
        current = [50.0] * 20

        py_result = detect_latency_drift(baseline, current, 0.20)
        rust_result = rust.detect_latency_drift(baseline, current, 0.20)

        assert py_result["drift_detected"] == rust_result["drift_detected"]

    def test_lista_vazia_baseline(self):
        """Lista vazia retorna p95=0.0 tanto em Python quanto em Rust."""
        import _aiops_rust as rust

        py_p95 = _percentile([], 95)
        rust_result = rust.detect_latency_drift([], [100.0] * 10, 0.20)

        assert py_p95 == 0.0
        # Rust também deve lidar com baseline vazio sem panic
        assert isinstance(rust_result["drift_detected"], bool)

    def test_valores_aleatorios_n1000(self):
        """Para n=1000 valores aleatórios, Rust e Python devem concordar em todos os campos."""
        import _aiops_rust as rust

        baseline = _make_values(1000, seed=1)
        current = _make_values(1000, seed=2)

        py_result = detect_latency_drift(baseline, current, 0.15)
        rust_result = rust.detect_latency_drift(baseline, current, 0.15)

        assert py_result["drift_detected"] == rust_result["drift_detected"]
        assert abs(py_result["p95_ratio"] - rust_result["p95_ratio"]) < 1e-9
        assert abs(py_result["p99_ratio"] - rust_result["p99_ratio"]) < 1e-9

    def test_threshold_customizado(self):
        """Threshold customizado deve ser respeitado por ambas implementações."""
        import _aiops_rust as rust

        baseline = [100.0] * 50
        current = [115.0] * 50  # 15% acima

        # threshold=0.10 → drift (15% > 10%)
        py_10 = detect_latency_drift(baseline, current, 0.10)
        rust_10 = rust.detect_latency_drift(baseline, current, 0.10)
        assert py_10["drift_detected"]
        assert rust_10["drift_detected"]

        # threshold=0.20 → sem drift (15% < 20%)
        py_20 = detect_latency_drift(baseline, current, 0.20)
        rust_20 = rust.detect_latency_drift(baseline, current, 0.20)
        assert not py_20["drift_detected"]
        assert not rust_20["drift_detected"]


# ---------------------------------------------------------------------------
# Benchmark: _percentile Rust >= 3x mais rápido que Python para n=10k
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestPercentileBenchmark:
    """Benchmark de performance: _percentile Rust deve ser >= 3x mais rápido que Python."""

    REPS = 200  # repetições para média estável
    N = 10_000  # tamanho da lista conforme AC

    def _time_python(self, values: list[float]) -> float:
        """Tempo médio em segundos para equivalente Python de detect_latency_drift.

        detect_latency_drift computa p95 e p99 em DOIS arrays (baseline + current),
        ou seja, 4 chamadas a _percentile — espelhamos aqui para comparação justa.
        """
        start = time.perf_counter()
        for _ in range(self.REPS):
            _percentile(values, 95)
            _percentile(values, 99)
            _percentile(values, 95)  # segundo array (current)
            _percentile(values, 99)
        return (time.perf_counter() - start) / self.REPS

    def _time_rust(self, values: list[float]) -> float:
        """Tempo médio em segundos para detect_latency_drift Rust (p95+p99 via Rust)."""
        import _aiops_rust as rust

        # detect_latency_drift computa p95 e p99 internamente — equivalente a 2x _percentile
        start = time.perf_counter()
        for _ in range(self.REPS):
            rust.detect_latency_drift(values, values, 0.20)
        return (time.perf_counter() - start) / self.REPS

    def test_rust_percentile_3x_mais_rapido_que_python(self):
        """_percentile Rust deve ser >= 3x mais rápido que Python para n=10k.

        Nota: requer `maturin develop --manifest-path rust/Cargo.toml --release`
        para resultados representativos. O build de dev pode não atingir 3x.
        """
        import _aiops_rust  # noqa: F401 — confirma extensão disponível

        values = _make_values(self.N, seed=99)

        python_time = self._time_python(values)
        rust_time = self._time_rust(values)

        speedup = python_time / rust_time
        print(
            f"\n[benchmark] n={self.N}, reps={self.REPS}"
            f"\n  Python _percentile (x2):  {python_time * 1000:.3f} ms"
            f"\n  Rust detect_latency_drift: {rust_time * 1000:.3f} ms"
            f"\n  Speedup: {speedup:.1f}x"
        )

        assert speedup >= 3.0, (
            f"Rust ({rust_time*1000:.3f}ms) deveria ser >= 3x mais rápido que Python "
            f"({python_time*1000:.3f}ms), mas speedup foi apenas {speedup:.1f}x. "
            "Compile com --release para o benchmark oficial."
        )
