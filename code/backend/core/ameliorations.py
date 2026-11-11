# -*- coding: utf-8 -*-
"""Application des idees du conseil.

Le dev agent ecrit son code dans ``workspace/ameliorations/<id>/code.py``.
Ce module installe ce code comme module d'amelioration dans
``E:\\ia\\ameliorations\\`` apres validation, tient un manifeste et permet
de revenir en arriere (desinstallation).

Pourquoi un registre plutot qu'une reecriture automatique du coeur :
le code produit par un LLM n'est pas fiable a 100 % et une fusion directe
dans ``backend/`` ou ``rust_src/`` casserait le serveur. L'application est
donc *reelle* (le fichier quitte le bac a sable et entre dans le projet,
il est reference et reutilisable), *validee* (compilation Python) et
*reversible* (backup + desinstallation).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

RACINE = Path(os.environ.get("LOCALAI_ROOT", r"E:\ia"))
WORKSPACE = RACINE / "workspace" / "ameliorations"
DEST = RACINE / "ameliorations"
MANIFESTE = RACINE / "data" / "ameliorations.json"
IDEES = RACINE / "data" / "conseil_idees.jsonl"

# Appels jugees risques s'ils sont executes au chargement du module.
_RISQUES = {
    "os.system", "os.popen", "os.remove", "os.rmdir", "shutil.rmtree",
    "subprocess.run", "subprocess.call", "subprocess.Popen", "eval", "exec",
}


def _slug(titre: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (titre or "").lower()).strip("_")
    return (s or "idee")[:48]


def _lire_manifeste() -> dict:
    try:
        data = json.loads(MANIFESTE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ecrire_manifeste(data: dict) -> None:
    MANIFESTE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFESTE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, MANIFESTE)


def _maj_statut(idee_id, statut: str, **extra) -> None:
    if not IDEES.is_file():
        return
    lignes = []
    for l in IDEES.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except Exception:
            lignes.append(l)
            continue
        if str(d.get("id")) == str(idee_id):
            d["statut"] = statut
            d.update(extra)
        lignes.append(json.dumps(d, ensure_ascii=False))
    IDEES.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def _appels_risques(source: str) -> list[str]:
    """Repere les appels dangereux au niveau module (pas dans une fonction).

    Une idee qui lance ``os.system`` a l'import est marquee ``a_valider`` :
    elle est installee mais l'utilisateur doit l'examiner avant usage.
    """
    trouves: list[str] = []
    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return trouves
    for noeud in arbre.body:  # uniquement le niveau module
        for enfant in ast.walk(noeud) if not isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else []:
            if isinstance(enfant, ast.Call):
                f = enfant.func
                nom = ""
                if isinstance(f, ast.Attribute):
                    base = f.value.id if isinstance(f.value, ast.Name) else ""
                    nom = f"{base}.{f.attr}" if base else f.attr
                elif isinstance(f, ast.Name):
                    nom = f.id
                if nom in _RISQUES and nom not in trouves:
                    trouves.append(nom)
    return trouves


def _deps_manquantes(source: str) -> list[str]:
    """Modules importes mais absents de l'environnement (hors stdlib).

    On se contente de `find_spec` : aucun module n'est execute, donc une idee
    qui ouvre une fenetre ou lance un process a l'import ne fait rien ici.
    """
    try:
        arbre = ast.parse(source)
    except SyntaxError:
        return []
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for a in noeud.names:
                noms.add(a.name.split(".")[0])
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and not noeud.level:
            noms.add(noeud.module.split(".")[0])
    internes = {"backend", "neo"}
    stdlib = getattr(sys, "stdlib_module_names", set())
    manquants = []
    for n in sorted(noms):
        if n in internes or n in stdlib:
            continue
        try:
            if importlib.util.find_spec(n) is None:
                manquants.append(n)
        except Exception:
            manquants.append(n)
    return manquants


def _compiler(chemin: Path) -> tuple[bool, str]:
    try:
        py_compile.compile(str(chemin), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)[:1200]
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def appliquer(idee_id) -> dict:
    """Installe l'idee dans le projet. Retourne un compte-rendu serialisable."""
    idee_id = str(idee_id)
    source = WORKSPACE / idee_id / "code.py"
    if not source.is_file():
        return {"ok": False, "id": idee_id,
                "erreur": f"aucun code.py dans {source.parent}"}
    try:
        texte = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "id": idee_id, "erreur": str(exc)}

    ok, erreur = _compiler(source)
    if not ok:
        _maj_statut(idee_id, "echec_application", application_erreur=erreur[:400])
        return {"ok": False, "id": idee_id, "erreur": f"compilation: {erreur}"}

    titre = ""
    for l in IDEES.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(l)
        except Exception:
            continue
        if str(d.get("id")) == idee_id:
            titre = str(d.get("titre", ""))
            break

    DEST.mkdir(parents=True, exist_ok=True)
    cible = DEST / f"{idee_id}_{_slug(titre)}.py"
    backup = ""
    if cible.is_file():
        backup = str(cible.with_suffix(f".bak_{int(time.time())}.py"))
        shutil.copy2(cible, backup)

    try:
        shutil.copy2(source, cible)
    except OSError as exc:
        return {"ok": False, "id": idee_id, "erreur": f"copie: {exc}"}

    # Verification apres copie : le fichier installe doit compiler.
    ok2, err2 = _compiler(cible)
    if not ok2:
        try:
            cible.unlink(missing_ok=True)
            if backup:
                shutil.copy2(backup, cible)
        except OSError:
            pass
        return {"ok": False, "id": idee_id, "erreur": f"verification: {err2}"}

    risques = _appels_risques(texte)
    manquants = _deps_manquantes(texte)
    statut = "a_valider" if (risques or manquants) else "appliquee"
    entree = {
        "id": idee_id,
        "titre": titre,
        "fichier": str(cible),
        "sha1": hashlib.sha1(cible.read_bytes()).hexdigest(),
        "octets": cible.stat().st_size,
        "date": datetime.now().isoformat(timespec="seconds"),
        "statut": statut,
        "appels_risques": risques,
        "dependances_manquantes": manquants,
        "backup": backup,
    }
    manifeste = _lire_manifeste()
    manifeste[idee_id] = entree
    _ecrire_manifeste(manifeste)
    _maj_statut(idee_id, statut, fichier_applique=str(cible))

    try:
        from backend.core.logs import log as _log

        _log("info", "ameliorations", "idee appliquee", id=idee_id,
             statut=statut, fichier=str(cible), risques=risques,
             dependances_manquantes=manquants)
    except Exception:
        pass
    return {"ok": True, **entree}


def desinstaller(idee_id) -> dict:
    idee_id = str(idee_id)
    manifeste = _lire_manifeste()
    entree = manifeste.pop(idee_id, None)
    if not entree:
        return {"ok": False, "id": idee_id, "erreur": "idee non appliquee"}
    try:
        Path(entree.get("fichier", "")).unlink(missing_ok=True)
    except OSError:
        pass
    _ecrire_manifeste(manifeste)
    _maj_statut(idee_id, "terminee", fichier_applique="")
    try:
        from backend.core.logs import log as _log

        _log("info", "ameliorations", "idee desinstallee", id=idee_id)
    except Exception:
        pass
    return {"ok": True, "id": idee_id, "statut": "terminee"}


def lister() -> dict:
    manifeste = _lire_manifeste()
    entrees = []
    for e in sorted(manifeste.values(), key=lambda v: v.get("date", ""), reverse=True):
        fichier = Path(e.get("fichier", ""))
        entrees.append({**e, "present": fichier.is_file()})
    return {"ok": True, "total": len(entrees), "appliquees": entrees,
            "dossier": str(DEST), "manifeste": str(MANIFESTE)}


def verifier(idee_id) -> dict:
    """Compile le code du workspace sans l'installer."""
    source = WORKSPACE / str(idee_id) / "code.py"
    if not source.is_file():
        return {"ok": False, "id": str(idee_id), "erreur": "code.py absent"}
    ok, erreur = _compiler(source)
    return {"ok": ok, "id": str(idee_id), "erreur": erreur}


if __name__ == "__main__":
    import sys

    action = sys.argv[1] if len(sys.argv) > 1 else "lister"
    if action == "lister":
        print(json.dumps(lister(), ensure_ascii=False, indent=1))
    elif action in ("appliquer", "desinstaller", "verifier"):
        fonction = {"appliquer": appliquer, "desinstaller": desinstaller,
                    "verifier": verifier}[action]
        print(json.dumps(fonction(sys.argv[2]), ensure_ascii=False, indent=1))
    else:
        print("usage: ameliorations.py [lister|appliquer|desinstaller|verifier] <id>")
