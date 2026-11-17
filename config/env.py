"""Carregamento único de ``.env`` / ``.env.local`` (F5-3).

Antes, cinco entrypoints (CLI V0, CLI V1, demo, webhook server e o script de
teste do Slack) tinham cada um seu parser manual de ``.env.local`` — com
diferenças sutis entre si (strip ou não, tratamento de aspas, raiz relativa
ao cwd ou ao arquivo). Agora todos chamam :func:`load_local_env`, que delega
ao ``python-dotenv`` já presente em ``requirements.txt``.

Precedência (preservada do CLI V0, que fazia ``load_dotenv()`` + ``setdefault``):
ambiente do processo > ``.env`` > ``.env.local``. Nenhum arquivo sobrescreve
uma variável já definida — um token exportado no shell sempre vence.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

ENV_FILES: tuple[str, ...] = (".env", ".env.local")


def project_root() -> Path:
    """Raiz do repositório (a pasta que contém ``config/``)."""
    return Path(__file__).resolve().parent.parent


def load_local_env(root: Path | None = None) -> list[Path]:
    """Carrega ``.env`` e ``.env.local`` da raiz, sem sobrescrever o ambiente.

    Args:
        root: Diretório onde procurar os arquivos. Default: raiz do repositório.

    Returns:
        Lista dos arquivos efetivamente carregados (vazia se nenhum existe).
    """
    base = Path(root) if root is not None else project_root()
    loaded: list[Path] = []
    for name in ENV_FILES:
        path = base / name
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded
