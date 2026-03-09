from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.webhook.schemas import ProblemDetail


class WebhookError(Exception):
    """Excecao que encapsula um ProblemDetail RFC 7807.

    Lancada em qualquer ponto do pipeline de validacao do webhook.
    Capturada pelo handler global e convertida em resposta JSON.
    """

    def __init__(self, problem: ProblemDetail) -> None:
        self.problem = problem
        super().__init__(problem.detail)


async def webhook_error_handler(
    request: Request,
    exc: WebhookError,
) -> JSONResponse:
    """Converte WebhookError em resposta RFC 7807 application/problem+json."""
    return JSONResponse(
        status_code=exc.problem.status,
        content=exc.problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def register_error_handlers(app: FastAPI) -> None:
    """Registra handlers de erro RFC 7807 no app FastAPI."""
    app.add_exception_handler(WebhookError, webhook_error_handler)
