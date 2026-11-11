"""Sélection des outils les plus pertinents pour la requête (évite de noyer un petit modèle sous 63 outils)."""
from __future__ import annotations

import math
import re
import unicodedata

_TOKEN = re.compile(r"[a-z0-9]+")


def normalize_tools(tools) -> list:
    """Uniformise les outils OpenAI/Ollama en dicts {name, description, parameters}."""
    out = []
    for t in tools or []:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        if not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def tokenize(text: str) -> list:
    """Mots en minuscules sans accents, tronqués à 5 lettres (racinisation grossière FR/EN)."""
    plain = unicodedata.normalize("NFKD", (text or "").lower().replace("_", " ")).encode("ascii", "ignore").decode()
    return [w[:5] for w in _TOKEN.findall(plain) if len(w) > 1]


def _tool_tokens(tool: dict):
    """Retourne (mots du nom, mots de la description et des paramètres) d'un outil."""
    props = (tool["parameters"] or {}).get("properties", {}) or {}
    body = set(tokenize(tool["description"])) | set(tokenize(" ".join(props)))
    return set(tokenize(tool["name"])), body


def select_tools(tools: list, query: str, k: int, pinned=()) -> list:
    """Garde au plus k outils : d'abord les « épinglés » (déjà utilisés), puis les mieux notés (TF-IDF, nom x3)."""
    if len(tools) <= k:
        return list(tools)
    docs = [_tool_tokens(t) for t in tools]
    df: dict = {}
    for name_tokens, body in docs:
        for w in name_tokens | body:
            df[w] = df.get(w, 0) + 1
    q = set(tokenize(query))
    scored = []
    for i, (t, (name_tokens, body)) in enumerate(zip(tools, docs)):
        score = sum(math.log(1 + len(tools) / df[w]) * (3 if w in name_tokens else 1)
                    for w in q if w in name_tokens or w in body)
        scored.append((-score, i, t))
    scored.sort(key=lambda s: (s[0], s[1]))
    chosen = [t for t in tools if t["name"] in set(pinned)][:k]
    for _, _, t in scored:
        if len(chosen) >= k:
            break
        if t not in chosen:
            chosen.append(t)
    return chosen
