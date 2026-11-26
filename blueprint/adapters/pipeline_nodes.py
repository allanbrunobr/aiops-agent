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


def make_predictive_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria o adaptador do estágio preditivo (run_predictive_stage).

    Guarda o PredictionReport (ou None) em ``metadata["_prediction"]``; o
    state_adapter o expõe como ``stages.prediction``. Nunca levanta: falha
    ou ausência de report degradam para ``None`` (paridade com step_predict V0).
    """

    def execute(state: IncidentState) -> IncidentState:
        prediction = None
        report_obj = (state.aggregated_report or {}).get("_report_obj")
        if report_obj is not None:
            try:
                from pipeline.predictive_stage import run_predictive_stage

                prediction = run_predictive_stage(report_obj)
            except Exception as exc:  # noqa: BLE001 — preditivo é best-effort
                log.warning("predictive.failed", error=repr(exc))
        return dataclasses.replace(
            state, metadata={**state.metadata, "_prediction": prediction}
        )

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
    # Cache em disco só quando a composition root informa o diretório (CLI):
    # --rebuild-rag ignora o cache, como o step_rag do V0.
    cache_dir = state.metadata.get("_rag_cache_dir")
    rebuild = bool(state.metadata.get("_rebuild_rag", False))
    content_hash = None
    try:
        if cache_dir:
            from rag.indexer import ObservabilityIndexer

            content_hash = ObservabilityIndexer._compute_content_hash(
                report_obj.log_templates,
                report_obj.metric_anomalies,
                report_obj.slow_traces,
            )
            if not rebuild and indexer.load_index(cache_dir, content_hash=content_hash):
                indexer._load_model()  # embedding das queries
                return
        indexer.build_index(
            report_obj.log_templates,
            report_obj.metric_anomalies,
            report_obj.slow_traces,
        )
        if cache_dir and content_hash is not None:
            try:
                indexer.save_index(cache_dir, content_hash=content_hash)
            except Exception as exc:  # cache é otimização
                log.warning("rag.index_cache_save_failed", error=str(exc))
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


def _record_diagnosis_timeline(metadata: dict, detection: dict | None = None) -> dict:
    """Transições da máquina de estados cobertas pelo node de diagnóstico.

    Mesmas transições e razões do V0 (``v0_pipeline`` ``_transition``): inclui
    IDLE→DETECTING e nomeia os serviços afetados. ``detection`` também é
    guardada em ``metadata["_detection"]`` (alimenta ``stages.detection``).
    """
    from domain.incident_state import with_transition

    detection = detection or {}
    affected = ", ".join(detection.get("affected_services", []))
    if detection:
        metadata = {**metadata, "_detection": detection}
    metadata = with_transition(
        metadata, "IDLE", "DETECTING", "Analisando dados de observabilidade..."
    )
    metadata = with_transition(
        metadata, "DETECTING", "DIAGNOSING", f"Investigando incidente em {affected}"
    )
    return with_transition(
        metadata, "DIAGNOSING", "MITIGATING", "Propondo remediação..."
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
            },
            detection,
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
        from app.builders import build_detector_agent

        detection = build_detector_agent(config).detect(report_obj)
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
            metadata=_record_diagnosis_timeline({**state.metadata, **extra}, detection),
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


# ---------------------------------------------------------------------------
# P4b (F5-4): memória episódica + aprendizado de skills (paridade com o V0)
# ---------------------------------------------------------------------------


def extract_fault_tags(report) -> list[str]:
    """Fault tags do AggregatedReport para roteamento/persistência de skills.

    Mesma heurística do ``_extract_fault_tags`` do V0 (removido junto com ele
    no F5-4f); mantida aqui para o V1 não depender do módulo V0.
    """
    tags: list[str] = []
    for tpl in getattr(report, "log_templates", []):
        tpl_lower = tpl.template.lower()
        if "connection pool" in tpl_lower or "pool exhausted" in tpl_lower:
            tags.append("connection_pool")
        if "memory" in tpl_lower or "oom" in tpl_lower or "heap" in tpl_lower:
            tags.append("memory_leak")
        if "timeout" in tpl_lower:
            tags.append("timeout")
        if "serial" in tpl_lower or "schema" in tpl_lower:
            tags.append("serialization_error")
    return list(set(tags)) or ["unknown"]


def persist_incident_step(
    memory_db, result: dict, report, scenario_name: str, elapsed_sec: float
) -> bool:
    """Grava o IncidentRecord (V0 ``step_persist_incident``). Best-effort.

    Returns:
        True se gravou; False se não havia root_cause ou se a gravação falhou.
    """
    from domain.confidence import confidence_to_score
    from rag.memory import IncidentRecord

    stages = result.get("stages", {})
    diagnosis = stages.get("diagnosis", {})
    root_cause = diagnosis.get("root_cause") or ""
    if not root_cause:
        return False
    try:
        services = getattr(report, "affected_services", None) or diagnosis.get(
            "affected_services", []
        )
        record = IncidentRecord.new(
            scenario=scenario_name,
            root_cause=root_cause,
            affected_services=list(services),
            diagnosis_confidence=confidence_to_score(diagnosis.get("confidence")),
            resolution_time_sec=elapsed_sec,
            tags=extract_fault_tags(report),
            evidence_summary="; ".join(
                str(e) for e in diagnosis.get("evidence", [])[:5]
            ),
        )
        memory_db.save_incident(record)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, mas registrado
        log.warning(
            "persistence.save_incident_failed",
            scenario=scenario_name,
            error=repr(exc),
        )
        return False


def learn_skills_step(
    memory_db,
    skill,
    *,
    tnr_result: str,
    reflection: dict,
    tools_used: list,
    iteration_count: int,
    use_llm: bool,
    client,
):
    """WRITE + EVOLVE do ciclo de skills (V0 ``step_skill_learning``).

    Grava o SkillOutcome (também em ``--no-llm``); o EMA de utility só roda com
    LLM (sem LLM a reflexão é placeholder, F16); skill fraca é reescrita como
    proposta pendente de revisão humana. Retorna a skill candidata ou None.
    """
    import datetime as _dt
    import uuid

    from agents.skill_evolver import SkillEvolver
    from rag.memory import SkillOutcome

    skill_id = getattr(skill, "skill_id", "default")
    tnr_passed = tnr_result == "success"
    outcome = SkillOutcome(
        outcome_id=str(uuid.uuid4()),
        skill_id=skill_id,
        incident_id=str(uuid.uuid4()),
        tnr_passed=tnr_passed,
        reflection_score=float(reflection.get("score", 0.5)),
        reflection_lesson=str(reflection.get("lesson", "")),
        tools_used=tools_used,
        iteration_count=iteration_count,
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    memory_db.save_skill_outcome(outcome)

    if use_llm and skill is not None:
        memory_db.update_skill_score(skill_id, tnr_passed, outcome.reflection_score)
        skill = memory_db.get_skill(skill_id) or skill

    if skill is None:
        return None
    evolver = SkillEvolver(client=client, memory_db=memory_db)
    if not evolver.should_evolve(skill):
        return None
    return evolver.evolve(skill)


def record_run_memory(
    state: IncidentState,
    *,
    use_llm: bool,
    elapsed_sec: float,
    memory_db=None,
    client=None,
) -> dict:
    """Persiste o incidente e o aprendizado de skills do run V1. Best-effort.

    Nunca levanta (não pode alterar o exit code). Retorna
    ``{"incident_saved": bool, "skill": <skill_id|None>, "evolved": <skill|None>}``.
    """
    summary: dict = {"incident_saved": False, "skill": None, "evolved": None}
    if not state.diagnosis:
        return summary
    try:
        from agents.skill_router import SkillRouter
        from blueprint.adapters.state_adapter import state_to_result

        if memory_db is None:
            from app.builders import build_memory_db

            memory_db = build_memory_db()
        result = state_to_result(state)
        report = (state.aggregated_report or {}).get("_report_obj")
        scenario = str(state.metadata.get("scenario") or "unknown")

        summary["incident_saved"] = persist_incident_step(
            memory_db, result, report, scenario, elapsed_sec
        )

        # READ + ROUTE: mesma skill que o roteador escolhe para este incidente.
        tags = extract_fault_tags(report)
        services = list(getattr(report, "affected_services", []) or [])
        router = SkillRouter(memory_db)
        router.seed_default_skills()
        skill = router.route(tags, services)
        summary["skill"] = getattr(skill, "skill_id", None)

        outcome = state.metadata.get("_agent_outcome")
        reflection = {"lesson": "no-llm mode", "score": 0.5, "priority_tools": []}
        outcome_reflection = getattr(outcome, "reflection", None)
        if use_llm and outcome_reflection:
            reflection = outcome_reflection
        tnr = (result["stages"].get("verification") or {}).get("result", "unknown")
        summary["evolved"] = learn_skills_step(
            memory_db,
            skill,
            tnr_result=tnr,
            reflection=reflection,
            tools_used=list(getattr(outcome, "tools_used", []) or []),
            iteration_count=int(getattr(outcome, "iterations", 0) or 0),
            use_llm=use_llm,
            client=client if use_llm else None,
        )
    except Exception as exc:  # noqa: BLE001 — memória é best-effort
        log.warning("v1.memory_skills_failed", error=repr(exc))
    return summary
