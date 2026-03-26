"""
Agent Orchestrator — orquestração agentic com plan/execute/reflect/replan.

Substitui o ReAct loop do DiagnosisAgent por um agente que decide seu próprio
plano de investigação baseado em skills e memória do Estágio 1.

Safety invariant (STRATUS): o agente decide O QUE investigar.
A safety layer (TNRSafetyGuard, validators) decide SE pode executar — e é
invariante, nunca controlada pelo LLM.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from agents.detector import DetectionAgent
from agents.diagnoser import DiagnosisAgent
from agents.mitigator import MitigationAgent
from agents.skill_router import DEFAULT_SKILL, SkillRouter
from rag.memory import IncidentMemoryDB
from safety.tnr import TNRSafetyGuard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PLAN_PROMPT = """You are an SRE agent planning an incident investigation.

INCIDENT SUMMARY:
{incident_summary}

AVAILABLE TOOLS:
{available_tools}

SELECTED SKILL: {skill_name} (utility: {utility_score}, based on {total_uses} past uses)
SKILL GUIDANCE: {skill_prompt_summary}

SIMILAR PAST INCIDENTS:
{similar_incidents}

LESSONS FROM PAST FAILURES:
{recent_reflections}

Create an investigation plan. Each step should:
1. Have a clear goal (what are you trying to find out?)
2. Specify which tool to use
3. Explain what result would confirm or refute the hypothesis

You can SKIP steps if past experience shows they're unnecessary for this type.
You can REORDER steps based on what's most likely to give signal fast.

Respond as JSON:
{{
    "hypothesis": "Initial hypothesis based on available data",
    "steps": [
        {{
            "step_id": 1,
            "goal": "What am I trying to find out",
            "tool": "tool_name",
            "tool_args": {{}},
            "expected_signal": "What result would confirm the hypothesis",
            "skip_condition": "Skip this step if..."
        }}
    ],
    "confidence_threshold": 0.8,
    "max_steps": 5
}}"""


STEP_REFLECT_PROMPT = """You are evaluating one step of an incident investigation.

HYPOTHESIS: {hypothesis}
STEP GOAL: {step_goal}
TOOL USED: {step_tool}
RESULT: {step_result}
EXPECTED SIGNAL: {expected_signal}
REMAINING STEPS: {remaining_steps}

Decide:
- "continue" — this step gave useful info, proceed to next step
- "replan" — this step shows the hypothesis is wrong, need new approach
- "finalize" — enough evidence to make a diagnosis now

Respond as JSON:
{{
    "decision": "continue|replan|finalize",
    "assessment": "What this result tells us",
    "confidence": 0.0,
    "reason": "Why this decision (only if replan)"
}}"""


REPLAN_PROMPT = """An incident investigation plan failed. Create a new plan.

ORIGINAL HYPOTHESIS: {original_hypothesis}
WHAT WAS TRIED:
{executed_steps}

WHY IT FAILED: {failure_reason}

INCIDENT DATA:
{incident_summary}

SKILL GUIDANCE:
{skill_guidance}

Create a NEW plan with a different approach. Don't repeat what already failed.
Respond as JSON:
{{
    "hypothesis": "New hypothesis based on what we learned",
    "steps": [
        {{
            "step_id": 1,
            "goal": "...",
            "tool": "tool_name",
            "tool_args": {{}},
            "expected_signal": "..."
        }}
    ]
}}"""


SYNTHESIZE_PROMPT = """Synthesize a final incident diagnosis from investigation evidence.

HYPOTHESIS: {hypothesis}
EVIDENCE COLLECTED:
{evidence}

INCIDENT SUMMARY:
{incident_summary}

Provide your diagnosis as JSON:
{{
    "root_cause": "One sentence describing the root cause",
    "evidence": ["Evidence 1", "Evidence 2"],
    "affected_services": ["service1"],
    "confidence": "high|medium|low",
    "timeline": "Sequence of events"
}}"""


FINAL_REFLECT_PROMPT = """Reflect on a completed incident investigation.

DIAGNOSIS: {diagnosis}
TNR RESULT: {tnr_result}
TOOLS USED: {tools_used}
PLAN STEPS: {plan_steps}
HYPOTHESIS: {hypothesis}

What worked? What should change next time?
Respond as JSON:
{{
    "lesson": "Key takeaway",
    "score": 0.0,
    "priority_tools": ["tool1", "tool2"],
    "prompt_suggestion": "What to improve"
}}"""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InvestigationStep:
    """Um passo no plano de investigação."""

    step_id: int
    goal: str
    tool: str
    tool_args: dict
    expected_signal: str
    skip_condition: str = ""
    result: str | None = None
    reflection: str | None = None


@dataclass
class InvestigationPlan:
    """Plano de investigação gerado pelo LLM."""

    hypothesis: str
    steps: list[InvestigationStep]
    confidence_threshold: float = 0.8
    max_steps: int = 5


@dataclass
class AgentOutcome:
    """Resultado completo de uma execução do AgentOrchestrator."""

    diagnosis: dict
    plan: InvestigationPlan
    tools_used: list[str] = field(default_factory=list)
    iterations: int = 0
    replans: int = 0
    tnr_passed: bool = False
    tnr_health_delta: float = 0.0
    tnr_message: str = ""
    mitigation: dict = field(default_factory=dict)
    reflection: dict | None = None


# ---------------------------------------------------------------------------
# AgentOrchestrator
# ---------------------------------------------------------------------------

# Tools que o planner pode incluir num plano — qualquer outra é filtrada.
_VALID_TOOLS = frozenset({
    "query_logs",
    "query_metrics",
    "query_traces",
    "search_similar",
    "correlate_signals",
    "reconstruct_timeline",
    "classify_fault_type",
})


class AgentOrchestrator:
    """Orquestrador agentic — decide o próprio plano de investigação.

    Fluxo:
      1. CONTEXTUALIZE — consulta skills + memória (Estágio 1)
      2. PLAN          — LLM gera plano de investigação
      3. EXECUTE       — executa steps com reflect entre cada um
      4. SAFETY        — Mitigation + TNR internamente (para replanning);
                         VersionValidator e PreValidator ficam no pipeline externo
                         (step_safety_validation / step_remediation_validation em main.py).
      5. LEARN         — reflexão final; persistência fica no caller (main.py)
    """

    MAX_REPLANS = 2

    def __init__(
        self,
        client,
        model: str,
        memory_db: IncidentMemoryDB | None,
        skill_router: SkillRouter | None,
        config=None,
    ) -> None:
        self.client = client
        self.model = model
        self.memory_db = memory_db
        self.skill_router = skill_router
        self.config = config

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, report, retriever) -> AgentOutcome:
        """Execução completa: detect → plan → execute → safety → learn."""
        # ── DETECT (determinístico — não muda) ────────────────────────
        detector = DetectionAgent()
        detection = detector.detect(report)

        if not detection.get("is_incident"):
            return AgentOutcome(
                diagnosis={"root_cause": None, "is_incident": False},
                plan=InvestigationPlan(hypothesis="No incident detected", steps=[]),
            )

        # ── 1. CONTEXTUALIZE ─────────────────────────────────────────
        tags = self._extract_tags(report)
        services = list(getattr(report, "affected_services", set()))
        skill = self.skill_router.route(tags, services) if self.skill_router else DEFAULT_SKILL

        similar: list = []
        if self.memory_db is not None:
            similar = self.memory_db.find_similar_incidents(
                affected_services=services, tags=tags, limit=3,
            )

        # ── 2. PLAN ──────────────────────────────────────────────────
        plan = self._generate_plan(report, skill, similar)
        replans = 0
        tools_used: list[str] = []
        diagnosis: dict | None = None

        # ── 3. EXECUTE (loop com reflect) ─────────────────────────────
        while replans <= self.MAX_REPLANS:
            for step in plan.steps:
                step.result = self._execute_step(step, report, retriever)
                tools_used.append(step.tool)

                step_reflection = self._reflect_on_step(plan, step, report)
                step.reflection = step_reflection.get("assessment", "")
                decision = step_reflection.get("decision", "continue")

                if decision == "finalize":
                    diagnosis = self._synthesize_diagnosis(plan, report, step_reflection, skill)
                    break

                if decision == "replan":
                    replans += 1
                    if replans <= self.MAX_REPLANS:
                        plan = self._replan(plan, step_reflection, report, skill)
                    break

                # decision == "continue" → próximo step

            if diagnosis:
                break

        if not diagnosis:
            diagnosis = self._synthesize_diagnosis(plan, report, {}, skill)

        # ── 4. SAFETY (invariante — nunca controlada pelo LLM) ────────
        mitigator = MitigationAgent()
        mitigation = mitigator.propose(diagnosis)
        tnr_guard = TNRSafetyGuard()
        tnr_result = mitigator.verify_with_tnr(mitigation, tnr_guard)
        tnr_passed = getattr(getattr(tnr_result, "result", None), "value", None) == "success"

        # TNR regression com replans disponíveis → mais uma tentativa
        if not tnr_passed and replans < self.MAX_REPLANS:
            replans += 1
            plan = self._replan(
                plan,
                {
                    "reason": f"TNR regression: {getattr(tnr_result, 'message', '')}",
                    "decision": "replan",
                },
                report,
                skill,
            )
            for step in plan.steps:
                step.result = self._execute_step(step, report, retriever)
                tools_used.append(step.tool)
            diagnosis = self._synthesize_diagnosis(plan, report, {}, skill)
            mitigation = mitigator.propose(diagnosis)
            tnr_result = mitigator.verify_with_tnr(mitigation, tnr_guard)
            tnr_passed = getattr(getattr(tnr_result, "result", None), "value", None) == "success"

        # ── 5. LEARN ─────────────────────────────────────────────────
        final_reflection = self._reflect_final(diagnosis, tnr_passed, tools_used, plan)

        return AgentOutcome(
            diagnosis=diagnosis,
            plan=plan,
            mitigation=mitigation,
            tnr_passed=tnr_passed,
            tnr_health_delta=getattr(tnr_result, "health_delta", 0.0),
            tnr_message=getattr(tnr_result, "message", ""),
            tools_used=tools_used,
            iterations=sum(1 for s in plan.steps if s.result is not None),
            replans=replans,
            reflection=final_reflection,
        )

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _generate_plan(self, report, skill, similar) -> InvestigationPlan:
        """LLM gera plano de investigação baseado em contexto + skill."""
        similar_text = "\n".join(
            f"- [{inc.scenario}] {inc.root_cause} (confidence: {inc.diagnosis_confidence})"
            for inc in similar[:3]
        ) or "(nenhum incidente similar encontrado)"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": PLAN_PROMPT.format(
                        incident_summary=report.to_compact_text()[:1500],
                        available_tools=self._list_tools(),
                        skill_name=skill.name,
                        utility_score=skill.utility_score,
                        total_uses=skill.total_uses,
                        skill_prompt_summary=skill.system_prompt[:300],
                        similar_incidents=similar_text,
                        recent_reflections=skill.last_reflection or "(nenhuma)",
                    ),
                }],
            )
            data = json.loads(response.content[0].text)
        except Exception:  # noqa: BLE001
            return self._default_plan(report)

        steps = [
            InvestigationStep(
                step_id=s["step_id"],
                goal=s["goal"],
                tool=s["tool"],
                tool_args=s.get("tool_args", {}),
                expected_signal=s.get("expected_signal", ""),
                skip_condition=s.get("skip_condition", ""),
            )
            for s in data.get("steps", [])
            if s.get("tool") in _VALID_TOOLS
        ]

        if not steps:
            return self._default_plan(report)

        return InvestigationPlan(
            hypothesis=data.get("hypothesis", "Unknown root cause"),
            steps=steps,
            confidence_threshold=data.get("confidence_threshold", 0.8),
            max_steps=data.get("max_steps", 5),
        )

    def _default_plan(self, report) -> InvestigationPlan:
        """Plano de fallback quando o LLM falha ou retorna plano inválido."""
        services = list(getattr(report, "affected_services", ["unknown"]))
        svc = services[0] if services else "unknown"
        return InvestigationPlan(
            hypothesis="Unknown fault — using default investigation strategy",
            steps=[
                InvestigationStep(
                    step_id=1,
                    goal="Classify fault type",
                    tool="classify_fault_type",
                    tool_args={"service": svc},
                    expected_signal="Fault category",
                ),
                InvestigationStep(
                    step_id=2,
                    goal="Correlate signals",
                    tool="correlate_signals",
                    tool_args={"service": svc, "time_window_minutes": 30},
                    expected_signal="Correlated anomalies",
                ),
            ],
        )

    def _execute_step(self, step: InvestigationStep, report, retriever) -> str:
        """Executa um step do plano reutilizando a infra de tools do DiagnosisAgent."""
        diagnoser = DiagnosisAgent()
        try:
            return diagnoser._execute_tool(step.tool, step.tool_args, report, retriever)
        except Exception as exc:  # noqa: BLE001
            return f"Tool execution failed: {exc}"

    def _reflect_on_step(
        self, plan: InvestigationPlan, step: InvestigationStep, report
    ) -> dict:
        """LLM avalia resultado de um step e decide: continue/replan/finalize."""
        remaining = len([
            s for s in plan.steps
            if s.step_id > step.step_id and s.result is None
        ])
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": STEP_REFLECT_PROMPT.format(
                        hypothesis=plan.hypothesis,
                        step_goal=step.goal,
                        step_tool=step.tool,
                        step_result=(step.result or "")[:800],
                        expected_signal=step.expected_signal,
                        remaining_steps=remaining,
                    ),
                }],
            )
            return json.loads(response.content[0].text)
        except Exception:  # noqa: BLE001
            return {"decision": "continue", "assessment": "Reflection parse failed"}

    def _replan(
        self,
        old_plan: InvestigationPlan,
        reflection: dict,
        report,
        skill,
    ) -> InvestigationPlan:
        """LLM gera novo plano baseado no que deu errado."""
        executed = [
            f"Step {s.step_id} ({s.tool}): {(s.result or 'not executed')[:100]}"
            for s in old_plan.steps
        ]
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": REPLAN_PROMPT.format(
                        original_hypothesis=old_plan.hypothesis,
                        executed_steps="\n".join(executed),
                        failure_reason=reflection.get("reason", "unknown"),
                        incident_summary=report.to_compact_text()[:1000],
                        skill_guidance=skill.system_prompt[:300],
                    ),
                }],
            )
            data = json.loads(response.content[0].text)
            steps = [
                InvestigationStep(
                    step_id=s["step_id"],
                    goal=s["goal"],
                    tool=s["tool"],
                    tool_args=s.get("tool_args", {}),
                    expected_signal=s.get("expected_signal", ""),
                )
                for s in data.get("steps", [])
                if s.get("tool") in _VALID_TOOLS
            ]
            return InvestigationPlan(
                hypothesis=data.get("hypothesis", old_plan.hypothesis),
                steps=steps or old_plan.steps,
            )
        except Exception:  # noqa: BLE001
            return old_plan  # fallback: mantém plano original

    def _synthesize_diagnosis(
        self, plan: InvestigationPlan, report, last_reflection: dict, skill=None
    ) -> dict:
        """LLM sintetiza diagnóstico final a partir das evidências coletadas.

        O skill.system_prompt é injetado como system message para que o domínio
        de conhecimento da skill guie não só o plano mas também a síntese.
        """
        evidence = [
            f"[{s.tool}] {s.goal}: {(s.result or 'no data')[:200]}"
            for s in plan.steps
            if s.result
        ]
        skill_prompt = getattr(skill, "system_prompt", None)
        system_msg = skill_prompt[:300] if skill_prompt else None
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{
                "role": "user",
                "content": SYNTHESIZE_PROMPT.format(
                    hypothesis=plan.hypothesis,
                    evidence="\n".join(evidence) or "(no evidence collected)",
                    incident_summary=report.to_compact_text()[:500],
                ),
            }],
        }
        if system_msg:
            kwargs["system"] = system_msg
        try:
            response = self.client.messages.create(**kwargs)
            return json.loads(response.content[0].text)
        except Exception:  # noqa: BLE001
            return DiagnosisAgent()._heuristic_diagnosis(report)

    def _reflect_final(
        self,
        diagnosis: dict,
        tnr_passed: bool,
        tools_used: list[str],
        plan: InvestigationPlan,
    ) -> dict:
        """Reflexão final sobre toda a investigação."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": FINAL_REFLECT_PROMPT.format(
                        diagnosis=json.dumps(diagnosis, default=str)[:500],
                        tnr_result="success" if tnr_passed else "regression",
                        tools_used=tools_used,
                        plan_steps=len(plan.steps),
                        hypothesis=plan.hypothesis,
                    ),
                }],
            )
            return json.loads(response.content[0].text)
        except Exception:  # noqa: BLE001
            return {"lesson": "Reflection unavailable", "score": 0.5}

    def _extract_tags(self, report) -> list[str]:
        """Extrai fault tags do report para roteamento de skills."""
        tags = []
        for tpl in getattr(report, "log_templates", []):
            tpl_lower = tpl.template.lower()
            if "connection pool" in tpl_lower:
                tags.append("connection_pool")
            if "memory" in tpl_lower or "oom" in tpl_lower:
                tags.append("memory_leak")
            if "timeout" in tpl_lower:
                tags.append("timeout")
            if "serial" in tpl_lower or "schema" in tpl_lower:
                tags.append("serialization_error")
        return list(set(tags)) or ["unknown"]

    def _list_tools(self) -> str:
        """Lista tools disponíveis para o planner."""
        return (
            "- query_logs(service, keyword): Search logs for patterns\n"
            "- query_metrics(service, metric_name): Get metric anomaly details\n"
            "- query_traces(service): Get slow trace details\n"
            "- search_similar(query): Semantic search across all data (RAG)\n"
            "- correlate_signals(service, time_window_minutes): Cross-correlate log/metric/trace\n"
            "- reconstruct_timeline(services): Build chronological event timeline\n"
            "- classify_fault_type(service): Classify probable fault category"
        )
