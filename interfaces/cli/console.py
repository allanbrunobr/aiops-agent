"""Instância única do Rich ``Console`` para toda a CLI.

Um só ``Console`` garante largura/estilo consistentes entre os módulos e
preserva a ordem de impressão. Rich resolve ``sys.stdout`` no momento do
``print``, então ``capsys``/``redirect_stdout`` nos testes continuam
funcionando mesmo com a instância criada em import-time.
"""

from __future__ import annotations

from rich.console import Console

console = Console()

__all__ = ["console"]
