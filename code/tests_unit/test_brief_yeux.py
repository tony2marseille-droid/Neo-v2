"""Brief du studio : couleur des yeux naturelle en photo."""
import pytest

from backend.core import direction

BASE = {
    "analyse": "x", "comprehension": "c", "style": "photo", "cadrage": "portrait", "titre": "t",
    "elements": ["vibrant green eyes", "short black hair"],
    "prompt": ("Photorealistic portrait of a 40-year-old woman with short black hair and vibrant green eyes, "
               "soft studio lighting, detailed skin texture, shallow depth of field, 85mm lens."),
}


def test_yeux_naturels_en_photo():
    r = direction._nettoyer(dict(BASE), "une femme de 40 ans, cheveux noirs courts, yeux verts")
    assert "with short black hair and green eyes," in r["prompt"] and "vibrant" not in r["prompt"]
    assert r["elements"] == ["green eyes", "short black hair"]


@pytest.mark.parametrize("avant,apres", [
    ("piercing light blue eyes", "light blue eyes"), ("Striking emerald green eyes", "emerald green eyes"),
    ("deep brown eyes", "brown eyes"), ("glowing eyes", "eyes"), ("bright-blue eyes", "blue eyes"),
    ("green eyes", "green eyes"), ("vibrant red dress", "vibrant red dress"), ("bright smile", "bright smile"),
])
def test_seuls_les_qualificatifs_des_yeux_sont_retires(avant, apres):
    assert direction._YEUX_SATURES.sub(r"\2", avant) == apres


@pytest.mark.parametrize("demande", [
    "une elfe aux yeux verts éclatants", "un cyborg aux yeux qui brillent", "un démon aux yeux rouges",
    "portrait néon, yeux bleus électriques",
])
def test_regard_irreel_respecte_quand_il_est_demande(demande):
    assert "vibrant green eyes" in direction._nettoyer(dict(BASE), demande)["prompt"]


def test_style_dessine_non_concerne():
    r = direction._nettoyer({**BASE, "style": "anime"}, "une femme aux yeux verts, style manga")
    assert "vibrant green eyes" in r["prompt"]
