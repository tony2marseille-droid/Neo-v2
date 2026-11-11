# -*- coding: utf-8 -*-
"""Journal general de Neo (JSONL rotatif).

Un seul point d'ecriture pour le backend Python (routes, outils, taches
detachees). Chaque ligne est un objet JSON, ce qui permet de filtrer par
source/niveau sans parser du texte libre.

Fichier par defaut : ``<racine>\\logs\\neo.log`` (surcharge : LOCALAI_LOG).
Rotation : 10 Mo x 5 archives. Toute erreur d'ecriture est avalee : un
journal ne doit jamais faire echouer une requete utilisateur.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from backend.core import reglages

RACINE = reglages.racine()
FICHIER = Path(os.environ.get("LOCALAI_LOG", str(RACINE / "logs" / "neo.log")))
MAX_OCTETS = int(os.environ.get("LOCALAI_LOG_MAX", str(10 * 1024 * 1024)))
ARCHIVES = int(os.environ.get("LOCALAI_LOG_KEEP", "5"))

_verrou = threading.Lock()
_niveau_min = os.environ.get("LOCALAI_LOG_NIVEAU", "info").lower()
_ORDRE = {"debug": 10, "info": 20, "avertissement": 30, "erreur": 40, "critique": 50}


def _rotation() -> None:
    try:
        if FICHIER.stat().st_size < MAX_OCTETS:
            return
    except OSError:
        return
    for i in range(ARCHIVES - 1, 0, -1):
        src = FICHIER.with_name(f"{FICHIER.name}.{i}")
        dst = FICHIER.with_name(f"{FICHIER.name}.{i + 1}")
        try:
            if src.exists():
                os.replace(src, dst)
        except OSError:
            pass
    try:
        os.replace(FICHIER, FICHIER.with_name(f"{FICHIER.name}.1"))
    except OSError:
        pass


def log(niveau: str, source: str, message: str, **champs) -> None:
    """Ecrit une entree de journal. Ne leve jamais."""
    niveau = (niveau or "info").lower()
    if _ORDRE.get(niveau, 20) < _ORDRE.get(_niveau_min, 20):
        return
    entree = {
        "ts": round(time.time(), 3),
        "date": datetime.now().isoformat(timespec="seconds"),
        "niveau": niveau,
        "source": source,
        "message": str(message)[:4000],
    }
    for k, v in champs.items():
        try:
            json.dumps(v)
            entree[k] = v
        except (TypeError, ValueError):
            entree[k] = repr(v)[:500]
    try:
        ligne = json.dumps(entree, ensure_ascii=False)
    except Exception:
        return
    try:
        with _verrou:
            FICHIER.parent.mkdir(parents=True, exist_ok=True)
            _rotation()
            with FICHIER.open("a", encoding="utf-8") as f:
                f.write(ligne + "\n")
    except OSError:
        # Dernier recours : stderr (visible dans les .out des taches).
        try:
            print(f"[logs] {ligne}", file=sys.stderr, flush=True)
        except Exception:
            pass


def debug(source: str, message: str, **champs) -> None:
    log("debug", source, message, **champs)


def info(source: str, message: str, **champs) -> None:
    log("info", source, message, **champs)


def avertissement(source: str, message: str, **champs) -> None:
    log("avertissement", source, message, **champs)


def erreur(source: str, message: str, **champs) -> None:
    log("erreur", source, message, **champs)


@contextmanager
def chrono(source: str, action: str, **champs):
    """Mesure une duree et journalise succes/echec.

    ``with chrono("sd", "generation", prompt=...):`` ecrit une ligne avec
    ``duree_s`` et ``ok`` (ou ``erreur``).
    """
    debut = time.time()
    try:
        yield
    except Exception as exc:
        log("erreur", source, action, duree_s=round(time.time() - debut, 2),
            ok=False, erreur=f"{type(exc).__name__}: {exc}", **champs)
        raise
    else:
        log("info", source, action, duree_s=round(time.time() - debut, 2),
            ok=True, **champs)


def lire(n: int = 200, niveau: str = "", source: str = "") -> list[dict]:
    """Retourne les n dernieres entrees (les plus recentes en dernier)."""
    n = max(1, min(int(n or 200), 5000))
    lignes: list[str] = []
    try:
        with FICHIER.open("r", encoding="utf-8", errors="replace") as f:
            lignes = f.readlines()[-n * 4:]
    except OSError:
        return []
    entrees = []
    for l in lignes:
        l = l.strip()
        if not l:
            continue
        try:
            e = json.loads(l)
        except Exception:
            continue
        if niveau and e.get("niveau") != niveau:
            continue
        if source and source.lower() not in str(e.get("source", "")).lower():
            continue
        entrees.append(e)
    return entrees[-n:]


def stats() -> dict:
    """Taille du journal et repartition par niveau (pour /api/logs/stats)."""
    try:
        taille = FICHIER.stat().st_size
    except OSError:
        taille = 0
    entrees = lire(5000)
    par_niveau: dict[str, int] = {}
    par_source: dict[str, int] = {}
    for e in entrees:
        par_niveau[e.get("niveau", "?")] = par_niveau.get(e.get("niveau", "?"), 0) + 1
        s = str(e.get("source", "?"))
        par_source[s] = par_source.get(s, 0) + 1
    return {
        "fichier": str(FICHIER),
        "taille_octets": taille,
        "entrees_recentes": len(entrees),
        "par_niveau": par_niveau,
        "par_source": dict(sorted(par_source.items(), key=lambda kv: -kv[1])[:15]),
    }
