# -*- coding: utf-8 -*-
"""Routes API vidéo pour Néo : storyboard, clips, render.
Préfixe : /api/video
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from urllib.parse import quote
import os
import re

from backend.tools.t_video import (
    tool_video_storyboard,
    tool_video_clip,
    tool_video_clip_all,
    tool_video_render,
    VIDEO_DIR,
)

router = APIRouter(prefix="/api/video", tags=["video"])

# Extensions servies par /file : uniquement des medias et documents. Sans ce
# filtre, /file exposait n'importe quel fichier de E:\ia\data (bases SQLite,
# journaux, code).
EXT_SERVIES = {
    ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".exr", ".bmp", ".gif",
    ".mp4", ".webm", ".mov", ".mkv", ".avi",
    ".stl", ".3mf", ".obj", ".glb", ".gltf", ".ply", ".step", ".stp",
    ".pdf", ".txt", ".md", ".json", ".csv",
    ".docx", ".xlsx", ".pptx", ".scad", ".gcode",
}

CATEGORIES = {
    "images": {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".exr", ".bmp", ".gif"},
    "video": {".mp4", ".webm", ".mov", ".mkv", ".avi"},
    "mesh": {".stl", ".3mf", ".obj", ".glb", ".gltf", ".ply", ".step", ".stp", ".scad", ".gcode"},
    "documents": {".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx", ".xls",
                  ".pptx", ".odt", ".rtf", ".json"},
    "code": {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cpp",
             ".c", ".h", ".yaml", ".yml", ".toml", ".sh", ".ps1", ".bat", ".sql"},
}


def _neo_roots() -> list[Path]:
    """Dossiers Documents de Neo, tous profils confondus, dedupliques."""
    import glob

    candidats = [
        os.environ.get("LOCALAI_NEO_DOCS", ""),
        # Le dossier « Documents\Neo » de chaque profil : le service tourne
        # sous un autre compte que l'utilisateur.
        *sorted(glob.glob(os.environ.get("SystemDrive", "C:") + r"\Users\*\Documents\Neo")),
        str(Path.home() / "Documents" / "Neo"),
        r"C:\WINDOWS\system32\config\systemprofile\Documents\Neo",
    ]
    roots, vus = [], set()
    for c in candidats:
        if not c:
            continue
        try:
            p = Path(c).resolve()
        except OSError:
            continue
        cle = str(p).lower()
        if p.is_dir() and cle not in vus:
            vus.add(cle)
            roots.append(p)
    return roots


def _espace_de_travail() -> Path:
    """`<racine>/workspace` : la ou Neo range ce qu'il produit par le chat
    (images, documents, modeles 3D)."""
    return (Path(__file__).resolve().parents[2] / "workspace").resolve()


# Fichiers de travail qu'on ne montre pas : essais intermediaires, vues de
# controle, copies reduites, notes internes.
_INTERNE = re.compile(
    r"(_apercu|_thumb|_ecran|_ref|_modele)\.\w+$|_(face|profil|dos|iso)\.png$|^piece_t\d+\.|^spec\.json$|"
    r"\.brief\.json$|^storyboard_.*\.json$|^placeholder_|^ref_\d+\.",
    re.IGNORECASE,
)
_DOSSIER_INTERNE = re.compile(r"^(wan_|_tsr_|essai_|pt$|__pycache__)", re.IGNORECASE)


def _racines_listees() -> list[Path]:
    """Dossiers montres dans « Fichiers de Neo » : les anciens Documents\\Neo
    et l'espace de travail. Jusqu'au 11/11/2026 seul Documents\\Neo etait
    liste : les images, documents et modeles 3D produits par le chat (ranges
    dans workspace) n'apparaissaient nulle part."""
    return _neo_roots() + _dossiers_de_production()


def _dossiers_de_production() -> list[Path]:
    """Les dossiers de l'espace de travail ou arrivent les fichiers faits pour
    l'utilisateur. Le reste de workspace (essais des agents, auto-amelioration)
    n'est ni liste ni servi."""
    travail = _espace_de_travail()
    return [d for d in (travail / n for n in ("images", "documents", "modeles_3d", "pipeline_3d")) if d.is_dir()]


def _racines_servies() -> list[Path]:
    """Racines autorisees en lecture par l'API video."""
    roots = [Path(VIDEO_DIR).resolve()] + _dossiers_de_production()
    roots += _neo_roots()
    # Sorties du generateur d'images (E:\ia\data).
    roots.append((Path(__file__).resolve().parents[2] / "data").resolve())
    uniq, vus = [], set()
    for r in roots:
        cle = str(r).lower()
        if r.exists() and cle not in vus:
            vus.add(cle)
            uniq.append(r)
    return uniq


def _dans_racine(p: Path, roots: list[Path]) -> bool:
    cible = os.path.normcase(os.path.abspath(str(p)))
    for r in roots:
        base = os.path.normcase(os.path.abspath(str(r)))
        if cible == base or cible.startswith(base + os.sep):
            return True
    return False


def _categorie(p: Path) -> str:
    ext = p.suffix.lower()
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "autres"


def _apercu(p: Path) -> str | None:
    # `_ecran.jpg` : copie d'ecran d'une image 4K ; `_iso.png` : vue d'un modele 3D.
    for suf in ("_apercu.jpg", "_apercu.png", "_thumb.jpg", "_thumb.png", "_ecran.jpg", "_iso.png"):
        c = p.with_name(p.stem + suf)
        if c.is_file():
            return str(c)
    return None


@router.get("/file")
def video_file(path: str):
    """Sert un media/document depuis les racines autorisees (lecture seule)."""
    roots = _racines_servies()
    p = Path(path)
    cibles = [p] if p.is_absolute() else [r / path for r in roots]
    for c in cibles:
        try:
            c = c.resolve()
        except OSError:
            continue
        if not _dans_racine(c, roots):
            continue
        if c.is_file() and c.suffix.lower() in EXT_SERVIES:
            return FileResponse(c, headers={"Cache-Control": "private, max-age=300"})
    raise HTTPException(status_code=404, detail="Fichier introuvable ou non autorise")


class NeoDeleteReq(BaseModel):
    paths: list[str]


@router.post("/neo-delete")
def video_neo_delete(req: NeoDeleteReq) -> dict:
    """Supprime un ou plusieurs fichiers des dossiers Neo autorises (Documents
    de Neo, dossier video, data). Supprime aussi l'apercu cote a cote
    (_apercu.jpg/png). Garde-fou : seuls les fichiers sous les racines
    autorisees peuvent etre supprimes."""
    roots = _racines_servies()
    supprimes: list[str] = []
    erreurs: list[str] = []
    for raw in req.paths:
        p = Path(raw)
        cibles = [p] if p.is_absolute() else [r / raw for r in roots]
        trouve = None
        for c in cibles:
            try:
                c = c.resolve()
            except OSError:
                continue
            if _dans_racine(c, roots) and c.is_file():
                trouve = c
                break
        if trouve is None:
            erreurs.append(f"{raw} : introuvable ou non autorise")
            continue
        if trouve.suffix.lower() not in EXT_SERVIES:
            erreurs.append(f"{raw} : extension non supprimable")
            continue
        try:
            # Apercu cote a cote (master TIFF/EXR -> _apercu.jpg) supprime avec lui.
            for suf in ("_apercu.jpg", "_apercu.png", "_thumb.jpg", "_thumb.png"):
                ap = trouve.with_name(trouve.stem + suf)
                try:
                    if ap.is_file() and _dans_racine(ap.resolve(), roots):
                        ap.unlink()
                        supprimes.append(str(ap))
                except OSError:
                    pass
            trouve.unlink()
            supprimes.append(str(trouve))
        except OSError as exc:
            erreurs.append(f"{raw} : {exc}")
    return {"ok": not erreurs, "supprimes": supprimes, "erreurs": erreurs}


class StoryboardReq(BaseModel):
    histoire: str
    modele: str = ""
    duree_totale: int = 0


class ClipReq(BaseModel):
    storyboard_path: str
    shot_id: int = 1


class RenderReq(BaseModel):
    project_name: str
    audio_texte: str = ""
    duree_totale: int = 0
    # Timestamp (epoch s) du storyboard : ne monter que les clips crees apres
    # cette date, pour ne pas melanger les projets. 0 = pas de filtre.
    since: float = 0


class ListeReq(BaseModel):
    pattern: str = ""


# Les handlers sont des `def` SYNCHRONES : FastAPI les execute dans son pool
# de threads. En `async def`, les appels bloquants (Ollama jusqu'a 180 s,
# FFmpeg jusqu'a 300 s) gelaient toute la boucle, donc tout le serveur.


def _reponse_outil(resultat: str) -> dict:
    """Les outils signalent l'echec par un texte « Erreur... » : ok=False."""
    return {"ok": not str(resultat).startswith("Erreur"), "resultat": resultat}


@router.post("/storyboard")
def video_storyboard(req: StoryboardReq):
    try:
        return _reponse_outil(
            tool_video_storyboard(req.histoire, req.modele, req.duree_totale or None))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/clip")
def video_clip(req: ClipReq):
    try:
        return _reponse_outil(tool_video_clip(req.storyboard_path, req.shot_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/clips-all")
def video_clips_all(req: ClipReq):
    """Génère automatiquement tous les clips d'un storyboard."""
    try:
        return _reponse_outil(tool_video_clip_all(req.storyboard_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/render")
def video_render(req: RenderReq):
    try:
        return _reponse_outil(tool_video_render(
            req.project_name, req.audio_texte, req.duree_totale or None, since=req.since))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/liste")
def video_liste(pattern: str = ""):
    """Liste les fichiers vidéo du dossier de sortie."""
    try:
        pattern = pattern or "*"
        fichiers = []
        for f in sorted(VIDEO_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
            # Construire le chemin relatif pour l'endpoint /file
            rel_path = f.relative_to(VIDEO_DIR).as_posix()
            fichiers.append({
                "nom": f.name,
                "chemin": str(f),
                "url": f"file?path={quote(rel_path)}",
                "taille": f.stat().st_size,
            })
        return {"ok": True, "dossier": str(VIDEO_DIR), "fichiers": fichiers}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/status")
def video_status():
    """État des dépendances vidéo (ComfyUI, Ollama, FFmpeg)."""
    import subprocess

    import httpx

    from backend.core.config import ollama_url

    statut = {
        "dossier_sortie": str(VIDEO_DIR),
        "comfyui": False,
        "ollama": False,
        "ffmpeg": False,
        "modeles_ollama": [],
    }

    try:
        r = httpx.get(f"{ollama_url()}/api/tags", timeout=5)
        statut["ollama"] = r.status_code == 200
        if statut["ollama"]:
            statut["modeles_ollama"] = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    try:
        r = httpx.get("http://127.0.0.1:8188/system_stats", timeout=5)
        statut["comfyui"] = r.status_code == 200
    except Exception:
        pass

    # ffmpeg n'est pas forcement dans le PATH du compte SYSTEM : tester le
    # PATH puis les emplacements connus (C:\ffmpeg\bin\ffmpeg.exe).
    for exe in ("ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\ffmpeg.exe"):
        try:
            res = subprocess.run(
                [exe, "-version"], capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode == 0:
                statut["ffmpeg"] = True
                statut["ffmpeg_exe"] = exe
                break
        except Exception:
            continue

    return {"ok": True, "statut": statut}


@router.get("/neo-files")
def video_neo_files(request: Request) -> dict:
    """Fichiers de Neo pour la vue Documents.

    - scanne TOUS les profils Documents/Neo (utilisateur courant + SYSTEM + surcharge env) ;
    - deduplique par chemin reel (le dossier `video` etait scanne deux fois) ;
    - ecarte les placeholders transparents et les vignettes `_apercu` ;
    - renvoie `preview` (vignette reelle) et `url` absolue utilisable en <img>.
    """
    base = str(request.base_url).rstrip("/")
    vus: set[str] = set()
    fichiers = []
    racines = _racines_listees()

    for root in racines:
        for f in root.rglob("*"):
            try:
                if not f.is_file():
                    continue
                rp = f.resolve()
                cle = os.path.normcase(str(rp))
                if cle in vus:
                    continue
                nom = f.name
                if _INTERNE.search(nom) or "_apercu" in nom or "_thumb" in nom:
                    continue
                if any(_DOSSIER_INTERNE.match(d) for d in f.relative_to(root).parts[:-1]):
                    continue
                if f.suffix.lower() in (".tmp", ".part", ".crdownload") or _categorie(f) == "autres":
                    continue
                vus.add(cle)
                st = f.stat()
                apercu = _apercu(f)
                fichiers.append({
                    "name": nom,
                    "path": str(rp),
                    "size_bytes": st.st_size,
                    "modified": int(st.st_mtime),
                    "type": _categorie(f),
                    "ext": f.suffix.lower(),
                    "source": root.name,
                    "preview": apercu,
                    "url": f"{base}/api/video/file?path={quote(str(rp))}",
                    "preview_url": (
                        f"{base}/api/video/file?path={quote(apercu)}" if apercu else None
                    ),
                })
            except OSError:
                continue

    fichiers.sort(key=lambda x: x["modified"], reverse=True)
    total = len(fichiers)
    fichiers = fichiers[:600]            # les plus recents ; la page reste fluide
    return {
        "ok": True,
        "total": total,
        "racines": [str(r) for r in racines],
        "files": fichiers,
    }