"""Client de démonstration : boucle d'agent avec 3 outils « dépôt » factices, via le proxy."""
import argparse
import json

import httpx

STOCK = {"VIS-M6": 120, "ECROU-M6": 80, "RONDELLE-M6": 300}


def depot_lister(categorie: str = "") -> list:
    """Liste les références du dépôt (filtre optionnel par préfixe)."""
    return [ref for ref in STOCK if ref.startswith(categorie.upper())]


def depot_stock(reference: str) -> dict:
    """Retourne la quantité en stock d'une référence."""
    return {"reference": reference, "quantite": STOCK.get(reference.upper())}


def depot_entrer(reference: str, quantite: int) -> dict:
    """Ajoute une quantité au stock et retourne le nouveau total."""
    STOCK[reference.upper()] = STOCK.get(reference.upper(), 0) + quantite
    return {"reference": reference, "quantite": STOCK[reference.upper()]}


IMPL = {"depot_lister": depot_lister, "depot_stock": depot_stock, "depot_entrer": depot_entrer}


def _tool(name, description, props, required):
    """Fabrique la déclaration d'un outil au format OpenAI."""
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}}


TOOLS = [
    _tool("depot_lister", "Liste les références du dépôt", {"categorie": {"type": "string", "description": "préfixe, ex. VIS"}}, []),
    _tool("depot_stock", "Donne le stock d'une référence", {"reference": {"type": "string"}}, ["reference"]),
    _tool("depot_entrer", "Enregistre une entrée de stock", {"reference": {"type": "string"}, "quantite": {"type": "integer"}}, ["reference", "quantite"]),
]


def run_agent(base_url: str, model: str, prompt: str, max_steps: int = 5) -> str:
    """Boucle agent : appelle le proxy, exécute les tool_calls localement, renvoie le résultat au modèle."""
    messages = [{"role": "user", "content": prompt}]
    for _ in range(max_steps):
        r = httpx.post(f"{base_url}/v1/chat/completions", timeout=300,
                       json={"model": model, "messages": messages, "tools": TOOLS}).json()
        msg = r["choices"][0]["message"]
        messages.append(msg)
        if not msg.get("tool_calls"):
            return msg.get("content") or ""
        for tc in msg["tool_calls"]:
            fn = tc["function"]
            result = IMPL[fn["name"]](**json.loads(fn["arguments"]))
            print(f"  -> {fn['name']}({fn['arguments']}) = {result}")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
    return "(nombre maximal d'étapes atteint)"


def main() -> None:
    """Point d'entrée CLI : python demo_client.py --model qwen3:8b "ajoute 50 vis M6"."""
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--model", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8330")
    args = ap.parse_args()
    print(run_agent(args.url, args.model, args.prompt))


if __name__ == "__main__":
    main()
