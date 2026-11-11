"""Prompt d'émulation, schéma JSON contraint et conversion de l'historique (tool_calls -> texte)."""
from __future__ import annotations

import json

INSTRUCTIONS = """Tu peux utiliser des outils.
Outils disponibles (* = argument obligatoire) :
{catalog}

Réponds TOUJOURS par un unique objet JSON, sans aucun texte autour :
{{"calls": [{{"name": "<outil>", "arguments": {{...}}}}], "reply": "<texte>"}}
Règles :
- Si un outil est nécessaire, remplis "calls" (au plus {max_calls} appels) et laisse "reply" vide.
- Sinon "calls" vaut [] et "reply" contient ta réponse finale à l'utilisateur.
- N'utilise que les outils et arguments listés. N'invente aucune valeur manquante : demande-la dans "reply".
- Quand un résultat d'outil est fourni, exploite-le pour répondre ; n'appelle un autre outil que si c'est indispensable."""


def build_envelope_schema(names: list, max_calls: int) -> dict:
    """Schéma JSON imposé au modèle (décodage contraint) : {calls:[{name,arguments}], reply}."""
    return {
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "maxItems": max_calls,
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "enum": list(names)}, "arguments": {"type": "object"}},
                    "required": ["name", "arguments"],
                },
            },
            "reply": {"type": "string"},
        },
        "required": ["calls", "reply"],
    }


def render_tool(tool: dict) -> str:
    """Décrit un outil : signature typée (* = obligatoire), description, puis description des arguments."""
    schema = tool["parameters"] or {}
    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    parts = []
    for pname, p in props.items():
        p = p if isinstance(p, dict) else {}
        typ = "|".join(map(str, p["enum"])) if "enum" in p else p.get("type", "any")
        parts.append(f"{pname}: {typ}{'*' if pname in required else ''}")
    line = f"- {tool['name']}({', '.join(parts)})"
    if tool["description"]:
        line += f" : {tool['description'].strip()[:200]}"
    details = [f"    {n}: {p['description'].strip()[:100]}"
               for n, p in props.items() if isinstance(p, dict) and p.get("description")]
    return "\n".join([line] + details)


def build_system_prompt(original: str, tools: list, max_calls: int, extra_rule: str | None = None) -> str:
    """Assemble le prompt système d'origine, le catalogue d'outils et les règles de format."""
    text = INSTRUCTIONS.format(catalog="\n".join(render_tool(t) for t in tools), max_calls=max_calls)
    if extra_rule:
        text += f"\n- {extra_rule}"
    return f"{original}\n\n{text}" if original else text


def flatten_content(content):
    """Retourne (texte, images base64) depuis un contenu str ou une liste de parties OpenAI."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(part.get("text", ""))
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and "," in url:
                images.append(url.split(",", 1)[1])
    return "\n".join(texts), images


def args_dict(raw) -> dict:
    """Arguments d'appel en dict (OpenAI les fournit sous forme de chaîne JSON)."""
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def convert_messages(messages: list):
    """Transforme l'historique (tool_calls, rôle tool) en dialogue texte pur. Retourne (system, messages)."""
    system_parts, out, id_to_name = [], [], {}
    for m in messages:
        role = m.get("role")
        text, images = flatten_content(m.get("content"))
        if role == "system":
            system_parts.append(text)
        elif role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                if tc.get("id"):
                    id_to_name[tc["id"]] = fn.get("name", "")
                calls.append({"name": fn.get("name", ""), "arguments": args_dict(fn.get("arguments"))})
            out.append({"role": "assistant", "content": json.dumps({"calls": calls, "reply": text}, ensure_ascii=False)})
        elif role == "assistant":
            out.append({"role": "assistant", "content": json.dumps({"calls": [], "reply": text}, ensure_ascii=False)})
        elif role == "tool":
            name = m.get("name") or m.get("tool_name") or id_to_name.get(m.get("tool_call_id"), "outil")
            out.append({"role": "user", "content": f"[Résultat de l'outil {name}]\n{text}"})
        else:
            msg = {"role": role or "user", "content": text}
            if not images and isinstance(m.get("images"), list):
                # Ollama natif : images au niveau du message (base64 brut).
                images = [x for x in m["images"] if isinstance(x, str)]
            if images:
                msg["images"] = images
            out.append(msg)
    return "\n\n".join(p for p in system_parts if p), out


def used_tool_names(messages: list) -> list:
    """Noms des outils déjà appelés dans l'historique (gardés dans la sélection)."""
    return [tc.get("function", {}).get("name", "")
            for m in messages if m.get("role") == "assistant" for tc in (m.get("tool_calls") or [])]


def last_user_text(messages: list) -> str:
    """Texte du dernier message utilisateur réel (sert de requête à la sélection d'outils)."""
    for m in reversed(messages):
        if m.get("role") == "user" and not str(m.get("content", "")).startswith("[Résultat de l'outil"):
            return str(m.get("content", ""))
    return ""
