# Validação real-RAG (modelos HF reais) em linux/amd64.
# Existe para o Intel-Mac, onde torch 2.13 não tem wheel nativa: tudo roda
# dentro do container, nenhum passo instala torch no host.
# Uso: scripts/real-rag-docker.sh [args extras do pytest]
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/work/.hf-cache \
    PATH="/work/.venv/bin:${PATH}"

WORKDIR /work

RUN pip install --no-cache-dir uv

# Camada de dependências (cache): o projeto usa maturin/Rust como build-backend,
# então não instalamos o próprio pacote — só as deps travadas + grupo test.
# O preditivo tem fallback Python (pipeline/metric_predictor.py), Rust é dispensável.
COPY pyproject.toml uv.lock requirements.txt ./
RUN uv sync --frozen --no-install-project --group test

COPY . .

CMD ["python", "-m", "pytest", "tests/", "-m", "real_rag", "-v"]
