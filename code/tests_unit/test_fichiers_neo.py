"""« Fichiers de Neo » : ce que Neo produit par le chat doit apparaitre, pas ses fichiers de travail."""
import pytest

pytest.importorskip("fastapi")
from backend.api import routes_video as v  # noqa: E402


@pytest.fixture
def travail(tmp_path, monkeypatch):
    for chemin in (
        "images/portrait.jpg", "images/portrait_ecran.jpg", "images/portrait.brief.json", "images/ref_1794405298906.jpg",
        "images/video/shot1.mp4", "images/video/storyboard_1.json", "images/video/wan_abc/f_001.png",
        "documents/facture.pdf", "documents/devis.xlsx", "documents/solaire.pptx",
        "modeles_3d/lion.stl", "modeles_3d/lion_iso.png", "modeles_3d/lion_face.png", "modeles_3d/lion_modele.png",
        "pipeline_3d/20261111/support.3mf", "pipeline_3d/20261111/support_apercu.png",
        "pipeline_3d/20261111/piece_t1.scad", "pipeline_3d/20261111/spec.json",
        "ameliorations/4/note.md", "agents/dev/essai.py",
    ):
        f = tmp_path / chemin
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    monkeypatch.setattr(v, "_espace_de_travail", lambda: tmp_path)
    monkeypatch.setattr(v, "_neo_roots", lambda: [])
    return tmp_path


def test_liste_les_productions_du_chat_sans_les_fichiers_de_travail(travail):
    demande = type("Demande", (), {"base_url": "http://neo:8321/"})()
    reponse = v.video_neo_files(demande)
    par_nom = {f["name"]: f for f in reponse["files"]}
    assert sorted(par_nom) == ["devis.xlsx", "facture.pdf", "lion.stl", "portrait.jpg", "shot1.mp4",
                               "solaire.pptx", "support.3mf"]
    assert {f["type"] for f in reponse["files"]} == {"documents", "mesh", "images", "video"}
    # Vignettes : la copie d'ecran d'une image, la vue d'un modele 3D.
    assert par_nom["portrait.jpg"]["preview"].endswith("portrait_ecran.jpg")
    assert par_nom["lion.stl"]["preview"].endswith("lion_iso.png")
    assert par_nom["support.3mf"]["preview"].endswith("support_apercu.png")
    assert par_nom["facture.pdf"]["url"].startswith("http://neo:8321/api/video/file?path=")


def test_filtres_acceptes_sur_les_fichiers_de_neo_seulement(travail, monkeypatch):
    from fastapi import HTTPException
    from PIL import Image

    from backend.api import routes_neo
    from backend.core import security

    # Bac a sable des outils : tout est refuse ; seule l'appartenance aux dossiers de Neo compte.
    monkeypatch.setattr(security, "verifier_acces", lambda chemin, ecriture=False: "ACCES REFUSE")
    Image.new("RGB", (8, 8)).save(travail / "images" / "portrait.jpg")
    assert routes_neo._image_autorisee(str(travail / "images" / "portrait.jpg")).name == "portrait.jpg"
    ailleurs = travail / "agents" / "dev" / "photo.jpg"
    Image.new("RGB", (8, 8)).save(ailleurs)
    with pytest.raises(HTTPException) as refus:
        routes_neo._image_autorisee(str(ailleurs))
    assert refus.value.status_code == 403


def test_seuls_les_dossiers_de_production_sont_servis(travail):
    servis = v._racines_servies()
    assert v._dans_racine(travail / "documents" / "facture.pdf", servis)
    assert v._dans_racine(travail / "modeles_3d" / "lion.stl", servis)
    assert not v._dans_racine(travail / "agents" / "dev" / "essai.py", servis)
    assert not v._dans_racine(travail.parent / "config" / "secrets.json", servis)
    assert {".docx", ".xlsx", ".pptx"} <= v.EXT_SERVIES
