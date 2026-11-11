"""Tests unitaires et d'intégration (python -m unittest discover -s tests)."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from toolshim.capabilities import CapabilityDetector  # noqa: E402
from toolshim.config import Config  # noqa: E402
from toolshim.engine import Emulator  # noqa: E402
from toolshim.formats import to_openai  # noqa: E402
from toolshim.parsing import (coerce_value, extract_json, normalize_envelope,  # noqa: E402
                              resolve_tool_name, validate_call)
from toolshim.prompting import convert_messages  # noqa: E402
from toolshim.selector import normalize_tools, select_tools  # noqa: E402
from toolshim.server import create_app  # noqa: E402


def tool(name, desc, props=None, required=()):
    """Fabrique un outil au format OpenAI."""
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": list(required)}}}


TOOLS = [
    tool("depot_stock", "Donne le stock d'une référence", {"reference": {"type": "string"}}, ["reference"]),
    tool("depot_entrer", "Enregistre une entrée de stock", {"reference": {"type": "string"}, "quantite": {"type": "integer"}}, ["reference", "quantite"]),
    tool("meteo_ville", "Météo d'une ville", {"ville": {"type": "string"}}, ["ville"]),
]


class FakeUpstream:
    """Amont factice : rejoue des réponses texte scriptées."""

    def __init__(self, replies, caps=("completion",)):
        self.replies, self.payloads, self.caps = list(replies), [], list(caps)

    async def chat(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        return {"message": {"role": "assistant", "content": self.replies.pop(0)}, "prompt_eval_count": 5, "eval_count": 7}

    async def show(self, model):
        return {"capabilities": self.caps}

    async def aclose(self):
        pass


def body(prompt="ajoute 50 VIS-M6", **extra):
    """Corps de requête OpenAI minimal."""
    return {"model": "m", "messages": [{"role": "user", "content": prompt}], "tools": TOOLS, **extra}


def run(coro):
    """Exécute une coroutine de test."""
    return asyncio.run(coro)


class ParsingTests(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('```json\n{"a": 2}\n```'), {"a": 2})
        self.assertEqual(extract_json('Voici : {"a": {"b": "}"}} merci'), {"a": {"b": "}"}})
        self.assertEqual(extract_json('<think>hmm {x}</think>{"a": 3}'), {"a": 3})
        self.assertIsNone(extract_json("pas de json"))

    def test_normalize_plan_format(self):
        calls, reply = normalize_envelope({"action": "depot_stock", "parameters": {"reference": "X"}})
        self.assertEqual(calls, [{"name": "depot_stock", "arguments": {"reference": "X"}}])
        self.assertEqual(normalize_envelope({"calls": [], "reply": "salut"}), ([], "salut"))

    def test_coerce_and_validate(self):
        self.assertEqual(coerce_value("50", {"type": "integer"}), 50)
        self.assertEqual(coerce_value("false", {"type": "boolean"}), False)
        by = {t["name"]: t for t in normalize_tools(TOOLS)}
        ok, errs = validate_call({"name": "depot_entrer", "arguments": {"reference": "V", "quantite": "50", "zzz": 1}}, by)
        self.assertEqual(ok["arguments"], {"reference": "V", "quantite": 50})
        self.assertEqual(errs, [])
        bad, errs = validate_call({"name": "depot_entrer", "arguments": {"reference": "V"}}, by)
        self.assertIsNone(bad)
        self.assertIn("quantite", errs[0])

    def test_fuzzy_name(self):
        self.assertEqual(resolve_tool_name("Depot_Stok", ["depot_stock", "meteo_ville"]), "depot_stock")
        self.assertIsNone(resolve_tool_name("zzz", ["depot_stock"]))


class SelectorPromptTests(unittest.TestCase):
    def test_selector_prefers_relevant(self):
        many = normalize_tools([tool(f"outil_{i}", f"fait la chose {i}") for i in range(20)] + TOOLS)
        names = [t["name"] for t in select_tools(many, "quel est le stock de la référence VIS ?", 3)]
        self.assertIn("depot_stock", names)
        self.assertEqual(len(names), 3)

    def test_convert_history(self):
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "go"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "depot_stock", "arguments": '{"reference": "V"}'}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "12"}]
        system, out = convert_messages(msgs)
        self.assertEqual(system, "SYS")
        self.assertEqual(json.loads(out[1]["content"])["calls"][0]["name"], "depot_stock")
        self.assertIn("depot_stock", out[2]["content"])
        self.assertEqual(out[2]["role"], "user")


class EngineTests(unittest.TestCase):
    def emulate(self, replies, **kw):
        up = FakeUpstream(replies)
        res = run(Emulator(Config(**kw.pop("cfg", {})), up).run(body(**kw)))
        return res, up

    def test_first_try(self):
        res, up = self.emulate([json.dumps({"calls": [{"name": "depot_entrer", "arguments": {"reference": "VIS-M6", "quantite": "50"}}], "reply": ""})])
        self.assertEqual(res.calls, [{"name": "depot_entrer", "arguments": {"reference": "VIS-M6", "quantite": 50}}])
        self.assertEqual(res.attempts, 1)
        self.assertIn("format", up.payloads[0])

    def test_retry_after_garbage_and_missing_arg(self):
        good = json.dumps({"calls": [{"name": "depot_entrer", "arguments": {"reference": "V", "quantite": 5}}], "reply": ""})
        missing = json.dumps({"calls": [{"name": "depot_entrer", "arguments": {"reference": "V"}}], "reply": ""})
        res, up = self.emulate(["blabla", missing, good])
        self.assertEqual(res.attempts, 3)
        self.assertEqual(res.calls[0]["arguments"]["quantite"], 5)
        self.assertIn("quantite", up.payloads[2]["messages"][-1]["content"])

    def test_plain_reply_and_fallback(self):
        res, _ = self.emulate([json.dumps({"calls": [], "reply": "Bonjour"})])
        self.assertEqual((res.calls, res.reply), ([], "Bonjour"))
        res, _ = self.emulate(["texte libre"] * 3)
        self.assertEqual((res.calls, res.reply), ([], "texte libre"))

    def test_tool_choice_required(self):
        none = json.dumps({"calls": [], "reply": "non"})
        good = json.dumps({"calls": [{"name": "meteo_ville", "arguments": {"ville": "Orange"}}], "reply": ""})
        res, _ = self.emulate([none, good], tool_choice="required")
        self.assertEqual(res.calls[0]["name"], "meteo_ville")

    def test_history_tool_result_in_prompt(self):
        up = FakeUpstream([json.dumps({"calls": [], "reply": "Il y a 12 unités."})])
        b = body()
        b["messages"] += [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "depot_stock", "arguments": '{"reference": "V"}'}}]},
                          {"role": "tool", "tool_call_id": "c1", "content": '{"quantite": 12}'}]
        res = run(Emulator(Config(), up).run(b))
        self.assertEqual(res.reply, "Il y a 12 unités.")
        self.assertIn("Résultat de l'outil depot_stock", up.payloads[0]["messages"][-1]["content"])


class FormatAndServerTests(unittest.TestCase):
    CALL = json.dumps({"calls": [{"name": "depot_stock", "arguments": {"reference": "VIS-M6"}}], "reply": ""})

    def test_openai_format(self):
        r = to_openai("m", [{"name": "depot_stock", "arguments": {"reference": "V"}}], "", {})
        ch = r["choices"][0]
        self.assertEqual(ch["finish_reason"], "tool_calls")
        self.assertEqual(json.loads(ch["message"]["tool_calls"][0]["function"]["arguments"]), {"reference": "V"})

    def test_server_openai_and_stream(self):
        app = create_app(Config(), upstream=FakeUpstream([self.CALL, self.CALL]))
        with TestClient(app) as c:
            r = c.post("/v1/chat/completions", json=body()).json()
            self.assertEqual(r["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "depot_stock")
            s = c.post("/v1/chat/completions", json=body(stream=True))
            self.assertIn("[DONE]", s.text)
            self.assertIn("tool_calls", s.text)

    def test_server_ollama(self):
        app = create_app(Config(), upstream=FakeUpstream([self.CALL]))
        with TestClient(app) as c:
            lines = [json.loads(x) for x in c.post("/api/chat", json=body()).text.splitlines()]
            self.assertEqual(lines[0]["message"]["tool_calls"][0]["function"]["arguments"], {"reference": "VIS-M6"})
            self.assertTrue(lines[-1]["done"])

    def test_capability_detection(self):
        for caps, expected in ((["completion", "tools"], True), (["completion"], False)):
            det = CapabilityDetector(Config(), FakeUpstream([], caps))
            self.assertIs(run(det.supports_native_tools("x")), expected)
        det = CapabilityDetector(Config(native_models=["qwen3*"], emulated_models=["qwen3-old*"]), FakeUpstream([]))
        self.assertTrue(run(det.supports_native_tools("qwen3:8b")))
        self.assertFalse(run(det.supports_native_tools("qwen3-old:1b")))


if __name__ == "__main__":
    unittest.main()
