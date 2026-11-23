# Backtest do monitor contínuo (VB4)

O monitor contínuo (`pipeline/continuous_monitor.py`, camada barata sem LLM) deixou de ser
"trabalho futuro" do Eixo D: o backtest abaixo o replaya **tick a tick** via
`CsvMetricsSource` + `ContinuousMonitor`. Arquitetura em duas camadas:
`docs/vigia-barato-arquitetura.md`.

- Script: `scripts/backtest_continuous.py` (`--write-golden` grava
  `validation/golden/continuous_backtest.json`; CSVs ficam em diretório temporário,
  nunca em `output/`).
- Teste: `pytest tests/test_continuous_backtest.py -m slow -v`.
- Famílias: cenário HPA (VB-T1, seeds 42–46, rótulo = `MAXED_OUT_AT`) + séries do D5
  (pool_exhaustion, hpa_saturation, memory_leak positivas; flat/noise negativas;
  noise_heavy só reportada). Limiar EWMA por série = 90% do teto/request.

## Números (5 seeds, base 42)

| Métrica | Valor |
|---|---|
| recall (positivos com aviso antes da falha) | 0.85 (17/20) |
| precision | 1.00 |
| falsos positivos em flat/ruído puro | 0 |
| lead time HPA (seed 42 canônica) | 59 min |
| lead time HPA (mín nas detectadas) | 43 min |
| lead time médio (todos os detectados) | 20.8 min |
| noise_heavy com aviso | 0/5 |

## Limite honesto

Nas seeds 44, 45 e 46 do cenário HPA o monitor **não avisa** (recall HPA = 2/5). A memória
oscila em torno de 0,90 do request e o limiar único `ewma_threshold` (0.90) não é cruzado de
forma sustentada. O recall global ≥ 0.8 vem das séries sintéticas, que têm tendência
forte; não é evidência de que o HPA é sempre detectado. Caminho: limiar por métrica /
calibração, fora do write-set desta tarefa. O teste `test_limite_conhecido_hpa_depende_da_seed`
documenta o limite em vez de escondê-lo.

Sondas do instrumento: monitor cego → recall 0; monitor alarmista → precision < 0.7.
Tudo simulado; nenhuma ação em infra.
