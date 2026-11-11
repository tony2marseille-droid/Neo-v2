# -*- coding: utf-8 -*-
"""Culture generale Neo : dimensions d'objets courants + recherche de fiches.

Alimente TOUS les agents (cad3d : cotes realistes ; general : reponses
factuelles). La base d'objets est un JSON local enrichissable par le cycle
d'apprentissage (audit-generation-3d.md §8).
"""
import json
import os
from pathlib import Path

_RACINE = os.environ.get("LOCALAI_ROOT", r"E:\ia")
_FICHIER_OBJETS = Path(_RACINE) / "data" / "culture_objets.json"

# Base de depart : objets courants, dimensions typiques en mm.
_OBJETS_DEFAUT = {
    "telephone": {"dims": [78, 160, 8], "note": "smartphone moderne (L x H x epaisseur)"},
    "tablette": {"dims": [180, 250, 8], "note": "tablette 10 pouces"},
    "tasse": {"dims": [85, 95, 85], "note": "diametre 85, hauteur 95 mm (cafe)"},
    "verre": {"dims": [70, 90, 70], "note": "verre a eau standard"},
    "pile aa": {"dims": [14.5, 50.5, 14.5], "note": "diametre 14.5 x longueur 50.5 mm", "alias": ["aa", "lr6"]},
    "pile aaa": {"dims": [10.5, 44.5, 10.5], "note": "diametre 10.5 x longueur 44.5 mm", "alias": ["aaa", "lr3"]},
    "vis m3": {"dims": [3, 16, 5.5], "note": "diametre 3 mm, tete 5.5 mm, longueurs 6 a 40 mm", "alias": ["m3", "vis"]},
    "vis m4": {"dims": [4, 20, 7], "note": "diametre 4 mm, tete 7 mm", "alias": ["m4"]},
    "vis m5": {"dims": [5, 25, 8.5], "note": "diametre 5 mm, tete 8.5 mm", "alias": ["m5"]},
    "ecrou m3": {"dims": [5.5, 2.4, 5.5], "note": "across flats 5.5 mm, hauteur 2.4 mm", "alias": ["ecrou"]},
    "bille": {"dims": [6, 6, 6], "note": "bille 6 mm (roulement) - existent en 3 a 25 mm", "alias": ["bille 6mm"]},
    "roulement 608": {"dims": [22, 7, 22], "note": "interieur 8, exterieur 22, largeur 7 mm (skateboard)", "alias": ["608", "roulement"]},
    "cle": {"dims": [50, 20, 5], "note": "cle de porte standard (L x l x ep.)", "alias": ["cle de porte"]},
    "carte bancaire": {"dims": [85.6, 53.98, 0.76], "note": "format ISO ID-1", "alias": ["carte", "cb", "carte bleue"]},
    "porte": {"dims": [900, 2040, 40], "note": "porte interieure standard francaise (L x H x ep.)"},
    "main": {"dims": [90, 190, 30], "note": "main adulte moyenne (L x H x ep.) - utile pour l'echelle"},
    "tete": {"dims": [150, 210, 190], "note": "tete humaine moyenne (L x H x P) - buste ~300 mm"},
    "bouteille eau": {"dims": [65, 240, 65], "note": "bouteille 50 cl standard"},
    "canette": {"dims": [66, 115, 66], "note": "canette 33 cl standard"},
    "livre": {"dims": [150, 210, 25], "note": "format A5 livre de poche (L x H x ep.)"},
    "feuille a4": {"dims": [210, 297, 0.1], "note": "format A4 210 x 297 mm", "alias": ["a4", "feuille"]},
    "lego": {"dims": [8, 3.2, 8], "note": "brique standard : pas de 8 mm, hauteur 3.2 mm", "alias": ["brique", "brique lego"]},
    "des": {"dims": [16, 16, 16], "note": "de standard 16 mm", "alias": ["de", "de a jouer"]},
    "echiquier": {"dims": [450, 450, 10], "note": "cases 45-57 mm selon modele"},
    "souris": {"dims": [60, 110, 38], "note": "souris d'ordinateur moyenne (L x P x H)"},
    "clavier": {"dims": [450, 150, 25], "note": "clavier standard sans pave"},
    "ecran 24": {"dims": [540, 320, 45], "note": "ecran 24 pouces (L x H x ep. sans pied)", "alias": ["ecran", "moniteur"]},
    "porte clef": {"dims": [110, 200, 12], "note": "porte-clefs standard (avec cle)"},
    "bougie": {"dims": [60, 120, 60], "note": "bougie cylindrique standard"},
    "gateau": {"dims": [200, 80, 200], "note": "gateau rond 20 cm de diametre, 8 cm de haut"},
    "raquette": {"dims": [680, 260, 30], "note": "raquette de tennis (L x l x ep.)"},
    "ballon foot": {"dims": [220, 220, 220], "note": "circonference 68-70 cm, diametre ~220 mm", "alias": ["ballon", "ballon de foot"]},
}


def _charger_base() -> dict:
    """Base d'objets : fichier enrichi sinon base de depart."""
    if _FICHIER_OBJETS.is_file():
        try:
            data = json.loads(_FICHIER_OBJETS.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
    # Amorcage du fichier avec la base par defaut (idempotent).
    try:
        _FICHIER_OBJETS.parent.mkdir(parents=True, exist_ok=True)
        _FICHIER_OBJETS.write_text(
            json.dumps(_OBJETS_DEFAUT, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass
    return _OBJETS_DEFAUT


def tool_object_dimensions(objet: str) -> str:
    """Dimensions typiques d'un objet courant (base locale de Neo).

    Retourne les dimensions en mm + une note d'usage. Sert a concevoir avec
    des cotes REALISTES (ex: fente de telephone = largeur telephone + 2 mm).
    """
    requete = (objet or "").strip().lower()
    if not requete:
        return "Erreur: nom d'objet vide."
    base = _charger_base()
    trouves = []
    for nom, info in base.items():
        noms = [nom] + [a.lower() for a in info.get("alias", [])]
        if any(requete in n or n in requete for n in noms):
            trouves.append((nom, info))
    if not trouves:
        connus = ", ".join(sorted(base.keys())[:18])
        return (
            f"Objet '{objet}' inconnu de ma base. Objets connus : {connus}... "
            "Utilise knowledge_search ou web_search pour une estimation, ou "
            "demande a l'utilisateur la cote precise."
        )
    lignes = []
    for nom, info in trouves[:4]:
        d = info.get("dims", [])
        dims_txt = " x ".join(f"{x:g}" for x in d) + " mm" if d else "?"
        lignes.append(f"- {nom} : {dims_txt} ({info.get('note', '')})")
    return (
        f"DIMENSIONS TYPIQUES de '{objet}' :\n" + "\n".join(lignes)
        + "\nUtilise ces cotes pour concevoir (ajoute un jeu de 0,2-0,5 mm "
        "pour les assemblages, 1-2 mm pour les fentes de passage)."
    )


def tool_knowledge_search(query: str) -> str:
    """Recherche dans la culture de Neo : connaissances, documents, memoires.

    Sources : table 'learned_knowledge' (question/answer), documents indexes
    (documents + document_chunks) et memoires ('memories').
    Chaque source echoue independamment : une table absente ne casse pas tout.
    """
    q = (query or "").strip()
    if not q:
        return "Erreur: requete vide."
    mots = [m for m in q.lower().split() if len(m) >= 3][:6]
    if not mots:
        mots = [q.lower()[:40]]
    resultats = []
    erreurs = []

    def _cherche(sql: str, params: tuple) -> list:
        from backend.core.database import get_db

        return get_db().query_all(sql, params)

    # 1. Connaissances apprises (question / answer)
    try:
        for m in mots[:3]:
            rows = _cherche(
                "SELECT question, answer FROM learned_knowledge "
                "WHERE LOWER(question) LIKE ? OR LOWER(answer) LIKE ? "
                "LIMIT 3",
                (f"%{m}%", f"%{m}%"),
            )
            for r in rows:
                resultats.append(
                    f"[CONNAISSANCE] {r['question']} : "
                    f"{str(r['answer'] or '')[:200]}"
                )
    except Exception as exc:
        erreurs.append(f"connaissances: {exc}")

    # 2. Documents indexes : nom de fichier + extraits (document_chunks)
    try:
        for m in mots[:3]:
            rows = _cherche(
                "SELECT id, filename FROM documents "
                "WHERE LOWER(filename) LIKE ? LIMIT 3",
                (f"%{m}%",),
            )
            for r in rows:
                resultats.append(f"[DOCUMENT] {r['filename']}")
            rows = _cherche(
                "SELECT c.content AS content, d.filename AS filename "
                "FROM document_chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE LOWER(c.content) LIKE ? LIMIT 3",
                (f"%{m}%",),
            )
            for r in rows:
                resultats.append(
                    f"[EXTRAIT {r['filename']}] {str(r['content'] or '')[:200]}"
                )
    except Exception as exc:
        erreurs.append(f"documents: {exc}")

    # 3. Memoires apprises
    try:
        for m in mots[:2]:
            rows = _cherche(
                "SELECT content FROM memories WHERE LOWER(content) LIKE ? LIMIT 3",
                (f"%{m}%",),
            )
            for r in rows:
                resultats.append(f"[MEMOIRE] {str(r['content'] or '')[:180]}")
    except Exception as exc:
        erreurs.append(f"memoires: {exc}")

    if resultats:
        # Deduplication en conservant l'ordre.
        vus = set()
        uniques = []
        for ligne in resultats:
            if ligne not in vus:
                vus.add(ligne)
                uniques.append(ligne)
        return "\n".join(uniques[:20])

    if erreurs and len(erreurs) == 3:
        return "Erreur acces base de connaissances : " + " | ".join(erreurs)
    return f"Aucun resultat dans la culture de Neo pour '{query}'."


TOOL_SPECS_CULTURE = {
    "object_dimensions": {
        "level": "USER",
        "description": (
            "Donne les dimensions typiques en mm d'un objet courant "
            "(telephone, vis M3, carte bancaire, main, brique lego...). "
            "A utiliser pour concevoir avec des cotes realistes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objet": {
                    "type": "string",
                    "description": "Nom de l'objet (ex: 'telephone', 'vis M3')",
                },
            },
            "required": ["objet"],
        },
    },
    "knowledge_search": {
        "level": "USER",
        "description": (
            "Cherche dans la culture de Neo : connaissances apprises, "
            "documents indexes (noms + extraits) et memoires."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Texte ou mots-cles a chercher",
                },
            },
            "required": ["query"],
        },
    },
}
