"""Camada de aplicacao — orquestra casos de uso e define portas de saida.

Esta camada depende de ``domain/`` e expõe contratos (``ports/``) consumidos
pela camada de adapters. Não importa nada de ``adapters/`` nem de frameworks
de borda (FastAPI, MCP, Anthropic SDK).
"""
