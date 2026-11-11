# -*- coding: utf-8 -*-
"""Briques communes des outils : sandbox de chemins, garde SSRF, recherche
d'executables, helpers trimesh et appel LLM court."""
import ipaddress
import os
import socket
import threading
from pathlib import Path

from backend.core.security import charger_regles, journaliser


_SANDBOX_LOCK = threading.Lock()

_CMD_FORBIDDEN_CHARS = ("|", "&", ";", ">", "<", "`", "$(")

_CMD_DENY_DEFAULT = [
    "del", "rmdir", "format", "reg", "schtasks", "net", "sc",
    "powershell", "pwsh", "cmd", "curl", "wget", "ssh", "scp", "taskkill",
]

# Liste blanche par defaut de run_command. Les interpreteurs (python, node)
# et git ont ete retires : `python -c "__import__('os').system(...)"`, un
# script node ou `git -c alias.x=!cmd` executent n'importe quoi et annulent
# la sandbox. Pour du code : check_code (POWER, timeout) ; pour git : les
# outils dedies git_status / git_diff.
_CMD_ALLOW_DEFAULT = [
    "openscad", "openscad.exe", "dir", "type", "findstr", "findstr.exe",
]

# Refuses meme si un pack de regles (security_rules.json) les autorise
# encore dans cmd_allow : ce sont des executeurs de code arbitraire.
_CMD_INTERDITS_TOUJOURS = {
    "python", "python.exe", "python3", "python3.exe", "pythonw", "pythonw.exe",
    "py", "py.exe", "node", "node.exe", "git", "git.exe", "wscript", "wscript.exe",
    "cscript", "cscript.exe", "mshta", "mshta.exe", "rundll32", "rundll32.exe",
}

# Extensions refusees en ecriture en plus du pack de regles (scripts et
# raccourcis executables par l'Explorateur Windows).
_EXT_BLOQUEES_SUPPL = {
    ".js", ".jse", ".vbe", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".com",
    ".psm1", ".pif", ".cpl", ".scf",
}


def _est_sous(chemin: Path, racine: Path) -> bool:
    try:
        chemin.relative_to(racine)
        return True
    except ValueError:
        return False


def _default_root() -> Path:
    """Racine par defaut selon l'OS (Windows: E:\\ia, Linux: /opt/oracle ou ~/.oracle)."""
    if os.name == "nt":
        return Path(os.environ.get("LOCALAI_ROOT", r"E:\ia"))
    # Linux/macOS : /opt/oracle (si root) ou ~/.oracle (utilisateur)
    if os.geteuid() == 0:
        return Path("/opt/oracle")
    return Path.home() / ".oracle"


def _sandbox_cfg() -> dict:
    regles = charger_regles()
    racine = _default_root()
    return {
        "root": Path(regles.get("sandbox_root", str(racine / "workspace"))),
        "read_roots": [Path(p) for p in regles.get(
            "sandbox_read_roots",
            [str(racine / "workspace"), str(racine / "data" / "documents")])],
        "cmd_allow": [a.lower() for a in regles.get("cmd_allow", _CMD_ALLOW_DEFAULT)],
        "cmd_deny": [d.lower() for d in regles.get("cmd_deny", _CMD_DENY_DEFAULT)],
        "timeout_s": int(regles.get("timeout_s", 60)),
        "max_output": int(regles.get("max_output", 20000)),
    }


def _dossier_produits() -> Path | None:
    """Dossier des images/videos generees (reglage neo_documents_dir)."""
    try:
        from backend.core.config import settings

        neo = getattr(settings, "neo_documents_dir", None)
        return Path(neo).expanduser() if neo else None
    except Exception:
        return None


def _refus_sandbox(outil: str, chemin: str, raison: str, ecriture: bool) -> str:
    journaliser("read_refused" if not ecriture else "tool_denied",
                {"tool": outil, "path": chemin, "reason": raison, "write": ecriture})
    return f"refusé: {raison} ({outil}: {chemin})"


def _resolve_sandbox(path: str, outil: str, ecriture: bool) -> tuple:
    """(Path, None) si le chemin resolu est sous une racine autorisee, sinon
    (None, refus). Ecriture : sandbox_root seul. Lecture : sandbox_read_roots
    + dossier des produits generes. Un chemin relatif part de sandbox_root."""
    cfg = _sandbox_cfg()
    brut = str(path or "")
    if not brut.strip():
        return None, _refus_sandbox(outil, brut, "chemin vide", ecriture)
    p = Path(brut.strip()).expanduser()
    if not p.is_absolute():
        p = cfg["root"] / p
    racines = [cfg["root"]] if ecriture else list(cfg["read_roots"])
    if not ecriture:
        produits = _dossier_produits()
        if produits:
            racines.append(produits)
    try:
        resol = p.resolve()
    except OSError:
        return None, _refus_sandbox(outil, brut, "resolution impossible (lien/junction)", ecriture)
    for r in racines:
        try:
            r_resol = r.resolve()
        except OSError:
            continue
        if _est_sous(resol, r_resol):
            return resol, None
    journaliser("acces_refuse" if ecriture else "lecture_refusee",
                {"tool": outil, "path": str(resol), "write": ecriture})
    return None, _refus_sandbox(
        outil, brut,
        f"chemin hors des racines autorisees {[str(r) for r in racines]}", ecriture)


def extension_interdite(chemin) -> str | None:
    """Message de blocage si le nom de fichier est dangereux, sinon None.

    A appeler sur le chemin BRUT et sur le chemin RESOLU : Windows ignore les
    points/espaces finaux (`x.bat.`) et `x.bat::$DATA` ecrit dans le flux
    principal de x.bat."""
    brut = str(chemin or "")
    nom = Path(brut).name
    if nom and nom != nom.rstrip(" ."):
        return "ECRITURE BLOQUEE : nom de fichier termine par un point ou un espace."
    if ":" in os.path.splitdrive(brut)[1]:
        return "ECRITURE BLOQUEE : ':' interdit dans un nom de fichier (flux NTFS)."
    suffixe = Path(brut).suffix.lower()
    bloquees = {e.lower() for e in charger_regles().get("blocked_extensions_write", [])}
    if suffixe in bloquees | _EXT_BLOQUEES_SUPPL:
        journaliser("ecriture_bloquee", {"path": brut, "extension": suffixe})
        return (f"ECRITURE BLOQUEE : l'extension {suffixe} est interdite par les "
                "regles de securite (executables/scripts).")
    return None


def chemin_lecture(path: str, outil: str) -> tuple:
    """(Path resolu, None) si lisible par un outil, sinon (None, refus)."""
    return _resolve_sandbox(path, outil, ecriture=False)


def chemin_ecriture(path: str, outil: str) -> tuple:
    """(Path resolu, None) si l'outil peut ecrire ici (workspace uniquement,
    extension autorisee), sinon (None, refus)."""
    blocage = extension_interdite(path)
    if blocage:
        journaliser("tool_denied", {"tool": outil, "path": str(path), "reason": blocage})
        return None, blocage
    p, refus = _resolve_sandbox(path, outil, ecriture=True)
    if refus:
        return None, refus
    # Un .git/config ecrit par le modele (core.fsmonitor, filtres...) ferait
    # executer une commande au prochain git_status/git_diff.
    blocage = extension_interdite(p) or (
        "ECRITURE BLOQUEE : ecriture dans un dossier .git interdite."
        if any(part.lower() == ".git" for part in p.parts) else None)
    if blocage:
        journaliser("tool_denied", {"tool": outil, "path": str(p), "reason": blocage})
        return None, blocage
    return p, None


# ---- Garde SSRF : seules les adresses publiques sont joignables ----

class UrlRefusee(Exception):
    """URL visant une adresse locale/privee (levee par le hook httpx)."""


def _ip_publique(ip) -> bool:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def refus_url(url: str) -> str | None:
    """None si l'URL http(s) vise uniquement des adresses publiques, sinon le
    motif du refus. Bloque loopback (serveur Neo :8320, Ollama, ComfyUI),
    reseau prive, link-local, multicast et plages reservees."""
    from urllib.parse import urlsplit

    try:
        morceaux = urlsplit((url or "").strip())
        hote = morceaux.hostname
        port = morceaux.port or (443 if morceaux.scheme == "https" else 80)
    except ValueError:
        return "refusé: URL invalide"
    if morceaux.scheme not in ("http", "https") or not hote:
        return "refusé: URL invalide (http/https uniquement)"
    hote = hote.rstrip(".").lower()
    if hote == "localhost" or hote.endswith(".localhost"):
        return f"refusé: hote local interdit ({hote})"
    try:
        infos = socket.getaddrinfo(hote, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return f"refusé: hote introuvable ({hote})"
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            return f"refusé: adresse illisible pour {hote}"
        if not _ip_publique(ip):
            journaliser("ssrf_refuse", {"url": url[:300], "ip": str(ip)})
            return f"refusé: adresse non publique ({hote} -> {ip})"
    return None


def _hook_ssrf(request) -> None:
    refus = refus_url(str(request.url))
    if refus:
        raise UrlRefusee(refus)


def client_http(**options):
    """httpx.Client dont CHAQUE requete (redirections comprises) passe par la
    garde SSRF. A utiliser pour toute URL fournie par le modele."""
    import httpx

    options.setdefault("follow_redirects", True)
    return httpx.Client(event_hooks={"request": [_hook_ssrf]}, **options)


def http_get(url: str, **options):
    """GET protege SSRF (leve UrlRefusee si une etape vise une adresse locale)."""
    timeout = options.pop("timeout", 20)
    with client_http(timeout=timeout) as client:
        return client.get(url, **options)


def _html_unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


html_unescape = _html_unescape


# Ancien nom LOCALAI_OPENCAD (faute de frappe) accepte pour compatibilite.
_OPENCAD_PATHS = [
    os.environ.get("LOCALAI_OPENSCAD", "") or os.environ.get("LOCALAI_OPENCAD", ""),
    str(_default_root() / "tools" / "OpenSCAD" / "openscad-2021.01" / "openscad.exe"),
    "/usr/bin/openscad",
    "/usr/local/bin/openscad",
    "/opt/openscad/bin/openscad",
    r"C:\Program Files\OpenSCAD\openscad.exe",
    r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
]
_SLICER_PATHS = {
    "prusa": [
        os.environ.get("LOCALAI_PRUSA", ""),
        "/usr/bin/prusa-slicer-console",
        "/usr/bin/prusa-slicer",
        r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
        r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
    ],
    "orca": [
        os.environ.get("LOCALAI_ORCA", ""),
        "/usr/bin/orca-slicer-console",
        "/usr/bin/orca-slicer",
        r"C:\Program Files\OrcaSlicer\orca-console.exe",
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
    ],
    "cura": [
        os.environ.get("LOCALAI_CURA", ""),
        "/usr/bin/CuraEngine",
        r"C:\Program Files\Ultimaker Cura 5*\CuraEngine.exe",
    ],
    "bambu": [
        os.environ.get("LOCALAI_BAMBU", ""),
        "/usr/bin/bambu-studio-console",
        "/usr/bin/bambu-studio",
        r"C:\Program Files\Bambu Studio\bambu-studio-console.exe",
        r"C:\Program Files\Bambu Studio\BambuStudio.exe",
    ],
}


def _find_exe(patterns: list[str], env: str = "") -> str:
    """Premier executable existant. `env` : variable d'environnement propre a
    l'outil cherche (ex. LOCALAI_OPENSCAD), prioritaire si elle pointe un fichier."""
    import glob as _glob

    surcharge = os.environ.get(env, "") if env else ""
    if surcharge and Path(surcharge).exists():
        return surcharge
    for p in patterns:
        if not p:
            continue
        if "*" in p:
            found = sorted(_glob.glob(p))
            if found:
                return found[-1]
            continue
        if Path(p).exists():
            return p
    return ""


def _load_trimesh(mesh_path: str):
    import trimesh

    return trimesh.load(str(mesh_path), force="mesh")


def _poser_z0(mesh) -> None:
    """Repose l'objet sur le plateau (Z min = 0) et le centre en XY."""
    import numpy as np

    b = np.asarray(mesh.bounds, dtype=float)
    mesh.apply_translation([
        -(b[0][0] + b[1][0]) / 2.0,
        -(b[0][1] + b[1][1]) / 2.0,
        -b[0][2],
    ])


def _find_vision_model() -> str:
    import httpx

    try:
        base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
        tags = httpx.get(f"{base}/api/tags", timeout=6).json().get("models", [])
        names = [m.get("name", "") for m in tags]
    except Exception:
        return ""
    patterns = [
        "gemma4", "qwen2.5vl", "qwen2-vl", "qvq", "llava",
        "minicpm-v", "llama3.2-vision", "gemma3", "moondream",
    ]
    for pat in patterns:
        for n in names:
            if pat in n.lower():
                return n
    return ""


def _llm_rapide(prompt: str, timeout_s: int = 120, num_ctx: int = 0) -> str:
    """Appel court au modele auxiliaire local pour synthese/variations.
    num_ctx > 0 : fenetre de contexte explicite (sinon defaut Ollama, 2048-4096,
    qui tronque silencieusement les longues entrees)."""
    import httpx as _httpx

    corps = {"model": "gemma3:4b", "prompt": prompt, "stream": False}
    if num_ctx > 0:
        corps["options"] = {"num_ctx": int(num_ctx)}
    try:
        rep = _httpx.post(
            "http://127.0.0.1:11434/api/generate", json=corps, timeout=timeout_s,
        ).json()
        return rep.get("response", "")
    except Exception:
        return ""
