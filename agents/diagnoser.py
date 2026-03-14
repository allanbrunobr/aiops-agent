"""
Diagnosis Agent — ReAct loop com Claude para Root Cause Analysis.

Baseado nos papers:
  - Microsoft RCA Agent (2024): ReAct loop com tools
  - OpsAgent (Nankai 2025): multi-modal data processing
  - STRATUS (NeurIPS 2025): structured agent loop

O agente raciocina passo a passo, usando tools para buscar
dados adicionais, até convergir em um diagnóstico.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from anthropic import Anthropic
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

console = Console()

SYSTEM_PROMPT = """You are an expert SRE agent performing Root Cause Analysis on a production incident.

You have access to the following tools:
1. query_logs(service, keyword) - Search logs for a specific service and keyword
2. query_metrics(service, metric_name) - Get metric details for a service
3. query_traces(service) - Get slow traces for a service
4. search_similar(query) - Semantic search across all observability data

RULES:
- Think step by step. First understand WHAT is failing, then WHY.
- Use tools to gather evidence. Don't guess.
- Cross-correlate logs, metrics, and traces to build a complete picture.
- Look for temporal correlation (what changed right before the incident?).
- Consider cascade effects (service A failing may cause service B errors).

When you have enough evidence, provide your final diagnosis in this JSON format:
```json
{
    "root_cause": "One sentence describing the root cause",
    "evidence": ["Evidence 1", "Evidence 2", ...],
    "affected_services": ["service1", "service2"],
    "confidence": "high|medium|low",
    "timeline": "Sequence of events"
}
```

IMPORTANT: Wrap your final answer in <DIAGNOSIS> tags like:
<DIAGNOSIS>{"root_cause": "...", ...}</DIAGNOSIS>
"""


class DiagnosisAgent:
    """Agente de diagnóstico usando ReAct loop com Claude."""

    def __init__(self, max_iterations: int = 8, model: str = "claude-sonnet-4-20250514"):
        self.max_iterations = max_iterations
        self.model = model
        self.client = None

    def _get_client(self) -> Anthropic:
        if self.client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("API key not configured")
            self.client = Anthropic(api_key=api_key)
        return self.client

    def diagnose(self, report, retriever) -> dict:
        """Executa ReAct loop para diagnosticar o incidente."""
        # Prepara contexto inicial com dados pré-processados
        initial_context = report.to_text()

        # Cria as tools simuladas
        tools = self._build_tools(report, retriever)

        # ReAct loop
        messages = [
            {"role": "user", "content": (
                f"Analyze this production incident and find the root cause.\n\n"
                f"{initial_context}\n\n"
                f"Use the tools available to investigate further. "
                f"Think step by step and provide your diagnosis."
            )}
        ]

        diagnosis = None
        for iteration in range(1, self.max_iterations + 1):
            console.print(f"\n  [dim]--- Iteration {iteration}/{self.max_iterations} ---[/dim]")

            try:
                response = self._call_llm(messages, tools)
            except Exception as e:
                console.print("  [red]LLM call failed. Falling back to heuristic mode.[/red]")
                # Fallback: diagnóstico baseado em heurística
                return self._heuristic_diagnosis(report)

            # Processa a resposta
            assistant_text = ""
            tool_calls = []

            for block in response.content:
                if block.type == "text":
                    assistant_text += block.text
                elif block.type == "tool_use":
                    tool_calls.append(block)

            # Mostra o raciocínio
            if assistant_text:
                # Extrai thought
                for line in assistant_text.split("\n"):
                    line = line.strip()
                    if line:
                        if any(line.startswith(p) for p in ["Thought:", "Let me", "I need", "Looking", "The", "Based", "This"]):
                            console.print(f"  💭 Thought: {line[:120]}", style="italic")
                        break

            # Verifica se tem diagnóstico final
            if "<DIAGNOSIS>" in assistant_text:
                diagnosis = self._parse_diagnosis(assistant_text)
                if diagnosis:
                    break

            # Executa tool calls
            if tool_calls:
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for tc in tool_calls:
                    tool_name = tc.name
                    tool_input = tc.input
                    console.print(f"  🔧 Action: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:80]})")

                    result = self._execute_tool(tool_name, tool_input, report, retriever)
                    console.print(f"  👁️ Observation: {result[:100]}...")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": result,
                    })

                messages.append({"role": "user", "content": tool_results})
            elif not diagnosis:
                # Sem tool calls e sem diagnóstico — pede para concluir
                messages.append({"role": "assistant", "content": assistant_text})
                messages.append({"role": "user", "content": (
                    "Please provide your final diagnosis now. "
                    "Wrap it in <DIAGNOSIS> tags as instructed."
                )})

        if not diagnosis:
            diagnosis = self._heuristic_diagnosis(report)

        # Mostra diagnóstico
        console.print(Panel(
            f"[bold]Root Cause:[/bold] {diagnosis.get('root_cause', 'Unknown')}\n"
            f"[bold]Confidence:[/bold] {diagnosis.get('confidence', 'unknown')}\n"
            f"[bold]Evidence:[/bold] {', '.join(diagnosis.get('evidence', [])[:3])}",
            title="Diagnosis Result",
            border_style="green",
        ))

        return diagnosis

    def _call_llm(self, messages: list, tools: list) -> object:
        """Chama Claude com tools."""
        client = self._get_client()
        return client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
        )

    def _build_tools(self, report, retriever) -> list:
        """Define as tools disponíveis para o agente."""
        return [
            {
                "name": "query_logs",
                "description": "Search logs for a specific service and keyword pattern. Returns matching log entries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Service name (e.g., 'payment-svc')"},
                        "keyword": {"type": "string", "description": "Keyword to search for in log messages"},
                    },
                    "required": ["service", "keyword"],
                },
            },
            {
                "name": "query_metrics",
                "description": "Get metric anomaly details for a specific service and metric name.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Service name"},
                        "metric_name": {"type": "string", "description": "Metric name (e.g., 'cpu_usage', 'latency', 'error_rate', 'connection_pool')"},
                    },
                    "required": ["service"],
                },
            },
            {
                "name": "query_traces",
                "description": "Get slow trace details for a specific service, including call paths and bottlenecks.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Service name"},
                    },
                    "required": ["service"],
                },
            },
            {
                "name": "search_similar",
                "description": "Semantic search across all observability data (logs, metrics, traces). Use natural language queries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language search query"},
                    },
                    "required": ["query"],
                },
            },
            # === Enhanced tools (TAMO-inspired) ===
            {
                "name": "correlate_signals",
                "description": (
                    "Cross-correlate signals across logs, metrics, and traces within a time window. "
                    "Finds temporal relationships between anomalies in different data sources. "
                    "Based on TAMO: multi-modality alignment for cross-modal feature consistency."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Service to correlate signals for"},
                        "time_window_minutes": {"type": "integer", "description": "Time window in minutes (default: 5)", "default": 5},
                    },
                    "required": ["service"],
                },
            },
            {
                "name": "reconstruct_timeline",
                "description": (
                    "Build a chronological event timeline from all data sources (logs, metrics, traces). "
                    "Shows the sequence of events leading to the incident. "
                    "Useful for understanding cascade failures and identifying the initial trigger."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "services": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Services to include in timeline (empty = all services)",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "classify_fault_type",
                "description": (
                    "Classify the type of fault based on observed patterns. "
                    "Returns probable fault categories: config_change, resource_exhaustion, "
                    "dependency_failure, performance_degradation, deployment_issue. "
                    "Based on TAMO: fault type classification tool."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "Primary affected service"},
                    },
                    "required": ["service"],
                },
            },
        ]

    def _execute_tool(self, tool_name: str, tool_input: dict, report, retriever) -> str:
        """Executa uma tool e retorna o resultado como texto."""
        try:
            if tool_name == "query_logs":
                service = tool_input.get("service", "")
                keyword = tool_input.get("keyword", "")
                matches = []
                for tpl in report.log_templates:
                    if (service.lower() in ",".join(tpl.services).lower() and
                            keyword.lower() in tpl.template.lower()):
                        svcs = ", ".join(sorted(tpl.services))
                        matches.append(
                            f"[{tpl.level}][{svcs}] \"{tpl.template}\" x{tpl.count} "
                            f"({tpl.first_seen.strftime('%H:%M:%S')}→{tpl.last_seen.strftime('%H:%M:%S')})"
                        )
                if not matches:
                    return f"No logs found for service='{service}' keyword='{keyword}'"
                return "\n".join(matches[:10])

            elif tool_name == "query_metrics":
                service = tool_input.get("service", "")
                metric = tool_input.get("metric_name", "")
                matches = []
                for a in report.metric_anomalies:
                    if service.lower() in a.service.lower():
                        if not metric or metric.lower() in a.metric_name.lower():
                            matches.append(
                                f"{a.service}/{a.metric_name}: {a.anomaly_type} "
                                f"value={a.value} (baseline={a.mean}, {a.deviation_sigma}sigma) "
                                f"at {a.timestamp.strftime('%H:%M:%S')}"
                            )
                if not matches:
                    return f"No metric anomalies found for service='{service}'"
                return "\n".join(matches)

            elif tool_name == "query_traces":
                service = tool_input.get("service", "")
                matches = []
                for t in report.slow_traces:
                    if service.lower() in t.bottleneck_service.lower() or service.lower() in " ".join(t.path).lower():
                        path_str = " → ".join(t.path)
                        attrs = ", ".join(f"{k}={v}" for k, v in t.attributes.items()) if t.attributes else ""
                        matches.append(
                            f"{path_str}: {t.total_duration_ms:.0f}ms (normal: {t.normal_duration_ms:.0f}ms) "
                            f"bottleneck={t.bottleneck_service} | {attrs}"
                        )
                if not matches:
                    return f"No slow traces found involving service='{service}'"
                return "\n".join(matches)

            elif tool_name == "search_similar":
                query = tool_input.get("query", "")
                if retriever:
                    results = retriever.search(query, top_k=5)
                    return retriever.format_results(results)
                return "RAG not available"

            # === Enhanced tools (TAMO-inspired) ===

            elif tool_name == "correlate_signals":
                return self._tool_correlate_signals(tool_input, report)

            elif tool_name == "reconstruct_timeline":
                return self._tool_reconstruct_timeline(tool_input, report)

            elif tool_name == "classify_fault_type":
                return self._tool_classify_fault_type(tool_input, report)

            return f"Unknown tool: {tool_name}"

        except Exception as e:
            return f"Tool error: {e}"

    def _parse_diagnosis(self, text: str) -> dict | None:
        """Extrai diagnóstico das tags <DIAGNOSIS>."""
        start = text.find("<DIAGNOSIS>")
        end = text.find("</DIAGNOSIS>")
        if start == -1 or end == -1:
            return None

        json_str = text[start + len("<DIAGNOSIS>"):end].strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Tenta extrair campos manualmente
            return {"root_cause": json_str, "confidence": "medium", "evidence": []}

    # === Enhanced TAMO-inspired tool implementations ===

    def _tool_correlate_signals(self, tool_input: dict, report) -> str:
        """
        Cross-correlate signals across logs, metrics, and traces.
        Based on TAMO: multi-modality alignment for cross-modal feature consistency.
        """
        service = tool_input.get("service", "").lower()
        window_min = tool_input.get("time_window_minutes", 5)

        correlations = []

        # 1. Find all signals for this service
        service_logs = [
            tpl for tpl in report.log_templates
            if service in ",".join(tpl.services).lower() and tpl.level in ("ERROR", "FATAL", "WARN")
        ]
        service_metrics = [
            a for a in report.metric_anomalies
            if service in a.service.lower()
        ]
        service_traces = [
            t for t in report.slow_traces
            if service in t.bottleneck_service.lower() or service in " ".join(t.path).lower()
        ]

        if not service_logs and not service_metrics and not service_traces:
            return f"No signals found for service '{service}'"

        # 2. Temporal correlation: find overlapping time windows
        correlations.append(f"=== Signal Correlation for '{service}' (window: {window_min}min) ===\n")

        # Log-Metric correlation
        for a in service_metrics:
            correlated_logs = []
            for tpl in service_logs:
                if tpl.first_seen and a.timestamp:
                    delta = abs((a.timestamp - tpl.first_seen).total_seconds())
                    if delta <= window_min * 60:
                        correlated_logs.append(
                            f"  [{tpl.level}] \"{tpl.template}\" (x{tpl.count}, "
                            f"delta={delta:.0f}s from metric anomaly)"
                        )
            if correlated_logs:
                correlations.append(
                    f"METRIC-LOG CORRELATION: {a.metric_name} anomaly ({a.deviation_sigma}σ) "
                    f"at {a.timestamp.strftime('%H:%M:%S')} correlates with:"
                )
                correlations.extend(correlated_logs)

        # Metric-Trace correlation
        for t in service_traces:
            correlated_metrics = []
            for a in service_metrics:
                if t.timestamp and a.timestamp:
                    delta = abs((a.timestamp - t.timestamp).total_seconds())
                    if delta <= window_min * 60:
                        correlated_metrics.append(
                            f"  {a.metric_name}: {a.value} ({a.deviation_sigma}σ, delta={delta:.0f}s)"
                        )
            if correlated_metrics:
                path_str = " → ".join(t.path)
                correlations.append(
                    f"\nTRACE-METRIC CORRELATION: slow trace {path_str} ({t.total_duration_ms:.0f}ms) "
                    f"correlates with metric anomalies:"
                )
                correlations.extend(correlated_metrics)

        # Cross-service correlation
        all_services = set()
        for tpl in report.log_templates:
            if tpl.level in ("ERROR", "FATAL"):
                all_services.update(tpl.services)
        for a in report.metric_anomalies:
            all_services.add(a.service)

        other_services = all_services - {s for s in all_services if service in s.lower()}
        if other_services:
            correlations.append(
                f"\nCROSS-SERVICE: Other services with anomalies in same window: "
                f"{', '.join(sorted(other_services))}"
            )

        # Summary
        total_signals = len(service_logs) + len(service_metrics) + len(service_traces)
        correlations.append(
            f"\nSUMMARY: {total_signals} signals found "
            f"({len(service_logs)} log patterns, {len(service_metrics)} metric anomalies, "
            f"{len(service_traces)} slow traces)"
        )

        return "\n".join(correlations)

    def _tool_reconstruct_timeline(self, tool_input: dict, report) -> str:
        """
        Reconstruct chronological event timeline from all data sources.
        """
        filter_services = [s.lower() for s in tool_input.get("services", [])]

        events = []

        # Collect events from logs
        for tpl in report.log_templates:
            if tpl.level not in ("ERROR", "FATAL", "WARN"):
                continue
            if filter_services and not any(
                fs in ",".join(tpl.services).lower() for fs in filter_services
            ):
                continue
            if tpl.first_seen:
                svcs = ", ".join(sorted(tpl.services))
                events.append((
                    tpl.first_seen,
                    f"[LOG][{tpl.level}][{svcs}] \"{tpl.template}\" (first of {tpl.count})"
                ))
            if tpl.last_seen and tpl.last_seen != tpl.first_seen:
                events.append((
                    tpl.last_seen,
                    f"[LOG][{tpl.level}][{svcs}] \"{tpl.template}\" (last occurrence)"
                ))

        # Collect events from metrics
        for a in report.metric_anomalies:
            if filter_services and not any(fs in a.service.lower() for fs in filter_services):
                continue
            events.append((
                a.timestamp,
                f"[METRIC][{a.service}] {a.metric_name} {a.anomaly_type}: "
                f"{a.value} ({a.deviation_sigma}σ deviation)"
            ))

        # Collect events from traces
        for t in report.slow_traces:
            if filter_services and not any(
                fs in t.bottleneck_service.lower() or fs in " ".join(t.path).lower()
                for fs in filter_services
            ):
                continue
            path_str = " → ".join(t.path)
            events.append((
                t.timestamp,
                f"[TRACE] {path_str}: {t.total_duration_ms:.0f}ms "
                f"(bottleneck: {t.bottleneck_service})"
            ))

        if not events:
            return "No events found for the specified services."

        # Sort chronologically
        events.sort(key=lambda e: e[0])

        # Format timeline
        lines = ["=== Event Timeline ===\n"]
        prev_time = None
        for ts, desc in events:
            time_str = ts.strftime("%H:%M:%S")
            if prev_time:
                gap = (ts - prev_time).total_seconds()
                if gap > 60:
                    lines.append(f"  ... ({gap:.0f}s gap) ...")
            lines.append(f"{time_str} | {desc}")
            prev_time = ts

        # Add time span
        total_span = (events[-1][0] - events[0][0]).total_seconds()
        lines.append(f"\nTotal span: {total_span:.0f}s ({total_span/60:.1f} min)")
        lines.append(f"Events: {len(events)}")

        return "\n".join(lines)

    def _tool_classify_fault_type(self, tool_input: dict, report) -> str:
        """
        Classify fault type based on observed patterns.
        Based on TAMO: fault type classification tool.
        """
        service = tool_input.get("service", "").lower()

        # Score each fault type
        fault_scores = {
            "config_change": 0.0,
            "resource_exhaustion": 0.0,
            "dependency_failure": 0.0,
            "performance_degradation": 0.0,
            "deployment_issue": 0.0,
        }
        evidence = {k: [] for k in fault_scores}

        # Analyze logs
        for tpl in report.log_templates:
            if service and service not in ",".join(tpl.services).lower():
                continue
            tpl_lower = tpl.template.lower()

            if any(kw in tpl_lower for kw in ["config", "configuration", "setting", "parameter"]):
                fault_scores["config_change"] += 3.0
                evidence["config_change"].append(f"Log: \"{tpl.template}\" (x{tpl.count})")

            if any(kw in tpl_lower for kw in ["pool", "exhausted", "oom", "memory", "limit"]):
                fault_scores["resource_exhaustion"] += 3.0
                evidence["resource_exhaustion"].append(f"Log: \"{tpl.template}\" (x{tpl.count})")

            if any(kw in tpl_lower for kw in ["connection refused", "unavailable", "unreachable", "dns"]):
                fault_scores["dependency_failure"] += 3.0
                evidence["dependency_failure"].append(f"Log: \"{tpl.template}\" (x{tpl.count})")

            if any(kw in tpl_lower for kw in ["timeout", "slow", "latency", "deadline"]):
                fault_scores["performance_degradation"] += 2.0
                evidence["performance_degradation"].append(f"Log: \"{tpl.template}\" (x{tpl.count})")

            if any(kw in tpl_lower for kw in ["deploy", "version", "rollout", "upgrade", "restart"]):
                fault_scores["deployment_issue"] += 3.0
                evidence["deployment_issue"].append(f"Log: \"{tpl.template}\" (x{tpl.count})")

        # Analyze metrics
        for a in report.metric_anomalies:
            if service and service not in a.service.lower():
                continue

            if "connection_pool" in a.metric_name or "memory" in a.metric_name:
                fault_scores["resource_exhaustion"] += a.deviation_sigma
                evidence["resource_exhaustion"].append(
                    f"Metric: {a.metric_name} = {a.value} ({a.deviation_sigma}σ)"
                )

            if "latency" in a.metric_name:
                fault_scores["performance_degradation"] += a.deviation_sigma * 0.5
                evidence["performance_degradation"].append(
                    f"Metric: {a.metric_name} = {a.value} ({a.deviation_sigma}σ)"
                )

            if "error_rate" in a.metric_name:
                fault_scores["dependency_failure"] += a.deviation_sigma * 0.3
                fault_scores["config_change"] += a.deviation_sigma * 0.3

        # Analyze traces
        for t in report.slow_traces:
            if service and service not in t.bottleneck_service.lower():
                continue

            slowdown = t.total_duration_ms / max(t.normal_duration_ms, 1)
            if slowdown > 10:
                fault_scores["resource_exhaustion"] += 2.0
                fault_scores["performance_degradation"] += 1.0

            if t.attributes:
                attrs_str = str(t.attributes).lower()
                if "pool" in attrs_str or "connection" in attrs_str:
                    fault_scores["resource_exhaustion"] += 3.0

        # Rank and format
        ranked = sorted(fault_scores.items(), key=lambda x: x[1], reverse=True)

        lines = [f"=== Fault Type Classification for '{service or 'all services'}' ===\n"]
        for fault_type, score in ranked:
            if score > 0:
                bar = "█" * min(int(score), 20)
                lines.append(f"{fault_type:30s} | score={score:5.1f} | {bar}")
                for ev in evidence[fault_type][:3]:
                    lines.append(f"  → {ev}")

        if ranked[0][1] > 0:
            lines.append(f"\nMost likely fault type: {ranked[0][0].upper()}")
            lines.append(f"Confidence: {'high' if ranked[0][1] > ranked[1][1] * 2 else 'medium'}")

        return "\n".join(lines)

    def _heuristic_diagnosis(self, report) -> dict:
        """Diagnóstico fallback baseado em heurística (sem LLM)."""
        console.print("  [yellow]Using heuristic fallback (no LLM)[/yellow]")

        # Encontra o serviço mais afetado
        service_errors = {}
        for tpl in report.log_templates:
            if tpl.level in ("ERROR", "FATAL"):
                for svc in tpl.services:
                    service_errors[svc] = service_errors.get(svc, 0) + tpl.count

        primary_service = max(service_errors, key=service_errors.get) if service_errors else "unknown"

        # Extrai versão do deploy dos templates de log
        import re as _re
        deployed_version = None
        for tpl in report.log_templates:
            m = _re.search(r'image=\S+:(v[\d.]+)', tpl.template)
            if m:
                deployed_version = m.group(1)
                break

        # Busca padrões conhecidos nos templates
        evidence = []
        root_cause = f"Multiple errors detected in {primary_service}"
        known_pattern_matched = False

        serialization_signals = []
        for tpl in report.log_templates:
            tpl_lower = tpl.template.lower()
            if "connection pool" in tpl_lower or "pool exhausted" in tpl_lower:
                root_cause = f"Connection pool exhaustion in {primary_service} due to config change (max_connections reduced)"
                evidence.append(f"Pool exhaustion: {tpl.template} (x{tpl.count})")
                known_pattern_matched = True
            elif "memory" in tpl_lower or "oom" in tpl_lower or "heap" in tpl_lower:
                root_cause = f"Memory leak (OOM/heap exhaustion) with unclosed connections detected in {primary_service}"
                evidence.append(f"Memory issue: {tpl.template} (x{tpl.count})")
                known_pattern_matched = True
            elif "timed out" in tpl_lower or "timeout" in tpl_lower:
                evidence.append(f"Timeouts: {tpl.template} (x{tpl.count})")
                known_pattern_matched = True
            elif "deploy" in tpl_lower or "config" in tpl_lower:
                evidence.append(f"Config change: {tpl.template}")
            if "serial" in tpl_lower or "schema" in tpl_lower or "corrupt" in tpl_lower or "codec" in tpl_lower:
                serialization_signals.append(tpl.template)

        for a in report.metric_anomalies:
            evidence.append(f"Metric anomaly: {a.service}/{a.metric_name} = {a.value} ({a.deviation_sigma}sigma)")

        # Ordena affected_services com primary_service primeiro
        affected = list(report.affected_services)
        if primary_service in affected:
            affected.remove(primary_service)
            affected.insert(0, primary_service)

        # Confidence depende de padrões reconhecidos: "medium" se match, "low" se não.
        if known_pattern_matched:
            confidence = "medium"
        else:
            confidence = "low"
            if serialization_signals:
                root_cause = f"Data corruption or serialization/schema error detected in {primary_service}"
            else:
                root_cause = f"Unknown fault type detected in {primary_service}"

        result = {
            "root_cause": root_cause,
            "evidence": evidence[:5],
            "affected_services": affected,
            "confidence": confidence,
            "timeline": "Heuristic analysis - check logs for temporal correlation",
        }
        if deployed_version:
            result["deployed_version"] = deployed_version
        return result
