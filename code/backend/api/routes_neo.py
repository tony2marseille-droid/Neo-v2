# -*- coding: utf-8 -*-
"""Routes du service Python de Neo (port 8321) : filtres photo et fiches.

Ce service reste allume : les apercus de filtres repondent en une seconde, sans
lancer un Python a chaque appel. Meme controle d'acces que les routes video
(`serve_video.verifier_jeton`).
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

photo = APIRouter(prefix="/api/photo", tags=["photo"])
fiches = APIRouter(prefix="/api/fiches", tags=["fiches"])


# ---- Filtres photo ----

def _image_autorisee(chemin: str, ecriture: bool = False) -> Path:
    """Chemin d'image verifie par le bac a sable de Neo (404/403 sinon)."""
    from backend.core import filtres_photo
    from backend.core.security import verifier_acces
    from backend.tools.common import _default_root

    p = Path(str(chemin or "").strip().strip('"'))
    if not p.is_absolute():
        p = Path(_default_root()) / p
    p = filtres_photo.source(str(p))
    if p.suffix.lower() not in filtres_photo.EXTENSIONS:
        raise HTTPException(status_code=400, detail="Ce fichier n'est pas une image que les filtres savent lire.")
    refus = verifier_acces(str(p), ecriture=ecriture)
    if refus:
        # Les dossiers de « Fichiers de Neo » (dont les anciens Documents\Neo,
        # hors du bac a sable des outils) sont deja servis et supprimables par
        # ce service : leurs images acceptent aussi les filtres.
        from backend.api.routes_video import _dans_racine, _racines_servies

        if not _dans_racine(p.resolve(), _racines_servies()):
            raise HTTPException(status_code=403, detail=refus)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Image introuvable.")
    return p


class DemandeFiltre(BaseModel):
    path: str
    filtre: str = ""
    force: float = 1.0


@photo.get("/filtres")
def photo_filtres() -> dict:
    from backend.core import filtres_photo

    return {"filtres": filtres_photo.liste()}


@photo.post("/apercus")
def photo_apercus(d: DemandeFiltre) -> dict:
    """Tous les filtres rendus sur une copie reduite de l'image (~1 s)."""
    from backend.core import filtres_photo

    return filtres_photo.apercus(str(_image_autorisee(d.path)))


@photo.post("/appliquer")
def photo_appliquer(d: DemandeFiltre) -> dict:
    """Enregistre une copie filtree, en pleine resolution, a cote de l'original."""
    from backend.core import filtres_photo

    p = _image_autorisee(d.path, ecriture=True)
    try:
        sortie = filtres_photo.enregistrer(str(p), d.filtre, d.force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": sortie, "nom": Path(sortie).name}


# ---- Fiches de connaissances ----

class NouvelleFiche(BaseModel):
    sujet: str
    recherche: bool = True
    illustrer: bool = False


class Export(BaseModel):
    type_doc: str = "pdf"


def _ou_404(fiche: dict | None) -> dict:
    if not fiche:
        raise HTTPException(status_code=404, detail="Fiche inconnue.")
    return fiche


def _ou_erreur(fiche: dict) -> dict:
    """Une fiche, ou l'explication de l'echec (502 : le modele n'a pas suivi)."""
    if fiche.get("erreur") and not fiche.get("id"):
        raise HTTPException(status_code=502, detail=fiche["erreur"])
    return fiche


@fiches.get("")
def fiches_liste(q: str = "", categorie: str = "") -> dict:
    from backend.core import fiches as f

    return {"fiches": f.lister(q, categorie), "categories": f.CATEGORIES}


@fiches.post("")
def fiches_creer(d: NouvelleFiche) -> dict:
    """Neo cherche, redige et enregistre la fiche (10 a 40 s)."""
    from backend.core import fiches as f

    return _ou_erreur(f.creer(d.sujet, recherche=d.recherche, avec_image=d.illustrer))


@fiches.get("/{fiche_id}")
def fiches_lire(fiche_id: int) -> dict:
    from backend.core import fiches as f

    return _ou_404(f.lire(fiche_id))


@fiches.delete("/{fiche_id}")
def fiches_supprimer(fiche_id: int) -> dict:
    from backend.core import fiches as f

    if not f.supprimer(fiche_id):
        raise HTTPException(status_code=404, detail="Fiche inconnue.")
    return {"deleted": fiche_id}


@fiches.post("/{fiche_id}/actualiser")
def fiches_actualiser(fiche_id: int) -> dict:
    from backend.core import fiches as f

    _ou_404(f.lire(fiche_id))
    return _ou_erreur(f.actualiser(fiche_id))


@fiches.post("/{fiche_id}/illustrer")
def fiches_illustrer(fiche_id: int) -> dict:
    from backend.core import fiches as f

    _ou_404(f.lire(fiche_id))
    fiche = f.illustrer(fiche_id)
    if fiche.get("erreur"):
        raise HTTPException(status_code=502, detail=fiche["erreur"])
    return fiche


@fiches.post("/{fiche_id}/exporter")
def fiches_exporter(fiche_id: int, d: Export) -> dict:
    from backend.core import fiches as f

    _ou_404(f.lire(fiche_id))
    message = f.exporter(fiche_id, d.type_doc)
    if not message.startswith("Document cree: "):
        raise HTTPException(status_code=502, detail=message)
    return {"ok": True, "path": message[len("Document cree: "):].split(" (")[0]}
