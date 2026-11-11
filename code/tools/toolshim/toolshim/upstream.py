"""Client HTTP vers le serveur LLM réel (Ollama)."""
from __future__ import annotations

import httpx


class Upstream:
    """Enveloppe httpx : appels chat non streamés, /api/show et relais brut en flux."""

    def __init__(self, cfg):
        """Crée le client httpx pointant sur cfg.upstream_url."""
        self.client = httpx.AsyncClient(
            base_url=cfg.upstream_url, timeout=httpx.Timeout(cfg.request_timeout, connect=10.0))

    async def chat(self, payload: dict) -> dict:
        """POST /api/chat (stream=false) et renvoie le JSON de réponse."""
        r = await self.client.post("/api/chat", json=payload)
        r.raise_for_status()
        return r.json()

    async def show(self, model: str) -> dict:
        """POST /api/show : métadonnées et capacités du modèle."""
        r = await self.client.post("/api/show", json={"model": model})
        r.raise_for_status()
        return r.json()

    async def open_stream(self, method: str, path: str, content: bytes, headers: dict, params: dict):
        """Ouvre une requête brute vers l'amont et renvoie la réponse httpx non consommée (flux)."""
        req = self.client.build_request(method, path, content=content, headers=headers, params=params)
        return await self.client.send(req, stream=True)

    async def aclose(self) -> None:
        """Ferme le client HTTP."""
        await self.client.aclose()
