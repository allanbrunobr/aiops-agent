# ADR 0003 — Saída do Pydantic-backed domain via DTOs de borda e dataclasses puros

- **Status:** Accepted
- **Data:** 2026-04-29
- **Decisores:** allanbrunobr
- **Supersedes:** ADR 0001
- **Subfase relacionada:** Fase 3 estrutural — Domain Purity e DTOs de Borda

## Contexto

ADR 0001 deixou `NormalizedAlert` e `IncidentState` em `domain/` como
`pydantic.BaseModel` por escolha pragmática, com 4 critérios cumulativos de saída
e gatilhos explícitos para revisão. A Fase 2 estrutural (PRs #11, #12, #13) entregou
`adapters/persistence/`, `application/ports/`, `adapters/llm/anthropic_client.py` e
`server/cli.py`, e removeu os shims `core/models.py` e `core/store.py`.

`core/protocols.py` ficou reduzido a `CredentialProvider`. O domínio está topologicamente
estável, mas continua dependendo de Pydantic. A condição "puro Python (stdlib)" prometida
na docstring de `domain/__init__.py` ainda não vale.

### Estado atual em `main` (referência objetiva)

| Contrato | Localização | Tipo base | Observações |
|---|---|---|---|
| `LogEntry`, `MetricPoint`, `Span`, `SimulatedIncident`, `PredictionReport`, `MetricSeries` | `domain/incident.py` | `@dataclass` puro | Nada a fazer — já são stdlib |
| `MitigationAction` | `domain/incident.py` | `dict` subclass com `__getattr__/__setattr__` | Único contrato com contrato dual atributo+chave |
| `NormalizedAlert` | `domain/alert.py` | `pydantic.BaseModel(frozen=True)` + `field_validator("severity")` | Validador de severity precisa ir para a borda |
| `IncidentState` | `domain/incident_state.py` | `pydantic.BaseModel(populate_by_name=True)` | Origem dos 11 `state.model_copy(update={...})` |

### Sites de produção que referenciam Pydantic

- `state.model_copy(update={...})` — 11 call sites, mapeados:
  - `main.py:1880`
  - `server/api/accuracy.py:78`
  - `blueprint/adapters/safety_nodes.py:73, 129, 195, 283`
  - `blueprint/adapters/pipeline_nodes.py:48, 90, 125, 147, 158, 200`
- `model_dump_json()` / `model_validate_json(...)` — exercitados em `tests/test_core.py:72-74`
  (NormalizedAlert) e `tests/test_core.py:97-100` (IncidentState).
- `field_validator("severity")` — `domain/alert.py`, aceita `{critical, error, warning, info}`
  case-insensitive normalizando para lowercase.
- `MitigationAction` — re-exportado por `safety/tnr.py:21`. Snapshots
  `tests/snapshots/{full_pipeline_result, memory_leak_mitigation, metric_anomalies, mitigation, safety_validation, unknown_fault_mitigation}.json`
  contêm os campos `description`, `command`, `risk_level` (e `rollback_command` quando definido)
  no formato wire que **não pode mudar** — `tests/snapshots/` é invariante por CLAUDE.md.

### Critérios de saída do ADR 0001 — situação

1. **`model_copy(update=...)` substituível.** OK — `dataclasses.replace(state, **kwargs)`
   é equivalente direto, stdlib, sem custo de tipo. Os 11 sites são reescrita mecânica.
2. **DTOs de borda separados.** Pendente. `server/webhook/` valida via `NormalizedAlert`
   diretamente. Precisa migrar para `server/webhook/schemas.py` Pydantic e converter para
   dataclass do domínio na ingestão.
3. **Round-trip JSON preservado.** Os testes em `tests/test_core.py` exercitam Pydantic.
   Precisam migrar para serializador explícito (stdlib `dataclasses.asdict()` +
   `json.dumps()` com encoder de `datetime`) ou rodar pelo DTO da borda.
4. **Validação de `severity` preservada.** Precisa migrar para a borda — DTO Pydantic
   (`server/webhook/schemas.py`) e/ou normalizadores (`server/webhook/normalizers/*`).

Os 4 critérios são adressáveis em escopo da Fase 3. Nenhum exige mudança fora do
domínio + borda HTTP + testes.

## Decisão

Sair do Pydantic no domínio com a seguinte decomposição:

1. **DTOs Pydantic ficam em `server/webhook/schemas.py`.** Esta é a borda canônica de
   ingestão de alertas externos (PagerDuty, Grafana, Databricks). FastAPI continua
   integrando direto com Pydantic.

   **MCP e A2A não recebem DTOs próprios nesta fase.** Auditoria de evidência (2026-04-29):

   | Canal | Caminho | Constrói entidades de domínio direto? |
   |---|---|---|
   | MCP — primário | `mcp_server/tools.py:759` (`normalizer_registry.normalize(...)`) | Não. Reusa `server/webhook/normalizers/*`. |
   | MCP — fallback | `mcp_server/tools.py:765-781` | **Sim.** Constrói `NormalizedAlert(...)` com severity map inline (linha 767-768). |
   | A2A | `server/routes/a2a.py:131-168` | **Sim.** Constrói `NormalizedAlert(...)` (linha 150) e `IncidentState(...)` (linha 162) direto, com `_map_severity` inline (linha 131). |

   Apesar de A2A e do fallback MCP construírem entidades de domínio na borda, **a Fase 3
   não introduz DTOs Pydantic dedicados para estes canais**. Razões:

   - **Mudança mecânica via PR B/PR C.** Quando PR B altera o construtor de
     `NormalizedAlert` (`BaseModel` → `@dataclass(frozen=True)`) e PR C altera
     `IncidentState`, os 3 sites acima (`mcp_server/tools.py:770`, `server/routes/a2a.py:150`,
     `:162`) sofrem ajuste sintático mecânico no mesmo PR. Não há trabalho de DTO
     adicional para fazer — só atualizar a forma de instanciar.
   - **Severity já normalizado por mapas inline.** MCP fallback (`_severity_map`) e A2A
     (`_map_severity`) já convertem para o vocabulário canônico antes de construir.
     A validação Pydantic do `field_validator("severity")` é redundante com esses mapas
     — quando o validator sair de `domain/alert.py` (PR B), MCP e A2A continuam
     normalizando localmente sem perda de garantia.
   - **DTOs separados seriam prematuros.** Hoje MCP e A2A produzem `NormalizedAlert`
     1:1; criar `MCPAlertDTO` e `A2AAlertDTO` adicionaria 2 schemas Pydantic sem que
     nenhum campo divirja do contrato HTTP. Cumpre o critério de saída #1 deste ADR
     ("múltiplos canais divergem em forma de DTO") — quando isto deixar de valer, ADR
     sucessor introduz DTOs específicos.

   **Consequência de escopo:** PR B inclui atualização dos 2 sites de `NormalizedAlert`
   em `mcp_server/tools.py:770` e `server/routes/a2a.py:150`. PR C inclui atualização
   do site de `IncidentState` em `server/routes/a2a.py:162`. Não há PR adicional.

2. **`MitigationAction` vira `@dataclass(frozen=True)` no domínio.** Campos preservados:
   `description: str`, `command: str`, `risk_level: str`, `rollback_command: str | None`.
   `dataclasses.asdict(action)` deve produzir wire format idêntico ao atual
   `dict(action)` para preservar os 6 snapshots impactados. Acesso por chave
   (`action["field"]`) deixa de ser suportado — call sites migram para acesso por
   atributo (`action.field`). `safety/tnr.py:21` mantém o re-export.

3. **`NormalizedAlert` vira `@dataclass(frozen=True)`.** O `field_validator("severity")`
   é movido para `server/webhook/schemas.py` (DTO Pydantic na borda HTTP) e para os
   normalizers em `server/webhook/normalizers/*` (PagerDuty, Grafana, Databricks).
   No domínio, `NormalizedAlert` confia que entrou normalizado — qualquer construção
   programática (testes, fixtures) que precise de validação usa o DTO da borda.

4. **`IncidentState` vira `@dataclass`.** Não-frozen — o pipeline sempre retornou novo
   estado via `model_copy`, mas o objeto em si não precisa ser imutável e a mutabilidade
   ajuda com tooling (debugger, REPL). Os 11 call sites de `state.model_copy(update=...)`
   migram para `dataclasses.replace(state, **kwargs)` direto. **Não** introduzir método
   `.replace()` no `IncidentState` — `dataclasses.replace` é a forma idiomática stdlib e
   evita reinventar API que existe.

5. **Round-trip JSON via serializador explícito.** Substituir `model_dump_json()` /
   `model_validate_json(...)` em testes por serializador stdlib (`dataclasses.asdict` +
   `json.dumps` com encoder ISO-8601 para `datetime`, e `json.loads` + factory function
   no caminho inverso). Production paths que serializam para HTTP usam o DTO Pydantic
   da borda — não o domínio.

### Plano de implementação — 3 PRs atômicos

A ordem segue risco crescente; cada PR é independente e bisectável.

**PR A — `MitigationAction` → frozen dataclass.** Risco mínimo (poucos call sites, sem
borda HTTP afetada).

- `domain/incident.py`: substitui o `dict subclass` por `@dataclass(frozen=True)`.
- Migra call sites que usam `action["field"]` para `action.field` (auditar via
  `grep -rn 'action\[' --include="*.py"`).
- Verifica que `dataclasses.asdict(MitigationAction(...))` produz wire format idêntico
  aos 6 snapshots afetados. Re-roda `pytest tests/ -v` — snapshots **não** podem mudar.

**PR B — `NormalizedAlert` → frozen dataclass + DTO de borda.** Risco médio (toca
borda HTTP, MCP, A2A).

- Cria `server/webhook/schemas.py` com `NormalizedAlertDTO(BaseModel, frozen=True)`
  contendo o `field_validator("severity")` migrado.
- `domain/alert.py`: substitui `BaseModel` por `@dataclass(frozen=True)`. Remove
  `field_validator`.
- Atualiza `server/webhook/normalizers/{pagerduty,grafana,databricks}.py` para construir
  o DTO primeiro, depois converter para `NormalizedAlert` (dataclass) na saída do
  normalizer.
- Atualiza sites de construção direta de `NormalizedAlert` fora de `server/webhook/`:
  `mcp_server/tools.py:770` (fallback MCP) e `server/routes/a2a.py:150` (handler A2A).
  Ambos continuam construindo a entidade de domínio direto — só muda a sintaxe
  (`@dataclass(frozen=True)` em vez de `BaseModel`).
- Migra `tests/test_core.py:72-74` para serializador explícito ou para teste do DTO da
  borda.

**PR C — `IncidentState` → dataclass + 11 call sites de `model_copy`.** Maior risco
(toca pipeline + safety + main + A2A).

- `domain/incident_state.py`: substitui `BaseModel` por `@dataclass`. Remove
  `model_config`. `Field(default_factory=...)` vira `field(default_factory=...)` da
  stdlib.
- Migra os 11 sites de `state.model_copy(update={...})` para
  `dataclasses.replace(state, **kwargs)`. Lista exata em "Estado atual" acima — fazer
  em commit separado dentro do PR para facilitar review.
- Atualiza site de construção direta de `IncidentState` fora do pipeline:
  `server/routes/a2a.py:162` (handler A2A). Continua construindo direto — só muda a
  sintaxe.
- Migra `tests/test_core.py:97-100` para serializador explícito.
- Atualiza `domain/incident_state.py` docstring removendo a nota "temporariamente
  Pydantic-backed".
- Atualiza `domain/__init__.py` removendo a exceção sobre Pydantic.

## Consequências

**Positivas:**

- `domain/` deixa de depender de Pydantic. Critério `rg "pydantic|BaseModel|ConfigDict|Field|field_validator" domain` passa a retornar 0.
- Validação concentrada na borda: severity é validado uma vez na ingestão (DTO ou
  normalizer), nunca no fluxo interno. Reduz chance de validação dupla com mensagens
  divergentes.
- Mutações funcionais via `dataclasses.replace` são idiomáticas stdlib — leitor não
  precisa conhecer Pydantic para entender o pipeline.
- Tests de unidade do domínio rodam sem Pydantic instalado (relevante para
  `requirements-test-unit.txt`).
- ADR 0001 sai do estado "Accepted mas com critério de saída pendente". Domínio fica
  arquiteturalmente coerente com a docstring de `domain/__init__.py`.

**Negativas:**

- Refactor de 11 call sites de `model_copy` é mecânico mas tem alta superfície (toca
  `pipeline_nodes`, `safety_nodes`, `accuracy.py`, `main.py`). Risco de regressão se
  algum site usa Pydantic-specific behavior implícito (ex.: validação no `update={...}`).
  Mitigação: PR C dedicado, com snapshots e CI inteiros como rede.
- DTO duplicado entre `server/webhook/schemas.py` (Pydantic) e `domain/alert.py`
  (dataclass) — dois nomes para o mesmo conceito (`NormalizedAlertDTO` vs.
  `NormalizedAlert`). É o custo da pureza do domínio. Mitigação: docstring no DTO
  apontando para o domain entity e vice-versa.
- `MitigationAction` perde acesso por chave (`action["risk_level"]`). Call sites antigos
  que dependiam de `dict(action)` para serializar precisam usar `dataclasses.asdict`.
  Auditar via grep antes do PR A.

**Mitigações:**

- Cada PR é atômico, bisectável, com CI verde antes de mergear.
- Wire format dos snapshots é invariante — qualquer mudança em `dataclasses.asdict`
  output quebra `pytest tests/test_*snapshot*.py` imediatamente.
- ADR 0002 não é afetado: `LLMClient` continua passthrough, `agents/diagnoser.py`
  continua parseando JSON do Anthropic SDK e construindo `MitigationAction` —
  só muda o construtor (`MitigationAction(description=..., ...)` em vez de
  `MitigationAction(description=..., ...)` que internamente é `dict.__init__`).

## Critérios de saída

Esta decisão deve ser revisitada quando **qualquer um** dos itens abaixo ocorrer:

1. **Múltiplos canais de ingestão divergem em forma de DTO.** Hoje PagerDuty, Grafana e
   Databricks compartilham `NormalizedAlert` único. Se A2A ou MCP precisarem de DTOs
   próprios não-mapeáveis para `NormalizedAlert`, esta decisão precisa ser revisitada
   para discutir multiple DTOs da mesma entidade de domínio.
2. **Validação no domínio volta a ser necessária.** Se aparecer caso onde o domínio
   precisa rejeitar entrada inválida (ex.: invariantes de pipeline state machine),
   `dataclass` + factory function pode não bastar e talvez `attrs` ou validators
   manuais precisem entrar.
3. **Performance de `dataclasses.replace` vira gargalo.** Improvável dada a frequência
   atual (1 vez por node, ~8 nodes por incidente), mas se o pipeline crescer para
   centenas de mutações por execução, vale considerar mutação in-place explícita.
4. **DTOs Pydantic explodem em complexidade.** Se `server/webhook/schemas.py` virar
   100+ linhas com lógica condicional, é sinal para extrair `interfaces/http/schemas/`
   ou similar. Endereçado por ADR sucessor sobre layout de interfaces.

## Gatilho para revisão

Reavaliar este ADR quando:

- Nova fonte de alerta exigir DTO próprio que não mapeia 1:1 para `NormalizedAlert`.
- Refactor da máquina de estados de `IncidentState` (campo `state`) introduzir
  invariantes que `dataclass` não consegue expressar barato.
- Adição de `attrs`, `msgspec` ou outra lib de modelagem ao stack — momento natural
  para revisitar a escolha de stdlib pura.
- Phase 4 (reorganização de interfaces) reposicionar `server/webhook/` para
  `interfaces/http/` — schemas seguem o movimento.

## Notas

- Este ADR **substitui** ADR 0001. A entrada do ADR 0001 deve ser marcada
  `Status: Superseded by ADR 0003` no momento do merge deste documento.
- ADR 0002 (`LLMClient`) **não é afetado**. Continua passthrough. A interação com
  Pydantic ali era nula — a porta sempre devolveu objetos Anthropic-shaped.
- `tests/snapshots/` é invariante por CLAUDE.md. PR A precisa provar empiricamente
  (rodando os testes) que o wire format de `MitigationAction` não muda. Se mudar,
  PR A não merge — a alternativa é manter `MitigationAction` como `dict subclass` e
  documentar a exceção, mas isso contraria a decisão.
- Os critérios de aceite de Fase 3 do execution plan são atendidos por este ADR + 3
  PRs atômicos. CI local (`scripts/ci-local.sh unit` + `integration`) deve rodar verde
  ao final de cada PR.
- A docstring de `domain/__init__.py` que afirma "puro Python (stdlib): sem Pydantic"
  passa a refletir realidade ao final do PR C.
