"""Testes de validação scenario-agnostic do pipeline AIOps Agent (Story 2.5).

Valida:
- Todos os cenários executam pipeline completo sem erros (AC #1)
- Nenhum módulo de pipeline contém nomes de serviço hardcoded (AC #2)
- result["stages"] tem chaves idênticas para todos os cenários (AC #3)
- Postmortem gerado é cenário-apropriado sem referências hardcoded (AC #4)
- Pipeline completa em <60 segundos (AC #5)
"""
from __future__ import annotations

import io
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

# Garante registro de todos os cenários no registry antes dos testes.
import data.sample_incident  # noqa: F401

from data.registry import list_scenarios


# ──────────────────────────────────────────────────────────────────────
# Task 2: Cross-scenario integration tests (AC #1, #3)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestAllScenariosCompleteSuccessfully:
    """Testes parametrizados: todos os cenários completam pipeline (AC #1)."""

    def test_pipeline_succeeds(self, scenario_pipeline_bundle) -> None:
        """Pipeline completo deve retornar success=True para todo cenário."""
        result = scenario_pipeline_bundle["result"]
        name = scenario_pipeline_bundle["scenario_name"]
        assert result.get("success") is True, (
            f"Pipeline falhou para cenário '{name}': {result}"
        )

    def test_stage_keys_are_identical(self, scenario_pipeline_bundle) -> None:
        """result['stages'] deve ter chaves idênticas para todo cenário (AC #3)."""
        expected_keys = {
            "detection", "diagnosis", "mitigation", "verification",
            "safety_validation", "remediation_validation",
        }
        result = scenario_pipeline_bundle["result"]
        name = scenario_pipeline_bundle["scenario_name"]
        actual_keys = set(result.get("stages", {}).keys())
        assert actual_keys == expected_keys, (
            f"Cenário '{name}': chaves {actual_keys} != {expected_keys}"
        )

    def test_timeline_is_non_empty(self, scenario_pipeline_bundle) -> None:
        """result['timeline'] deve ser lista não-vazia com dicts de transição."""
        result = scenario_pipeline_bundle["result"]
        name = scenario_pipeline_bundle["scenario_name"]
        timeline = result.get("timeline", [])
        assert len(timeline) > 0, f"Cenário '{name}': timeline vazia"
        for t in timeline:
            assert "from" in t and "to" in t and "time" in t, (
                f"Cenário '{name}': transição incompleta: {t}"
            )

    def test_all_states_visited(self, scenario_pipeline_bundle) -> None:
        """State machine deve visitar todos os estados esperados (AC #1)."""
        required_states = {"DETECTING", "DIAGNOSING", "MITIGATING", "VERIFYING", "RESOLVED"}
        result = scenario_pipeline_bundle["result"]
        name = scenario_pipeline_bundle["scenario_name"]
        states_visited = {t["to"] for t in result.get("timeline", [])}
        missing = required_states - states_visited
        assert not missing, (
            f"Cenário '{name}': estados não visitados: {missing}"
        )


# ──────────────────────────────────────────────────────────────────────
# Task 3: Diagnosis contract tests (AC #2, #3)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestDiagnosisContractAcrossScenarios:
    """Testes de contrato do diagnóstico para todos os cenários."""

    def test_diagnosis_has_contract_shape(self, scenario_pipeline_bundle) -> None:
        """Diagnóstico deve ter exatamente 5 chaves do contrato."""
        expected_keys = {"root_cause", "evidence", "affected_services", "confidence", "timeline"}
        diagnosis = scenario_pipeline_bundle["result"]["stages"]["diagnosis"]
        name = scenario_pipeline_bundle["scenario_name"]
        assert set(diagnosis.keys()) == expected_keys, (
            f"Cenário '{name}': chaves {set(diagnosis.keys())} != {expected_keys}"
        )

    def test_diagnosis_has_non_empty_root_cause(self, scenario_pipeline_bundle) -> None:
        """root_cause deve ser string não-vazia."""
        diagnosis = scenario_pipeline_bundle["result"]["stages"]["diagnosis"]
        name = scenario_pipeline_bundle["scenario_name"]
        assert isinstance(diagnosis["root_cause"], str) and len(diagnosis["root_cause"]) > 0, (
            f"Cenário '{name}': root_cause vazio ou não-string"
        )

    def test_diagnosis_has_affected_services(self, scenario_pipeline_bundle) -> None:
        """affected_services deve ser lista não-vazia."""
        diagnosis = scenario_pipeline_bundle["result"]["stages"]["diagnosis"]
        name = scenario_pipeline_bundle["scenario_name"]
        assert isinstance(diagnosis["affected_services"], list), (
            f"Cenário '{name}': affected_services não é lista"
        )
        assert len(diagnosis["affected_services"]) > 0, (
            f"Cenário '{name}': affected_services vazia"
        )

    def test_mitigation_has_action_and_pattern(self, scenario_pipeline_bundle) -> None:
        """Mitigação deve ter action (MitigationAction) e pattern_matched (string)."""
        mitigation = scenario_pipeline_bundle["result"]["stages"]["mitigation"]
        name = scenario_pipeline_bundle["scenario_name"]
        assert "action" in mitigation, f"Cenário '{name}': mitigation sem action"
        assert "pattern_matched" in mitigation, f"Cenário '{name}': mitigation sem pattern_matched"
        assert isinstance(mitigation["pattern_matched"], str), (
            f"Cenário '{name}': pattern_matched não é string"
        )


# ──────────────────────────────────────────────────────────────────────
# Task 4: Postmortem validation tests (AC #4)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPostmortemAcrossScenarios:
    """Testes de postmortem gerado para todos os cenários (AC #4)."""

    def test_postmortem_generated_without_error(self, scenario_pipeline_bundle) -> None:
        """step_postmortem() deve gerar arquivo sem erros para todo cenário."""
        from main import step_postmortem

        bundle = scenario_pipeline_bundle
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(bundle["result"], bundle["incident"])

        assert path.exists(), f"Postmortem não gerado para '{bundle['scenario_name']}'"
        assert path.stat().st_size > 0, f"Postmortem vazio para '{bundle['scenario_name']}'"
        path.unlink(missing_ok=True)

    def test_postmortem_no_hardcoded_payment_svc(self, scenario_pipeline_bundle) -> None:
        """Postmortem NÃO deve conter 'payment-svc v2.3.1' hardcoded (AC #4).

        A string 'payment-svc' pode aparecer em diagnósticos do cenário payment-pool-exhaustion
        (é o serviço real), mas 'payment-svc v2.3.1' é hardcoded e não deve aparecer.
        """
        from main import step_postmortem

        bundle = scenario_pipeline_bundle
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(bundle["result"], bundle["incident"])

        text = path.read_text(encoding="utf-8")
        path.unlink(missing_ok=True)

        # A versão hardcoded "v2.3.1" nunca deve aparecer.
        assert "v2.3.1" not in text, (
            f"Cenário '{bundle['scenario_name']}': postmortem contém referência hardcoded 'v2.3.1'"
        )

    def test_postmortem_contains_scenario_name(self, scenario_pipeline_bundle) -> None:
        """Postmortem deve referenciar o scenario_name correto."""
        from main import step_postmortem

        bundle = scenario_pipeline_bundle
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(bundle["result"], bundle["incident"])

        text = path.read_text(encoding="utf-8")
        path.unlink(missing_ok=True)

        assert bundle["scenario_name"] in text, (
            f"Cenário '{bundle['scenario_name']}' não encontrado no texto do postmortem"
        )

    def test_postmortem_contains_diagnosis_root_cause(self, scenario_pipeline_bundle) -> None:
        """Postmortem deve incluir o root_cause do diagnóstico."""
        from main import step_postmortem

        bundle = scenario_pipeline_bundle
        diagnosis = bundle["result"]["stages"]["diagnosis"]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(bundle["result"], bundle["incident"])

        text = path.read_text(encoding="utf-8")
        path.unlink(missing_ok=True)

        # root_cause deve aparecer pelo menos no Root Cause section.
        root_cause = diagnosis["root_cause"]
        assert root_cause in text, (
            f"Cenário '{bundle['scenario_name']}': root_cause '{root_cause}' não no postmortem"
        )

    def test_postmortem_action_items_match_pattern(self, scenario_pipeline_bundle) -> None:
        """Action items devem corresponder ao pattern_matched, não ser hardcoded."""
        from main import step_postmortem

        bundle = scenario_pipeline_bundle
        mitigation = bundle["result"]["stages"]["mitigation"]
        pattern = mitigation.get("pattern_matched", "default")

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(bundle["result"], bundle["incident"])

        text = path.read_text(encoding="utf-8")
        path.unlink(missing_ok=True)

        # Se padrão é "unknown", action items devem conter "Investigate".
        if pattern == "unknown":
            assert "Investigate" in text, (
                f"Cenário unknown-fault: action items devem conter 'Investigate'"
            )
        # Se padrão é "connection pool", action items devem conter "connection pool".
        elif pattern == "connection pool":
            assert "connection pool" in text.lower(), (
                f"Cenário payment-pool: action items devem conter 'connection pool'"
            )
        # Se padrão é "memory", action items devem conter "memory".
        elif pattern == "memory":
            assert "memory" in text.lower(), (
                f"Cenário memory-leak: action items devem conter 'memory'"
            )
        else:
            # Garante que novos patterns não passam silenciosamente sem validação.
            pytest.fail(
                f"Cenário '{bundle['scenario_name']}': pattern_matched='{pattern}' "
                f"não tem validação de action items — adicione um branch elif."
            )


# ──────────────────────────────────────────────────────────────────────
# Task 5: Pipeline performance test (AC #5)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.slow
class TestPipelinePerformance:
    """Teste de performance: pipeline deve completar em <60s (AC #5)."""

    def test_pipeline_completes_within_60_seconds(self, scenario_pipeline_bundle) -> None:
        """Pipeline completo deve completar em menos de 60 segundos."""
        from data.registry import get_scenario

        name = scenario_pipeline_bundle["scenario_name"]
        incident = get_scenario(name)(seed=42)

        from tests.conftest import _run_pipeline

        t0 = time.time()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            _run_pipeline(incident, scenario_name=name)
        elapsed = time.time() - t0

        assert elapsed < 60, (
            f"Cenário '{name}': pipeline levou {elapsed:.1f}s (limite: 60s)"
        )


# ──────────────────────────────────────────────────────────────────────
# Task 6: Static analysis for hardcoded service names (AC #2)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestNoPipelineHardcodedServiceNames:
    """Validação estática: módulos de pipeline não contêm nomes de serviço hardcoded (AC #2)."""

    # Módulos de pipeline que DEVEM ser scenario-agnostic.
    PIPELINE_MODULES = [
        "pipeline/log_parser.py",
        "pipeline/metric_filter.py",
        "pipeline/trace_filter.py",
        "pipeline/aggregator.py",
        "agents/detector.py",
        
    ]

    # Nomes de serviço que NÃO devem aparecer nesses módulos.
    FORBIDDEN_SERVICE_NAMES = [
        "payment-svc",
        "auth-svc",
        "gateway",
        "database-svc",
        "order-svc",
        "data-processor",
        "ingest-svc",
        "scheduler",
        "storage-gw",
    ]

    def test_pipeline_modules_no_hardcoded_service_names(self) -> None:
        """Scan pipeline source code for forbidden hardcoded service names."""
        project_root = Path(__file__).parent.parent
        violations = []

        for module_path in self.PIPELINE_MODULES:
            full_path = project_root / module_path
            if not full_path.exists():
                continue

            source = full_path.read_text(encoding="utf-8")
            for service_name in self.FORBIDDEN_SERVICE_NAMES:
                if service_name in source:
                    for i, line in enumerate(source.splitlines(), 1):
                        if service_name not in line:
                            continue
                        stripped = line.strip()
                        # Ignora linhas de comentário puro.
                        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                            continue
                        # Ignora se o service name aparece apenas na parte de comentário inline.
                        code_part = line.split("#")[0] if "#" in line else line
                        if service_name not in code_part:
                            continue
                        violations.append(
                            f"{module_path}:{i}: contém '{service_name}'"
                        )

        assert not violations, (
            f"Módulos de pipeline contêm nomes de serviço hardcoded:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_excluded_modules_are_acceptable(self) -> None:
        """Confirma que diagnoser e mitigator NÃO estão na lista de scan.

        Esses módulos contêm keywords de fault type (não nomes de serviço)
        que são aceitáveis para lógica heurística e de remediação.
        """
        excluded = {"agents/diagnoser.py", "agents/mitigator.py"}
        scanned = set(self.PIPELINE_MODULES)
        assert not excluded.intersection(scanned), (
            "diagnoser.py e mitigator.py não devem ser escaneados "
            "(contêm keywords de fault type aceitáveis)"
        )
