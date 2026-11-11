# -*- coding: utf-8 -*-
"""Implementation d'une idee approuvee par le conseil (version robuste).

Usage : python -u impl_idee.py <id> <titre> <description>
Boucle de 3 tentatives max avec le dev agent pour produire au moins un fichier de code
sous workspace/ameliorations/<id>/ . Met a jour le statut (terminee / echec).
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

SERVEUR = "http://127.0.0.1:8320"
RACINE = Path(".").resolve()


def log(msg):
    print(f"[impl_idee] {msg}", flush=True)


def maj_statut(idee_id, statut):
    p = Path("data/conseil_idees.jsonl")
    lignes = []
    for l in p.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        d = json.loads(l)
        if str(d.get("id")) == str(idee_id):
            d["statut"] = statut
        lignes.append(json.dumps(d, ensure_ascii=False))
    p.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def code_files_exist(dossier):
    return any(
        p.is_file() and p.suffix in {".py", ".rs", ".tsx", ".ts", ".js"}
        for p in dossier.iterdir()
    )


def run_chat(idee_id, dossier, titre, description, tentative):
    dwin = str(dossier).replace("/", "\\")
    chemin_code = dwin + "\\code.py"
    if tentative == 1:
        tache = (
            f"Implemente cette amelioration de Neo :\n"
            f"TITRE : {titre}\nDESCRIPTION : {description}\n\n"
            f"REGLES OBLIGATOIRES :\n"
            f"- Utilise UNIQUEMENT des chemins ABSOLUS commencant par {dwin}\n"
            f"- Ecris obligatoirement un fichier de code : {chemin_code}\n"
            f"- Pas de chemin relatif, pas d'autre dossier.\n\n"
            f"METHODE :\n"
            f"1) plan.md (5 etapes max) sous {dwin}\\plan.md\n"
            f"2) Le CODE complet dans {chemin_code}\n"
            f"3) Verifie {chemin_code} avec check_code\n"
            f"4) implementation.md sous {dwin}\\implementation.md\n"
        )
    else:
        tache = (
            f"TENTATIVE {tentative} : le fichier de code n'a pas encore ete cree.\n"
            f"Ecris OBLIGATOIREMENT le code Python dans ce fichier exact :\n{chemin_code}\n"
            f"Ce chemin est dans le dossier autorisé {dwin}.\n"
            f"Ne demande pas, ecris le fichier maintenant.\n"
            f"TITRE : {titre}\nDESCRIPTION : {description}\n"
        )
    body = json.dumps({
        "message": tache,
        "model": "localai-prog-v9.2-q5km:latest",
        "agent_id": "dev",
        "temperature": 0.3,
    }).encode("utf-8")
    log(f"tentative {tentative} : envoi au dev agent...")
    reponse = ""
    try:
        req = urllib.request.Request(
            f"{SERVEUR}/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1800) as flux:
            for ligne in flux:
                ligne = ligne.decode("utf-8", errors="replace").strip()
                if not ligne.startswith("data: "):
                    continue
                try:
                    ev = json.loads(ligne[6:])
                except Exception:
                    continue
                if ev.get("type") == "token":
                    tok = ev.get("content", "")
                    reponse += tok
                    print(tok, end="", flush=True)
                elif ev.get("type") == "error":
                    print(f"\n[erreur cerveau] {ev.get('error')}")
                    return False
                elif ev.get("type") == "done":
                    break
    except Exception as exc:
        print(f"\n[erreur flux] {exc}")
        return False
    print()  # newline after token stream
    (dossier / "implementation.md").write_text(
        f"# Implementation idee #{idee_id} : {titre}\n\n{reponse}",
        encoding="utf-8",
    )
    return code_files_exist(dossier)


def main(idee_id, titre, description):
    dossier = RACINE / "workspace" / "ameliorations" / str(idee_id)
    dossier.mkdir(parents=True, exist_ok=True)
    debut = time.time()
    ok = False
    for tentative in range(1, 4):
        ok = run_chat(idee_id, dossier, titre, description, tentative)
        if ok:
            break
        log(f"tentative {tentative} : aucun fichier de code, reprise...")
    minutes = round((time.time() - debut) / 60, 1)
    fichiers = sorted(p.name for p in dossier.iterdir() if p.is_file())
    if ok:
        maj_statut(idee_id, "terminee")
        log(f"TERMINE en {minutes} min : {', '.join(fichiers)}")
        return 0
    maj_statut(idee_id, "echec")
    log(f"ECHEC en {minutes} min : aucun fichier de code ({fichiers})")
    return 1


if __name__ == "__main__":
    sys.exit(main(
        sys.argv[1] if len(sys.argv) > 1 else "",
        sys.argv[2] if len(sys.argv) > 2 else "",
        sys.argv[3] if len(sys.argv) > 3 else "",
    ))
