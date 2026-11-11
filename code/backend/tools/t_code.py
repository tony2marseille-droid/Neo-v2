# -*- coding: utf-8 -*-
"""Outils d'edition de code (inspires d'Aider, sans dependance lourde).

Travaille sur des fichiers locaux, utilise le LLM auxiliaire pour produire le
nouveau contenu, ecrit le fichier, puis renvoie le diff git (et commit optionnel).
Aucune installation externe : git est deja present sur la machine.
"""
import os
import re
import shutil
import subprocess

from backend.tools.common import _llm_rapide, chemin_ecriture, chemin_lecture


def _git(repo_dir: str, *args: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        return out.strip() or ("(exit %d)" % r.returncode)
    except FileNotFoundError:
        return "git introuvable"
    except Exception as exc:
        return f"Erreur git: {exc}"


def tool_code_edit(task: str, file_path: str, repo_dir: str = "", commit: bool = False) -> str:
    task = (task or "").strip()
    file_path = (file_path or "").strip()
    if not task or not file_path:
        return "Erreur: task et file_path requis"
    # Ecriture confinee au workspace + extensions executables refusees.
    p, refus = chemin_ecriture(file_path, "code_edit")
    if refus:
        return refus
    file_path = str(p)
    if repo_dir:
        r, refus = chemin_lecture(repo_dir, "code_edit")
        if refus:
            return refus
        repo_dir = str(r)
    else:
        repo_dir = str(p.parent)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        original = ""
    except Exception as exc:
        return f"Erreur lecture: {exc}"

    # Limite de taille pour ne pas saturer le LLM
    if len(original) > 12000:
        return "Erreur: fichier trop grand (>12000 car.) pour edition LLM"

    prompt = (
        "Tu es un editeur de code expert. Modifie le fichier ci-dessous selon la tache.\n"
        "Renvoie UNIQUEMENT le nouveau contenu complet du fichier, sans explication, "
        "sans balises markdown, sans ```.\n\n"
        f"TACHE: {task}\n\n"
        f"FICHIER: {file_path}\n\n"
        f"CONTENU ACTUEL:\n{original}\n\n"
        "NOUVEAU CONTENU:"
    )
    # Contexte dimensionne pour l'entree ET la sortie (~3 car./token) : la
    # fenetre par defaut d'Ollama tronquait le fichier renvoye.
    num_ctx = min(32768, max(4096, (len(prompt) + len(original)) // 3 + 1024))
    nouveau = _llm_rapide(prompt, 180, num_ctx=num_ctx)
    if not nouveau.strip():
        return "Erreur: LLM n'a rien renvoye"

    # nettoyage eventuel de balises markdown
    nouveau = nouveau.strip()
    if nouveau.startswith("```"):
        nouveau = re.sub(r"^```[a-zA-Z]*\n?", "", nouveau)
        nouveau = re.sub(r"\n?```$", "", nouveau)

    # Garde-fou : une sortie beaucoup plus courte que l'original est presque
    # toujours une troncature du modele, pas une edition voulue.
    if original and len(nouveau) < len(original) * 0.5:
        return (f"Erreur: sortie LLM suspecte ({len(nouveau)} car. pour {len(original)} "
                "a l'origine, probable troncature) ; fichier NON modifie.")

    # ecriture (copie .bak de l'original avant)
    try:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        if original:
            shutil.copyfile(file_path, file_path + ".bak")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(nouveau)
    except Exception as exc:
        return f"Erreur ecriture: {exc}"

    # diff (fichier non suivi par git : on affiche le statut court)
    diff = _git(repo_dir, "diff", "--", file_path)
    if not diff or diff.startswith("(exit"):
        diff = _git(repo_dir, "status", "--short")
    sauvegarde = f" (sauvegarde {file_path}.bak)" if original else ""
    out = [f"[code_edit] {file_path}{sauvegarde}", f"diff:\n{diff[:4000]}"]

    if commit:
        _git(repo_dir, "add", file_path)
        msg = f"neo: {task[:60]}"
        c = _git(repo_dir, "commit", "-m", msg)
        out.append(f"commit: {c[:500]}")

    return "\n".join(out)


def tool_code_review(file_path: str, focus: str = "") -> str:
    file_path = (file_path or "").strip()
    if not file_path:
        return "Erreur: file_path requis"
    p, refus = chemin_lecture(file_path, "code_review")
    if refus:
        return refus
    file_path = str(p)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return f"Erreur lecture: {exc}"
    if len(content) > 12000:
        content = content[:12000] + "\n... (tronque)"
    prompt = (
        "Tu es un reviewer de code senior. Revue le fichier ci-dessous.\n"
        "Signale : bugs, failles de securite, mauvaises pratiques, lisibilite. "
        "Sois concis, en francais, avec severite (critique/majeur/mineur).\n"
        + (f"Focus: {focus}\n" if focus else "")
        + f"\nFICHIER: {file_path}\n\n{content}\n\nREVUE:"
    )
    revue = _llm_rapide(prompt, 180)
    return revue.strip() or "(revue indisponible)"


def tool_git_status(repo_dir: str) -> str:
    repo_dir = (repo_dir or "").strip()
    if not repo_dir:
        return "Erreur: repo_dir requis"
    p, refus = chemin_lecture(repo_dir, "git_status")
    if refus:
        return refus
    repo_dir = str(p)
    return _git(repo_dir, "status", "--short") + "\n\n" + _git(repo_dir, "log", "--oneline", "-5")
