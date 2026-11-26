#!/usr/bin/env bash
# scripts/real-rag-docker.sh — roda `pytest -m real_rag` num container linux/amd64.
#
# Caminho para Intel-Mac (torch 2.13 sem wheel nativa): build e execução ficam
# no container; nada de torch é instalado no host. Veja docs/testing.md.
#
# Uso:
#   scripts/real-rag-docker.sh                      # pytest tests/ -m real_rag -v
#   scripts/real-rag-docker.sh -k roundtrip         # args extras vão para o pytest
#
# Variáveis: IMAGE (default aiops-real-rag), DOCKER (default docker; ex.: "docker --context m1-docker").
# O exit code do container é o do pytest (set -e propaga).
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-aiops-real-rag}"
DOCKER="${DOCKER:-docker}"

# shellcheck disable=SC2086  # DOCKER pode conter flags (--context ...)
$DOCKER build --platform linux/amd64 -f docker/real-rag.Dockerfile -t "$IMAGE" .

# Cache HF em volume nomeado: evita re-baixar ~340MB a cada execução.
# shellcheck disable=SC2086
$DOCKER run --rm --platform linux/amd64 -v aiops-real-rag-hf:/work/.hf-cache "$IMAGE" \
    python -m pytest tests/ -m real_rag -v "$@"
