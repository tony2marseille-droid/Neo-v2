"""Securite fichiers : sandbox sur le dossier d'installation + dossiers autorises,
regles type antivirus (pack versionne, rechargement a chaud), journal d'audit,
quota disque.

Principe : Oracle vit confine dans son dossier. Toute lecture/ecriture hors de
ce dossier exige que l'utilisateur ait accorde ce dossier explicitement.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path
REGLES_DEFAUT = {
    "version": "2026-08-24.1",
    "require_permission_outside_home": True,
    "blocked_command_patterns": [
        "format ", "shutdown", "restart-computer", "rd /s", "rmdir /s",
        "del /f /s", "erase /f", "diskpart", "cipher /w", "reg add",
        "reg delete", "schtasks /create", "sc delete", "bcedit",
        "vssadmin delete", "wmic shadowcopy",
    ],
    "blocked_extensions_write": [".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi"],
    "max_write_mb": 500,
}


def dossier_installation() -> Path:
    """Dossier de base de l'application (exe en mode figé, sources sinon)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def HOME() -> Path:
    return dossier_installation()


# Ancre sur la racine du projet (et non le dossier courant du process).
SECURITY_DIR = dossier_installation() / "data"
RULES_PATH = SECURITY_DIR / "security_rules.json"
LOG_PATH = SECURITY_DIR / "security_log.jsonl"


def charger_regles() -> dict:
    """Charge le pack de regles ; se met a jour a chaque appel (hot reload).

    Un fichier illisible n'est plus ecrase en silence : il est conserve en
    `.corrupt` (regles de l'utilisateur recuperables) et l'incident journalise.
    """
    if not RULES_PATH.exists():
        return sauvegarder_regles(REGLES_DEFAUT)
    try:
        regles = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        if isinstance(regles, dict) and "version" in regles:
            return {**REGLES_DEFAUT, **regles}
        erreur = "pack sans cle 'version'"
    except Exception as exc:
        erreur = str(exc)
    copie = RULES_PATH.with_name(RULES_PATH.name + ".corrupt")
    try:
        shutil.copy2(RULES_PATH, copie)
    except OSError:
        pass
    journaliser("regles_corrompues", {"path": str(RULES_PATH), "copie": str(copie),
                                      "erreur": erreur[:300]})
    return sauvegarder_regles(REGLES_DEFAUT)


def sauvegarder_regles(regles: dict) -> dict:
    fusionnees = {**REGLES_DEFAUT, **(regles or {})}
    SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    RULES_PATH.write_text(json.dumps(fusionnees, ensure_ascii=False, indent=2), encoding="utf-8")
    return fusionnees


def dossiers_autorises() -> list[str]:
    from backend.core.database import get_db

    row = get_db().query_one("SELECT value FROM app_settings WHERE key='allowed_roots'")
    if not row:
        return []
    try:
        liste = json.loads(row["value"])
        return [str(p) for p in liste if p]
    except Exception:
        return []


def accorder_dossier(chemin: str) -> list[str]:
    """Accorde un dossier (et ses sous-dossiers). Retourne la liste a jour."""
    cible = Path(chemin).expanduser().resolve()
    if not cible.exists():
        raise FileNotFoundError(f"Dossier inexistant : {cible}")
    actuels = dossiers_autorises()
    if str(cible) not in actuels:
        actuels.append(str(cible))
        _enregistrer_dossiers(actuels)
        journaliser("folder_granted", {"path": str(cible)})
    return actuels


def retirer_dossier(chemin: str) -> list[str]:
    cible = str(Path(chemin).expanduser().resolve())
    actuels = [p for p in dossiers_autorises() if p != cible]
    _enregistrer_dossiers(actuels)
    journaliser("folder_revoked", {"path": cible})
    return actuels


def _enregistrer_dossiers(dossiers: list[str]) -> None:
    from backend.core.app_settings import set_setting

    set_setting("allowed_roots", json.dumps(dossiers, ensure_ascii=False))


def _est_sous(chemin: Path, racine: Path) -> bool:
    try:
        chemin.relative_to(racine)
        return True
    except ValueError:
        return False


def verifier_acces(chemin: str | Path, ecriture: bool = False) -> str | None:
    """Retourne None si l'acces est autorise, sinon un message d'erreur explique.

    Autorise sans condition : le dossier d'installation (+ data), le dossier
    temporaire systeme, et tout dossier accorde par l'utilisateur.

    Un chemin relatif est resolu depuis HOME() puis soumis au meme controle :
    « ..\\..\\Users\\x » ne s'echappe plus du bac a sable. Les chemins relatifs
    a un lecteur (« C:foo ») ou a la racine du lecteur courant (« \\foo »)
    dependent du dossier courant du process : refuses.
    """
    if os.environ.get("LOCALAI_UNRESTRICTED") == "1":
        return None
    try:
        cible = Path(chemin).expanduser()
        if not cible.is_absolute():
            if cible.drive or cible.root:
                journaliser("acces_refuse" if ecriture else "lecture_refusee",
                            {"path": str(chemin), "write": ecriture})
                return (f"ACCES REFUSE : chemin ambigu {chemin} (relatif a un "
                        "lecteur). Utilisez un chemin absolu.")
            cible = HOME() / cible
        cible = cible.resolve()
    except Exception:
        return f"Chemin invalide : {chemin}"

    home = HOME()
    temp = Path(os.environ.get("TEMP", str(home)))
    if _est_sous(cible, home) or _est_sous(cible, temp):
        return None
    # Dossier produit de Neo (images/videos generees) : hors installation par
    # conception (Documents de l'utilisateur), donc autorise explicitement.
    try:
        from backend.core.config import settings as _reglages

        neo = getattr(_reglages, "neo_documents_dir", None)
        if neo and _est_sous(cible, Path(neo).expanduser().resolve()):
            return None
    except Exception:
        pass
    for racine in dossiers_autorises():
        if _est_sous(cible, Path(racine)):
            return None

    journaliser(
        "acces_refuse" if ecriture else "lecture_refusee",
        {"path": str(cible), "write": ecriture},
    )
    return (
        f"ACCES REFUSE : {cible} est hors du dossier d'installation et des dossiers "
        f"autorises ({len(dossiers_autorises())} accordes). Demandez a l'utilisateur "
        "d'accorder ce dossier via Security > Dossiers autorises, puis reessayez."
    )


def commande_autorisee(commande: str) -> str | None:
    """Verifie une commande shell contre les motifs interdits du pack."""
    regles = charger_regles()
    basse = (commande or "").lower()
    for motif in regles.get("blocked_command_patterns", []):
        if motif.lower() in basse:
            journaliser("commande_bloquee", {"command": commande[:200], "motif": motif})
            return (
                f"COMMANDE BLOQUEE par la securite (motif interdit : '{motif}'). "
                "Utilisez une approche differente."
            )
    return None


def extension_autorisee_ecriture(chemin: str | Path) -> str | None:
    regles = charger_regles()
    suffix = Path(str(chemin)).suffix.lower()
    if suffix in regles.get("blocked_extensions_write", []):
        journaliser("ecriture_bloquee", {"path": str(chemin), "extension": suffix})
        return (
            f"ECRITURE BLOQUEE : l'extension {suffix} est interdite par les regles "
            "de securite (executables/scripts)."
        )
    return None


def journaliser(evenement: str, details: dict | None = None) -> None:
    """Journal d'audit append-only (type antivirus)."""
    try:
        SECURITY_DIR.mkdir(parents=True, exist_ok=True)
        entree = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": evenement, **(details or {})}
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    except Exception:
        pass


def lire_journal(limite: int = 100) -> list[dict]:
    try:
        lignes = LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(l) for l in lignes[-limite:]]
    except Exception:
        return []


def taille_journal() -> int:
    """Nombre d'entrees du journal, sans parser le JSON de chaque ligne."""
    try:
        with LOG_PATH.open("rb") as f:
            return sum(1 for ligne in f if ligne.strip())
    except OSError:
        return 0


# Le parcours complet du dossier (~77 000 fichiers) depasse le delai du
# worker resident : resultat mis en cache 10 minutes dans le process.
_USAGE_TTL_S = 600
_usage_cache: tuple[float, dict[str, int], int] | None = None


def _parcourir_home(home: Path) -> tuple[dict[str, int], int]:
    repartition: dict[str, int] = {}
    total = 0
    for racine, _dossiers, fichiers in os.walk(home):
        rel = Path(racine).relative_to(home).parts
        cle = rel[0] if rel else "(racine)"
        for nom in fichiers:
            try:
                taille = os.stat(os.path.join(racine, nom)).st_size
            except OSError:
                continue
            total += taille
            repartition[cle] = repartition.get(cle, 0) + taille
    return repartition, total


def usage_disque() -> dict:
    """Espace utilise par le dossier d'installation, par sous-dossier."""
    global _usage_cache
    home = HOME()
    if _usage_cache is None or time.time() - _usage_cache[0] > _USAGE_TTL_S:
        _usage_cache = (time.time(), *_parcourir_home(home))
    _, repartition, total = _usage_cache
    from backend.core.app_settings import get_setting

    try:
        quota_go = float(get_setting("storage_quota_gb", "20"))
    except Exception:
        quota_go = 20.0
    return {
        "home": str(home),
        "used_gb": round(total / 1024**3, 2),
        "quota_gb": quota_go,
        "percent": round(100 * total / 1024**3 / max(quota_go, 0.1), 1),
        "breakdown_gb": {k: round(v / 1024**3, 2) for k, v in sorted(repartition.items(), key=lambda x: -x[1])[:12]},
    }
