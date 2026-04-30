"""Composition root da aplicação.

Camada que une `domain/`, `application/`, `adapters/` e `interfaces/` em
instâncias concretas para os pontos de entrada (CLI, HTTP, MCP, demo).

Diferente de `application/`, este pacote pode importar frameworks de borda
e adapters concretos — é exatamente onde a wiring acontece.
"""
