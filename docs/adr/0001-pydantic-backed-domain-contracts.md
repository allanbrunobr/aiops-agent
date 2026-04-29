# ADR 0001 — Contratos do domínio temporariamente Pydantic-backed

- **Status:** Superseded by [ADR 0003](./0003-domain-purity.md)
- **Data:** 2026-04-26
- **Substituído em:** 2026-04-29
- **Decisores:** allanbrunobr
- **Subfase relacionada:** Fase 1 estrutural — 1a (PR #2) e 1b (PRs #4, #5, #6)

> **Nota (2026-04-29):** Esta decisão foi substituída por ADR 0003. A Fase 2 estrutural
> removeu `core/models.py` e os shims; a Fase 3 sai do Pydantic-backed domain via DTOs
> de borda em `server/webhook/schemas.py` e dataclasses puros em `domain/`. O conteúdo
> abaixo é mantido como histórico — qualquer afirmação que contradiga ADR 0003 está
> desatualizada.

## Contexto

A Fase 1 do refactor estrutural moveu os contratos compartilhados do pipeline AIOps de
`core/models.py` para a camada `domain/`, mantendo `core/models.py` como shim explícito
para preservar os ~50 import sites existentes. Os tipos relevantes hoje são:

| Contrato | Localização atual | Tipo base |
|---|---|---|
| `LogEntry`, `MetricPoint`, `Span`, `SimulatedIncident`, `PredictionReport`, `MetricSeries` | `domain/incident.py` | `@dataclass` puro |
| `MitigationAction` | `domain/incident.py` | `dict` subclass com `__getattr__/__setattr__` |
| `NormalizedAlert` | `domain/alert.py` | `pydantic.BaseModel` (`frozen=True`) |
| `IncidentState` | `domain/incident_state.py` | `pydantic.BaseModel` (`populate_by_name=True`) |

A regra de dependência da camada `domain/` (documentada em `domain/__init__.py`) é
unidirecional: outras camadas podem importar de `domain/`, mas `domain/` não importa
de mais nada do projeto. Isso já está respeitado.

O ponto que **não** está respeitado é a regra "puro Python (stdlib): sem Pydantic".
`NormalizedAlert` e `IncidentState` foram movidos preservando `BaseModel` por escolha
pragmática — converter agora exigiria refactor amplo dos call sites.

## Decisão

Manter `NormalizedAlert` e `IncidentState` em `domain/` como `pydantic.BaseModel` por
enquanto. **Não** converter para dataclass como parte do trabalho atual.

## Consequências

**Positivas:**

- Migração estrutural concluída sem regressão funcional. Identidade runtime preservada
  (`core.models.X is domain.<mod>.X` para os 4 contratos).
- Os 18+ call sites de `state.model_copy(update={...})` em produção (`pipeline_nodes`
  ×6, `safety_nodes` ×4, `accuracy.py:78`, `main.py:1872`) continuam funcionando sem
  mudança.
- O validator de `severity` em `NormalizedAlert` (Pydantic `field_validator`) e o
  round-trip JSON exercitado em `tests/test_core.py:95-96` continuam intactos.
- Borda HTTP/MCP (FastAPI) integra direto com Pydantic — nenhum DTO adicional precisou
  ser introduzido.

**Negativas:**

- A camada `domain/` **não** é framework-agnostic ainda. Pydantic é uma dependência
  efetiva do domínio.
- A docstring de `domain/__init__.py` afirma "puro Python (stdlib)" — fica
  temporariamente em desacordo com o conteúdo. Precisa ser atualizada quando esta
  decisão evoluir.
- O domínio depende de uma decisão de framework HTTP por transitividade. Trocar
  Pydantic seria um refactor não-local.

**Mitigações:**

- Os shims em `core/models.py` mantêm o ponto de entrada histórico estável. Qualquer
  futura decisão de mudar a representação interna pode preservar a interface
  `from core.models import ...`.
- Identidade runtime e annotations resolvidos preservam o comportamento de tipo
  (Pydantic field types, isinstance, Protocol matching).

## Critérios de saída

Esta decisão deve ser revisitada quando **qualquer um** dos itens abaixo mudar:

1. **`model_copy(update=...)` substituível.** Seja via `dataclasses.replace` + helper,
   seja por mudança de modelo de mutação (state machine explícita, immutable updates
   funcionais via função pura). Hoje há 18+ chamadas em produção.
2. **DTOs de borda separados.** Introdução de uma camada `adapters/<x>/schemas.py`
   (FastAPI/MCP) que valida/serializa entrada/saída, deixando `domain/` puro. Isso
   habilita conversão `NormalizedAlert` → dataclass sem afetar a borda HTTP.
3. **Round-trip JSON preservado.** `tests/test_core.py:95-96` exercita
   `state.model_dump_json()` + `IncidentState.model_validate_json(...)`. Substituição
   do Pydantic exige equivalente (provavelmente via DTO de borda + serializador
   explícito).
4. **Validação de `severity` preservada.** `NormalizedAlert.severity` aceita apenas
   `{critical, error, warning, info}` (case-insensitive, normaliza para lowercase).
   Substituição exige validador equivalente, idealmente no ponto de ingestão (DTO de
   borda) e não no domínio.

Os 4 critérios são cumulativos: nenhum dos call sites de produção pode quebrar quando
a saída acontecer.

## Gatilho para revisão

Reavaliar este ADR quando **qualquer um** dos eventos abaixo ocorrer:

- Próxima mudança funcional relevante em `server/webhook/`, `server/routes/a2a.py` ou
  `mcp_server/` que precise tocar a forma do contrato (campo novo, validator novo).
- Refactor planejado da máquina de estados do incidente (`state` field de
  `IncidentState`) — momento natural para introduzir mutações explícitas e
  potencialmente sair do `model_copy(update=...)`.
- Adição de nova fonte de alerta que exija um validator próprio diferente do atual,
  reforçando o caso para separar domain entity de DTO de borda.

Até lá, o domínio fica estável neste estado intermediário.

## Notas

- Este ADR cobre **apenas** a representação dos contratos no domínio. Não trata da
  organização interna de `pipeline/`, `agents/`, `blueprint/`, `safety/`, nem da
  Fase 2 do refactor estrutural.
- A decisão é deliberadamente conservadora: prefere estabilidade do pipeline em
  produção a pureza arquitetural de curto prazo.
