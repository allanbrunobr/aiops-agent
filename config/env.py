"""Carga única de `.env.local` e `.env` para os pontos de entrada (M3/F5-10, F5-3).

Antes havia cinco parsers artesanais, um deles divergente, e nenhum tratava
aspas nem `export`. Aqui o parsing fica com o python-dotenv.

Precedência: variável já presente no processo > `.env.local` > `.env`.
Nada é sobrescrito (`override=False`) — `.env.local` são overrides locais e
vence o `.env` compartilhado; um token exportado no shell sempre vence.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# Raiz padrão de carga. O conftest da suíte redireciona este atributo para um
# diretório vazio, para que nenhum teste leia o .env/.env.local do checkout.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Ordem de carga = precedência entre arquivos (primeiro carregado vence,
# pois override=False): .env.local antes de .env.
ENV_FILES: tuple[str, ...] = (".env.local", ".env")


def project_root() -> Path:
    """Raiz real do repositório (a pasta que contém ``config/``).

    Independente de ``REPO_ROOT``: redirecionamentos de teste não afetam
    quem só precisa localizar arquivos do projeto (defaults.yaml, main.py).
    """
    return Path(__file__).resolve().parent.parent


def load_local_env(root: Path | None = None) -> bool:
    """Carrega `<root>/.env.local` e depois `<root>/.env`, sem sobrescrever.

    `root` padrão: ``REPO_ROOT`` (a raiz do repositório, não o diretório
    corrente), para que a CLI, a demo e os scripts de infra leiam o mesmo
    arquivo de onde quer que sejam chamados.

    Returns:
        True se ao menos um dos arquivos existia; False se nenhum existe
        (ausência dos arquivos não é erro).
    """
    base = Path(root) if root is not None else REPO_ROOT
    found = False
    for name in ENV_FILES:
        path = base / name
        if path.is_file():
            load_dotenv(path, override=False)
            found = True
    return found
