from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from core.credentials import EnvCredentialProvider
from core.lifecycle import LifecycleEvent

log = structlog.get_logger()

# Mapeamento de nos do pipeline para labels legíveis
_NODE_LABELS: dict[str, str] = {
    "telemetry_sanitizer": "Sanitization",
    "log_parser": "Log Parsing (Drain3)",
    "metric_filter": "Metric Analysis (3-sigma)",
    "trace_filter": "Trace Correlation (p95)",
    "context_compressor": "Context Compression",
    "rag_retriever": "RAG Search",
    "root_cause_analyzer": "Root Cause Analysis",
    "pre_execution_validator": "Safety: Pre-Execution",
    "tnr": "Safety: TNR Check",
    "version_validator": "Safety: Version Check",
    "postmortem_generator": "Postmortem Generation",
}

# Erros do Slack que NAO devem ser retentados
_NON_RETRYABLE_ERRORS = {"invalid_auth", "token_revoked", "account_inactive", "token_expired"}


class SlackNotifier:
    """Notificador Slack via Bot API com atualizacoes progressivas.

    Implementa LifecycleObserver para receber eventos do BlueprintEngine.
    Falhas no Slack sao logadas mas NUNCA afetam a execucao do pipeline.
    """

    def __init__(
        self,
        channel: str,
        creds: EnvCredentialProvider,
        *,
        token_key: str = "SLACK_BOT_TOKEN",
        max_retries: int = 3,
        initial_backoff_s: float = 1.0,
        backoff_multiplier: float = 2.0,
        fallback_dir: str = "output",
        dry_run: bool = False,
    ) -> None:
        """Inicializa cliente Slack com configuracoes completas."""
        from pathlib import Path

        self._channel = channel
        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._backoff_multiplier = backoff_multiplier
        self._fallback_dir = Path(fallback_dir)
        self._dry_run = dry_run
        self._message_ts: dict[str, str] = {}          # correlation_id -> message timestamp
        self._channel_ids: dict[str, str] = {}           # correlation_id -> resolved channel ID
        self._completed_nodes: dict[str, list[str]] = {}  # correlation_id -> lista de nos concluidos

        if dry_run:
            log.info("slack.dry_run_mode", channel=channel)
            self._client: AsyncWebClient | None = None
        else:
            token = creds.get(token_key)
            if not token:
                log.warning("slack.no_token", key=token_key)
                self._client = None
            else:
                self._client = AsyncWebClient(token=token)

    async def on_event(self, event: LifecycleEvent) -> None:
        """Recebe eventos do pipeline e atualiza Slack. Nunca lanca excecoes."""
        if self._client is None and not self._dry_run:
            return

        try:
            if event.event_type == "pipeline.started":
                await self._post_initial_message(event)
            elif event.event_type == "node.completed":
                await self._update_progress(event)
            elif event.event_type == "pipeline.completed":
                await self._post_final_message(event)
            elif event.event_type == "pipeline.failed":
                await self._post_failure_message(event)
            elif event.event_type in ("prediction.warning", "prediction.alert"):
                await self._post_prediction_message(event)
        except SlackApiError as exc:
            log.warning(
                "slack.api_error",
                event_type=event.event_type,
                correlation_id=event.correlation_id,
                error=str(exc),
            )
        except Exception:
            log.warning(
                "slack.unexpected_error",
                event_type=event.event_type,
                correlation_id=event.correlation_id,
                exc_info=True,
            )

    async def _send_with_retry(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        context: str,
        correlation_id: str,
    ) -> Any:
        """Executa operacao Slack com retry exponencial.

        Retorna resultado em caso de sucesso.
        Lanca excecao se todos os retries falharem.
        """
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                result = await operation()
                if attempt > 1:
                    log.info(
                        "slack.retry_succeeded",
                        context=context,
                        correlation_id=correlation_id,
                        attempt=attempt,
                    )
                return result
            except SlackApiError as exc:
                error_code = exc.response.get("error", "unknown")
                last_error = exc

                # Erros de autenticacao nao sao retentaveis
                if error_code in _NON_RETRYABLE_ERRORS:
                    log.error(
                        "slack.non_retryable_error",
                        context=context,
                        correlation_id=correlation_id,
                        error=error_code,
                    )
                    raise

                backoff = self._initial_backoff_s * (self._backoff_multiplier ** (attempt - 1))
                log.warning(
                    "slack.retry_attempt",
                    context=context,
                    correlation_id=correlation_id,
                    attempt=attempt,
                    max_retries=self._max_retries,
                    backoff_s=backoff,
                    error=error_code,
                )

                if attempt < self._max_retries:
                    await asyncio.sleep(backoff)

            except Exception as exc:
                last_error = exc
                backoff = self._initial_backoff_s * (self._backoff_multiplier ** (attempt - 1))
                log.warning(
                    "slack.retry_attempt",
                    context=context,
                    correlation_id=correlation_id,
                    attempt=attempt,
                    max_retries=self._max_retries,
                    backoff_s=backoff,
                    error=str(exc),
                )

                if attempt < self._max_retries:
                    await asyncio.sleep(backoff)

        # Todos os retries falharam
        raise last_error  # type: ignore[misc]

    async def _post_initial_message(self, event: LifecycleEvent) -> None:
        """Posta ou loga mensagem inicial com resumo do incidente e status 'Diagnosing...'."""
        cid = event.correlation_id
        self._completed_nodes[cid] = []
        blocks = self._build_initial_blocks(event)

        if self._dry_run:
            log.info(
                "slack.dry_run",
                action="post_initial",
                correlation_id=cid,
                channel=self._channel,
                blocks=blocks,
            )
            self._message_ts[cid] = "dry-run"
            return

        async def _send() -> Any:
            return await self._client.chat_postMessage(  # type: ignore[union-attr]
                channel=self._channel,
                blocks=blocks,
                text=f"Incident {cid}: Diagnosing...",
            )

        response = await self._send_with_retry(_send, context="initial_message", correlation_id=cid)
        self._message_ts[cid] = response["ts"]
        self._channel_ids[cid] = response.get("channel", self._channel)
        log.info("slack.message_posted", correlation_id=cid, ts=response["ts"], channel_id=response["channel"])

    async def _update_progress(self, event: LifecycleEvent) -> None:
        """Atualiza mensagem com progresso do pipeline via chat.update()."""
        cid = event.correlation_id
        ts = self._message_ts.get(cid)
        if not ts:
            return

        self._completed_nodes.setdefault(cid, []).append(event.node_name or "unknown")
        blocks = self._build_progress_blocks(cid, event)

        if self._dry_run:
            log.info(
                "slack.dry_run",
                action="update_progress",
                correlation_id=cid,
                node=event.node_name,
                blocks=blocks,
            )
            return

        # chat.update requer channel ID (nao nome) — usa o ID resolvido no postMessage
        channel_id = self._channel_ids.get(cid, self._channel)

        async def _send() -> Any:
            return await self._client.chat_update(  # type: ignore[union-attr]
                channel=channel_id,
                ts=ts,
                blocks=blocks,
                text=f"Incident {cid}: Processing stage {event.node_name}...",
            )

        await self._send_with_retry(_send, context="update_progress", correlation_id=cid)

    async def _post_final_message(self, event: LifecycleEvent) -> None:
        """Posta mensagem final com retry. Fallback local se todos falharem."""
        cid = event.correlation_id
        ts = self._message_ts.get(cid)
        blocks = self._build_final_blocks(event)
        fallback_text = f"Incident {cid}: Diagnosis complete"

        if self._dry_run:
            log.info(
                "slack.dry_run",
                action="post_final",
                correlation_id=cid,
                channel=self._channel,
                blocks=blocks,
            )
            self._message_ts.pop(cid, None)
            self._completed_nodes.pop(cid, None)
            return

        # chat.update requer channel ID (nao nome)
        channel_id = self._channel_ids.get(cid, self._channel)

        async def _send() -> None:
            if ts:
                await self._client.chat_update(  # type: ignore[union-attr]
                    channel=channel_id,
                    ts=ts,
                    blocks=blocks,
                    text=fallback_text,
                )
            else:
                await self._client.chat_postMessage(  # type: ignore[union-attr]
                    channel=self._channel,
                    blocks=blocks,
                    text=fallback_text,
                )

        try:
            await self._send_with_retry(_send, context="final_message", correlation_id=cid)
            log.info("slack.final_message", correlation_id=cid)
        except Exception:
            # Todos os retries falharam — fallback local
            log.error(
                "slack.delivery_exhausted",
                correlation_id=cid,
                max_retries=self._max_retries,
            )
            from server.notifications.fallback import LocalFallbackWriter
            fallback = LocalFallbackWriter(str(self._fallback_dir))
            fallback.write(cid, event.data)

        # Limpa estado independente de sucesso/falha
        self._message_ts.pop(cid, None)
        self._channel_ids.pop(cid, None)
        self._completed_nodes.pop(cid, None)

    async def _post_failure_message(self, event: LifecycleEvent) -> None:
        """Posta mensagem de falha do pipeline."""
        cid = event.correlation_id
        ts = self._message_ts.get(cid)
        error = event.data.get("error", "Unknown error")

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"Pipeline Failed: {cid}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f":x: *Error:* {error}"}},
        ]

        if self._dry_run:
            log.info("slack.dry_run", action="post_failure", correlation_id=cid, blocks=blocks)
            self._message_ts.pop(cid, None)
            self._completed_nodes.pop(cid, None)
            return

        # chat.update requer channel ID (nao nome)
        channel_id = self._channel_ids.get(cid, self._channel)

        async def _send() -> None:
            if ts:
                await self._client.chat_update(  # type: ignore[union-attr]
                    channel=channel_id,
                    ts=ts,
                    blocks=blocks,
                    text=f"Incident {cid}: Pipeline failed",
                )
            else:
                await self._client.chat_postMessage(  # type: ignore[union-attr]
                    channel=self._channel,
                    blocks=blocks,
                    text=f"Incident {cid}: Pipeline failed",
                )

        await self._send_with_retry(_send, context="failure_message", correlation_id=cid)
        self._message_ts.pop(cid, None)
        self._channel_ids.pop(cid, None)
        self._completed_nodes.pop(cid, None)

    # --- Block Kit builders ---

    def _build_initial_blocks(self, event: LifecycleEvent) -> list[dict]:
        """Monta blocos iniciais com resumo do incidente."""
        cid = event.correlation_id
        title = event.data.get("title", "Incident detected")
        severity = event.data.get("severity", "unknown")
        service = event.data.get("service", "unknown")

        return [
            {"type": "header", "text": {"type": "plain_text", "text": f"Incident: {cid}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{title}*\n"
                f"Severity: `{severity}` | Service: `{service}`\n"
                f"Status: :hourglass_flowing_sand: *Diagnosing...*"
            )}},
            {"type": "divider"},
        ]

    def _build_progress_blocks(self, correlation_id: str, event: LifecycleEvent) -> list[dict]:
        """Monta blocos com checklist de progresso dos estagios."""
        completed = self._completed_nodes.get(correlation_id, [])
        lines = []
        for node_id, label in _NODE_LABELS.items():
            if node_id in completed:
                duration = ""
                if event.node_name == node_id:
                    duration = f" ({event.data.get('duration_ms', '?')}ms)"
                lines.append(f":white_check_mark: {label}{duration}")
            else:
                lines.append(f":black_square_button: {label}")

        return [
            {"type": "header", "text": {"type": "plain_text", "text": f"Incident: {correlation_id}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ]

    def _build_prediction_blocks(self, data: dict) -> list[dict]:
        """Monta blocos Block Kit para notificações de predição pré-incidente.

        Aplica cor vermelha para 'prediction.alert' (risk >= 60) e amarela
        para 'prediction.warning' (30 <= risk < 60).

        Args:
            data: Dicionário com campos do PredictionReport:
                  event_level, risk_score, eta_minutes, signals, matched_history.

        Returns:
            Lista de blocos Block Kit prontos para envio via Slack attachments.
        """
        event_level = data.get("event_level", "prediction.warning")
        risk_score = data.get("risk_score", 0.0)
        eta = data.get("eta_minutes")
        signals = data.get("signals", [])
        matched_history = data.get("matched_history", [])

        # Header diferenciado por nível
        if event_level == "prediction.critical":
            header_text = f"🚨 Incident Imminent — ETA ~{eta:.1f} min" if eta is not None else "🚨 Incident Imminent"
        elif event_level == "prediction.alert":
            header_text = f"⚠️ Degradation Alert — Incident likely in ~{eta:.1f} min" if eta is not None else "⚠️ Degradation Alert"
        else:
            header_text = f"📈 Degradation Warning — Risk {risk_score:.0f}%"

        # ETA formatado
        eta_text = f"{eta:.1f} min" if eta is not None else "Unknown"

        # Nomes dos sinais (primeiros 5)
        if signals:
            signal_names = [
                s.__class__.__name__ if not isinstance(s, str) else s
                for s in signals[:5]
            ]
            signals_text = ", ".join(signal_names)
        else:
            signals_text = "No signals"

        # Histórico — apenas primeira correspondência
        if matched_history:
            first = matched_history[0]
            history_text = (
                f"incident_id={first.get('incident_id', '?')} "
                f"(similarity={first.get('similarity_score', 0):.2f})"
            )
        else:
            history_text = "No historical match"

        return [
            {"type": "header", "text": {"type": "plain_text", "text": header_text}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Risk:* {risk_score:.0f}%"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*ETA:* {eta_text}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Signals:* {signals_text}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Historical Match:* {history_text}"}},
        ]

    async def _post_prediction_message(self, event: LifecycleEvent) -> None:
        """Envia mensagem Slack para eventos prediction.warning, alert e critical."""
        blocks = self._build_prediction_blocks(event.data)
        event_level = event.data.get("event_level", event.event_type)
        if event_level == "prediction.critical":
            color = "#8b0000"  # vermelho escuro — iminente
        elif event.event_type == "prediction.alert":
            color = "#f2495c"  # vermelho
        else:
            color = "#ff780a"  # laranja
        scenario = event.data.get("scenario", event.correlation_id)

        async def _send() -> None:
            await self._client.chat_postMessage(
                channel=self._channel,
                text=f"{event.event_type}: {scenario}",
                attachments=[{"color": color, "blocks": blocks}],
            )

        await self._send_with_retry(_send, context="_post_prediction_message",
                                    correlation_id=event.correlation_id)
        log.info("slack.prediction_posted", event_type=event.event_type,
                 correlation_id=event.correlation_id)

    def _build_final_blocks(self, event: LifecycleEvent) -> list[dict]:
        """Monta blocos finais com diagnostico completo para leitura mobile."""
        cid = event.correlation_id
        data = event.data

        root_cause = data.get("root_cause", "N/A")
        confidence = data.get("confidence", 0.0)
        evidence = data.get("evidence", [])
        proposed_fix = data.get("proposed_fix") or "N/A"
        risk_level = data.get("risk_level", "unknown")
        blast_radius = data.get("blast_radius", "unknown")
        duration_ms = data.get("total_duration_ms", "?")

        # Formata lista de evidencias
        evidence_text = "\n".join(f"  - {e}" for e in evidence) if evidence else "  None"

        # Cor do indicador de confianca
        confidence_pct = int(confidence * 100)
        if confidence >= 0.8:
            conf_emoji = ":large_green_circle:"
        elif confidence >= 0.5:
            conf_emoji = ":large_yellow_circle:"
        else:
            conf_emoji = ":red_circle:"

        return [
            {"type": "header", "text": {"type": "plain_text", "text": f"Diagnosis Complete: {cid}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*Root Cause:*\n{root_cause}\n\n"
                f"*Evidence:*\n{evidence_text}"
            )}},
            {"type": "divider"},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Confidence:*\n{conf_emoji} {confidence_pct}%"},
                {"type": "mrkdwn", "text": f"*Risk Level:*\n`{risk_level}`"},
                {"type": "mrkdwn", "text": f"*Blast Radius:*\n`{blast_radius}`"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{duration_ms}ms"},
            ]},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*Proposed Fix:*\n```{proposed_fix}```"
            )}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"AIOps Agent | {cid}"},
            ]},
        ]
