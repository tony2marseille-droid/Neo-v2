"""Extraction tolérante du JSON produit par le modèle, normalisation et validation des appels d'outils."""
from __future__ import annotations

import difflib
import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def strip_think(text: str) -> str:
    """Retire les blocs <think>…</think> des modèles à raisonnement."""
    return _THINK.sub("", text or "").strip()


def _balanced_end(text: str, start: int):
    """Index de l'accolade/crochet fermant qui équilibre celui de `start` (chaînes respectées), sinon None."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json(text: str):
    """Extrait le premier JSON d'un texte (think, blocs ``` et prose ignorés). None si rien d'exploitable."""
    cleaned = strip_think(text)
    fence = _FENCE.search(cleaned)
    candidates = [cleaned] + ([fence.group(1).strip()] if fence else [])
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            pass
    for c in candidates:
        for start in [i for i, ch in enumerate(c) if ch in "{["][:20]:
            end = _balanced_end(c, start)
            if end is not None:
                try:
                    return json.loads(c[start:end + 1])
                except ValueError:
                    continue
    return None


def normalize_envelope(obj):
    """Ramène les variantes courantes (calls, tool_calls, action/parameters, name/arguments) à (appels, réponse)."""
    reply, raw = "", []
    if isinstance(obj, list):
        raw = obj
    elif isinstance(obj, dict):
        reply = obj.get("reply") or obj.get("answer") or obj.get("response") or ""
        if isinstance(obj.get("calls"), list):
            raw = obj["calls"]
        elif isinstance(obj.get("tool_calls"), list):
            raw = obj["tool_calls"]
        elif any(k in obj for k in ("action", "name", "tool")):
            raw = [obj]
    calls = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item = item.get("function", item)
        name = item.get("name") or item.get("action") or item.get("tool")
        args = item.get("arguments", item.get("parameters", item.get("args", {})))
        if name and str(name).lower() not in ("none", "final_answer", "reply"):
            calls.append({"name": str(name), "arguments": args})
    return calls, (str(reply) if reply else "")


def resolve_tool_name(name: str, names: list):
    """Nom d'outil exact, insensible à la casse ou approchant (difflib) ; None si introuvable."""
    if name in names:
        return name
    lower = {n.lower(): n for n in names}
    if name.lower() in lower:
        return lower[name.lower()]
    close = difflib.get_close_matches(name.lower(), list(lower), n=1, cutoff=0.75)
    return lower[close[0]] if close else None


def coerce_value(value, schema):
    """Convertit value vers le type JSON-schema déclaré quand c'est sans ambiguïté ; sinon la rend telle quelle."""
    typ = schema.get("type") if isinstance(schema, dict) else None
    try:
        if typ == "integer":
            if isinstance(value, (bool, int)):
                return value
            f = float(value)
            return int(f) if f == int(f) else value
        if typ == "number":
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else float(value)
        if typ == "boolean":
            return value.strip().lower() in ("true", "1", "oui", "yes") if isinstance(value, str) else bool(value)
        if typ == "string" and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if typ in ("array", "object") and isinstance(value, str):
            return json.loads(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError : int(float("1e999")) ; valeur rendue telle quelle.
        pass
    return value


def validate_call(call: dict, tools_by_name: dict):
    """Valide un appel (nom, arguments, obligatoires, types, enums). Retourne (appel_nettoyé|None, erreurs)."""
    name = resolve_tool_name(call["name"], list(tools_by_name))
    if name is None:
        return None, [f"outil inconnu « {call['name']} » (disponibles : {', '.join(tools_by_name)})"]
    args = call.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    if not isinstance(args, dict):
        return None, [f"{name} : « arguments » doit être un objet JSON"]
    schema = tools_by_name[name]["parameters"] or {}
    props = schema.get("properties", {}) or {}
    clean, errors = {}, []
    for k, v in args.items():
        if props and k not in props:
            continue
        clean[k] = coerce_value(v, props.get(k, {}))
        enum = props[k].get("enum") if isinstance(props.get(k), dict) else None
        if enum and clean[k] not in enum:
            errors.append(f"{name}.{k} doit valoir l'un de {enum}")
    for r in schema.get("required", []) or []:
        if clean.get(r) is None:
            errors.append(f"{name} : argument obligatoire manquant « {r} »")
    return ({"name": name, "arguments": clean} if not errors else None), errors
