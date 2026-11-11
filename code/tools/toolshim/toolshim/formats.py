"""Formatage des réponses émulées au dialecte du client (OpenAI ou Ollama), en bloc ou en flux."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone


def new_call_id() -> str:
    """Identifiant d'appel d'outil au format OpenAI (call_xxx)."""
    return "call_" + uuid.uuid4().hex[:24]


def _usage(raw: dict):
    """Retourne (tokens prompt, tokens générés) depuis la réponse Ollama brute."""
    return int(raw.get("prompt_eval_count") or 0), int(raw.get("eval_count") or 0)


def to_openai(model: str, calls: list, reply: str, raw: dict) -> dict:
    """Construit un chat.completion OpenAI (arguments sérialisés en chaîne JSON, finish_reason tool_calls)."""
    message = {"role": "assistant", "content": reply or None}
    if calls:
        message["tool_calls"] = [{
            "id": new_call_id(), "type": "function",
            "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
        } for c in calls]
    p, e = _usage(raw)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": p, "completion_tokens": e, "total_tokens": p + e},
    }


def to_ollama(model: str, calls: list, reply: str, raw: dict) -> dict:
    """Construit une réponse /api/chat Ollama (arguments en objet)."""
    message = {"role": "assistant", "content": reply or ""}
    if calls:
        message["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
    p, e = _usage(raw)
    return {
        "model": model, "created_at": datetime.now(timezone.utc).isoformat(), "message": message,
        "done": True, "done_reason": "stop", "prompt_eval_count": p, "eval_count": e,
    }


def openai_sse(resp: dict):
    """Rejoue une réponse OpenAI complète en flux SSE : un delta, la fin, puis [DONE]."""
    choice = resp["choices"][0]
    msg = choice["message"]
    base = {"id": resp["id"], "object": "chat.completion.chunk", "created": resp["created"], "model": resp["model"]}
    delta = {"role": "assistant"}
    if msg.get("content"):
        delta["content"] = msg["content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(msg["tool_calls"])]
    yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}, ensure_ascii=False) + "\n\n"
    yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}]}) + "\n\n"
    yield "data: [DONE]\n\n"


def ollama_ndjson(resp: dict):
    """Rejoue une réponse Ollama en NDJSON : la ligne message, puis la ligne done."""
    yield json.dumps({"model": resp["model"], "created_at": resp["created_at"],
                      "message": resp["message"], "done": False}, ensure_ascii=False) + "\n"
    yield json.dumps({**resp, "message": {"role": "assistant", "content": ""}}, ensure_ascii=False) + "\n"
