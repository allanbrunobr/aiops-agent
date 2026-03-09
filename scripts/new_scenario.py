from __future__ import annotations

"""
Script de scaffold para novos cenários de diagnóstico.

Uso: python scripts/new_scenario.py <scenario-name>
Exemplo: python scripts/new_scenario.py redis-timeout
"""

import argparse
import os
import sys
import textwrap


def _to_snake_case(name: str) -> str:
    """Converte nome de cenário para snake_case."""
    return name.replace("-", "_").lower()


def _generate_scenario_file(name: str, snake: str) -> str:
    """Gera conteúdo do arquivo de cenário."""
    return textwrap.dedent(f"""\
        from __future__ import annotations

        \"\"\"
        Cenário simulado: {name}.

        Causa raiz: (descrever causa raiz aqui).
        \"\"\"

        import random
        import datetime

        from data.models import LogEntry, MetricPoint, Span, SimulatedIncident


        def generate_{snake}(seed: int = 42) -> SimulatedIncident:
            \"\"\"Gera cenário de {name}.

            Args:
                seed: Semente para reprodutibilidade. Mesmo seed -> mesma saída.

            Returns:
                SimulatedIncident com logs, métricas e traces do incidente.
            \"\"\"
            random.seed(seed)

            incident_start = datetime.datetime(2026, 3, 4, 14, 19, 0)

            logs = _generate_logs(incident_start)
            metrics = _generate_metrics(incident_start)
            traces = _generate_traces(incident_start)

            return SimulatedIncident(
                logs=logs,
                metrics=metrics,
                traces=traces,
            )


        def _generate_logs(incident_start: datetime.datetime) -> list[LogEntry]:
            \"\"\"Gera logs simulados para o cenário.\"\"\"
            logs = []
            # TODO: adicionar entradas de log pre-incident, trigger, degradation, full incident
            logs.sort(key=lambda x: x.timestamp)
            return logs


        def _generate_metrics(
            incident_start: datetime.datetime,
            interval_seconds: int = 15,
            duration_minutes: int = 60,
        ) -> list[MetricPoint]:
            \"\"\"Gera métricas simuladas com anomalia injetada.\"\"\"
            metrics = []
            # TODO: adicionar pontos de métricas normais e anômalas
            return metrics


        def _generate_traces(incident_start: datetime.datetime, count: int = 100) -> list[Span]:
            \"\"\"Gera traces distribuídos com spans anômalos.\"\"\"
            traces = []
            # TODO: adicionar spans normais e lentos
            return traces
    """)


def _generate_golden_file(name: str) -> str:
    """Gera conteúdo do golden file."""
    import json
    golden = {
        "scenario_name": name,
        "scenario_seed": 42,
        "expected_detection": {
            "incident_declared": True,
            "correlated_signals": {
                "error_logs": True,
                "metric_anomalies": True,
                "slow_traces": True
            },
            "min_signals_required": 2
        },
        "expected_diagnosis": {
            "affected_services": ["TODO-service"],
            "root_cause_contains": "TODO-root-cause-keyword",
            "severity": "high"
        },
        "expected_affected_services": ["TODO-service"],
        "validation_mode": "no-llm"
    }
    return json.dumps(golden, indent=2)


def _generate_test_stub(name: str, snake: str) -> str:
    """Gera stub de teste para o novo cenário."""
    return textwrap.dedent(f"""\
        from __future__ import annotations

        import pytest
        from data.{snake}_scenario import generate_{snake}


        def test_{snake}_generates_incident():
            \"\"\"Verifica que cenário gera dados de incidente válidos.\"\"\"
            incident = generate_{snake}(seed=42)
            assert incident is not None
            # TODO: adicionar verificações específicas do cenário


        def test_{snake}_is_deterministic():
            \"\"\"Verifica que cenário é determinístico com mesmo seed.\"\"\"
            inc1 = generate_{snake}(seed=42)
            inc2 = generate_{snake}(seed=42)
            assert len(inc1.logs) == len(inc2.logs)
            assert len(inc1.metrics) == len(inc2.metrics)
    """)


def main() -> None:
    """Gera arquivos de scaffold para um novo cenário."""
    parser = argparse.ArgumentParser(
        description="Scaffold de novo cenário de diagnóstico AIOps"
    )
    parser.add_argument("name", help="Nome do cenário (ex: redis-timeout)")
    args = parser.parse_args()

    name = args.name.lower()
    snake = _to_snake_case(name)

    # Arquivos a criar
    files = {
        f"data/{snake}_scenario.py": _generate_scenario_file(name, snake),
        f"data/golden/{name}.json": _generate_golden_file(name),
        f"tests/test_{snake}.py": _generate_test_stub(name, snake),
    }

    created = []
    skipped = []

    for path, content in files.items():
        if os.path.exists(path):
            skipped.append(path)
            continue
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        created.append(path)

    print(f"\nScaffold para cenário '{name}' gerado:")
    for path in created:
        print(f"  ✅ Created: {path}")
    for path in skipped:
        print(f"  ⚠️  Skipped (exists): {path}")

    print(f"""
Next steps:
  1. Edit data/{snake}_scenario.py — add logs, metrics, traces
  2. Edit data/golden/{name}.json — fill in expected detection/diagnosis
  3. Run: python main.py --no-llm --scenario {name}
  4. Edit tests/test_{snake}.py — add scenario-specific assertions
  5. Create data/knowledge/{name}.json — add RAG knowledge pattern
""")


if __name__ == "__main__":
    main()
