from __future__ import annotations
"""
Player de cenário: avança o ponteiro de tempo simulado e fatia o corpus.

ScenarioPlayer traduz tempo real (wall-clock) em tempo simulado usando
o speed_factor, e retorna apenas os eventos do corpus até sim_now.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from demo.scenarios import CorpusItem


class ScenarioPlayer:
    """Controla o avanço do tempo simulado sobre o corpus de eventos."""

    def __init__(
        self,
        corpus: list[CorpusItem],
        speed_factor: float = 4.0,
    ) -> None:
        # Corpus ordenado por timestamp simulado
        self._corpus = sorted(corpus, key=lambda x: x[0])
        self.speed_factor = speed_factor

        # Tempo de início simulado é o primeiro timestamp do corpus
        self._sim_start: datetime = self._corpus[0][0] if self._corpus else datetime.utcnow()
        # sim_now aponta para o momento atual na simulação
        self._sim_now: datetime = self._sim_start
        # Índice do próximo evento ainda não entregue
        self._cursor: int = 0

    @property
    def sim_now(self) -> datetime:
        """Retorna o tempo simulado atual."""
        return self._sim_now

    @property
    def sim_start(self) -> datetime:
        """Retorna o tempo simulado de início do corpus."""
        return self._sim_start

    @property
    def elapsed_sim_seconds(self) -> float:
        """Retorna segundos simulados decorridos desde o início."""
        return (self._sim_now - self._sim_start).total_seconds()

    @property
    def is_finished(self) -> bool:
        """Retorna True se todos os eventos do corpus foram entregues."""
        return self._cursor >= len(self._corpus)

    def advance(self, elapsed_wall_seconds: float) -> list[CorpusItem]:
        """Avança o tempo simulado e retorna novos eventos.

        Parâmetros
        ----------
        elapsed_wall_seconds : float
            Segundos reais decorridos desde o último avanço.

        Retorna
        -------
        list[CorpusItem]
            Eventos do corpus com timestamp <= sim_now (ainda não entregues).
        """
        # Avança o tempo simulado proporcional ao speed_factor
        sim_delta = timedelta(seconds=elapsed_wall_seconds * self.speed_factor)
        self._sim_now += sim_delta

        # Coleta eventos até sim_now (mantém cursor para eficiência)
        new_items: list[CorpusItem] = []
        while self._cursor < len(self._corpus):
            ts, item = self._corpus[self._cursor]
            if ts <= self._sim_now:
                new_items.append((ts, item))
                self._cursor += 1
            else:
                break

        return new_items

    def update_speed(self, new_speed_factor: float) -> None:
        """Atualiza speed_factor sem reiniciar o cenário.

        O avanço do tempo simulado continua a partir de sim_now atual.
        """
        self.speed_factor = float(new_speed_factor)

    def reset(self) -> None:
        """Reinicia o player para o início do corpus sem reconstruí-lo."""
        self._sim_now = self._sim_start
        self._cursor = 0
