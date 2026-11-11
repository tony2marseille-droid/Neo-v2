# -*- coding: utf-8 -*-
"""Veille : repos GitHub + modeles Hugging Face a la mode.

Usage : python -u veille.py
Produit : data/veille_<date>.jsonl — pour chaque repo/modele tendance :
resume (LLM) + gain potentiel si ajoute au projet Neo.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

OLLAMA = "http://127.0.0.1:11434"
# Modele choisi dynamiquement parmi les modeles REELLEMENT installes
# (l'ancien nom code en dur « localai-prog-v9.2-q5km » n'existe plus :
# toutes les analyses echouaient en silence).
MODELE = ""
SORTIE = Path("data")
PROGRES = SORTIE / "veille_progress.json"

# `log` etait appele partout mais n'etait plus defini : le script plantait
# des la premiere ligne (NameError) et la veille ne produisait plus rien.
log = lambda m: print(f"[veille] {m}", flush=True)


def _choisir_modele() -> str:
    global MODELE
    if MODELE:
        return MODELE
    import os

    force = os.environ.get("LOCALAI_VEILLE_MODEL", "")
    if force:
        MODELE = force
        return MODELE
    try:
        rep = json.loads(urllib.request.urlopen(
            f"{OLLAMA}/api/tags", timeout=10).read().decode("utf-8"))
        installes = [m.get("name", "") for m in rep.get("models", [])]
    except Exception:
        installes = []
    for pref in ("localai-prog-v10-q5km:latest", "localai-prog-v10:latest",
                 "localai-prog-v9.4:latest", "qwen3:14b", "qwen3:8b"):
        if pref in installes:
            MODELE = pref
            return MODELE
    MODELE = installes[0] if installes else "qwen3:8b"
    return MODELE

CONTEXT = (
    "Contexte du projet Neo : assistant IA personnel 100% local sous Windows, "
    "serveur Rust, backend Python (images 4K FLUX+Kontext+QC vision, 3D "
    "Hunyuan/OpenSCAD/Blender/G-code, documents Office, RAG, voix Whisper+SAPI, "
    "Telegram, auto-amelioration : coach + corpus RLVR + fine-tune LoRA + bench "
    "double juge). GPU RTX 3080 10 Go, modeles 8B quantises via Ollama.\n\n"
)


def ecrire_progres(statut, total, traite, dernier=""):
    try:
        PROGRES.write_text(
            json.dumps({
                "statut": statut,
                "total": total,
                "traite": traite,
                "dernier": dernier,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def lire_progres():
    try:
        return json.loads(PROGRES.read_text(encoding="utf-8"))
    except Exception:
        return {"statut": "inconnu", "total": 0, "traite": 0, "dernier": ""}


def http_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "neo-veille"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def chat(prompt, max_tokens=500):
    body = json.dumps({
        "model": _choisir_modele(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"},
    )
    rep = json.loads(urllib.request.urlopen(req, timeout=240).read().decode("utf-8"))
    return rep.get("message", {}).get("content", "")


def github_trending(limit=12):
    log("github : recherche des repos a la mode...")
    url = (
        "https://api.github.com/search/repositories?q=created:>2026-09-01+topic:ai"
        "&sort=stars&order=desc&per_page=" + str(limit)
    )
    try:
        rep = http_json(url)
    except Exception as exc:
        log(f"github indisponible : {exc}")
        return []
    repos = []
    for r in rep.get("items", [])[:limit]:
        repos.append({
            "source": "github",
            "nom": r.get("full_name", ""),
            "url": r.get("html_url", ""),
            "etoiles": r.get("stargazers_count", 0),
            "langage": r.get("language") or "",
            "description": (r.get("description") or "")[:300],
        })
    return repos


def hf_trending(limit=8):
    log("huggingface : modeles populaires recents...")
    try:
        rep = http_json(
            "https://huggingface.co/api/models?sort=likes&direction=-1&limit=60"
        )
    except Exception as exc:
        log(f"huggingface indisponible : {exc}")
        return []
    modeles = []
    for m in rep:
        mid = m.get("modelId") or m.get("id") or ""
        if any(k in mid.lower() for k in ["neo", "localai"]):
            continue
        if m.get("likes", 0) < 500:
            continue
        modeles.append({
            "source": "huggingface",
            "nom": mid,
            "url": f"https://huggingface.co/{mid}",
            "etoiles": m.get("likes", 0),
            "langage": m.get("pipeline_tag") or "",
            "description": (m.get("cardData", {}).get("summary") or m.get("description") or "")[:300],
        })
        if len(modeles) >= limit:
            break
    return modeles


def analyser(item):
    prompt = (
        CONTEXT
        + f"Repo/modele a la mode :\nNOM : {item['nom']}\n"
        f"DESCRIPTION : {item['description']}\nLANGAGE/TAG : {item['langage']}\n\n"
        "Reponds EXACTEMENT en ce format (concis) :\n"
        "RESUME: <2 phrases sur ce que c'est>\n"
        "GAIN: <2 phrases sur l'apport concret si integre a Neo, ou 'aucun "
        "apport pertinent' si hors sujet>\n"
        "INTEGRATION: OUI ou NON (une brique de ce repo/modele peut-elle "
        "etre integree au projet Neo ?)\n"
        "FAISABILITE: 1-5 (5 = trivial, 1 = tres lourd)\n"
        "EFFORT: faible, moyen ou eleve\n"
        "COMMENT: <1 phrase sur COMMENT l'integrer (module, outil, "
        "remplacement...) ou 'aucune' si hors sujet>\n"
    )
    try:
        rep = chat(prompt, 450)
    except Exception as exc:
        return {"resume": "", "gain": f"analyse impossible : {exc}",
                "integration": "?", "faisabilite": 0, "effort": "",
                "integration_comment": ""}
    resume, gain, comment = "", "", ""
    integration, faisabilite, effort = "?", 0, ""
    for ligne in rep.splitlines():
        l = ligne.strip()
        u = l.upper()
        if u.startswith("RESUME:"):
            resume = l[7:].strip()
        elif u.startswith("GAIN:"):
            gain = l[5:].strip()
        elif u.startswith("INTEGRATION:"):
            v = l[12:].strip().upper()
            integration = "OUI" if v.startswith("OUI") else (
                "NON" if v.startswith("NON") else "?")
        elif u.startswith("FAISABILITE:"):
            import re as _re

            m = _re.search(r"[1-5]", l)
            faisabilite = int(m.group(0)) if m else 0
        elif u.startswith("EFFORT:"):
            effort = l[7:].strip().lower()
        elif u.startswith("COMMENT:"):
            comment = l[8:].strip()
    return {
        "resume": resume or rep[:200],
        "gain": gain or "non evalue",
        "integration": integration,
        "faisabilite": faisabilite,
        "effort": effort,
        "integration_comment": comment,
    }


def main():
    date = time.strftime("%Y-%m-%d")
    log(f"veille du {date}")
    items = github_trending() + hf_trending()
    import os

    limite = int(os.environ.get("VEILLE_LIMIT", "0") or "0")
    if limite > 0:
        items = items[:limite]
    total = len(items)
    log(f"{total} candidats")
    ecrire_progres("en_cours", total, 0)
    resultats = []
    for i, item in enumerate(items, 1):
        log(f"({i}/{total}) {item['nom']}")
        ana = analyser(item)
        item["resume"] = ana["resume"]
        item["gain"] = ana["gain"]
        # Etude d'integration (point 15) : comme les idees du conseil, chaque
        # trouvaille de veille est evaluee (possible ? faisabilite, effort,
        # comment l'integrer).
        item["integration"] = ana.get("integration", "?")
        item["faisabilite"] = ana.get("faisabilite", 0)
        item["effort"] = ana.get("effort", "")
        item["integration_comment"] = ana.get("integration_comment", "")
        item["date"] = date
        resultats.append(item)
        ecrire_progres("en_cours", total, i, item.get("nom", ""))
    sortie = SORTIE / f"veille_{time.strftime('%Y%m%d')}.jsonl"
    with sortie.open("w", encoding="utf-8") as f:
        for r in resultats:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ecrire_progres("termine", total, total, "")
    log(f"TERMINE : {total} analyses -> {sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
