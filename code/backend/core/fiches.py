# -*- coding: utf-8 -*-
"""Fiches de connaissances de Neo : creation, lecture, gestion.

Une fiche porte sur un SUJET (« Le pont du Gard », « La photosynthese »), pas
sur une question. Elle a un titre, une categorie, un resume de deux phrases, un
corps structure (En bref / Points cles / A retenir), ses sources et, si on le
demande, une illustration.

Trois facons d'en creer une, un seul code :
  - depuis la page Documents (« Nouvelle fiche ») ;
  - dans la conversation (« fais-moi une fiche sur … », outil `fiche_creer`) ;
  - toute seule, quand Neo vient de repondre apres une recherche web sur un
    vrai sujet (`depuis_reponse`) — le modele juge d'abord si le sujet merite
    une fiche.

Historique : jusqu'au 11/11/2026 les fiches venaient d'un script hors depot
(`fiche_auto.py`) qui prenait les six premiers mots de la question pour titre
(« Une photo d'un boulanger devant son »), ecrivait « ## En bref » en guise de
resume, et aucune page ne les affichait.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

CATEGORIES = ["sciences", "technologie", "histoire", "geographie", "nature", "personnalites", "culture",
              "cuisine", "sante", "sport", "pratique", "autre"]
MODELES = ("gemma4:12b", "qwen3:14b", "qwen3:8b", "gemma3:4b")
OLLAMA = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS fiches (id INTEGER PRIMARY KEY AUTOINCREMENT, titre TEXT NOT NULL, "
    "categorie TEXT DEFAULT 'autre', resume TEXT DEFAULT '', contenu_md TEXT DEFAULT '', "
    "image_path TEXT DEFAULT '', source TEXT DEFAULT '', created_at TEXT, updated_at TEXT)"
)


def _db():
    from backend.core.database import get_db

    db = get_db()
    db.execute(_SCHEMA)
    try:                       # colonne ajoutee le 11/11/2026 : adresses des sources
        db.execute("ALTER TABLE fiches ADD COLUMN sources TEXT DEFAULT ''")
    except Exception:
        pass
    return db


def _maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ligne(r) -> dict:
    d = dict(r)
    try:
        d["sources"] = json.loads(d.get("sources") or "[]")
    except Exception:
        d["sources"] = []
    return d


# ---- Lecture et gestion ----

def lister(recherche: str = "", categorie: str = "") -> list[dict]:
    """Fiches les plus recentes d'abord, sans leur corps."""
    sql = ("SELECT id, titre, categorie, resume, image_path, source, sources, created_at, updated_at "
           "FROM fiches WHERE 1=1")
    params: list = []
    if categorie:
        sql += " AND categorie = ?"
        params.append(categorie)
    if recherche.strip():
        sql += " AND (LOWER(titre) LIKE ? OR LOWER(contenu_md) LIKE ?)"
        params += [f"%{recherche.strip().lower()}%"] * 2
    return [_ligne(r) for r in _db().query_all(sql + " ORDER BY updated_at DESC LIMIT 500", tuple(params))]


def lire(fiche_id: int) -> dict | None:
    r = _db().query_one("SELECT * FROM fiches WHERE id = ?", (int(fiche_id),))
    return _ligne(r) if r else None


def supprimer(fiche_id: int) -> bool:
    """Retire la fiche de la base. Son illustration reste sur le disque."""
    if not lire(fiche_id):
        return False
    _db().execute("DELETE FROM fiches WHERE id = ?", (int(fiche_id),))
    return True


def pertinentes(message: str, maximum: int = 2) -> list[dict]:
    """Fiches dont le titre recoupe le message (mots de 4 lettres et plus) :
    ce que Neo sait deja et peut reprendre dans sa reponse."""
    mots = {m for m in re.findall(r"[a-zà-ÿ]{4,}", message.lower())}
    if not mots:
        return []
    notees = []
    for f in _db().query_all("SELECT id, titre, resume, contenu_md FROM fiches", ()):
        titre = set(re.findall(r"[a-zà-ÿ]{4,}", str(f["titre"]).lower()))
        communs = len(mots & titre)
        if titre and communs and communs >= min(2, len(titre)):
            notees.append((communs / len(titre), dict(f)))
    notees.sort(key=lambda x: -x[0])
    return [f for _, f in notees[:maximum]]


# ---- Redaction ----

def _modele() -> str:
    try:
        import httpx

        noms = [m.get("name", "") for m in httpx.get(f"{OLLAMA}/api/tags", timeout=10).json().get("models", [])]
        for m in MODELES:
            if m in noms:
                return m
    except Exception:
        pass
    return MODELES[0]


def _demander_json(consigne: str, delai_s: int = 180) -> dict:
    """Reponse JSON du modele ({} si illisible ou en panne)."""
    import httpx

    modele = _modele()
    options = {"temperature": 0.3, "num_predict": 1800}
    try:
        from backend.core.config import contexte_pour

        options["num_ctx"] = contexte_pour(modele, 8192)       # meme contexte que la conversation
    except Exception:
        pass
    try:
        rep = httpx.post(f"{OLLAMA}/api/chat", timeout=delai_s, json={
            "model": modele, "stream": False, "think": False, "format": "json", "options": options,
            "messages": [{"role": "user", "content": consigne}]}).json()
        brut = (rep.get("message") or {}).get("content") or ""
        m = re.search(r"\{.*\}", brut, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


_FORME = (
    'Reponds en JSON : {"fichable": true|false, "titre": "…", "categorie": "…", "resume": "…", '
    '"en_bref": "…", "points_cles": ["…"], "a_retenir": ["…"]}\n'
    "- titre : le NOM du sujet, 2 a 6 mots, comme une entree d'encyclopedie (« Le pont du Gard », "
    "« La photosynthese »). Jamais une question, jamais une phrase coupee.\n"
    "- categorie : une seule parmi " + ", ".join(CATEGORIES) + ".\n"
    "- resume : deux phrases simples qui disent l'essentiel.\n"
    "- en_bref : un paragraphe de 3 a 5 phrases.\n"
    "- points_cles : 4 a 6 faits precis (dates, chiffres, noms), une phrase chacun.\n"
    "- a_retenir : 2 ou 3 choses marquantes ou utiles.\n"
    "N'ecris que ce dont tu es sur ou qui figure dans la matiere fournie. En francais."
)


def _corps(d: dict) -> str:
    points = [str(p).strip() for p in (d.get("points_cles") or []) if str(p).strip()]
    retenir = [str(p).strip() for p in (d.get("a_retenir") or []) if str(p).strip()]
    parties = ["## En bref", str(d.get("en_bref") or d.get("resume") or "").strip()]
    if points:
        parties += ["", "## Points clés"] + [f"- {p}" for p in points]
    if retenir:
        parties += ["", "## À retenir"] + [f"- {p}" for p in retenir]
    return "\n".join(parties).strip()


def _valide(d: dict) -> bool:
    titre = str(d.get("titre") or "").strip()
    return bool(titre) and len(titre) <= 80 and "?" not in titre and len(str(d.get("en_bref") or "")) >= 60


def _matiere_web(sujet: str) -> tuple[str, list[str]]:
    """(texte trouve sur le web, adresses lues). Vide si le web ne repond pas."""
    try:
        from backend.tools.t_web import tool_web_fetch, tool_web_search

        resultats = tool_web_search(sujet)
    except Exception:
        return "", []
    adresses = []
    for u in re.findall(r"https?://[^\s\"'<>)\]]+", resultats):
        u = u.rstrip(".,;")
        if u not in adresses and "duckduckgo" not in u:
            adresses.append(u)
    textes, lues = [resultats[:2500]], []
    for u in adresses[:2]:
        try:
            page = tool_web_fetch(u)
        except Exception:
            continue
        if page and not page.lower().startswith("erreur"):
            textes.append(page[:3500])
            lues.append(u)
    return "\n\n---\n\n".join(textes), lues or adresses[:3]


def _enregistrer(d: dict, source: str, sources: list[str], fiche_id: int | None = None) -> dict:
    db = _db()
    titre = str(d["titre"]).strip().rstrip(".")
    categorie = str(d.get("categorie") or "autre").strip().lower()
    categorie = categorie if categorie in CATEGORIES else "autre"
    resume, corps, quand = str(d.get("resume") or "").strip(), _corps(d), _maintenant()
    if fiche_id is None:
        existante = db.query_one("SELECT id FROM fiches WHERE LOWER(titre) = ?", (titre.lower(),))
        fiche_id = existante["id"] if existante else None
    if fiche_id is None:
        db.execute("INSERT INTO fiches (titre, categorie, resume, contenu_md, image_path, source, sources, "
                   "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (titre, categorie, resume, corps, "", source, json.dumps(sources), quand, quand))
        fiche_id = db.query_one("SELECT id FROM fiches WHERE titre = ? ORDER BY id DESC", (titre,))["id"]
    else:
        db.execute("UPDATE fiches SET titre=?, categorie=?, resume=?, contenu_md=?, source=?, sources=?, "
                   "updated_at=? WHERE id=?",
                   (titre, categorie, resume, corps, source, json.dumps(sources), quand, int(fiche_id)))
    return lire(fiche_id)


def creer(sujet: str, recherche: bool = True, avec_image: bool = False, source: str = "manuel",
          fiche_id: int | None = None) -> dict:
    """Redige une fiche sur `sujet` et l'enregistre. Retourne la fiche, ou
    {"erreur": …}. `recherche` : lire le web d'abord (sinon savoir du modele)."""
    sujet = str(sujet or "").strip()[:200]
    if len(sujet) < 2:
        return {"erreur": "Donne un sujet (ex. « le pont du Gard »)."}
    matiere, sources = _matiere_web(sujet) if recherche else ("", [])
    consigne = (f"Redige la fiche de connaissance du sujet : {sujet}\n\n"
                + (f"MATIERE TROUVEE SUR LE WEB (a verifier et resumer, ne recopie pas) :\n{matiere[:9000]}\n\n"
                   if matiere else "Aucune matiere fournie : appuie-toi sur ce que tu sais avec certitude.\n\n")
                + _FORME + '\n"fichable" vaut true ici.')
    d = _demander_json(consigne)
    if not _valide(d):
        return {"erreur": "La redaction a echoue (modele indisponible ou reponse inexploitable). Reessaie."}
    fiche = _enregistrer(d, source, sources, fiche_id)
    if avec_image:
        fiche = illustrer(fiche["id"]) if not fiche.get("image_path") else fiche
    return fiche


def actualiser(fiche_id: int) -> dict:
    """Reecrit une fiche a partir de son titre (nouvelle recherche)."""
    f = lire(fiche_id)
    if not f:
        return {"erreur": "fiche inconnue"}
    sujet = f["titre"]
    # Ancienne fiche automatique : son titre est un debut de question, la
    # question entiere est restee dans `source`.
    if str(f.get("source", "")).startswith("auto: "):
        sujet = f["source"][6:] or sujet
    return creer(sujet, source=f.get("source") or "manuel", fiche_id=int(fiche_id))


def depuis_reponse(question: str, reponse: str) -> str:
    """Fiche tiree d'une reponse que Neo vient de donner apres une recherche.
    Le modele decide si le sujet en merite une : pas pour un cours de bourse,
    une meteo, une demande d'image ou une question personnelle."""
    consigne = (
        "Neo vient de repondre a son utilisateur apres une recherche web. Faut-il garder une fiche de "
        "connaissance ? OUI seulement si la reponse porte sur un SUJET durable qu'on aimera relire (un lieu, "
        "une personne, une notion, une technique, un evenement). NON pour une information qui perime (cours, "
        "meteo, actualite du jour), une demande de creation, un avis personnel.\n\n"
        f"QUESTION : {question[:400]}\n\nREPONSE DE NEO :\n{reponse[:5000]}\n\n" + _FORME
        + '\nSi la reponse est NON : {"fichable": false}.')
    d = _demander_json(consigne)
    if not d.get("fichable") or not _valide(d):
        return ""
    sources = [u.rstrip(".,;") for u in dict.fromkeys(re.findall(r"https?://[^\s\"'<>)\]]+", reponse))][:5]
    fiche = _enregistrer(d, f"conversation : {question[:120]}", sources)
    return f"FICHE : {fiche['titre']} ({fiche['categorie']})"


# ---- Illustration et export ----

def _dossier_images() -> Path:
    from backend.tools.common import _default_root

    d = Path(_default_root()) / "workspace" / "images" / "fiches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def illustrer(fiche_id: int) -> dict:
    """Dessine l'illustration de la fiche avec le studio (version legere)."""
    f = lire(fiche_id)
    if not f:
        return {"erreur": "fiche inconnue"}
    en = _demander_json(
        'Write a short English image description (20 words at most) to illustrate an encyclopedia card. '
        'Answer in JSON: {"prompt": "…"}.\n' + f"Subject: {f['titre']}. {f.get('resume', '')}", 60).get("prompt")
    prompt = (f"{en or f['titre']}, editorial illustration photo, clear single subject, natural light, "
              "rich detail, no text, no watermark")
    sortie = _dossier_images() / f"fiche_{int(fiche_id)}_{int(time.time())}.png"
    try:
        from backend.core.sdgen import generer

        retour = generer(prompt, str(sortie), largeur=1024, hauteur=640, traduire=False, resolution="hd", brut=True)
    except Exception as exc:
        return {**f, "erreur": f"illustration impossible ({exc})"}
    if not sortie.is_file():
        return {**f, "erreur": f"illustration non produite ({str(retour)[:200]})"}
    _db().execute("UPDATE fiches SET image_path = ?, updated_at = ? WHERE id = ?",
                  (str(sortie), _maintenant(), int(fiche_id)))
    return lire(fiche_id)


def exporter(fiche_id: int, type_doc: str = "pdf") -> str:
    """Ecrit la fiche en PDF ou Word ; retourne le message de l'outil documents."""
    f = lire(fiche_id)
    if not f:
        return "Erreur: fiche inconnue"
    from backend.tools.t_docs import tool_document_create

    corps = f"# {f['titre']}\n\n*{f.get('resume', '')}*\n\n{f.get('contenu_md', '')}"
    if f.get("sources"):
        corps += "\n\n## Sources\n" + "\n".join(f"- {s}" for s in f["sources"])
    return tool_document_create(f["titre"], corps, type_doc)


def tool_fiche_creer(sujet: str) -> str:
    """Outil de conversation : cree (ou met a jour) la fiche d'un sujet."""
    f = creer(sujet, source="conversation")
    if f.get("erreur"):
        return "Erreur: " + f["erreur"]
    return (f"FICHE ENREGISTREE : « {f['titre']} » ({f['categorie']}), visible dans Documents > Fiches.\n"
            f"Resume : {f['resume']}\n\n{f['contenu_md']}")
