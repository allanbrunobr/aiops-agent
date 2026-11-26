# Prompt do implementador — Robustez Eixos A/B/C (fila long-run)

Você é um implementador Claude trabalhando no repositório `aiops-agent`. Cada sessão
implementa **UMA** tarefa da fila `feature_list.json`, na ordem, e a marca `passes: true`
somente após verificar verde **aqui no M5**.

## SPEC AUTORITATIVA

A especificação técnica completa, com evidências `file:line`, critérios de aceite e
write-set de cada tarefa, está em **`ROBUSTNESS_PLAN.md`**. LEIA a seção da tarefa antes
de implementar:

| Tarefa | Seção do plano | Tipo |
|---|---|---|
| A1 testes real-RAG | `## 3. FASE A` → `### A1` (~:93) | deep |
| A2 job CI real-rag | `### A2` (~:111) | quick win, dep A1 |
| A4 failure-injection | `### A4` (~:134) | deep |
| A5 mocks assinatura estrita | `### A5` (~:149) | QUICK WIN |
| B1 degradação RAG run_v0 | `## 4. FASE B` → `### B1` (~:165) | deep, dep A4 |
| B7 retry Databricks+LLM | `### B7` (~:230) | médio |
| B8 validação telemetria | `### B8` (~:241) | médio |
| C3 edge cases pre_validator | `## 5. FASE C` → `### C3` (~:275) | quick win |
| C4 versão desconhecida ≠ segura | `### C4` (~:288) | médio |
| C5 invariante aprovação humana | `### C5` (~:294) | médio, dep C3 |
| C6 blast radius conservador | `### C6` (~:301) | médio |
| A3 container Intel-Mac | `### A3` (~:122) | quick win, dep A1 |
| B3 guards NaN/inf/span | `### B3` (~:188) | quick win |
| B6 post_state/rollback no try | `### B6` (~:224) | quick win |

Contexto do projeto e a lição que motiva tudo: `ROBUSTNESS_PLAN.md` §0 e §1 (o bug
`num_workers` passou no CI porque os mocks absorviam kwargs e nenhum teste carregava
modelo real — fechar esse ponto cego é o Eixo A, prioridade máxima).

## ⛔ REGRA DE VERIFICAÇÃO (sobrepõe qualquer nota antiga)

Verifique **AQUI no M5**, direto com o venv do projeto. **NÃO** use `scripts/ci-local.sh`
para a suíte unit (ele cria um venv fresco em `/tmp` = pesado → o hook `m5-guard` bloqueia
no M5 e você vai deferir para o M1 e falhar). Use:

- Unit: `.venv/bin/python -m pytest tests -m unit -q`
- Integration (A4/B7): `.venv/bin/python -m pytest tests -m integration -q`
- real-RAG (A1): `.venv/bin/python -m pytest tests -m real_rag -v` (M5 é arm64, torch 2.4.1
  funciona; o job unit continua sem torch via `importorskip`)
- Rust (B3): `cargo test --manifest-path rust/Cargo.toml` + `cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings`
- Lint/tipos: `.venv/bin/ruff check .` + `.venv/bin/mypy domain application core`
- Smoke: `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/smoke-out` (exit ∈ {0,3})

Para A2 (job CI): verifique o YAML + o job `real-rag` do `scripts/ci-local.sh` localmente;
o run real no GitHub Actions acontece no push (não bloqueie a sessão esperando o CI remoto).

**Marque `passes: true` após a verificação verde + commit. Uma sessão basta por tarefa.**
Não defira a suíte para o M1. Não rode testes concorrentes com outra sessão.

## Regras de implementação (inegociáveis)

1. **Teste que falha antes do fix.** Escreva o teste-regressão primeiro, veja falhar, implemente, veja passar.
2. **Write-set explícito.** Toque só os arquivos listados na seção do plano. Não refactor fora do escopo.
3. **Nada de infra real.** TNR é SIMULADO (SECURITY.md); o agente propõe + aprovação humana, nunca executa mitigação real. Fail-closed: na dúvida, exit 3 (approval) ou 2 (regressão), nunca falso sucesso exit 0.
4. **Nada gravar em `output/` do repo.** Testes usam `tmp_path` (fixture `tests/conftest.py:201-215` `_forbid_writes_to_repo_output`).
5. **Confirme `file:line`** antes de editar (o plano cita linhas; elas podem ter driftado — verifique).
6. **Não quebre o V0** nem a paridade Rust↔Python. Snapshot só com justificativa.
7. **Sem deps novas** além das já em `requirements.txt`/`pyproject.toml`.
8. **Opção mais honesta:** se um critério não for alcançável sem falso positivo, documente o limite em vez de forçar.
9. Commit por tarefa: `fix(...)`/`feat(...)`/`test(...)` + corpo curto citando a seção do plano.

## Nota sobre C5

C5 (invariante de aprovação) depende da decisão agregada governar o exit (C1). Se o teste
do invariante falhar porque o CLI descarta o agregado (`v0_pipeline.py:782` retorna só
`pre_validator`), implemente C1 conforme `ROBUSTNESS_PLAN.md` §5 C1 como parte de C5
(`v0_pipeline.py:753-846`, `display.py:48-59`, exit 3 quando o agregado bloquear).

## Verificação final (após a fila)

`.venv/bin/python -m pytest tests -m unit -q` verde; `-m integration` verde; `-m real_rag`
verde (M5); `cargo test` + clippy verdes; ruff/mypy limpos; smokes V0/V1 exit ∈ {0,3}.
