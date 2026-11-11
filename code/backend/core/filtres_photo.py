# -*- coding: utf-8 -*-
"""Filtres et effets photo de Neo (apres generation).

Un seul code pour l'apercu et pour l'image finale : l'apercu montre exactement
ce qui sera enregistre. `apercus()` rend TOUS les filtres d'un coup sur une
copie reduite (960 px) ; l'interface les garde en memoire et passe de l'un a
l'autre sans attendre. `enregistrer()` applique le filtre choisi a l'image
pleine resolution et ecrit une COPIE : l'original n'est jamais modifie.

Les effets dont la taille compte (flou, grain, vignette) sont exprimes en
fraction de l'image, pour avoir le meme rendu a 960 px et en 4K.
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
TAILLE_APERCU = 960


# ---- Briques (tableaux float32 RVB entre 0 et 1) ----

def _lum(a: np.ndarray) -> np.ndarray:
    return (a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722)[..., None]


def _saturation(a, s):
    lum = _lum(a)
    return lum + (a - lum) * s


def _contraste(a, c, pivot=0.5):
    return (a - pivot) * c + pivot


def _courbe_s(a, force):
    """Courbe en S : noirs plus denses, clairs plus lumineux."""
    lisse = a * a * (3 - 2 * a)
    return a + (lisse - a) * force


def _temperature(a, t, teinte=0.0):
    """t > 0 : plus chaud (rouge +, bleu -). teinte > 0 : vers le magenta."""
    return a * np.array([1 + t, 1 - teinte, 1 - t], dtype=np.float32)


def _exposition(a, e):
    return a * (2.0 ** e)


def _noirs_releves(a, niveau):
    """Noirs « mats » : le noir pur devient un gris fonce."""
    return a * (1 - niveau) + niveau


def _virage(a, ombres, lumieres, force):
    """Teinte differente dans les ombres et dans les lumieres (split toning)."""
    lum = _lum(a)
    teinte = (1 - lum) * np.array(ombres, dtype=np.float32) + lum * np.array(lumieres, dtype=np.float32)
    return a + (teinte - 0.5) * force


def _vignette(a, force, rayon=0.75):
    h, w = a.shape[:2]
    y, x = np.ogrid[:h, :w]
    d = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
    masque = np.clip((d - rayon * 0.5) / (1 - rayon * 0.5), 0, 1) ** 2
    return a * (1 - force * masque[..., None].astype(np.float32))


def _grain(a, force):
    """Grain argentique, de taille proportionnelle a l'image."""
    h, w = a.shape[:2]
    echelle = max(1.0, max(h, w) / 1400)
    gen = np.random.default_rng(7)                       # meme grain a chaque rendu
    petit = gen.normal(0, 1, (max(2, int(h / echelle)), max(2, int(w / echelle)))).astype(np.float32)
    bruit = np.asarray(Image.fromarray(petit).resize((w, h), Image.BILINEAR))
    return a + bruit[..., None] * force * (1 - 0.5 * _lum(a))


def _vers_image(a: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


def _flou(a, fraction):
    rayon = max(1.0, fraction * max(a.shape[:2]))
    return np.asarray(_vers_image(a).filter(ImageFilter.GaussianBlur(rayon)), dtype=np.float32) / 255


def _nettete(a, force):
    return a + (a - _flou(a, 0.0015)) * force


# ---- Filtres ----

def _noir_blanc(a):
    return np.repeat(_courbe_s(_lum(a), 0.35), 3, axis=2)


def _noir_blanc_dur(a):
    lum = _contraste(_lum(a * np.array([1.15, 1.0, 0.75], dtype=np.float32)), 1.45)
    return _vignette(_grain(np.repeat(lum, 3, axis=2), 0.035), 0.35)


def _sepia(a):
    lum = _courbe_s(_lum(a), 0.2)
    return _noirs_releves(lum * np.array([1.07, 0.92, 0.72], dtype=np.float32), 0.03)


def _vintage(a):
    b = _noirs_releves(_saturation(a, 0.78), 0.07)
    b = _virage(_temperature(b, 0.07), (0.46, 0.5, 0.56), (0.56, 0.52, 0.42), 0.22)
    return _vignette(_grain(b, 0.03), 0.3)


def _chaud(a):
    return _saturation(_temperature(a, 0.09, -0.01), 1.08)


def _froid(a):
    return _saturation(_temperature(a, -0.08), 0.96)


def _vif(a):
    return _courbe_s(_saturation(a, 1.38), 0.25)


def _doux(a):
    b = _noirs_releves(_saturation(_contraste(a, 0.86), 0.88), 0.04)
    return b * 0.82 + _flou(b, 0.004) * 0.18


def _contraste_fort(a):
    return _nettete(_courbe_s(a, 0.75), 0.4)


def _cinema(a):
    b = _virage(_saturation(a, 0.92), (0.40, 0.52, 0.58), (0.60, 0.52, 0.42), 0.26)
    return _vignette(_courbe_s(b, 0.4), 0.22)


def _dramatique(a):
    b = _saturation(_courbe_s(_exposition(a, -0.12), 0.9), 0.72)
    return _vignette(_nettete(b, 0.6), 0.45)


def _mat(a):
    return _noirs_releves(_saturation(_contraste(a, 0.9), 0.82), 0.1)


def _lumineux(a):
    return _saturation(_noirs_releves(_exposition(a, 0.22), 0.03), 1.05)


def _clair_obscur(a):
    return _vignette(_courbe_s(_exposition(a, -0.3), 0.6), 0.5, rayon=0.6)


def _reve(a):
    lueur = _flou(np.clip(a, 0, 1), 0.012)
    return _saturation(1 - (1 - a) * (1 - lueur * 0.45), 1.08)       # fusion « ecran »


def _net(a):
    return _nettete(a, 0.9)


def _retro(a):
    """Traitement croise : une courbe differente par couleur."""
    r = _courbe_s(a[..., 0:1], 0.5)
    v = a[..., 1:2] ** 0.92
    b = a[..., 2:3] * 0.82 + 0.1
    return _saturation(np.concatenate([r, v, b], axis=2), 1.12)


def _automne(a):
    return _saturation(a * np.array([1.1, 0.98, 0.8], dtype=np.float32), 1.15)


def _croquis(a):
    """Dessin au crayon : l'image divisee par son negatif floute."""
    lum = _lum(np.clip(a, 0, 1))
    flou = _lum(_flou(np.repeat(1 - lum, 3, axis=2), 0.006))
    trait = np.clip(lum / np.maximum(1 - flou, 0.02), 0, 1) ** 1.6
    return np.repeat(trait, 3, axis=2)


def _pop(a):
    niveaux = 5
    return _saturation(np.round(np.clip(_courbe_s(a, 0.4), 0, 1) * (niveaux - 1)) / (niveaux - 1), 1.5)


def _grain_seul(a):
    return _grain(a, 0.06)


def _vignette_seule(a):
    return _vignette(a, 0.55)


# (identifiant, nom affiche, famille, fonction) — l'ordre est celui de l'interface.
FILTRES = [
    ("vif", "Couleurs vives", "Couleur", _vif),
    ("chaud", "Chaleureux", "Couleur", _chaud),
    ("froid", "Froid", "Couleur", _froid),
    ("automne", "Automne", "Couleur", _automne),
    ("lumineux", "Lumineux", "Lumière", _lumineux),
    ("contraste", "Contraste", "Lumière", _contraste_fort),
    ("doux", "Doux", "Lumière", _doux),
    ("clair_obscur", "Clair-obscur", "Lumière", _clair_obscur),
    ("cinema", "Cinéma", "Ambiance", _cinema),
    ("dramatique", "Dramatique", "Ambiance", _dramatique),
    ("vintage", "Vintage", "Ambiance", _vintage),
    ("retro", "Rétro", "Ambiance", _retro),
    ("mat", "Mat", "Ambiance", _mat),
    ("reve", "Rêve", "Ambiance", _reve),
    ("noir_blanc", "Noir et blanc", "Noir et blanc", _noir_blanc),
    ("noir_blanc_dur", "Noir intense", "Noir et blanc", _noir_blanc_dur),
    ("sepia", "Sépia", "Noir et blanc", _sepia),
    ("net", "Netteté", "Effet", _net),
    ("grain", "Grain", "Effet", _grain_seul),
    ("vignette", "Vignette", "Effet", _vignette_seule),
    ("croquis", "Croquis", "Effet", _croquis),
    ("pop", "Pop art", "Effet", _pop),
]
_PAR_ID = {f[0]: f for f in FILTRES}


def liste() -> list[dict]:
    return [{"id": i, "nom": nom, "famille": famille} for i, nom, famille, _ in FILTRES]


def appliquer(image: Image.Image, filtre: str, force: float = 1.0) -> Image.Image:
    """Image filtree. `force` entre 0 (original) et 1 (filtre entier)."""
    if filtre not in _PAR_ID:
        raise ValueError(f"filtre inconnu : {filtre}")
    a = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    b = np.clip(_PAR_ID[filtre][3](a), 0, 1)
    force = min(max(float(force), 0.0), 1.0)
    return _vers_image(a + (b - a) * force)


def source(chemin: str) -> Path:
    """Fichier a traiter. Pour un apercu (`…_apercu.jpg`), l'image d'origine a
    cote (TIFF 4K du studio) si elle existe : c'est elle qu'on filtre."""
    p = Path(chemin)
    if p.stem.endswith("_apercu"):
        for ext in (".tif", ".tiff", ".png", ".exr"):
            maitre = p.with_name(p.stem[: -len("_apercu")] + ext)
            if maitre.is_file() and maitre.suffix.lower() in EXTENSIONS:
                return maitre
    return p


def _ouvrir(chemin: Path) -> Image.Image:
    image = Image.open(chemin)
    if image.mode in ("I;16", "I;16B", "I;16L", "I"):          # TIFF 16 bits
        image = Image.fromarray((np.asarray(image, dtype=np.float32) / 257).clip(0, 255).astype(np.uint8))
    return image.convert("RGB")


def _jpeg_url(image: Image.Image, qualite: int = 84) -> str:
    tampon = io.BytesIO()
    image.save(tampon, "JPEG", quality=qualite)
    return "data:image/jpeg;base64," + base64.b64encode(tampon.getvalue()).decode("ascii")


def apercus(chemin: str, taille: int = TAILLE_APERCU) -> dict:
    """Tous les filtres rendus sur une copie reduite : {original, filtres:[…]}."""
    debut = time.time()
    p = source(chemin)
    image = _ouvrir(p)
    largeur, hauteur = image.size
    image.thumbnail((taille, taille), Image.LANCZOS)
    rendus = [{"id": i, "nom": nom, "famille": famille, "apercu": _jpeg_url(appliquer(image, i))}
              for i, nom, famille, _ in FILTRES]
    return {"source": str(p), "largeur": largeur, "hauteur": hauteur, "original": _jpeg_url(image),
            "filtres": rendus, "duree_s": round(time.time() - debut, 2)}


def enregistrer(chemin: str, filtre: str, force: float = 1.0) -> str:
    """Applique le filtre a l'image pleine resolution et ecrit une copie a cote
    de l'original (`<nom>_<filtre>.jpg`, PNG si l'original est un PNG).
    Retourne le chemin de la copie."""
    p = source(chemin)
    resultat = appliquer(_ouvrir(p), filtre, force)
    ext = ".png" if p.suffix.lower() == ".png" else ".jpg"
    sortie = p.with_name(f"{p.stem}_{filtre}{ext}")
    n = 2
    while sortie.exists():
        sortie = p.with_name(f"{p.stem}_{filtre}_{n}{ext}")
        n += 1
    if ext == ".png":
        resultat.save(sortie, "PNG")
    else:
        resultat.save(sortie, "JPEG", quality=95, subsampling=0)
    return str(sortie)
