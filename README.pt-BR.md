# AIOps Agent

[English](README.md) · **Português**

[![CI](https://github.com/allanbrunobr/aiops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/allanbrunobr/aiops-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Rust](https://img.shields.io/badge/rust-PyO3%20(opcional)-orange)
![Status](https://img.shields.io/badge/status-proof--of--concept-yellow)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Plataforma de **resposta autônoma a incidentes** que combina LLM, RAG e uma máquina de estados multiagente para diagnosticar e propor mitigações a partir de dados de observabilidade (logs, métricas e traces).

O pipeline cobre o ciclo completo de um incidente: sanitização da telemetria, pré-processamento, compressão de contexto, busca semântica em incidentes históricos, diagnóstico ReAct, proposta de mitigação, verificação de segurança e geração de postmortem.

A implementação é fundamentada em sete artigos acadêmicos de AIOps (2024–2025). Consulte [Referências acadêmicas](#referências-acadêmicas).

![Como o AIOps Agent funciona: o agente prevê o incidente, avisa no canal, roda o diagnóstico, propõe a correção e aguarda aprovação humana](docs/screenshots/how-it-works.gif)

<sub>Visão geral animada, ilustrativa, montada a partir de saídas reais do projeto. Abaixo, o dashboard de demonstração real.</sub>

![Dashboard de demonstração: incidente de esgotamento do pool de conexões, da operação normal até o alerta, em velocidade acelerada](docs/screenshots/demo.gif)

<sub>Dashboard de demonstração (`python interfaces/demo/server.py`) em velocidade acelerada: score de risco, previsão de deriva da latência e logs enquanto o incidente evolui.</sub>

---

## Sumário

- [Principais recursos](#principais-recursos)
- [Arquitetura](#arquitetura)
- [Início rápido](#início-rápido)
- [Uso da CLI](#uso-da-cli)
- [Dashboard de demonstração](#dashboard-de-demonstração)
- [Notificações no Slack](#notificações-no-slack)
- [Configuração](#configuração)
- [Integrações](#integrações)
- [Extensão Rust (opcional)](#extensão-rust-opcional)
- [Testes e CI](#testes-e-ci)
- [Ambiente local com Docker](#ambiente-local-com-docker)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Referências acadêmicas](#referências-acadêmicas)
- [Limitações conhecidas](#limitações-conhecidas)
- [Documentação adicional](#documentação-adicional)
- [Licença](#licença)

---

## Principais recursos

- **Orquestração por máquina de estados** (`IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED`), com rollback.
- **Diagnóstico ReAct com Claude**: o agente consulta logs, métricas, traces e a base de conhecimento de forma iterativa.
- **Modo heurístico** (`--no-llm`): executa o pipeline completo sem chamadas ao LLM e sem custo.
- **Pré-processamento de telemetria**: Drain3 para logs, detecção 3-sigma para métricas e filtragem p95 para traces.
- **Compressão de contexto** por prioridade, dentro de um orçamento de tokens.
- **RAG** com FAISS e `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dimensões) sobre incidentes históricos.
- **Camadas de segurança**: sanitização contra payloads adversariais, cálculo de *blast radius*, checagem de versão/CVE e *Transactional No-Regression* (TNR).
- **Detecção preditiva**: score de risco e ETA de degradação antes do incidente.
- **Interfaces**: CLI, dashboard web em tempo real (WebSocket), servidor MCP (stdio/SSE), API A2A e normalizadores de webhook (Grafana, PagerDuty, Databricks).
- **Notificações** progressivas no Slack (Block Kit) e criação opcional de issues no Jira.
- **Aceleração opcional em Rust** (PyO3) para os estágios críticos, com fallback automático para Python.

---

## Arquitetura

### Fluxo do pipeline

```
 Alerta / dados de observabilidade (simulados ou Databricks)
        │
        ▼
 TelemetrySanitizer ─── bloqueia payloads adversariais (reward hacking)
        │
        ▼
 Aggregator ─────────── Drain3 (logs) · 3-sigma (métricas) · p95 (traces)
        │
        ▼
 ContextCompressor ──── compressão por prioridade dentro do orçamento de tokens
        │
        ▼
 RAG Retriever ──────── FAISS + sentence-transformers (384d)
        │
        ▼
 Orchestrator (máquina de estados)
   IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED
        ├─ Diagnoser ──────── loop ReAct com Claude (logs, métricas, traces, busca)
        ├─ PreValidator ───── blast radius e score de risco
        ├─ VersionValidator ─ checagem de versão e CVE
        └─ TNR ────────────── rollback se a saúde do serviço piorar
        │
        ▼
 Postmortem (Markdown/JSON) · Slack · Jira
```

O projeto oferece dois motores de execução:

| Motor | Ativação | Descrição |
|-------|----------|-----------|
| V0 (sequencial) | padrão | Pipeline sequencial em `main.py` |
| V1 (Blueprint Engine) | `--pipeline-v1` ou `--data-source databricks` | Orquestração como grafo tipado (`blueprint/`) |

### Camadas

O código segue uma arquitetura em camadas (portas e adaptadores). As decisões estão registradas em [`docs/adr/`](docs/adr/).

| Camada | Diretório | Responsabilidade |
|--------|-----------|------------------|
| Domínio | `domain/` | Entidades imutáveis (`IncidentState`, `NormalizedAlert`, `MitigationAction`) sem dependências externas |
| Aplicação | `application/ports/` | Portas (`LLMClient`, `IncidentStore`, `CredentialProvider`, `Reporter`) |
| Adaptadores | `adapters/` | Implementações das portas (Anthropic, armazenamento em memória, credenciais via env, reporters) |
| Interfaces | `interfaces/` | Pontos de entrada: HTTP, MCP e demo |
| Composição | `app/builders.py` | Raiz de composição: monta e conecta as dependências |

---

## Início rápido

### Pré-requisitos

- Python 3.12 ou superior
- (Opcional) `ANTHROPIC_API_KEY` para o modo LLM
- (Opcional) Toolchain Rust estável para compilar a extensão nativa

### Instalação

```bash
git clone https://github.com/allanbrunobr/aiops-agent.git
cd aiops-agent

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

O `requirements.txt` inclui PyTorch, FAISS e `sentence-transformers`. Por isso, a primeira instalação baixa algumas centenas de MB.

### Configuração mínima

```bash
cp .env.example .env.local
# Edite .env.local e defina ANTHROPIC_API_KEY (necessária apenas no modo LLM)
```

### Primeira execução

```bash
# Modo heurístico: sem chamadas ao LLM e sem custo
python main.py --no-llm

# Pipeline completo com Claude
python main.py
```

O cenário padrão (`payment-pool-exhaustion`) simula o esgotamento do pool de conexões do `payment-svc` após um deploy defeituoso. O resultado é exibido no terminal com `rich`, e o postmortem é gravado em `output/`.

- Na primeira execução, o modelo de embeddings (`all-MiniLM-L6-v2`) é baixado do Hugging Face. Use `--no-rag` para pular essa etapa ou para rodar sem internet.
- No cenário padrão, a CLI termina com **código de saída `3`**. Isso é esperado: a mitigação proposta tem risco médio e fica aguardando aprovação humana. Consulte [Códigos de saída](#uso-da-cli).

---

## Uso da CLI

```bash
python main.py [opções]
```

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--no-llm` | Modo heurístico, sem chamadas ao Claude | desativado |
| `--no-rag` | Não indexa a base RAG (inicialização mais rápida) | desativado |
| `--rebuild-rag` | Reconstrói o índice RAG e ignora o cache | desativado |
| `--compact` | Saída compacta no terminal | desativado |
| `--scenario NAME` | `payment-pool-exhaustion`, `memory-leak` ou `unknown-fault` | `payment-pool-exhaustion` |
| `--data-source SOURCE` | `simulated` ou `databricks` | `simulated` |
| `--pipeline-v1` | Usa o Blueprint Engine (V1) | desativado |
| `--config FILE` | Arquivo YAML com limiares de análise | `config/defaults.yaml` |
| `--output-dir DIR` | Diretório dos postmortems | `output/` |
| `--output-format FORMAT` | `markdown`, `json` ou `both` | `markdown` |
| `--tracker TRACKER` | Cria issue após o diagnóstico (`jira` ou `none`) | sem tracker |

> `--data-source databricks` não pode ser combinado com `--scenario`.

**Códigos de saída**

| Código | Significado |
|--------|-------------|
| `0` | Incidente resolvido e verificado pelo TNR |
| `1` | Erro irrecuperável em algum estágio |
| `2` | Regressão detectada pelo TNR (saúde pós-ação pior que a pré-ação) |
| `3` | Ação bloqueada pela camada de segurança (requer aprovação humana) |

---

## Dashboard de demonstração

Dashboard web com simulação de incidentes em tempo real. Backend (FastAPI + WebSocket) e frontend estático são servidos pelo mesmo processo.

```bash
source .venv/bin/activate
python interfaces/demo/server.py            # porta padrão: 47891
python interfaces/demo/server.py --port 8080
```

Acesse em **http://localhost:47891/demo/**.

> Use o interpretador do virtualenv (`.venv/bin/python`). Se o Python do sistema for usado, dependências como `structlog` podem faltar. Nesse caso, o log exibe `SlackNotifier init falhou` e o pipeline da demo pode ficar incompleto.

### Cenários

| Cenário | Duração simulada | Comportamento |
|---------|------------------|---------------|
| Payment Pool Exhaustion | 32 min | `connection_pool_active` sobe de 5% a 86% em curva logística |
| Pool Exhaustion + Recovery | 40 min | Esgotamento do pool; no minuto 25 o time de Ops aumenta `max_connections` e o sistema se recupera |
| JVM Memory Leak | 60 min | `memory_used_mb` cresce linearmente de 2048 MB a 7987 MB |
| Unknown Fault Mode | 40 min | Templates de log inéditos surgem no minuto 5; a latência dispara no minuto 10 |

Cada tick de logs, métricas e traces é transmitido via WebSocket. Quando o score de risco ultrapassa o limiar, o agente executa o diagnóstico e propõe a correção.

### Modo narrativo (`/demo/story`)

Página voltada a apresentações. Ela consome o mesmo WebSocket e sobrepõe uma narrativa em português a cada fase (`PLAYING → DEGRADING → WARNING → ALERT → INCIDENT → RESOLVED`), com mensagem Slack simulada, impacto financeiro projetado e linha do tempo animada.

```
http://localhost:47891/demo/story?scenario=payment-pool-exhaustion
http://localhost:47891/demo/story?scenario=payment-pool-recovery
```

Apenas esses dois cenários têm narrativa. Os demais exibem somente os painéis técnicos. O conteúdo narrativo está disponível em `GET /demo/api/narrative/{scenario}`.

### Janela nativa (macOS, opcional)

```bash
.venv/bin/pip install pywebview
.venv/bin/python interfaces/demo/desktop_app.py
```

O launcher inicia o servidor em um subprocesso, aguarda `/demo/` responder e abre uma janela WebKit. O servidor é encerrado quando a janela é fechada.

### Recursos opcionais da demo

- **Diagnóstico via LLM**: defina `ANTHROPIC_API_KEY` em `.env.local`. Sem a chave, a demo usa heurísticas.
- **Notificações Slack**: defina `SLACK_BOT_TOKEN` e `SLACK_CHANNEL`. A demo publica os eventos `prediction.warning`, `prediction.critical`, `diagnosis.completed` e `incident.resolved`.

---

## Notificações no Slack

O `SlackNotifier` publica o incidente no canal assim que o pipeline começa, acompanha o progresso de cada etapa e, ao final, publica o diagnóstico. O diagnóstico traz causa raiz, evidências, confiança, nível de risco, *blast radius* e a correção proposta. As capturas abaixo vêm de um workspace de teste.

<img src="docs/screenshots/slack-connection-pool.png" alt="Mensagens do AIOps Agent Bot no canal #aiops-alerts: abertura do incidente e diagnóstico completo de esgotamento do pool de conexões" width="760">

<details>
<summary>Outros diagnósticos (certificado TLS expirado, vazamento de memória, RBAC)</summary>

<br>

<img src="docs/screenshots/slack-circuit-breaker.png" alt="Diagnóstico: certificado mTLS expirado abre o circuit breaker do payment-gateway" width="760">

<img src="docs/screenshots/slack-oom-tls.png" alt="Diagnósticos: vazamento de memória com OOMKilled e certificado TLS expirado" width="760">

<img src="docs/screenshots/slack-rbac-403.png" alt="Diagnóstico: mapeamento RBAC incorreto causa HTTP 403 em operações de escrita" width="760">

</details>

Para configurar um Slack App real, consulte [`infra/SLACK-SETUP.md`](infra/SLACK-SETUP.md). Para testar sem workspace, use o mock descrito em [Ambiente local com Docker](#ambiente-local-com-docker).

---

## Configuração

### Variáveis de ambiente

A CLI e a demo carregam `.env.local` automaticamente. A CLI também lê `.env`. Há dois modelos: [`.env.example`](.env.example), com as variáveis da CLI (Anthropic e Databricks), e [`env.example`](env.example), com as do ambiente local em `infra/` (webhook do Grafana e Slack).

| Variável | Uso | Obrigatória |
|----------|-----|-------------|
| `ANTHROPIC_API_KEY` | Diagnóstico via Claude | Apenas no modo LLM |
| `AIOPS_LLM_MODEL` | Modelo Claude (padrão: `claude-sonnet-4-20250514`) | Não |
| `SLACK_BOT_TOKEN` / `SLACK_CHANNEL` | Notificações Slack | Não |
| `SLACK_API_BASE_URL` | URL alternativa da API do Slack (por exemplo, o mock de `infra/`) | Não |
| `GRAFANA_WEBHOOK_TOKEN` | Autenticação do webhook Grafana | Não |
| `DATABRICKS_HOST` / `DATABRICKS_TOKEN` / `DATABRICKS_WAREHOUSE_ID` | `--data-source databricks` | Apenas com Databricks |
| `JIRA_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_PROJECT_KEY` | `--tracker jira` | Apenas com Jira |

> Nunca versione arquivos `.env*` com credenciais reais. O `.gitignore` já exclui esses arquivos.

### Arquivos de configuração

- [`config/defaults.yaml`](config/defaults.yaml): limiares de análise, Blueprint Engine, webhook, Slack e MCP. Valores podem ser sobrescritos por variáveis com prefixo `AIOPS__` (por exemplo, `AIOPS__MCP__OAUTH__JWT_SECRET`).
- [`config.yaml`](config.yaml): mapa de criticidade por serviço, usado no cálculo de *blast radius*.

---

## Integrações

| Integração | Local | Observações |
|------------|-------|-------------|
| Slack | `interfaces/http/notifications/slack.py` | Block Kit progressivo, retry exponencial e fallback local em `output/`. Guia: [`infra/SLACK-SETUP.md`](infra/SLACK-SETUP.md) |
| Jira | `tools/jira_tracker.py` | Ativada com `--tracker jira` |
| Databricks | `tools/databricks_connector.py` | Fonte de dados real via `--data-source databricks` |
| MCP | `interfaces/mcp/` | Ferramentas `diagnose_incident`, `validate_action` e `search_incidents`. Transportes stdio e SSE, com rate limiting e OAuth 2.1 |
| A2A | `interfaces/http/routes/a2a.py` | API agent-to-agent montada pela aplicação HTTP |
| Webhooks | `interfaces/http/webhook/` | Normalizadores para Grafana, PagerDuty e Databricks, com deduplicação |

Servidor MCP via stdio (Claude Desktop e uso local):

```bash
python -m interfaces.mcp.server
```

---

## Extensão Rust (opcional)

Os estágios críticos de desempenho (`log_parser`, `metric_filter`, `trace_filter`, `context_compressor`, `telemetry_sanitizer`, `cusum_ewma`, `trace_predictor`) têm implementação em Rust exposta via PyO3 como o módulo `_aiops_rust`. Sem a extensão, o pipeline usa automaticamente as implementações em Python.

```bash
scripts/build_rust.sh            # build de desenvolvimento no .venv
scripts/build_rust.sh --release  # build otimizado (necessário para benchmarks)
cargo test --manifest-path rust/Cargo.toml
```

---

## Testes e CI

```bash
python -m pytest tests/                                # suíte completa
python -m pytest tests/ -m unit                        # unitários (sem ML, sem API)
python -m pytest tests/ -m "integration or snapshot"   # integração e snapshot
```

Marcadores disponíveis: `unit`, `integration`, `snapshot` e `slow`, definidos em `pyproject.toml`.

Os testes em `tests/rust/` exigem a [extensão Rust](#extensão-rust-opcional) compilada. Sem ela, rode `python -m pytest tests/ --ignore=tests/rust`. O benchmark de desempenho só passa com o build `--release`.

O workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) executa três jobs em paralelo, sem chaves de API nem download de modelos:

1. **Unit Tests**: dependências mínimas.
2. **Integration & Snapshot Tests**: dependências completas.
3. **Rust Build & PyO3 Extension**: `cargo test`, build da extensão e suíte completa.

Para reproduzir o CI localmente:

```bash
scripts/ci-local.sh              # todos os jobs
scripts/ci-local.sh unit         # apenas um job: unit | integration | rust
```

---

## Ambiente local com Docker

O diretório [`infra/`](infra/) sobe um ambiente de ponta a ponta para testar o recebimento de alertas: um exportador simula as métricas do `payment-svc`, o Prometheus as coleta, o Grafana avalia as regras de alerta e envia um webhook ao AIOps Agent. Um mock da API do Slack está incluído.

```bash
docker compose -f infra/docker-compose.yaml up -d --build   # Prometheus, Grafana, exporter e Slack mock
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local python infra/run_webhook_server.py   # porta 47890
bash infra/test-webhook.sh                                  # envia um alerta Grafana simulado
```

| Serviço | Endereço |
|---------|----------|
| Grafana | http://localhost:3001 (`admin` / `aiops123`) |
| Prometheus | http://localhost:9090 |
| Metrics exporter | http://localhost:9101/metrics |
| Slack mock | http://localhost:47892/_mock/messages |
| Webhook server | http://localhost:47890/health |

Para enviar as notificações ao mock em vez do Slack real, defina `SLACK_API_BASE_URL=http://localhost:47892/api/` e `SLACK_BOT_TOKEN=xoxb-mock-token`. O passo a passo completo está em [`infra/README.md`](infra/README.md).

---

## Estrutura do repositório

```
aiops-agent/
├── main.py              # CLI e orquestração do pipeline
├── agents/              # Orchestrator, Diagnoser, Detector, Mitigator, skill router/evolver, cliente A2A
├── pipeline/            # Sanitizer, parsers, filtros, aggregator, compressor, preditores e risk scoring
├── rag/                 # Indexação FAISS, retrievers, reranker, expansão de consulta e memória
├── safety/              # PreValidator, TNR, VersionValidator e auditoria
├── blueprint/           # Blueprint Engine (pipeline como grafo tipado)
├── domain/              # Entidades de domínio imutáveis
├── application/ports/   # Portas da camada de aplicação
├── adapters/            # Implementações das portas (LLM, persistência, credenciais, reporters)
├── app/                 # Raiz de composição
├── interfaces/
│   ├── demo/            # Dashboard web (FastAPI + WebSocket) e modo narrativo
│   ├── http/            # App FastAPI: health, webhooks, notificações, A2A, admin
│   └── mcp/             # Servidor MCP (stdio/SSE) e rate limiter
├── auth/                # OAuth 2.1, PKCE, validação JWT/OIDC
├── core/                # Logging, ciclo de vida e protocolos compartilhados
├── config/              # Configuração YAML e loader (pydantic-settings)
├── data/                # Cenários simulados, modelos, base de conhecimento e golden sets
├── tools/               # Conectores (Databricks, Jira, Slack, FAISS)
├── validation/          # Runner e cenários de validação (RAG, knowledge)
├── rust/                # Extensão nativa `_aiops_rust` (PyO3)
├── infra/               # Ambiente Docker (Prometheus, Grafana) para testes locais
├── scripts/             # Build Rust, CI local e gerador de cenários
├── tests/               # Testes unitários, de integração e snapshot
└── docs/                # Arquitetura, ADRs, guias e artigos de referência
```

Para criar um novo cenário de incidente, use `scripts/new_scenario.py` e o modelo em [`docs/contributing/scenario-template.md`](docs/contributing/scenario-template.md).

---

## Referências acadêmicas

| Técnica | Artigo | Aplicação no projeto |
|---------|--------|----------------------|
| Orquestração por máquina de estados com rollback | STRATUS (NeurIPS 2025) | `agents/orchestrator.py`, `safety/tnr.py` |
| Diagnóstico iterativo ReAct com ferramentas | OpsAgent (2025) | `agents/diagnoser.py` |
| Correlação multissinal (logs, métricas, traces) | RCACopilot (Microsoft, 2024) | `pipeline/aggregator.py` |
| Compressão de contexto por prioridade | Acon (2025) | `pipeline/context_compressor.py` |
| Alocação de orçamento de tokens para observabilidade | AOI (2025) | `pipeline/context_compressor.py` |
| RAG assistido por ferramentas para RCA | TAMO (2025) | `rag/` |
| Defesa contra telemetria adversarial (AIOpsShield) | When AIOps Become "AI Oops" (2025) | `pipeline/telemetry_sanitizer.py`, `safety/` |

Os PDFs estão em [`docs/papers/`](docs/papers/).

---

## Limitações conhecidas

- **O pipeline acionado por webhook ainda é um stub.** O servidor `infra/run_webhook_server.py` recebe, normaliza, deduplica e enfileira os alertas, mas o `PipelineRunner` apenas registra `pipeline.stub_executed`. O diagnóstico completo roda pela CLI (`main.py`), pela demo e pelas interfaces MCP e A2A.
- O projeto é uma **prova de conceito**. As ações de mitigação são propostas e validadas, mas não são executadas em infraestrutura real.

---

## Documentação adicional

| Documento | Conteúdo |
|-----------|----------|
| [`docs/index.md`](docs/index.md) | Índice geral da documentação |
| [`docs/architecture.md`](docs/architecture.md) | Decisões de arquitetura, máquina de estados e camadas de segurança |
| [`docs/development-guide.md`](docs/development-guide.md) | Ambiente de desenvolvimento e convenções |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records |
| [`infra/README.md`](infra/README.md) | Ambiente local com Docker (Grafana, Prometheus, Slack mock) |
| [`infra/SLACK-SETUP.md`](infra/SLACK-SETUP.md) | Configuração de um Slack App real |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Guia de contribuição |

---

## Licença

Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE) para mais detalhes.
