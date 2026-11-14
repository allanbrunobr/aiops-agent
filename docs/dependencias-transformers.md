# Dependências de ML: `transformers`, `sentence-transformers` e `torch`

Nota de 14/11/2026. Ela registra por que os PRs #23 e #24 do Dependabot não foram
mergeados e o que bloqueia a correção de segurança do `transformers`.

## Estado na `main` (`3195fc2`)

| Pacote | Restrição em `pyproject.toml` e `requirements.txt` | Versão no `uv.lock` |
|---|---|---|
| `sentence-transformers` | `>=2.2.0,<4.0.0` | 3.4.1 |
| `transformers` | `>=4.41.0,<5.0.0` | 4.57.6 |
| `torch` | `>=2.1.0,<2.5.0` | 2.4.1 |

## A correção de segurança do `transformers` está bloqueada

O GitHub tem alertas de segurança abertos para o `transformers`. A faixa afetada mais
alta é `< 5.10.0`, então a correção exige `transformers` 5.10 ou mais novo.

O Dependabot tentou essa atualização duas vezes, em 11/11/2026 às 14:56 e às 15:06 UTC.
As duas falharam com `dependency_file_not_resolvable`:

- o projeto exige `sentence-transformers>=2.2.0,<4.0.0`;
- todas as versões de `sentence-transformers` de 2.2.0 a 3.4.1 exigem `transformers<5.0.0`;
- logo, `transformers==5.10.1` não tem solução com essa restrição.

Além disso, o próprio projeto declara `transformers<5.0.0`.

## Por que #23 e #24 não foram mergeados

Decisão do Allan: não mergear nenhum dos dois, nem fechá-los ou comentá-los no GitHub.

- **#23** (`sentence-transformers` 3.4.1 → 5.6.0) só muda a restrição do
  `sentence-transformers` para `<6.0.0`. Ele mantém `transformers<5.0.0`, então sozinho não
  traz a correção de segurança. Mudar uma versão maior do `sentence-transformers` também
  muda a API usada pelo RAG (`rag/indexer.py`, `rag/retriever.py`).
- **#24** (`torch` 2.4.1 → 2.13.0) muda a restrição para `<2.14.0` e acrescenta 18 pacotes
  ao `uv.lock`, entre eles as bibliotecas CUDA 13 (`cuda-toolkit`, `nvidia-cublas` e outras),
  no lugar das CUDA 12. Ele não toca no `transformers`.

O CI dos dois passou. O CI do GitHub não baixa modelos nem exercita o RAG real, então
esse resultado não mostra que as atualizações funcionam.

## Caminho para destravar

1. Num único PR, subir juntos `sentence-transformers` (para uma versão que aceite
   `transformers` 5) e `transformers` (`>=5.10`), e regenerar o `uv.lock` com `uv lock`.
2. Rodar a suíte completa com o RAG real (modelo baixado e índice FAISS reconstruído com
   `--rebuild-rag`) no M1. É um trabalho pesado e não deve rodar no M5.
3. Tratar o `torch` (#24) à parte, depois de decidir se a imagem de CI e as máquinas
   aceitam CUDA 13 ou se o projeto passa a usar só o índice CPU.
