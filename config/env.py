"""Carga única de `.env.local` e `.env` para os pontos de entrada (M3/F5-10).

Antes havia cinco parsers artesanais, um deles divergente, e nenhum tratava
aspas nem `export`. Aqui o parsing fica com o python-dotenv.

Precedência: variável já presente no processo > `.env.local` > `.env`.
Nada é sobrescrito (`override=False`).
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_local_env(root: Path | None = None) -> bool:
    """Carrega `<root>/.env.local` e depois `<root>/.env`, sem sobrescrever.

    `root` padrão: a raiz do repositório (não o diretório corrente), para que a
    CLI, a demo e os scripts de infra leiam o mesmo arquivo de onde quer que
    sejam chamados. Devolve True se ao menos um dos arquivos existia.
    """
    base = root if root is not None else REPO_ROOT
    found = False
    for name in (".env.local", ".env"):
        path = base / name
        if path.is_file():
            load_dotenv(path, override=False)
            found = True
    return found
