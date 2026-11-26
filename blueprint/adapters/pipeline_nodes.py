"""Adaptadores de nodes do pipeline para o Blueprint Engine.

Cada funcao make_*_node() retorna um Callable[[IncidentState], IncidentState]
que encapsula a logica existente do pipeline sem modifica-la.

Dependencia: blueprint/adapters/ importa de pipeline/, agents/, rag/.
O inverso NUNCA e verdadeiro.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

from config.loader import AppConfig
from core.logging import get_logger
from domain.incident_state import IncidentState

log = get_logger("blueprint.nodes")


def make_aggregator_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria funcao adaptadora para o Aggregator (LogParser + MetricFilter + TraceFilter).

    O Aggregator ja orquestra os tres sub-processadores internamente.
    Este adaptador traduz entre IncidentState e a interface existente.

    Args:
        config: Configuracao com thresholds para os sub-processadores.

    Returns:
        Callable que aceita e retorna IncidentState.
    """

    def execute(state: IncidentState) -> IncidentState:
        from pipeline.aggregator import Aggregator

        aggregator = Aggregator(
            sigma_threshold=config.thresholds.sigma_threshold,
            baseline_ratio=config.thresholds.baseline_ratio,
            percentile_threshold=config.thresholds.percentile_threshold,
            max_hops=config.thresholds.max_hops,
        )

        # Extrai dados brutos do metadata do estado
        raw_data = state.metadata.get("raw_incident_data", {})
        logs = raw_data.get("logs", [])
        metrics = raw_data.get("metrics", [])
        traces = raw_data.get("traces", [])

        report = aggregator.process(logs, metrics, traces)

        # Armazena report como dict serializavel no IncidentState
        return dataclasses.replace(
            state,
            aggregated_report={
                "log_templates_count": len(report.log_templates),
                "metric_anomalies_count": len(report.metric_anomalies),
                "slow_traces_count": len(report.slow_traces),
                "severity": report.severity,
                "affected_services": list(report.affected_services),
                "text": report.to_text(),
                "compact_text": report.to_compact_text(),
                # Referencia ao objeto vivo para nodes downstream
                "_report_obj": report,
            },
        )

    return execute


def make_context_compressor_node(
    config: AppConfig,
) -> Callable[[IncidentState], IncidentState]:
    """Cria funcao adaptadora para o ContextCompressor.

    Comprime o AggregatedReport para otimizar tokens do LLM.
    """

    def execute(state: IncidentState) -> IncidentState:
        from pipeline.context_compressor import ContextCompressor

        report_data = state.aggregated_report
        if report_data is None:
            return state

        compressor = ContextCompressor()

        # Usa o objeto AggregatedReport vivo se disponivel
        report_obj = report_data.get("_report_obj")
        if report_obj is not None:
            compressed_text, stats = compressor.compress(report_obj)
        else:
            # Fallback: comprime o texto bruto
            report_text = report_data.get("text", "")
            compressed_text, stats = compressor.compress_text(report_text)

        # Atualiza o campo de texto comprimido
        updated_report = {**report_data, "compressed_text": compressed_text}
        return dataclasses.replace(state, aggregated_report=updated_report)

    return execute


def _ensure_rag_index(state: IncidentState, report_obj) -> None:
    """Build lazy do índice RAG a partir do report agregado (F2-2).

    build_rag() cria o indexer vazio; o índice é construído aqui, no primeiro
    node que tem o report — sem isso o RAG do V1 nasce vazio. Falha de
    indexação degrada graciosamente (retriever retorna [] com index None),
    com warning estruturado — RAG não é camada de safety.
    """
    if state.no_rag:
        return
    retriever = state.metadata.get("_retriever")
    if retriever is None:
        return
    indexer = getattr(retriever, "indexer", None)
    if indexer is None or getattr(indexer, "index", None) is not None:
        return
    try:
        indexer.build_index(
            report_obj.log_templates,
            report_obj.metric_anomalies,
            report_obj.slow_traces,
        )
    except Exception as exc:
        log.warning(
            "rag.index_build_failed",
            correlation_id=state.correlation_id,
            error=str(exc),
        )


def proposed_action_from_mitigation(mitigation: dict | None) -> dict | None:
    """Converte a saída do MitigationAgent em IncidentState.proposed_action.

    Contrato de state.proposed_action (dict): description, command, risk_level,
    rollback. O MitigationAction do domain é a fonte; a conversão acontece aqui,
    na borda do blueprint (F2-1). Os safety nodes (version_validator,
    pre_validator, tnr) leem state.proposed_action; sem ele, os três fazem skip.
    """
    action = (mitigation or {}).get("action")
    if action is None:
        return None
    return {
        "description": getattr(action, "description", ""),
        "command": getattr(action, "command", ""),
        "risk_level": getattr(action, "risk_level", "MEDIUM"),
        "rollback": getattr(action, "rollback_command", None),
    }


def _record_diagnosis_timeline(metadata: dict) -> dict:
    """Transições da máquina de estados cobertas pelo node de diagnóstico."""
    from domain.incident_state import with_transition

    metadata = with_transition(
        metadata, "DETECTING", "DIAGNOSING", "Diagnosticando causa raiz..."
    )
    return with_transition(
        metadata, "DIAGNOSING", "MITIGATING", "Propondo mitigação..."
    )


def make_diagnoser_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria node agentic que usa AgentOrchestrator (Estágio 2) ou fallback heurístico."""

    def execute(state: IncidentState) -> IncidentState:
        # Lazy: app.builders importa rag.indexer (ML) e blueprint.pipelines
        # (que importa este módulo) — import no topo criaria ciclo e violaria
        # o isolamento de ML da coleta de testes.
        from app.builders import build_agent_orchestrator, build_detector_agent

        report_data = state.aggregated_report or {}
        report_obj = report_data.get("_report_obj")
        if report_obj is None:
            raise ValueError(
                "AggregatedReport not available — aggregator node must run first"
            )

        _ensure_rag_index(state, report_obj)
        retriever = state.metadata.get("_retriever")

        # F5-1/F5-2: detecção com os thresholds de config (paridade com a CLI)
        # e orchestrator via builder único — antes o node repetia o wiring.
        detection = build_detector_agent(config).detect(report_obj)
        orchestrator = build_agent_orchestrator(config)
        outcome = orchestrator.run(report_obj, retriever, detection=detection)

        metadata = _record_diagnosis_timeline(
            {
                **state.metadata,
                "_agent_outcome": outcome,
                "_pattern_matched": (outcome.mitigation or {}).get(
                    "pattern_matched", "unknown"
                ),
            }
        )
        return dataclasses.replace(
            state,
            diagnosis=outcome.diagnosis,
            proposed_action=proposed_action_from_mitigation(outcome.mitigation),
            metadata=metadata,
        )

    return execute


def make_diagnoser_fallback(
    config: AppConfig,
) -> Callable[[IncidentState], IncidentState]:
    """Cria funcao de fallback heuristico para o DiagnosisAgent.

    Usa a mesma logica de heuristic_diagnosis() do DiagnosisAgent,
    sem chamar a API do Claude.
    """

    def execute(state: IncidentState) -> IncidentState:
        from agents.diagnoser import DiagnosisAgent

        diagnoser = DiagnosisAgent()

        report_data = state.aggregated_report or {}
        report_obj = report_data.get("_report_obj")
        if report_obj is None:
            return dataclasses.replace(
                state,
                diagnosis={
                    "root_cause": "Unable to diagnose — no aggregated report available",
                    "evidence": [],
                    "affected_services": [],
                    "confidence": "low",
                    "timeline": "No data",
                },
            )

        _ensure_rag_index(state, report_obj)
        diagnosis = diagnoser.heuristic_diagnosis(report_obj)

        # Mesma proposta que o caminho com LLM faz dentro do AgentOrchestrator:
        # sem ela, os safety nodes seguintes não têm o que validar (F2-1,
        # inclusive no modo --no-llm).
        proposed_action = None
        extra: dict = {}
        if diagnosis.get("root_cause"):
            from agents.mitigator import MitigationAgent

            mitigation = MitigationAgent().propose_safe(diagnosis)
            proposed_action = proposed_action_from_mitigation(mitigation)
            extra["_pattern_matched"] = mitigation.get("pattern_matched", "unknown")

        return dataclasses.replace(
            state,
            diagnosis=diagnosis,
            proposed_action=proposed_action,
            metadata=_record_diagnosis_timeline({**state.metadata, **extra}),
        )

    return execute


def make_postmortem_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria funcao adaptadora para geracao de postmortem (renderer rico, sem LLM)."""

    def execute(state: IncidentState) -> IncidentState:
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from application.use_cases.postmortem import render_postmortem_markdown
        from blueprint.adapters.state_adapter import state_to_result

        incident = state.metadata.get("_incident")
        if incident is None:
            # Estado sem SimulatedIncident (ex.: alerta real): a tabela de
            # Timeline usa o instante de início do estado, sem inventar horários.
            t0 = state.started_at or datetime.now(timezone.utc)
            incident = SimpleNamespace(
                deploy_time=t0, spike_start=t0, detection_time=t0, fix_time=t0
            )
        postmortem = render_postmortem_markdown(
            state_to_result(state), incident, now=datetime.now()
        )
        return dataclasses.replace(state, postmortem_md=postmortem)

    return execute


def make_postmortem_fallback(
    config: AppConfig,
) -> Callable[[IncidentState], IncidentState]:
    """Fallback para postmortem — mesma logica, sem LLM."""
    # Para V1, o postmortem e template-based (nao usa LLM), entao fallback = execute
    return make_postmortem_node(config)
