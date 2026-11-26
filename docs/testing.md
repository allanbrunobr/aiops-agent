# Testes real-RAG: três caminhos

Os testes `-m real_rag` (`tests/real_rag/`) carregam modelos reais
(sentence-transformers, cross-encoder, faiss). Exigem torch 2.13, que só tem
wheel para linux x86_64/arm64 e macOS arm64. Escolha o caminho pela máquina:

| Onde | Comando | Observação |
|------|---------|------------|
| **CI (ubuntu-latest, x86_64)** | job `real-rag` em `.github/workflows/ci.yml` | automático a cada push; cache HF |
| **Intel-Mac (container linux/amd64)** | `scripts/real-rag-docker.sh [args pytest]` | build e execução no container; **nunca** instalar torch nativo no Intel |
| **Nativo arm64 (M5)** | `.venv/bin/python -m pytest tests/ -m real_rag -v` | direto no venv local |

## Container (Intel-Mac)

```bash
scripts/real-rag-docker.sh                 # roda pytest tests/ -m real_rag -v
scripts/real-rag-docker.sh -k roundtrip    # args extras vão ao pytest
DOCKER="docker --context m1-docker" scripts/real-rag-docker.sh   # Docker remoto
```

- `docker/real-rag.Dockerfile`: `python:3.12-slim` em `linux/amd64`,
  `uv sync --frozen --no-install-project --group test` (o projeto usa maturin;
  Rust não é necessário, o preditivo tem fallback Python).
- Cache dos modelos HF em volume nomeado `aiops-real-rag-hf` (~340MB, baixado
  uma vez).
- O exit code do script é o do pytest: exit 0 = verde.
- Fora do container, `pytest -m unit` segue verde sem torch (`importorskip`).
