from __future__ import annotations
"""
Rastreador de risco com estado persistente para monitoramento contínuo em produção.

Mantém bônus de persistência (acumula quando degradado) com decaimento rápido
quando os sinais melhoram — permitindo que o sistema reflita corretamente tanto
a piora quanto a recuperação de um incidente.

Separado do DemoAnalyzer para uso direto pelo pipeline de produção.
"""


class RiskTracker:
    """Rastreador de risco com bônus de persistência e decaimento adaptativo.

    Em produção, o pipeline é acionado continuamente (via polling ou stream).
    Este tracker mantém estado entre chamadas para:
      - Acumular confiança de incidente quando o risco persiste (bônus +5 a cada 15 ticks)
      - Decair rapidamente quando os sinais melhoram (3x mais rápido que acumulação)
      - Suavizar oscilações de ruído via EWMA (alpha=0.35)

    Uso típico em produção:
        tracker = RiskTracker()
        for tick in monitoring_loop():
            raw_score = compute_risk_score(metric, log, latency)
            adjusted_score = tracker.update(raw_score)
            emit_event(adjusted_score)
    """

    def __init__(
        self,
        *,
        decay_rate: int = 3,
        max_ticks: int = 90,
        smoothing_alpha: float = 0.35,
    ) -> None:
        """Inicializa o tracker.

        Args:
            decay_rate: Ticks decrementados por tick de melhora (padrão: 3).
                        Decaimento 3x mais rápido que acumulação (1 tick/tick).
            max_ticks: Teto de acumulação de ticks (90 → bônus máximo de +30).
            smoothing_alpha: Peso EWMA para score atual (0.35 = suavização moderada).
        """
        self._warning_ticks: int = 0
        self._smoothed_risk: float = 0.0
        self._decay_rate = decay_rate
        self._max_ticks = max_ticks
        self._smoothing_alpha = smoothing_alpha

    def update(self, raw_score: float) -> float:
        """Atualiza o tracker com novo score bruto e retorna score ajustado.

        Args:
            raw_score: Score bruto de 0–100 produzido por compute_risk_score().

        Returns:
            Score ajustado (0–100) com bônus de persistência e suavização EWMA.
        """
        if raw_score >= 30.0:
            self._warning_ticks = min(self._max_ticks, self._warning_ticks + 1)
        else:
            self._warning_ticks = max(0, self._warning_ticks - self._decay_rate)

        bonus = min(30.0, (self._warning_ticks // 15) * 5.0)
        adjusted = min(100.0, raw_score + bonus)

        self._smoothed_risk = min(
            100.0,
            self._smoothing_alpha * adjusted + (1.0 - self._smoothing_alpha) * self._smoothed_risk,
        )

        # Dead zone: sistema saudável (sem histórico de degradação e score baixo)
        # retorna 0 para evitar ruído de fundo. Sem isso, o floor de sinais mínimos
        # acumula via EWMA e o dashboard oscila em 4-20% mesmo sem anomalia real.
        if self._warning_ticks == 0 and self._smoothed_risk < 15.0:
            self._smoothed_risk = 0.0

        return self._smoothed_risk

    def reset(self) -> None:
        """Zera o estado do tracker (ex: após incidente resolvido ou nova sessão)."""
        self._warning_ticks = 0
        self._smoothed_risk = 0.0

    @property
    def warning_ticks(self) -> int:
        """Ticks acumulados acima do limiar de degradação."""
        return self._warning_ticks

    @property
    def smoothed_risk(self) -> float:
        """Último score suavizado retornado."""
        return self._smoothed_risk
