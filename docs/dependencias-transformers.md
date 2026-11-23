# Dependências de ML: `transformers`, `sentence-transformers` e `torch`

**Atualização de 23/11/2026 — hold RESOLVIDO.** Os bumps de segurança foram
mergeados em `main` (`3d48c0f`) e validados com RAG real num container linux x86_64
no M1. A nota original de 14/11/2026 (por que #23/#24 estavam em hold) está preservada
no fim, como histórico.

## Estado na `main` (`3d48c0f`)

| Pacote | Restrição em `pyproject.toml` e `requirements.txt` | Versão no `uv.lock` |
|---|---|---|
| `sentence-transformers` | `>=5.6.0,<6.0.0` | 5.7.0 |
| `transformers` | `>=5.10.0,<6.0.0` | 5.23.0 |
| `torch` | `>=2.13.0,<2.14.0` | 2.13.0 |

## O que foi feito (3 commits)

- `417a3a2` — sobe `sentence-transformers>=5.6` e `transformers>=5.10` juntos, regenera `uv.lock`.
- `aad6554` — fix de código: remove o kwarg `num_workers` de `SentenceTransformer.encode()`
  em `rag/indexer.py` (dois call sites). O `sentence-transformers` 5.7 removeu esse kwarg;
  `num_workers=0` (single-process) já é o default, semântica inalterada.
- `3d48c0f` — sobe `torch>=2.13,<2.14` e regenera `uv.lock` (CUDA 12 → CUDA 13 no linux).

## Descobertas que mudaram o plano

1. **`torch` não é separável.** O `transformers` 5.23 exige `torch>=2.6.0` em runtime
   (`transformers/utils/import_utils.py`, `is_torch_available()`). Com `torch` 2.4.1 ele
   desliga o PyTorch e rebenta com `NameError: name 'nn' is not defined`. Ou seja, o fix
   de `transformers`/`sentence-transformers` **obriga** a subir o `torch` — o #24 deixou de
   ser opcional. Escolheu-se `torch` 2.13 (alvo do #24) por já estar validado e limpar o
   máximo de alertas de uma vez.
2. **`transformers` 5.10.0 foi yanked** do PyPI. O lock usa 5.23.0 (stable 5.x mais recente).
3. **Quebra real de API no RAG:** `sentence-transformers` 5.7 rejeita `num_workers` no
   `encode()` (`ValueError: ... additional keyword arguments ... ['num_workers']`). Era
   exatamente a regressão que o CI verde não apanhava (não baixava modelos nem exercitava
   RAG real). Corrigida em `aad6554`.
4. **Intel macOS não roda o stack seguro.** O `torch` dropou macOS Intel após 2.2.2; não há
   wheel `torch>=2.4` para Intel Mac. A validação/CI tem de ser linux x86_64 ou Mac arm64.

## Validação (M1, container linux x86_64, config lockada)

`uv sync --frozen --all-groups` num venv fresh (não install exploratório):
`torch` 2.13.0+cu130, `transformers` 5.23.0, `sentence-transformers` 5.7.0, `faiss-cpu` 1.16.0 (CPU).

- Suíte unit: **1836 passed / 0 fail**.
- Integration + snapshot: **152 passed / 0 fail**.
- RAG real: `all-MiniLM-L6-v2` encode `(N,384)` normalizado; FAISS `IndexFlatIP` vizinho
  correto; CrossEncoder `qnli-distilroberta-base` relevante 0.91 / irrelevante 0.08.
- Smoke real `main.py --pipeline-v1 --no-llm --rebuild-rag`: exit 3 (approval-needed,
  fail-closed seguro), postmortem escrito, ~10s.
- `ruff check .` limpo; `test_packaging_consistency` 5 passed.

## Alertas Dependabot

Fecham **12 GHSA únicos** (todos os corrigíveis): `sentence-transformers` crit
`GHSA-jhr6-gm9c-rqjv`; 4 `transformers`; 7 `torch` (crit/high/low/medium).

Restam **4 GHSA sem fix upstream** (não há versão corrigida; ficam abertos
independentemente do bump):

- `torch` `GHSA-c678-jfcj-6jmf` (low), `GHSA-x3gm-94wq-g975` (low), `GHSA-f4hp-rmr7-r7v8` (medium)
- `transformers` `GHSA-x9r9-c232-4q39` (high)

Os PRs Dependabot #23 e #24 ficam superseded por este fix.

---

## Histórico — nota de 14/11/2026 (por que #23/#24 estavam em hold)

Registra por que os PRs #23 e #24 do Dependabot não foram mergeados na altura e o que
bloqueava a correção de segurança do `transformers`.

### Estado na `main` então (`3195fc2`)

| Pacote | Restrição | Versão no `uv.lock` |
|---|---|---|
| `sentence-transformers` | `>=2.2.0,<4.0.0` | 3.4.1 |
| `transformers` | `>=4.41.0,<5.0.0` | 4.57.6 |
| `torch` | `>=2.1.0,<2.5.0` | 2.4.1 |

### A correção de segurança do `transformers` estava bloqueada

A faixa afetada mais alta era `< 5.10.0`, então a correção exigia `transformers` 5.10+.
O Dependabot tentou duas vezes em 11/11/2026 (14:56 e 15:06 UTC); ambas falharam com
`dependency_file_not_resolvable`:

- o projeto exigia `sentence-transformers>=2.2.0,<4.0.0`;
- todas as versões de `sentence-transformers` de 2.2.0 a 3.4.1 exigem `transformers<5.0.0`;
- logo `transformers==5.10.1` não tinha solução com essa restrição; e o projeto declarava
  `transformers<5.0.0`.

### Por que #23 e #24 não foram mergeados então

Decisão do Allan: não mergear nenhum dos dois, nem fechá-los ou comentá-los no GitHub.

- **#23** (`sentence-transformers` 3.4.1 → 5.6.0) só mudava a restrição para `<6.0.0`;
  mantinha `transformers<5.0.0`, então sozinho não trazia a correção. Mudar a versão maior
  do `sentence-transformers` também muda a API usada pelo RAG (`rag/indexer.py`, `rag/retriever.py`).
- **#24** (`torch` 2.4.1 → 2.13.0) mudava a restrição para `<2.14.0` e acrescentava 18 pacotes
  ao `uv.lock` (CUDA 13 no lugar das CUDA 12). Não tocava no `transformers`.

O CI dos dois passou, mas o CI do GitHub não baixa modelos nem exercita o RAG real — esse
resultado não mostrava que as atualizações funcionam.

### Caminho para destravar (executado em 23/11/2026)

1. Num único PR, subir juntos `sentence-transformers` (versão que aceite `transformers` 5) e
   `transformers` (`>=5.10`), regenerar `uv.lock`. ✓ (`417a3a2`)
2. Rodar a suíte completa com RAG real (modelo baixado + FAISS `--rebuild-rag`) no M1, não no
   M5. ✓ (container linux x86_64 no M1; revelou e corrigiu a quebra `num_workers`)
3. Tratar o `torch` (#24) após decidir CUDA 13 vs CPU. ✓ — acabou acoplado ao fix do
   `transformers` (exige `torch>=2.6`); subiu-se para 2.13. CUDA 13 só pesa no linux CI;
   Mac arm64 é CPU/MPS. ✓ (`3d48c0f`)