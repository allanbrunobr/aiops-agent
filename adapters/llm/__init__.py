"""Adapters de provider LLM.

Conforme ADR 0002, todo import direto de ``anthropic`` no projeto deve estar
contido neste pacote. Outros modulos consomem o adapter via factory ou via
DI do tipo concreto ``AnthropicLLMClient``.
"""
