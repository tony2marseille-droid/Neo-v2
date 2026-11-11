#!/usr/bin/env python3
"""
Extraction automatique de faits depuis une conversation Neo.
Usage : python extract_facts.py [conversation_id]

Lance en SCRIPT par le serveur Rust : la racine du projet est ajoutee au
chemin d'import pour reutiliser la configuration commune (base et Ollama).
"""
import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import ollama_url, settings  # noqa: E402

# Meme base que le reste de Neo (NEO_DB_PATH, sinon <data_dir>/localai.db) :
# l'ancienne oracle.db codee en dur etait vide, aucun fait n'etait extrait.
DB = settings.db_path
OLLAMA = ollama_url() + "/api/generate"
MODEL = "qwen3:8b"

PROMPT_SYSTEM = """Tu es un extracteur de faits. À partir de cette conversation,
extrais les faits concrets sur l'utilisateur (prénom, âge, ville, métier,
préférences, projets, santé, famille, etc.). Chaque fait doit être court,
vérifiable et autonome.

Format de réponse : une ligne par fait, exactement comme ceci :
FAIT: <contenu du fait> | TAG: fait_utilisateur
FAIT: <contenu du fait> | TAG: preference

Si aucun fait, réponds uniquement : AUCUN

Ne rajoute aucun texte avant ou après. Pas de markdown, pas de code block."""

TAGS = {"fait_utilisateur", "preference", "info_personnelle"}


def ollama_generate(prompt: str) -> str:
    payload = {"model": MODEL, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.1, "num_predict": 512}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    return d.get("response", "")


def normaliser(texte: str) -> str:
    return re.sub(r"[^\w\s]", "", texte.lower().strip())


def faits_deja_presents(con: sqlite3.Connection) -> set:
    rows = con.execute("SELECT content FROM memories").fetchall()
    return {normaliser(r[0]) for r in rows}


def extraire_faits(conversation_id: int, con: sqlite3.Connection, deja: set) -> list:
    rows = con.execute(
        "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    if not rows:
        return []

    historique = "\n".join(f"{r[0]}: {r[1]}" for r in rows)
    prompt = (
        f"{PROMPT_SYSTEM}\n\nConversation :\n{historique}\n\nFaits extraits :"
    )
    reponse = ollama_generate(prompt).strip()

    if reponse == "AUCUN":
        return []

    nouveaux = []
    for ligne in reponse.splitlines():
        ligne = ligne.strip()
        if not ligne.upper().startswith("FAIT:"):
            continue
        corps = ligne[5:].strip()
        if "| TAG:" in corps:
            contenu, tag_part = corps.split("| TAG:", 1)
            contenu = contenu.strip()
            tag = tag_part.strip()
        else:
            contenu = corps.strip()
            tag = "fait_utilisateur"
        if not contenu:
            continue
        if tag not in TAGS:
            tag = "fait_utilisateur"
        cle = normaliser(contenu)
        if cle in deja:
            continue
        deja.add(cle)
        nouveaux.append({"content": contenu, "tag": tag})
    return nouveaux


def inserer_faits(faits: list, con: sqlite3.Connection):
    now = datetime.now(timezone.utc).isoformat()
    for f in faits:
        con.execute(
            "INSERT INTO memories (content, tag, created_at) VALUES (?, ?, ?)",
            (f["content"], f["tag"], now),
        )
    con.commit()


def main():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row

    if len(sys.argv) > 1:
        conv_ids = [int(sys.argv[1])]
    else:
        conv_ids = [
            r[0] for r in con.execute(
                "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 10"
            )
        ]

    deja = faits_deja_presents(con)
    total_nouveaux = 0

    for cid in conv_ids:
        nouveaux = extraire_faits(cid, con, deja)
        if nouveaux:
            inserer_faits(nouveaux, con)
            total_nouveaux += len(nouveaux)
            print(f"Conv {cid}: +{len(nouveaux)} fait(s)")
            for f in nouveaux:
                print(f"    - [{f['tag']}] {f['content'][:80]}")
        else:
            print(f"Conv {cid}: aucun fait nouveau")

    print(f"\nTotal : {total_nouveaux} fait(s) ajouté(s)")
    con.close()


if __name__ == "__main__":
    main()
