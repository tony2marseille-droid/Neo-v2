"""Serveur FastAPI : intercepte /v1/chat/completions et /api/chat, relaie tout le reste vers Ollama."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .capabilities import CapabilityDetector
from .config import load_config
from .engine import Emulator
from .formats import ollama_ndjson, openai_sse, to_ollama, to_openai
from .upstream import Upstream

log = logging.getLogger("toolshim")
HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "accept-encoding"}


async def forward(request: Request, path: str, body: bytes | None = None):
    """Relaie la requête telle quelle vers l'amont et renvoie sa réponse en flux (502 si injoignable)."""
    content = body if body is not None else await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    try:
        up = await request.app.state.upstream.open_stream(
            request.method, path, content, headers, dict(request.query_params))
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"amont injoignable : {exc}"}, status_code=502)
    return StreamingResponse(up.aiter_raw(), status_code=up.status_code,
                             media_type=up.headers.get("content-type"), background=BackgroundTask(up.aclose))


async def handle_chat(request: Request, flavor: str):
    """Relaie si pas d'outils ou modèle natif ; sinon émule le tool calling et répond dans le dialecte du client."""
    raw_body = await request.body()
    try:
        body = json.loads(raw_body)
    except ValueError:
        return JSONResponse({"error": "JSON invalide"}, status_code=400)
    state = request.app.state
    model = body.get("model", "")
    if body.get("tool_choice") == "none" and body.get("tools"):
        body.pop("tools")
        raw_body = json.dumps(body).encode()
    if not body.get("tools") or await state.detector.supports_native_tools(model):
        return await forward(request, request.url.path, raw_body)
    stream = body.get("stream", flavor == "ollama")
    try:
        result = await state.emulator.run(body)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"amont indisponible : {exc}"}, status_code=502)
    log.info("émulation %s : %d appel(s) en %d tentative(s)", model, len(result.calls), result.attempts)
    if flavor == "openai":
        resp = to_openai(model, result.calls, result.reply, result.raw)
        return StreamingResponse(openai_sse(resp), media_type="text/event-stream") if stream else JSONResponse(resp)
    resp = to_ollama(model, result.calls, result.reply, result.raw)
    return StreamingResponse(ollama_ndjson(resp), media_type="application/x-ndjson") if stream else JSONResponse(resp)


def create_app(cfg=None, upstream=None, detector=None) -> FastAPI:
    """Fabrique l'application (upstream et detector injectables pour les tests)."""
    cfg = cfg or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Instancie amont, détecteur et moteur au démarrage ; ferme le client à l'arrêt."""
        app.state.cfg = cfg
        app.state.upstream = upstream or Upstream(cfg)
        app.state.detector = detector or CapabilityDetector(cfg, app.state.upstream)
        app.state.emulator = Emulator(cfg, app.state.upstream)
        yield
        await app.state.upstream.aclose()

    app = FastAPI(title="toolshim", lifespan=lifespan)

    @app.get("/toolshim/health")
    async def health(request: Request):
        """État du proxy : amont configuré et cache de détection des capacités."""
        return {"status": "ok", "upstream": cfg.upstream_url, "native_cache": request.app.state.detector.cache}

    @app.get("/health")
    async def health_simple():
        """Sonde de supervision minimale (watchdog)."""
        return {"status": "ok", "service": "toolshim"}

    @app.post("/v1/chat/completions")
    async def openai_chat(request: Request):
        """Endpoint compatible OpenAI."""
        return await handle_chat(request, "openai")

    @app.post("/api/chat")
    async def ollama_chat(request: Request):
        """Endpoint natif Ollama."""
        return await handle_chat(request, "ollama")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
    async def passthrough(path: str, request: Request):
        """Relais transparent de toutes les autres routes (tags, embeddings, pull…)."""
        return await forward(request, request.url.path)

    return app
