"""Moteur d'émulation : prompt -> sortie JSON contrainte -> validation -> nouvelles tentatives guidées."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .parsing import extract_json, normalize_envelope, strip_think, validate_call
from .prompting import (build_envelope_schema, build_system_prompt, convert_messages,
                        last_user_text, used_tool_names)
from .selector import normalize_tools, select_tools

log = logging.getLogger("toolshim")


@dataclass
class Result:
    """Résultat d'une émulation : appels validés, texte de réponse, tentatives utilisées, réponse amont brute."""

    calls: list = field(default_factory=list)
    reply: str = ""
    attempts: int = 1
    raw: dict = field(default_factory=dict)


def build_options(body: dict, cfg) -> dict:
    """Options de génération pour l'amont, depuis les champs OpenAI (max_tokens…) ou Ollama (options)."""
    opts = dict(body.get("options") or {})
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("seed", "seed"),
                     ("max_tokens", "num_predict"), ("stop", "stop")):
        if src in body and dst not in opts:
            opts[dst] = body[src]
    opts.setdefault("temperature", cfg.temperature)
    return opts


def choice_rule(tool_choice, tools: list):
    """Interprète tool_choice. Retourne (outils retenus, règle ajoutée au prompt, appel obligatoire ?)."""
    if tool_choice == "required":
        return tools, "Tu DOIS appeler au moins un outil.", True
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name")
        forced = [t for t in tools if t["name"] == name]
        if forced:
            return forced, f"Tu DOIS appeler l'outil {name}.", True
    return tools, None, False


class Emulator:
    """Émule le tool calling au-dessus d'un modèle qui ne sait que produire du texte."""

    def __init__(self, cfg, upstream):
        """Mémorise la configuration et le client amont."""
        self.cfg = cfg
        self.upstream = upstream

    @staticmethod
    def _validate(calls: list, by_name: dict):
        """Valide chaque appel. Retourne (appels valides, erreurs)."""
        valid, errors = [], []
        for c in calls:
            ok, errs = validate_call(c, by_name)
            if ok:
                valid.append(ok)
            errors.extend(errs)
        return valid, errors

    async def run(self, body: dict) -> Result:
        """Traite une requête chat avec `tools` : retourne les appels d'outils ou la réponse finale."""
        cfg = self.cfg
        tools, rule, must_call = choice_rule(body.get("tool_choice"), normalize_tools(body.get("tools")))
        system, history = convert_messages(body.get("messages", []))
        selected = select_tools(tools, last_user_text(history), cfg.max_tools_in_prompt,
                                pinned=used_tool_names(body.get("messages", [])))
        by_name = {t["name"]: t for t in selected}
        payload = {
            "model": body["model"], "stream": False, "options": build_options(body, cfg),
            "messages": [{"role": "system", "content": build_system_prompt(
                system, selected, cfg.max_calls_per_turn, rule)}] + history,
        }
        if cfg.constrained_output:
            payload["format"] = build_envelope_schema(list(by_name), cfg.max_calls_per_turn)
        if cfg.disable_thinking:
            payload["think"] = False

        raw, text, obj, valid, reply = {}, "", None, [], ""
        for attempt in range(1, cfg.max_retries + 2):
            raw = await self.upstream.chat(payload)
            text = (raw.get("message") or {}).get("content", "")
            obj = extract_json(text)
            if obj is None:
                feedback = "Réponse invalide : renvoie uniquement l'objet JSON demandé."
            else:
                calls, reply = normalize_envelope(obj)
                valid, errors = self._validate(calls, by_name)
                if not valid and must_call and not errors:
                    errors = ["au moins un appel d'outil est obligatoire"]
                if not valid and not reply.strip() and not errors:
                    errors = ["ni appel d'outil ni réponse : remplis « calls » ou « reply »"]
                if not errors:
                    return Result(valid[:cfg.max_calls_per_turn], "" if valid else reply, attempt, raw)
                feedback = "Erreurs : " + " ; ".join(errors) + ". Corrige et renvoie uniquement le JSON."
            log.warning("tentative %d/%d invalide : %s", attempt, cfg.max_retries + 1, feedback)
            payload["messages"] = payload["messages"] + [
                {"role": "assistant", "content": text}, {"role": "user", "content": feedback}]
        if valid and not must_call:
            return Result(valid[:cfg.max_calls_per_turn], "", cfg.max_retries + 1, raw)
        return Result([], reply or strip_think(text), cfg.max_retries + 1, raw)
