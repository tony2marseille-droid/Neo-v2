"""Reglages de la machine : ``neo.toml`` (pendant Python de ``neoconf.rs``).

Un seul fichier decrit ce qui change d'une installation a l'autre (dossiers,
ports, outils externes). Il alimente les variables d'environnement que Neo
lit deja (``LOCALAI_ROOT``, ``LOCALAI_SD_DIR``...).

Priorite pour chaque reglage :
1. variable d'environnement deja definie (jamais ecrasee) ;
2. valeur de ``neo.toml`` ;
3. defaut du code, relatif a la racine d'installation (``racine()``).

Recherche du fichier : ``NEO_CONFIG``, puis ``<LOCALAI_ROOT>/neo.toml``, puis
le dossier du projet et chacun de ses parents.

Ce module n'importe rien de ``backend`` : tous les autres peuvent l'importer.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

# (section, cle) -> variables d'environnement (meme table que neoconf.rs).
CORRESPONDANCES: list[tuple[str, str, tuple[str, ...]]] = [
    ("chemins", "racine", ("LOCALAI_ROOT",)),
    ("chemins", "donnees", ("LOCALAI_DATA", "LOCALAI_DATA_DIR")),
    ("chemins", "journal", ("LOCALAI_LOG",)),
    ("chemins", "espace_travail", ("NEO_ESPACE_TRAVAIL",)),
    ("chemins", "sd", ("LOCALAI_SD_DIR",)),
    ("chemins", "retouche", ("NEO_SD_RETOUCHE",)),
    ("chemins", "esrgan", ("LOCALAI_ESRGAN_DIR",)),
    ("chemins", "voix", ("NEO_VOIX_DIR",)),
    ("chemins", "parakeet", ("NEO_PARAKEET_DIR",)),
    ("chemins", "hunyuan", ("LOCALAI_HUNYUAN21_DIR",)),
    ("chemins", "modele_visages", ("NEO_YUNET",)),
    ("chemins", "cache_hf", ("HF_HOME",)),
    ("reseau", "hote", ("LOCALAI_HOST",)),
    ("reseau", "port", ("LOCALAI_PORT",)),
    ("ollama", "url", ("OLLAMA_URL",)),
    ("ollama", "executable", ("OLLAMA_BIN",)),
    ("ollama", "modeles", ("OLLAMA_MODELS",)),
    ("outils", "python", ("NEO_PYTHON",)),
    ("outils", "ffmpeg", ("NEO_FFMPEG",)),
    ("outils", "blender", ("LOCALAI_BLENDER",)),
    ("outils", "openscad", ("LOCALAI_OPENSCAD",)),
    ("outils", "trancheur", ("LOCALAI_SLICER",)),
    ("materiel", "palier_force", ("LOCALAI_FORCE_TIER",)),
]

# Racine du projet deduite de l'emplacement de ce fichier :
# <racine>/backend/core/reglages.py
_RACINE_PROJET = Path(__file__).resolve().parents[2]
_fichier_applique: Path | None = None
_deja_applique = False


def fichier() -> Path | None:
    """Chemin du ``neo.toml`` en vigueur, s'il existe."""
    if os.environ.get("NEO_CONFIG"):
        p = Path(os.environ["NEO_CONFIG"])
        return p if p.is_file() else None
    if os.environ.get("LOCALAI_ROOT"):
        p = Path(os.environ["LOCALAI_ROOT"]) / "neo.toml"
        if p.is_file():
            return p
    for d in (_RACINE_PROJET, *_RACINE_PROJET.parents):
        p = d / "neo.toml"
        if p.is_file():
            return p
    return None


def _texte(v) -> str | None:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (str, int, float)):
        return str(v)
    return None


def variables(contenu: str, dossier: Path) -> list[tuple[str, str]]:
    """Variables a definir d'apres un ``neo.toml`` situe dans ``dossier``.

    Pur (ne touche pas l'environnement). ``tomllib.TOMLDecodeError`` si le
    fichier est invalide.
    """
    doc = tomllib.loads(contenu)

    def lire(section: str, cle: str) -> str | None:
        v = _texte((doc.get(section) or {}).get(cle))
        return v if v and v.strip() else None

    racine = lire("chemins", "racine") or str(dossier)
    sortie = [("LOCALAI_ROOT", racine)]
    for section, cle, noms in CORRESPONDANCES:
        if (section, cle) == ("chemins", "racine"):
            continue
        v = lire(section, cle)
        if v is not None:
            sortie += [(n, v.replace("{racine}", racine)) for n in noms]
    for nom, v in (doc.get("variables") or {}).items():
        s = _texte(v)
        if s is not None:
            sortie.append((nom, s.replace("{racine}", racine)))
    return sortie


def appliquer() -> Path | None:
    """Lit ``neo.toml`` (une seule fois) et definit les variables absentes."""
    global _fichier_applique, _deja_applique
    if _deja_applique:
        return _fichier_applique
    _deja_applique = True
    chemin = fichier()
    if chemin is None:
        return None
    try:
        vars_ = variables(chemin.read_text(encoding="utf-8"), chemin.parent)
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"neo.toml invalide ({chemin}) : {e}")
        return None
    for nom, valeur in vars_:
        os.environ.setdefault(nom, valeur)
    _fichier_applique = chemin
    return chemin


def racine() -> Path:
    """Racine d'installation : LOCALAI_ROOT, sinon le dossier du projet."""
    appliquer()
    r = os.environ.get("LOCALAI_ROOT")
    return Path(r) if r else _RACINE_PROJET


def chemin(variable: str, *defaut_relatif: str) -> Path:
    """Chemin lu dans ``variable``, sinon ``racine()/<defaut_relatif...>``."""
    appliquer()
    v = os.environ.get(variable)
    return Path(v) if v else racine().joinpath(*defaut_relatif)
