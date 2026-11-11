"""Configuration du proxy : config.json puis surcharge par variables d'environnement TOOLSHIM_*."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    """Paramètres du proxy (valeurs par défaut adaptées à un Ollama local)."""

    upstream_url: str = "http://127.0.0.1:11434"
    host: str = "127.0.0.1"
    port: int = 8330
    native_models: list = field(default_factory=list)
    emulated_models: list = field(default_factory=list)
    auto_detect: bool = True
    max_tools_in_prompt: int = 8
    max_retries: int = 2
    max_calls_per_turn: int = 4
    request_timeout: float = 300.0
    constrained_output: bool = True
    temperature: float = 0.0
    disable_thinking: bool = False
    log_level: str = "INFO"


def _coerce(raw, current):
    """Convertit une valeur brute (texte d'environnement ou JSON) vers le type du champ par défaut."""
    if isinstance(current, bool):
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "oui")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return raw if isinstance(raw, list) else [s.strip() for s in str(raw).split(",") if s.strip()]
    return str(raw)


def load_config(path: str | None = None) -> Config:
    """Charge config.json (optionnel) puis applique les variables TOOLSHIM_<CHAMP>."""
    cfg = Config()
    candidate = Path(path or os.environ.get("TOOLSHIM_CONFIG", "config.json"))
    data = json.loads(candidate.read_text(encoding="utf-8")) if candidate.is_file() else {}
    for f in fields(cfg):
        env = os.environ.get(f"TOOLSHIM_{f.name.upper()}")
        raw = env if env is not None else data.get(f.name)
        if raw is not None:
            setattr(cfg, f.name, _coerce(raw, getattr(cfg, f.name)))
    return cfg
