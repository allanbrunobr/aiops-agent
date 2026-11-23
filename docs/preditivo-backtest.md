# Backtest preditivo (D5)

`scripts/backtest_predictive.py` mede precisão, recall e lead-time do estágio
preditivo (`pipeline/predictive_stage.run_predictive_stage`) contra falhas
injetadas, em vez de depender de inspeção.

## Como funciona
- Séries sintéticas determinísticas (seed fixa): falhas fortes
  (`pool_exhaustion`, `hpa_saturation`, `memory_leak`) e séries sem falha
  (`flat`, `noise`, `noise_heavy`).
- Cada série é replayada em janelas de 20 amostras (1 min); cada janela é um
  relatório agregado independente. Positivo = `prediction.alert` ou
  `prediction.warning` **antes** do instante da falha injetada.
- Recall = falhas fortes detectadas / total. Precision = TP / (TP + FP), com FP
  = série sem falha que gerou qualquer alerta. Lead-time = minutos entre o 1º
  alerta e a falha.
- Golden: `validation/golden/predictive_backtest.json` (regenerar com
  `python scripts/backtest_predictive.py --write-golden`). Teste:
  `pytest tests/test_predictive_backtest.py -m slow`.

## Limitações
- **Batch, não contínuo.** O subsistema é invocado por incidente; o backtest
  replaya janelas, não existe monitor rodando em loop. Um monitor contínuo
  (avaliar cada nova amostra, deduplicar alertas, histerese) é trabalho futuro.
- Séries sintéticas: provam que os portões D1–D4 funcionam nos padrões
  injetados, não calibram contra produção.
- `noise_heavy` (σ = 5% da média) é uma sonda: com 5 seeds não alerta, mas com
  `--seeds 20` apareceu 1 falso positivo em 20 — o detector não é imune a ruído
  forte. Fora da asserção de "zero alertas" (que cobre flat/ruído puro), mas
  conta na precision.
