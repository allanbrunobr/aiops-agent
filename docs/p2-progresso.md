# Progresso das correções P2

- **Origem:** §5 "P2 — próximo ciclo" de [`analise-20261109.md`](analise-20261109.md).
- **Branch:** `p2-correcoes`, local, criada a partir de `10370ce` (a ponta
  das correções P1 em `analise-aiops`). Sem push e sem PR.
- **Método:** o mesmo do P1, descrito em [`p1-progresso.md`](p1-progresso.md):
  teste falhando antes, um commit por item e, depois de cada item, `ruff check
  .` e os três jobs de pytest do CI com status 0.
- **Ordem:** gravidade do achado.

## Item P2-1 — MCP funcional (ARQ-02, 4B-02, 4B-03, SEC-08, T-b)

**Defeitos:**

- `diagnose_incident` chamava `engine.run(alert)`, mas a assinatura real é
  `run(pipeline, state)`. Toda chamada devolvia 500.
- O `create_app` montava o SSE **antes** de criar a engine, então o MCP nunca
  a recebia (devolvia 503).
- O `TelemetrySanitizer` não tinha `sanitize_text`. Por isso o
  `validate_action` reprovava qualquer comando, e a busca devolvia os campos
  redigidos por falha (o fail-closed do P1).
- Os testes antigos usavam dublês com essa mesma API inexistente, e por isso
  passavam verdes.

**Correção:**

- `pipeline/telemetry_sanitizer.py`: `sanitize_text(text)` aplica a texto solto
  as mesmas regras do campo `message` dos logs: neutraliza payload
  adversarial, redige email, IP e URL e, em modo estrito, abstrai conteúdo
  user-generated.
- `interfaces/mcp/tools.py`, `handle_diagnose_incident`:
  - monta um `IncidentState` e chama `engine.run(build_pipeline(config),
    state)`;
  - lê da resposta os campos que o estado tem: `diagnosis`, `proposed_action`,
    `safety_verdict` e `safety_reasons`;
  - usa LLM só com `ANTHROPIC_API_KEY`, a mesma regra da CLI;
  - num bloqueio de safety, responde `type: "safety_blocked"` (409) com o
    diagnóstico, a camada e o motivo.
- `interfaces/http/app.py`: engine e retriever são criados antes do
  `mount_sse`, repassados ao MCP e reusados em `app.state`.
- `tests/test_diagnose_incident.py`: os fakes passaram a ter o contrato real,
  `run(self, pipeline, state)`. O teste de rastreio agora também confere que o
  estado carrega o alerta normalizado.

**Testes:** `tests/test_mcp_contract.py`, 9 testes marcados `unit`, usando as
classes **reais**.

- **Antes da correção:** 8 falharam. O que passava (comando com PII
  bloqueado) passava porque, até então, **todo** comando era bloqueado.
- **Cenários cobertos:**
  - `sanitize_text` com texto limpo, com PII e com payload adversarial;
  - `validate_action` com as 4 camadas reais (comando limpo passa, comando
    com PII é bloqueado);
  - busca com o sanitizer real: redige a PII e mantém o resto do texto;
  - `diagnose_incident` com a `BlueprintEngine` real e com um bloqueio de
    safety;
  - `create_app` repassando engine e retriever ao `mount_sse`.

**CI local:** ruff limpo. unit 839 passaram, integration 138 passaram, suíte
completa 1530 passaram e 10 foram pulados. Status 0 nos três.

**Limite que continua:** a MCP e a A2A rodam o pipeline **sem telemetria**.
O estado nasce só com o alerta, sem logs, métricas ou traces, e o diagnóstico
sai da heurística sobre nada. Ligar uma fonte de telemetria a essas entradas é
trabalho de arquitetura (relatório: ARQ-10 e o stub do webhook), não deste
item.
