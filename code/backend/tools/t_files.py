# -*- coding: utf-8 -*-
import shlex
import subprocess
from pathlib import Path

from backend.core.security import commande_autorisee as _commande_bloquee, journaliser
from backend.tools.common import (_CMD_FORBIDDEN_CHARS, _CMD_INTERDITS_TOUJOURS,
    _SANDBOX_LOCK, _resolve_sandbox, _sandbox_cfg, chemin_ecriture)


def tool_list_dir(path: str) -> str:
    p, refus = _resolve_sandbox(path, "list_dir", ecriture=False)
    if refus:
        return refus
    if not p.exists():
        return f"Chemin introuvable: {p}"
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    lines = []
    for entry in entries[:200]:
        kind = "DIR " if entry.is_dir() else "FILE"
        size = entry.stat().st_size if entry.is_file() else 0
        lines.append(f"{kind}  {size:>12}  {entry.name}")
    return "\n".join(lines) if lines else "(vide)"


def tool_read_file(path: str) -> str:
    p, refus = _resolve_sandbox(path, "read_file", ecriture=False)
    if refus:
        return refus
    if not p.is_file():
        return f"Fichier introuvable: {p}"
    data = p.read_text("utf-8", errors="replace")
    if len(data) > 8000:
        return data[:8000] + f"\n...[tronque, {len(data)} caracteres au total]"
    return data


def tool_write_file(path: str, content: str) -> str:
    # Extension verifiee sur le chemin brut ET resolu (x.bat., x.bat::$DATA).
    p, refus = chemin_ecriture(path, "write_file")
    if refus:
        return refus
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Ecrit: {p} ({len(content)} caracteres)"


def _verifier_arguments(head: str, args: list[str]) -> str | None:
    """Chaque argument ressemblant a un chemin doit rester dans les racines de
    lecture (relatif = depuis le workspace) : sans cela `type` ou `findstr`
    avec un chemin absolu ou des `..` lisent n'importe ou."""
    if head in ("dir", "type") and any(c in "".join(args) for c in "%^!"):
        # cmd.exe developpe %VAR% / ^ / ! apres notre controle.
        return "refusé: caracteres %, ^ et ! interdits pour dir/type"
    for a in args:
        bas = a.lower()
        if head.startswith("findstr") and bas[:3] in ("/g:", "/f:", "/d:"):
            return f"refusé: option findstr {a[:3]} interdite en sandbox"
        valeur = a
        if a.startswith(("-", "/")):
            # Option : seule une valeur collee (-o=x, /c:x) peut etre un chemin.
            sep = "=" if "=" in a else (":" if a.startswith("/") and ":" in a else "")
            if not sep:
                continue
            valeur = a.split(sep, 1)[1]
            if head.startswith("findstr") and bas.startswith("/c:"):
                continue  # /c:"texte" = motif recherche, pas un chemin
        if not valeur:
            continue
        _, refus = _resolve_sandbox(valeur, "run_command", ecriture=False)
        if refus:
            return refus
    return None


def tool_run_command(command: str) -> str:
    cfg = _sandbox_cfg()
    cmd = (command or "").strip()
    if not cmd:
        return "refusé: commande vide"
    for ch in _CMD_FORBIDDEN_CHARS:
        if ch in cmd:
            msg = f"caractere interdit en sandbox: {ch!r}"
            journaliser("tool_denied", {"tool": "run_command",
                                        "command": cmd[:200], "reason": msg})
            return f"refusé: {msg} (pas de pipelines/redirections en sandbox)"
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError as exc:
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200],
                                    "reason": f"parsing KO: {exc}"})
        return f"refusé: ligne de commande non parseable ({exc})"
    parts = [p.strip('"') for p in parts if p.strip()]
    if not parts:
        return "refusé: commande vide"
    head = parts[0].lstrip(".").lower()
    nom_base = Path(head).name.lower()
    if nom_base in cfg["cmd_deny"] or head in cfg["cmd_deny"]:
        msg = f"executable interdit en sandbox: {parts[0]}"
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"refusé: {msg}"
    if head not in cfg["cmd_allow"] and nom_base not in cfg["cmd_allow"]:
        msg = (f"executable hors liste blanche: {parts[0]} "
               f"(autorisé: {', '.join(cfg['cmd_allow'])})")
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"refusé: {msg}"
    if nom_base in _CMD_INTERDITS_TOUJOURS or head in _CMD_INTERDITS_TOUJOURS:
        msg = f"interpreteur interdit en sandbox: {parts[0]} (utilise check_code)"
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"refusé: {msg}"
    refus_args = _verifier_arguments(head, parts[1:])
    if refus_args:
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": refus_args})
        return refus_args
    if head in ("dir", "type"):
        parts = ["cmd.exe", "/d", "/c"] + parts
    blocage = _commande_bloquee(cmd)
    if blocage:
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200],
                                    "reason": "motif securite blocage"})
        return blocage
    racine = cfg["root"]
    try:
        racine.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        with _SANDBOX_LOCK:
            proc = subprocess.run(
                parts,
                shell=False,
                cwd=str(racine),
                capture_output=True,
                text=True,
                timeout=cfg["timeout_s"],
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired:
        msg = f"TIMEOUT (> {cfg['timeout_s']}s)"
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"Erreur: timeout sandbox {msg}, process arrete"
    except FileNotFoundError:
        return f"refusé: executable introuvable: {parts[0]}"
    out = ((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr.strip() else "")).strip()
    cap = cfg["max_output"]
    if len(out) > cap:
        out = out[:cap] + f"\n...[sortie tronquee a {cap} caracteres]"
    return f"[code {proc.returncode}]\n{out if out else '(aucune sortie)'}"



def _afficher_derniere_expression(code: str) -> str:
    """Comme une console Python : si la derniere instruction est une expression
    (`statistics.stdev(v)`, `ecart_type`), sa valeur est affichee."""
    import ast

    try:
        arbre = ast.parse(code)
    except SyntaxError:
        return code
    if not arbre.body or not isinstance(arbre.body[-1], ast.Expr):
        return code
    derniere = arbre.body[-1]
    appel = derniere.value
    if isinstance(appel, ast.Call) and isinstance(appel.func, ast.Name) and appel.func.id == "print":
        return code
    # « a = 1; a » : l'expression partage sa ligne, on ne decoupe pas.
    if derniere.col_offset != 0 or (len(arbre.body) > 1 and arbre.body[-2].end_lineno >= derniere.lineno):
        return code
    lignes = code.splitlines()
    avant = "\n".join(lignes[: derniere.lineno - 1])
    expression = "\n".join(lignes[derniere.lineno - 1: derniere.end_lineno])
    return f"{avant}\n__resultat_neo = (\n{expression}\n)\nif __resultat_neo is not None:\n    print(__resultat_neo)\n"


def tool_check_code(code: str, langage: str = "python", executer: bool = False) -> str:
    """Agent de controle : valide la syntaxe et peut executer le code.

    L'execution lance du Python ARBITRAIRE : l'outil est de niveau POWER dans
    le registre (seul point d'entree du modele) ; ici on borne seulement le
    temps (15 s) et on isole l'interpreteur (-I, dossier temporaire jetable).
    """
    import ast
    import sys as _sys
    import tempfile

    code = (code or "").strip()
    if not code:
        return "Erreur: code vide"
    langage = (langage or "python").lower()
    rapports = []

    if langage in ("python", "py"):
        try:
            ast.parse(code)
            rapports.append("Syntaxe Python: OK")
        except SyntaxError as exc:
            return (
                f"ECHEC SYNTAXE Python ligne {exc.lineno}: {exc.msg}\n"
                f">>> {exc.text.strip() if exc.text else ''}"
            )
        if executer:
            try:
                with tempfile.TemporaryDirectory(prefix="check_code_", ignore_cleanup_errors=True) as tmp:
                    proc = subprocess.run(
                        [_sys.executable, "-I", "-X", "utf8", "-c", _afficher_derniere_expression(code)],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        cwd=tmp,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                sortie = (proc.stdout or "").strip()[:2000]
                err = (proc.stderr or "").strip()[:1000]
                # Sans sortie, le modele comblait le vide en inventant un
                # resultat (ecart-type « 3,7026 » au lieu de 3,7749, 09/11/2026).
                vide = ("\nSortie: AUCUNE (le programme n'affiche rien). N'invente aucune valeur : "
                        "ajoute print(...) et relance.") if not sortie and proc.returncode == 0 else ""
                rapports.append(
                    f"Execution sandbox: exit {proc.returncode}"
                    + (f"\nSortie:\n{sortie}" if sortie else vide)
                    + (f"\nErreurs:\n{err}" if err else "")
                )
            except subprocess.TimeoutExpired:
                rapports.append("Execution sandbox: TIMEOUT (>15s), code probablement bloquant")
    elif langage in ("javascript", "js", "node"):
        with tempfile.TemporaryDirectory(prefix="check_code_", ignore_cleanup_errors=True) as tmp:
            f = Path(tmp) / "_check_code.js"
            f.write_text(code, encoding="utf-8")
            try:
                proc = subprocess.run(
                    ["node", "--check", str(f)], capture_output=True, text=True, timeout=20
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"Controle JavaScript indisponible: {exc}"
        if proc.returncode == 0:
            rapports.append("Syntaxe JavaScript: OK")
        else:
            return f"ECHEC SYNTAXE JS:\n{(proc.stderr or '').strip()[:800]}"
    elif langage == "json":
        import json as _json

        try:
            _json.loads(code)
            rapports.append("JSON: valide")
        except ValueError as exc:
            return f"JSON INVALIDE: {exc}"
    else:
        return f"Langage non supporte: {langage} (python, javascript, json)"

    return "\n".join(rapports)
