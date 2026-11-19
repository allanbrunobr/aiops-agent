# Testes E2E (Playwright) — Demo Dashboard

Suíte end-to-end que exercita a UI web real do módulo de demonstração
(`interfaces/demo/server.py`) num browser headless (chromium), incluindo o
fluxo ao vivo por WebSocket.

## O que cobre

| Teste | Valida |
|---|---|
| `test_dashboard_loads` | `/demo/` renderiza: título, badge `IDLE`, 4 cenários, controles visíveis, sem erros de console/página |
| `test_start_scenario_streams_live_frames` | Start → botão Stop aparece, badge de fase sai de `IDLE` via WebSocket, log stream popula, `/demo/status` `is_running=true` |
| `test_stop_scenario_returns_control` | Stop → botão Start volta, `is_running=false` |
| `test_story_page_loads` | `/demo/story` renderiza a página de narrativa |
| `test_scenarios_api_lists_four` | `GET /demo/scenarios` lista os 4 cenários |
| `test_postmortem_not_ready_before_resolved` | `GET /demo/postmortem` → 204 antes de RESOLVED |
| `test_mutation_*` (4 testes) | `require_demo_token`: mutação sem/token-errado → 401, token correto → 200, leituras abertas |

## Pré-requisitos

```bash
.venv/bin/pip install playwright pytest-playwright
.venv/bin/python -m playwright install chromium
```

- **Browser:** chromium headless (baixado pelo Playwright, ~107 MiB).
- **Rede:** `index.html` carrega uPlot / ECharts / GSAP de `cdn.jsdelivr.net`;
  os testes de dashboard esperam essas libs (`wait_for_chart_libs`). Sem acesso
  à CDN, `initCharts` falha — rode num host com internet (M1) ou vendorize os
  assets como follow-up para tornar a suíte hermética.
- **Host:** rodar no **M1** (`ssh m1-docker`, repo `/tmp/iago-ci-m5/aiops-agent`).
  Nada pesado no M5. O servidor de demo sobe como subprocesso em porta efêmera,
  modo dev aberto (sem `AIOPS_DEMO_TOKEN`) para os testes de browser; um segundo
  servidor com token configurado cobre os testes negativos de auth.

## Como rodar

```bash
# no M1, dentro do repo
.venv/bin/python -m pytest tests/e2e -m e2e --timeout=120
```

Os testes são marcados `e2e` e **excluídos** dos jobs CI `unit` (`-m unit`) e
`integration or snapshot`. Não há job CI de e2e por padrão (dependência de
browser + CDN); adicionar um job dedicado é um follow-up opcional.

## Notas de design

- O servidor real é iniciado por fixture (`demo_server` / `demo_server_with_token`)
  em porta livre, com `ANTHROPIC_API_KEY` removido (determinístico, sem LLM).
- As asserções focam no fluxo de dados/controle dirigido pelo WebSocket (badge de
  fase, log stream, timeline, status), não em pixels de gráfico — robusto e rápido.
- `pytest.importorskip("playwright")` faz a suíte ser pulada graciosamente onde o
  Playwright não está instalado.
