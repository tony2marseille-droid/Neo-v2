"""Détection des modèles qui gèrent déjà nativement les appels d'outils (ils sont alors simplement relayés)."""
from __future__ import annotations

import fnmatch
import logging

log = logging.getLogger("toolshim")


def matches(model: str, patterns: list) -> bool:
    """True si le nom du modèle correspond à l'un des motifs fnmatch (insensible à la casse)."""
    return any(fnmatch.fnmatch(model.lower(), p.lower()) for p in patterns)


class CapabilityDetector:
    """Priorité : emulated_models > native_models > détection Ollama (/api/show) > émulation."""

    def __init__(self, cfg, upstream):
        """Prépare le cache modèle -> bool."""
        self.cfg = cfg
        self.upstream = upstream
        self.cache: dict = {}

    async def supports_native_tools(self, model: str) -> bool:
        """True si le modèle doit être relayé tel quel, False s'il faut émuler."""
        if matches(model, self.cfg.emulated_models):
            return False
        if matches(model, self.cfg.native_models):
            return True
        if not self.cfg.auto_detect:
            return False
        if model not in self.cache:
            result = await self._probe(model)
            if result is None:
                return False
            self.cache[model] = result
        return self.cache[model]

    async def _probe(self, model: str):
        """Interroge /api/show : « tools » dans capabilities, ou .Tools dans le template. None si l'amont échoue."""
        try:
            info = await self.upstream.show(model)
        except Exception as exc:  # noqa: BLE001 - l'échec ne doit pas être mis en cache
            log.warning("détection impossible pour %s : %s", model, exc)
            return None
        caps = info.get("capabilities")
        if isinstance(caps, list):
            return "tools" in caps
        return ".Tools" in (info.get("template") or "")
