"""Entrypoint do app HTTP (create_app): `python -m interfaces.http`.

Serve health, A2A (exige Bearer JWT), o servidor OAuth (só com segredo
configurado) e, se `mcp.enabled` e `transport: sse`, o MCP via SSE. Escuta em
127.0.0.1 por padrão. Equivalente: `uvicorn interfaces.http.app:create_app
--factory --host 127.0.0.1 --port 47893` (sem `--config`).

P3-2 (docs/analise-20261109.md, A5): antes nenhum processo servia create_app.
"""
from __future__ import annotations

import argparse

DEFAULT_PORT = 47893  # 47890 webhook, 47891 demo, 47892 mock de Slack


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AIOps Agent — HTTP/A2A/OAuth/MCP-SSE")
    parser.add_argument("--host", default="127.0.0.1", help="padrão: 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"padrão: {DEFAULT_PORT}")
    parser.add_argument("--config", default=None,
                        help="YAML de configuração (padrão: config/defaults.yaml)")
    args = parser.parse_args(argv)

    from config.env import load_local_env

    load_local_env()  # antes da config: AIOPS_* podem vir do .env.local

    import uvicorn

    from config.loader import load_config
    from core.logging import configure_logging
    from interfaces.http.app import create_app

    configure_logging()
    app = create_app(load_config(config_path=args.config))
    # log_config=None: os logs do uvicorn passam pelo handler configurado
    # (JSON, com redação de segredos) em vez da config própria dele.
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)


if __name__ == "__main__":
    main()
