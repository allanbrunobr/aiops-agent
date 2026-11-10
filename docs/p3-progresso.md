# Progresso das correções P3

- **Origem:** §5 "P3 — dívida estrutural" de
  [`analise-20261109.md`](analise-20261109.md).
- **Branch:** `p3-correcoes`, local, criada a partir da ponta da
  `p2-correcoes` (`727ee77`). Sem push e sem PR.
- **Restrições:**
  - não mexer em `.github/`, `ci-ok.sh`, hooks nem credenciais;
  - não decidir os itens que já são decisões do Allan (V0 × V1, gates de CI
    em `.github/`, `header` como padrão do MCP, `issuer`/`audience`, timeout
    do LLM, janela do Databricks);
  - pular o que depender dessas decisões.
- **Método:** o de sempre. Teste falhando antes quando há defeito, um commit
  por item, e `ruff check .` mais os três jobs de pytest do CI depois de cada
  item.

## P3-9a — CoT retriever chamava o reranker com os argumentos trocados (F5-12)

**Defeito.** `search_with_cot` fazia `self.reranker.rerank(query, raw)`, mas a
assinatura é `rerank(results, query, top_k=...)`. Além disso, o `search()`
herdado já aplicava o mesmo reranker. O rerank acontecia duas vezes, e uma
delas com os argumentos trocados.

**Correção** (`rag/cot_retriever.py`): busca com `use_reranking=False` e
reordena uma única vez com `rerank(raw, query, top_k=top_k)`, contra a query
**original** (a busca usa a expandida).

**Teste:** `tests/test_cot_reranker_call.py`.

- **Antes da correção:** falhou com 2 chamadas, a segunda com `results` e
  `query` invertidos.

**Suíte completa:** ruff limpo. unit 1459 passaram, integration 138
passaram, suíte completa 1637 passaram e 10 foram pulados. Status 0 nos três.
