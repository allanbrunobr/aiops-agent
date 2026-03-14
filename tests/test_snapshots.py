"""Testes de snapshot para validação de determinismo do pipeline.

Captura output do pipeline em modo seed-42 como snapshots JSON.
Na primeira execução, snapshots são criados em tests/snapshots/.
Nas subsequentes, compara output contra snapshots armazenados.

Qualquer mudança no pipeline que altere output causa FAIL com diff legível.
Use --snapshot-update para regenerar snapshots após mudanças intencionais.
"""
from __future__ import annotations

import pytest


@pytest.mark.snapshot
@pytest.mark.integration
class TestPreProcessingSnapshots:
    """Snapshots dos estágios de pré-processamento do pipeline.

    Usa aggregated_report fixture (sanitização + agregação).
    NÃO misturar com pipeline_result para evitar double-sanitization.
    """

    def test_log_templates_snapshot(self, aggregated_report, snapshot_manager):
        """Verifica que log templates do Drain3 são determinísticos com seed-42."""
        snapshot_manager.assert_match(
            aggregated_report.log_templates,
            "log_templates",
        )

    def test_metric_anomalies_snapshot(self, aggregated_report, snapshot_manager):
        """Verifica que anomalias métricas (3-sigma) são determinísticas."""
        snapshot_manager.assert_match(
            aggregated_report.metric_anomalies,
            "metric_anomalies",
        )

    def test_slow_traces_snapshot(self, aggregated_report, snapshot_manager):
        """Verifica que traces lentos (p95) são determinísticos."""
        snapshot_manager.assert_match(
            aggregated_report.slow_traces,
            "slow_traces",
        )

    def test_aggregated_report_text_snapshot(self, aggregated_report, snapshot_manager):
        """Verifica que report.to_text() gera output textual determinístico."""
        text = aggregated_report.to_text()
        snapshot_manager.assert_text_match(text, "aggregated_report_text")

    def test_compression_stats_snapshot(self, compression_stats, snapshot_manager):
        """Verifica que CompressionStats é determinístico com seed-42."""
        snapshot_manager.assert_match(
            compression_stats,
            "compression_stats",
        )


@pytest.mark.snapshot
@pytest.mark.integration
class TestPipelineResultSnapshots:
    """Snapshots do resultado completo do pipeline (agentes + safety).

    Usa pipeline_result fixture (pipeline completo em --no-llm --no-rag).
    NÃO misturar com aggregated_report para evitar double-sanitization.
    """

    def test_detection_snapshot(self, pipeline_result, snapshot_manager):
        """Verifica que detecção heurística é determinística."""
        detection = pipeline_result["stages"]["detection"]
        snapshot_manager.assert_match(detection, "detection")

    def test_diagnosis_snapshot(self, pipeline_result, snapshot_manager):
        """Verifica que diagnóstico heurístico é determinístico."""
        diagnosis = pipeline_result["stages"]["diagnosis"]
        snapshot_manager.assert_match(diagnosis, "diagnosis")

    def test_mitigation_snapshot(self, pipeline_result, snapshot_manager):
        """Verifica que ação de mitigação proposta é determinística.

        MitigationAction é um @dataclass — serializado via dataclasses.asdict()
        pelo normalize_for_snapshot.
        """
        mitigation = pipeline_result["stages"]["mitigation"]
        snapshot_manager.assert_match(mitigation, "mitigation")

    def test_safety_validation_snapshot(self, pipeline_result, snapshot_manager):
        """Verifica que validação de segurança pré-execução é determinística."""
        safety = pipeline_result["stages"].get("safety_validation")
        assert safety is not None, "safety_validation stage ausente no result"
        snapshot_manager.assert_match(safety, "safety_validation")

    def test_remediation_validation_snapshot(self, pipeline_result, snapshot_manager):
        """Verifica que validação de remediação (versão/CVE) é determinística."""
        remediation = pipeline_result["stages"].get("remediation_validation")
        assert remediation is not None, "remediation_validation stage ausente no result"
        snapshot_manager.assert_match(remediation, "remediation_validation")

    def test_full_pipeline_result_snapshot(self, pipeline_result, snapshot_manager):
        """Verifica que resultado completo do pipeline é determinístico.

        Captura toda a estrutura de result incluindo stages e timeline.
        """
        snapshot_manager.assert_match(pipeline_result, "full_pipeline_result")

    def test_postmortem_structure_snapshot(
        self, pipeline_result, simulated_incident, snapshot_manager,
    ):
        """Verifica que estrutura do postmortem é determinística.

        Gera postmortem markdown via step_postmortem(result, orchestrator, incident).
        Parâmetro orchestrator não é utilizado no corpo da função — passa-se None.
        Timestamps dinâmicos (datetime.now()) são normalizados antes da comparação.
        """
        import io
        from contextlib import redirect_stdout, redirect_stderr

        from main import step_postmortem

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            postmortem_path, _ = step_postmortem(
                pipeline_result, simulated_incident,
            )

        # Lê o postmortem gerado e limpa o arquivo temporário
        postmortem_text = postmortem_path.read_text(encoding="utf-8")
        postmortem_path.unlink(missing_ok=True)

        # Normaliza timestamps e compara contra snapshot
        snapshot_manager.assert_text_match(postmortem_text, "postmortem_structure")
