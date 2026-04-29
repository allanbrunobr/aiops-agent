# Plano de Execução do Refactoring

## Objetivo

Finalizar o refactoring arquitetural iniciado nas Fases 0, 1 e 2a sem abrir mudanças
funcionais desnecessárias. O plano abaixo é a ordem direta de execução para levar o
repositório a um estado mais limpo, testável e alinhado com os ADRs aceitos.

Baseline atual assumida: `main` em `84e0ec8`, PR #9 mergeado, `LLMClient` introduzido
como seam de teste e containment boundary conforme ADR 0002.

## Regras de Execução

- Um PR por fase. Não misturar movimentação de arquivos com mudança comportamental.
- Cada PR deve passar `scripts/ci-local.sh unit` localmente antes do push.
- Rodar CI completo antes de mergear: unit, integration/snapshot e Rust/PyO3.
- Usar shims somente como transição. Todo shim novo precisa ter critério de remoção.
- Não refatorar Pydantic em `domain/` sem cumprir os gatilhos do ADR 0001 ou aprovar
  um ADR sucessor.

## Fase 2b — Fechar a Seam LLM

**Branch:** `refactor/llm-seam-callers`

**Escopo:**

- Migrar `agents/orchestrator.py` para `client.create_message(...)`.
- Migrar `agents/skill_evolver.py` para `client.create_message(...)`.
- Tipar os construtores contra `application.ports.llm_client.LLMClient` onde couber.
- Remover a property `messages` de `adapters/llm/anthropic_client.py`.
- Atualizar docstrings do adapter e da porta para refletir que `.messages` não é mais
  contrato público.
- Atualizar mocks em `tests/test_orchestrator.py` e testes relacionados ao skill
  evolver.

**Critérios de aceite:**

```bash
rg -n "from anthropic|import anthropic|Anthropic\(" . -g "*.py"
rg -n "\.messages\.create" agents application adapters blueprint main.py -g "*.py"
python -m compileall application adapters agents
python -m pytest tests/test_orchestrator.py tests/test_llm_model_config.py -q
scripts/ci-local.sh unit
```

Resultado esperado: import direto do Anthropic apenas em
`adapters/llm/anthropic_client.py`; nenhuma chamada `.messages.create` fora do adapter.

## Fase 2c — Reporter Port com Eventos Estruturados

**Branch:** `refactor/reporter-port`

**Escopo:**

- Criar `application/ports/reporter.py`.
- Criar adapters:
  - `adapters/reporter/rich_console.py`
  - `adapters/reporter/structured_log.py`
- Definir vocabulário pequeno de eventos, por exemplo: `diagnosis_started`,
  `llm_call_failed`, `thought_observed`, `tool_called`, `tool_observed`,
  `diagnosis_completed`, `mitigation_proposed`, `human_approval_requested`.
- Migrar primeiro `agents/detector.py`, `agents/diagnoser.py` e
  `agents/mitigator.py`.
- Manter `main.py` com Rich por enquanto. CLI é interface, não domínio de agente.

**Critérios de aceite:**

```bash
rg -n "from rich|Console\(|Panel\(|console\.print" agents -g "*.py"
python -m compileall application adapters agents
python -m pytest tests/test_diagnose_incident.py tests/test_tool_methods.py tests/test_safety.py -q
scripts/ci-local.sh unit
```

Resultado esperado: agentes não importam Rich diretamente; saída visual fica em adapter.

## Fase 2d — Ports e Persistence

**Branch:** `refactor/incident-store-port`

**Escopo:**

- Mover `IncidentStore` de `core/protocols.py` para
  `application/ports/incident_store.py`.
- Mover `InMemoryStore` de `core/store.py` para
  `adapters/persistence/in_memory_store.py`.
- Criar `adapters/persistence/__init__.py`.
- Manter `core/protocols.py` e `core/store.py` como shims temporários com `__all__`.
- Atualizar imports em código de produção tocado pela mudança.

**Critérios de aceite:**

```bash
rg -n "from core\.protocols|from core\.store|import core\.protocols|import core\.store" .
python -m compileall application adapters core blueprint server mcp_server
python -m pytest tests/test_core.py tests/test_engine.py tests/test_api/test_accuracy.py -q
scripts/ci-local.sh unit
```

Resultado esperado: implementação concreta de store sai de `core/`; contratos de
aplicação ficam em `application/ports/`.

## Fase 2e — Retirar Shims Legados

**Branch:** `refactor/remove-core-shims`

**Escopo:**

- Atualizar imports remanescentes:
  - `core.models` -> `domain.incident`, `domain.alert`, `domain.incident_state`
  - `core.protocols` -> `application.ports.incident_store`
  - `core.store` -> `adapters.persistence.in_memory_store`
- Remover shims se não houver contrato público externo a preservar.
- Se houver risco de quebra externa, manter shims por uma versão com
  `DeprecationWarning` e abrir issue de remoção.

**Critérios de aceite:**

```bash
rg -n "from core\.models|import core\.models|from core\.protocols|from core\.store" .
python -m compileall .
scripts/ci-local.sh unit
scripts/ci-local.sh integration
```

Resultado esperado: `core/` deixa de ser ponto de passagem para contratos novos.

## Fase 3 — Domain Purity e DTOs de Borda

**Branch inicial:** `docs/adr-0003-domain-purity`

Esta fase depende do ADR 0001. Antes de código, escrever ADR 0003 decidindo a saída do
Pydantic-backed domain.

**ADR 0003 deve decidir:**

- Onde ficam DTOs Pydantic de webhook, HTTP, MCP e A2A.
- Como substituir `IncidentState.model_copy(update=...)`.
- Como preservar round-trip JSON hoje coberto por `model_dump_json()` e
  `model_validate_json(...)`.
- Como preservar validação de `NormalizedAlert.severity`.
- Como transformar `MitigationAction` de `dict subclass` em contrato explícito.

**Execução após ADR aceito:**

- Criar DTOs Pydantic em `server/webhook/schemas.py` e/ou
  `interfaces/*/schemas.py`.
- Converter `NormalizedAlert` para dataclass pura no domínio.
- Converter `IncidentState` para dataclass pura ou objeto de state machine explícito.
- Criar serializadores explícitos para JSON.
- Converter `MitigationAction` para dataclass com `to_dict()`/`from_dict()`.
- Atualizar call sites de `model_copy`, `model_dump_json`, `model_validate_json`,
  `dict(action)` e `action["field"]`.

**Critérios de aceite:**

```bash
rg -n "pydantic|BaseModel|ConfigDict|Field|field_validator" domain -g "*.py"
rg -n "model_copy|model_dump|model_validate|dict\(action\)|action\[" .
python -m pytest tests/test_core.py tests/epic2 tests/test_validate_action.py -q
scripts/ci-local.sh unit
scripts/ci-local.sh integration
```

Resultado esperado: `domain/` não depende de Pydantic; validação/serialização ficam nas
bordas.

## Fase 4 — Reorganização de Interfaces e Use Cases

**Branch:** `refactor/interface-layout`

Executar somente depois das fases anteriores estarem verdes. Esta fase é estrutural e
deve ser feita com `git mv` em commits separados de atualização de imports.

**Escopo recomendado:**

- `server/` -> `interfaces/http/`
- `mcp_server/` -> `interfaces/mcp/`
- `demo/` -> `interfaces/demo/`
- `main.py` -> `interfaces/cli/main.py` ou manter `main.py` como thin entrypoint que
  delega para `interfaces/cli/`.
- Avaliar `agents/`, `pipeline/`, `safety/`, `rag/` como `application/use_cases/`
  apenas se houver benefício concreto. Não fazer renomeação cosmética sem reduzir
  acoplamento.

**Critérios de aceite:**

```bash
python -m compileall interfaces application adapters domain
scripts/ci-local.sh unit
scripts/ci-local.sh integration
python main.py --no-llm --no-rag
```

Resultado esperado: entrypoints ficam agrupados como interfaces; regras de aplicação e
adapters permanecem independentes de framework de entrada.

## Fase 5 — Composition Builders

**Branch:** `refactor/composition-builders`

**Escopo:**

- Criar `application/builders.py` ou `app/builders.py` para subgraphs compartilhados.
- Extrair builders puros:
  - `build_diagnoser_agent(...)`
  - `build_detector_agent(...)`
  - `build_pipeline(...)`
  - `build_rag(...)`
- Manter cada entrypoint com sua composition root própria: CLI, HTTP, MCP e demo.
- Não centralizar middleware HTTP, parsing de CLI, handshake MCP ou assets de demo.

**Critérios de aceite:**

```bash
rg -n "build_.*agent|build_pipeline|build_rag" application app interfaces main.py server mcp_server demo
python -m compileall .
scripts/ci-local.sh unit
scripts/ci-local.sh integration
```

Resultado esperado: wiring duplicado sai dos entrypoints, mas cada interface mantém sua
responsabilidade top-level.

## Checklist Final

Executar quando todas as fases acima estiverem mergeadas:

```bash
git switch main
git pull origin main
scripts/ci-local.sh unit
scripts/ci-local.sh integration
scripts/ci-local.sh rust
rg -n "from anthropic|import anthropic|Anthropic\(" . -g "*.py"
rg -n "\.messages\.create" . -g "*.py"
rg -n "from rich|Console\(|Panel\(|console\.print" agents -g "*.py"
rg -n "from core\.models|from core\.protocols|from core\.store" .
rg -n "pydantic|BaseModel|ConfigDict|Field|field_validator" domain -g "*.py"
python main.py --no-llm --no-rag
```

## Definição de Pronto

O refactoring é considerado finalizado quando:

- SDK Anthropic está contido em `adapters/llm/`.
- Call sites LLM usam `LLMClient.create_message()`.
- Agentes não dependem de Rich.
- Ports vivem em `application/ports/`.
- Implementações concretas vivem em `adapters/`.
- Shims legados foram removidos ou têm data/issue explícita de remoção.
- `domain/` é puro Python ou há ADR aceito justificando exceção remanescente.
- CI completo está verde em `main`.
