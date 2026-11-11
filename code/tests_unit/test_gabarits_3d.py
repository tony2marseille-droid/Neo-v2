"""Gabarits 3D : choix par le modele, cotes bornees, script OpenSCAD, verdict du controle visuel."""
import json

import pytest

from backend.tools import gabarits_3d
from backend.tools.t_mesh import _verdict_vision


def test_script_reprend_les_cotes_et_borne_les_aberrantes():
    code, valeurs = gabarits_3d.script("support_telephone", {"largeur": 75, "angle": 200, "epaisseur": "x",
                                                             "inconnu": 5})
    assert valeurs["largeur"] == 75 and valeurs["angle"] == 85          # 200 degres : borne
    assert valeurs["epaisseur"] == 4 and "inconnu" not in valeurs        # illisible : defaut
    assert "largeur = 75;" in code and "angle = 85;" in code and "inconnu" not in code
    assert code.count("{") == code.count("}") and code.count("(") == code.count(")")


@pytest.mark.parametrize("nom", list(gabarits_3d.GABARITS))
def test_chaque_gabarit_donne_un_script_equilibre(nom):
    code, valeurs = gabarits_3d.script(nom)
    assert code.count("{") == code.count("}") and code.count("[") == code.count("]")
    for cle in gabarits_3d.GABARITS[nom]["parametres"]:
        assert f"{cle} = " in code and cle in valeurs


def test_texte_grave_sans_caractere_dangereux():
    code, valeurs = gabarits_3d.script("porte_cles", texte='Camille"); cube(999); //\\')
    assert valeurs["texte"] == "Camille cube999"
    assert 'texte = "Camille cube999";' in code


@pytest.mark.parametrize("reponse,attendu", [
    ('{"gabarit": "support_telephone", "parametres": {"largeur": 80, "angle": 60}}', "support_telephone"),
    ('Voici :\n```json\n{"gabarit": "pot", "parametres": {}}\n```', "pot"),
    ('{"gabarit": "aucun"}', None), ('{"gabarit": "licorne"}', None), ("je ne sais pas", None), ("", None),
])
def test_choix_du_gabarit(reponse, attendu):
    g = gabarits_3d.choisir("un support", lambda consigne: reponse)
    assert (g["nom"] if g else None) == attendu
    if g and attendu == "support_telephone":
        assert g["parametres"]["largeur"] == 80 and "largeur = 80;" in g["scad"]


def test_modele_en_panne_pas_de_gabarit():
    def panne(consigne):
        raise OSError("ollama arrete")

    assert gabarits_3d.choisir("un pot", panne) is None


def test_la_consigne_presente_tous_les_gabarits():
    consigne = gabarits_3d.consigne_de_choix("un crochet pour mon manteau")
    assert all(nom in consigne for nom in gabarits_3d.GABARITS) and "un crochet pour mon manteau" in consigne
    assert json.dumps({"gabarit": "aucun"}) in consigne


@pytest.mark.parametrize("texte,verdict", [
    ("VERDICT: OUI\nSCORE FORME: 9/10", "OUI"),
    ("**VERDICT:** NON\n**SCORE FORME:** 2/10", "NON"),          # le gras ne gene plus
    ("FONCTIONS:\n- base : PRESENTE\n- dossier incliné : ABSENTE\nVERDICT: OUI", "NON"),
    ("FONCTIONS:\n- base : PRESENTE\nVERDICT: OUI\nECARTS: rien d'absent", "OUI"),
    ("Je ne peux pas juger.", "INCERTAIN"),
])
def test_verdict_du_controle_visuel(texte, verdict):
    assert _verdict_vision(texte) == verdict
