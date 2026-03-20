from __future__ import annotations
"""
Servidor FastAPI para o módulo de demonstração AIOps.

Expõe endpoints REST para controle do cenário e WebSocket para
streaming de TickFrames ao dashboard em tempo real.

Uso:
    python demo/server.py [--port 8080]
"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from demo.engine import ScenarioEngine
from demo.scenarios import DemoScenarioConfig, SCENARIOS, get_scenarios_list

logger = logging.getLogger("demo.server")

# Diretório de arquivos estáticos
_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AIOps Demo Server")

# Serve arquivos estáticos em /demo/static/
app.mount("/demo/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Estado global do servidor
_engine: ScenarioEngine | None = None
_tick_task: asyncio.Task | None = None
_ws_connections: list[WebSocket] = []
_current_corpus_name: str = "payment-pool-exhaustion"


# ------------------------------------------------------------------ #
# Endpoints de controle                                               #
# ------------------------------------------------------------------ #

@app.get("/demo/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    """Serve o dashboard HTML principal."""
    index_path = _STATIC_DIR / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/demo/scenarios")
async def list_scenarios() -> JSONResponse:
    """Retorna lista de cenários disponíveis com metadados."""
    return JSONResponse(content=get_scenarios_list())


@app.post("/demo/start/{scenario_name}")
async def start_scenario(scenario_name: str) -> JSONResponse:
    """Inicia o cenário especificado."""
    global _engine, _tick_task, _current_corpus_name

    if scenario_name not in SCENARIOS:
        return JSONResponse(
            status_code=404,
            content={"error": f"Cenário '{scenario_name}' não encontrado."},
        )

    # Para task anterior se existir
    if _tick_task and not _tick_task.done():
        _tick_task.cancel()
        try:
            await _tick_task
        except asyncio.CancelledError:
            pass

    # Constrói corpus e inicia engine
    _current_corpus_name = scenario_name
    scenario_meta = SCENARIOS[scenario_name]
    corpus = scenario_meta["corpus_builder"]()

    config = DemoScenarioConfig(
        enable_llm=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )
    _engine = ScenarioEngine(corpus, scenario_name=scenario_name, config=config)
    _engine.start()

    # Inicia loop de tick em background
    _tick_task = asyncio.create_task(_tick_loop())

    return JSONResponse(content={"status": "started", "scenario": scenario_name})


@app.post("/demo/stop")
async def stop_scenario() -> JSONResponse:
    """Para o cenário e volta para IDLE."""
    global _tick_task
    if _tick_task and not _tick_task.done():
        _tick_task.cancel()
        try:
            await _tick_task
        except asyncio.CancelledError:
            pass
    if _engine:
        _engine.stop()
    return JSONResponse(content={"status": "stopped"})


@app.post("/demo/reset")
async def reset_scenario() -> JSONResponse:
    """Para e reinicia o cenário atual do zero."""
    await stop_scenario()
    if _current_corpus_name:
        return await start_scenario(_current_corpus_name)
    return JSONResponse(content={"status": "reset", "note": "no scenario was active"})


@app.get("/demo/status")
async def get_status() -> JSONResponse:
    """Retorna estado atual do cenário."""
    if _engine is None:
        return JSONResponse(content={"phase": "IDLE", "scenario": None})
    return JSONResponse(content={
        "phase": _engine.phase.value,
        "scenario": _engine.scenario_name,
        "is_running": _engine.is_running,
        "comparison_active": _engine.comparison_active,
    })


@app.patch("/demo/speed")
async def update_speed(body: dict) -> JSONResponse:
    """Atualiza o speed_factor em tempo real sem reiniciar o cenário.

    Body: {"speed_factor": 8.0}
    """
    speed_factor = body.get("speed_factor")
    if speed_factor is None or not isinstance(speed_factor, (int, float)):
        return JSONResponse(status_code=400, content={"error": "speed_factor inválido"})
    if _engine:
        _engine.update_speed(float(speed_factor))
    return JSONResponse(content={"status": "ok", "speed_factor": float(speed_factor)})


@app.post("/demo/compare/enable")
async def enable_comparison() -> JSONResponse:
    """Ativa modo de comparação (dois motores: preditivo vs tradicional)."""
    if _engine is None:
        return JSONResponse(status_code=400, content={"error": "Nenhum cenário ativo."})
    scenario_meta = SCENARIOS.get(_current_corpus_name)
    if not scenario_meta:
        return JSONResponse(status_code=400, content={"error": "Corpus não encontrado."})
    corpus = scenario_meta["corpus_builder"]()
    _engine.enable_comparison(corpus)
    return JSONResponse(content={"status": "comparison_enabled"})


@app.post("/demo/compare/disable")
async def disable_comparison() -> JSONResponse:
    """Desativa modo de comparação."""
    if _engine:
        _engine.disable_comparison()
    return JSONResponse(content={"status": "comparison_disabled"})


@app.get("/demo/postmortem")
async def get_postmortem() -> Response:
    """Retorna o postmortem markdown gerado quando o cenário chega a RESOLVED.

    Retorna 204 No Content se o cenário ainda não chegou a RESOLVED.
    """
    if _engine is None or _engine.last_postmortem is None:
        return Response(status_code=204)
    return PlainTextResponse(content=_engine.last_postmortem, media_type="text/markdown")


# ------------------------------------------------------------------ #
# WebSocket                                                           #
# ------------------------------------------------------------------ #

@app.websocket("/demo/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """WebSocket de streaming de TickFrames para o dashboard."""
    await ws.accept()
    _ws_connections.append(ws)
    try:
        while True:
            # Mantém conexão viva — tick é enviado pelo loop separado
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.remove(ws)


async def _broadcast(payload: dict | Any) -> None:
    """Envia payload JSON para todos os clientes WebSocket conectados."""
    if not _ws_connections:
        return
    if hasattr(payload, "dict"):
        data = json.dumps(payload.dict())
    else:
        data = json.dumps(payload)

    dead: list[WebSocket] = []
    for ws in list(_ws_connections):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_connections:
            _ws_connections.remove(ws)


async def _tick_loop() -> None:
    """Loop assíncrono que gera ticks a 1fps e transmite TickFrames."""
    if _engine is None:
        return
    try:
        while _engine.is_running:
            frame = _engine.tick()
            await _broadcast(frame)
            await asyncio.sleep(1.0)
        # Envia frame final (RESOLVED/IDLE)
        frame = _engine.tick()
        await _broadcast(frame)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("Erro no tick loop: %s", exc)


# ------------------------------------------------------------------ #
# Entry point                                                         #
# ------------------------------------------------------------------ #

def main() -> None:
    """Ponto de entrada para execução direta via CLI."""
    parser = argparse.ArgumentParser(description="AIOps Demo Server")
    parser.add_argument("--port", type=int, default=8080, help="Porta HTTP (padrão: 8080)")
    parser.add_argument("--host", default="0.0.0.0", help="Host de escuta")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger.info("Demo server ready on http://%s:%d/demo/", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
