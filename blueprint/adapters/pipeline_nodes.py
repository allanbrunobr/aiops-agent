"""Adaptadores de nodes do pipeline para o Blueprint Engine.

Cada funcao make_*_node() retorna um Callable[[IncidentState], IncidentState]
que encapsula a logica existente do pipeline sem modifica-la.

Dependencia: blueprint/adapters/ importa de pipeline/, agents/, rag/.
O inverso NUNCA e verdadeiro.
"""
from __future__ import annotations

from typing import Any, Callable

from config.loader import AppConfig
from domain.incident_state import IncidentState


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
        return state.model_copy(update={
            "aggregated_report": {
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
        })

    return execute


def make_context_compressor_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
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
        return state.model_copy(update={"aggregated_report": updated_report})

    return execute


def make_diagnoser_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria node agentic que usa AgentOrchestrator (Estágio 2) ou fallback heurístico."""

    def execute(state: IncidentState) -> IncidentState:
        import os
        from adapters.llm.anthropic_client import create_anthropic_llm_client
        from agents.orchestrator import AgentOrchestrator
        from agents.skill_router import SkillRouter
        from rag.memory import IncidentMemoryDB

        report_data = state.aggregated_report or {}
        report_obj = report_data.get("_report_obj")
        if report_obj is None:
            raise ValueError("AggregatedReport not available — aggregator node must run first")

        retriever = state.metadata.get("_retriever")
        model = os.environ.get("AIOPS_LLM_MODEL", "claude-sonnet-4-20250514")

        memory_db = IncidentMemoryDB()
        skill_router = SkillRouter(memory_db)
        skill_router.seed_default_skills()

        client = create_anthropic_llm_client(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        orchestrator = AgentOrchestrator(
            client=client, model=model,
            memory_db=memory_db, skill_router=skill_router,
            config=config,
        )
        outcome = orchestrator.run(report_obj, retriever)

        return state.model_copy(update={
            "diagnosis": outcome.diagnosis,
            "metadata": {**state.metadata, "_agent_outcome": outcome},
        })

    return execute


def make_diagnoser_fallback(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria funcao de fallback heuristico para o DiagnosisAgent.

    Usa a mesma logica de _heuristic_diagnosis() do DiagnosisAgent,
    sem chamar a API do Claude.
    """
    def execute(state: IncidentState) -> IncidentState:
        from agents.diagnoser import DiagnosisAgent

        diagnoser = DiagnosisAgent()

        report_data = state.aggregated_report or {}
        report_obj = report_data.get("_report_obj")
        if report_obj is None:
            return state.model_copy(update={
                "diagnosis": {
                    "root_cause": "Unable to diagnose — no aggregated report available",
                    "evidence": [],
                    "affected_services": [],
                    "confidence": "low",
                    "timeline": "No data",
                },
            })

        diagnosis = diagnoser._heuristic_diagnosis(report_obj)
        return state.model_copy(update={"diagnosis": diagnosis})

    return execute


def make_postmortem_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria funcao adaptadora para geracao de postmortem (agentic — usa LLM)."""
    def execute(state: IncidentState) -> IncidentState:
        # Gera postmortem a partir do diagnostico e dados do pipeline
        diagnosis = state.diagnosis or {}
        report_data = state.aggregated_report or {}

        # Template de postmortem simplificado (equivalente ao step_postmortem de main.py)
        root_cause = diagnosis.get("root_cause", "Unknown")
        evidence = diagnosis.get("evidence", [])
        affected = diagnosis.get("affected_services", [])
        confidence = diagnosis.get("confidence", "unknown")
        timeline = diagnosis.get("timeline", "N/A")

        postmortem = f"""# Incident Postmortem

## Summary
- **Root Cause:** {root_cause}
- **Confidence:** {confidence}
- **Affected Services:** {", ".join(affected) if affected else "Unknown"}
- **Severity:** {report_data.get("severity", "Unknown")}

## Evidence
{chr(10).join(f"- {e}" for e in evidence)}

## Timeline
{timeline}

## Lessons Learned
- Automated detection identified the incident via correlated signals
- {"LLM-assisted" if not state.no_llm else "Heuristic"} diagnosis with {confidence} confidence

## Action Items
- [ ] Review root cause and verify fix
- [ ] Update monitoring thresholds if needed
- [ ] Document lessons learned in knowledge base
"""
        return state.model_copy(update={"postmortem_md": postmortem})

    return execute


def make_postmortem_fallback(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Fallback para postmortem — mesma logica, sem LLM."""
    # Para V1, o postmortem e template-based (nao usa LLM), entao fallback = execute
    return make_postmortem_node(config)
