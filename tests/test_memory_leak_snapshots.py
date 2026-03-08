"""Testes de snapshot para o cenário de memory leak.

Captura outputs do pipeline memory-leak em modo seed-42 como snapshots JSON.
Separados dos snapshots do payment-pool para evitar conflitos.

Use --snapshot-update para regenerar snapshots após mudanças intencionais.
"""
from __future__ import annotations

import pytest


@pytest.mark.snapshot
@pytest.mark.integration
class TestMemoryLeakPipelineSnapshots:
    """Snapshots do pipeline memory-leak (agentes + safety).

    Usa memory_leak_pipeline_result fixture.
    Verifica que diagnóstico e mitigação são determinísticos para o cenário.
    """

    def test_memory_leak_diagnosis_snapshot(
        self, memory_leak_pipeline_result, snapshot_manager,
    ) -> None:
        """Verifica que diagnóstico heurístico do memory-leak é determinístico."""
        diagnosis = memory_leak_pipeline_result["stages"]["diagnosis"]
        snapshot_manager.assert_match(diagnosis, "memory_leak_diagnosis")

    def test_memory_leak_mitigation_snapshot(
        self, memory_leak_pipeline_result, snapshot_manager,
    ) -> None:
        """Verifica que ação de mitigação proposta para memory-leak é determinística."""
        mitigation = memory_leak_pipeline_result["stages"]["mitigation"]
        snapshot_manager.assert_match(mitigation, "memory_leak_mitigation")
