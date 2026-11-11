"""Aiguillage des demandes d'objets a fabriquer en 3D."""
import pytest

from backend.core import router


@pytest.fixture(autouse=True)
def sans_classifieur(monkeypatch):
    # Les regles seules : jamais d'appel au modele pendant les tests.
    monkeypatch.setattr(router, "_llm_intention", lambda msg: "")


@pytest.mark.parametrize("demande", [
    "Fais-moi un support de téléphone en 3D à imprimer, incliné à 60 degrés",
    "Fais-moi un modèle 3D d'une tête de lion à imprimer, 80 mm de haut",
    "Crée une boîte de rangement en 3D de 10 cm de long",
    "Je veux un crochet 3D à imprimer pour mon manteau",
    "Prépare un porte-clés pour mon imprimante 3D",
    "Conçois-moi un pot à crayons",
])
def test_objet_a_fabriquer_va_a_la_conception_3d(demande):
    assert router.route_agent(demande)[0] == "cad3d"


@pytest.mark.parametrize("demande,agent", [
    ("Génère une image d'un dragon en 3D", "image"),
    ("Dessine-moi un robot en 3D", "image"),
    ("Fais-moi un logo style 3D pour ma chaîne", "image"),
    ("Fais-moi un CV au format Word", "bureau"),
    ("Bonjour, ça va ?", "general"),
])
def test_le_reste_ne_part_pas_en_3d(demande, agent):
    assert router.route_agent(demande)[0] == agent


def test_question_sur_la_3d_reste_une_question():
    assert router.route_agent("C'est quoi un film en 3D ?")[0] != "cad3d"
