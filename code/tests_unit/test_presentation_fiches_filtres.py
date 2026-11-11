"""PowerPoint, fiches de connaissances et filtres photo."""
import numpy as np
import pytest
from PIL import Image

from backend.core import database, fiches, filtres_photo, router
from backend.tools import t_docs

PRESENTATION = (
    "# L'énergie solaire\n\nComprendre les enjeux et les techniques.\n\n"
    "## Pourquoi le solaire ?\n- Une ressource **inépuisable**\n- Des coûts en baisse\n\nNote : insister sur les coûts.\n\n"
    "## Les techniques\n- Photovoltaïque\n- Thermique\n- Note : montrer un panneau\n\n| Type | Rendement |\n| --- | --- |\n| Silicium | 22 % |\n\n"
    "## Longue liste\n" + "\n".join(f"- point {n}" for n in range(1, 12)) + "\n"
)


# ---- PowerPoint ----

def test_presentation_une_diapositive_par_titre(tmp_path):
    pptx = pytest.importorskip("pptx")
    sortie = tmp_path / "solaire.pptx"
    t_docs._vers_pptx("Energie_Solaire.pptx", PRESENTATION, sortie)
    diapos = list(pptx.Presentation(str(sortie)).slides)
    textes = [[f.text_frame.text for f in d.shapes if f.has_text_frame and f.text_frame.text] for d in diapos]
    assert len(diapos) == 5                      # titre + 3 sujets, dont le dernier sur deux diapositives
    assert textes[0][0] == "L'énergie solaire" and "Comprendre les enjeux" in textes[0][1]
    assert textes[1][0] == "Pourquoi le solaire ?" and "•  Une ressource inépuisable" in textes[1][1]
    assert diapos[1].notes_slide.notes_text_frame.text == "insister sur les coûts."
    assert any(f.has_table for f in diapos[2].shapes)
    # Une note ecrite parmi les puces rejoint les notes de l'orateur.
    assert diapos[2].notes_slide.notes_text_frame.text == "montrer un panneau" and "Note" not in textes[2][1]
    assert textes[4][0] == "Longue liste (suite)" and "point 9" in textes[4][1]
    assert "**" not in "".join(sum(textes, []))


def test_outil_document_accepte_le_powerpoint(tmp_path, monkeypatch):
    pytest.importorskip("pptx")
    monkeypatch.setattr(t_docs, "_dossier_documents", lambda: tmp_path)
    monkeypatch.setattr(t_docs, "_verifier_acces", lambda chemin, ecriture=False: None)
    monkeypatch.setattr(t_docs, "_extension_bloquee", lambda chemin: None)
    message = t_docs.tool_document_create("Énergie solaire", PRESENTATION, "PowerPoint")
    assert message.startswith("Document cree: ") and "energie_solaire.pptx" in message.lower().replace("é", "e")


@pytest.mark.parametrize("demande,agent", [
    ("Fais-moi une présentation PowerPoint de 5 diapositives sur l'énergie solaire", "bureau"),
    ("Prépare un diaporama sur notre voyage au Japon", "bureau"),
    ("Fais-moi une fiche sur le pont du Gard", "web"),
    ("Garde une fiche de ce qu'on vient de dire sur la photosynthèse", "web"),
    ("Fais-moi une fiche PDF sur le pont du Gard", "bureau"),
    ("Tu peux me faire une fiche à propos des volcans ?", "web"),
])
def test_aiguillage_presentations_et_fiches(monkeypatch, demande, agent):
    monkeypatch.setattr(router, "_llm_intention", lambda msg: "")
    assert router.route_agent(demande)[0] == agent


# ---- Fiches ----

@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_instance", database.Database(tmp_path / "essai.db"))
    monkeypatch.setattr(fiches, "_matiere_web", lambda sujet: ("matiere", ["https://exemple.fr/pont"]))


FICHE = {"fichable": True, "titre": "Le pont du Gard", "categorie": "histoire",
         "resume": "Un aqueduc romain. Il est classé à l'UNESCO.",
         "en_bref": "Le pont du Gard est un pont-aqueduc romain à trois niveaux qui enjambe le Gardon.",
         "points_cles": ["Construit vers 50 apr. J.-C.", "48 mètres de haut"], "a_retenir": ["Classé en 1985"]}


def test_fiche_creee_lue_mise_a_jour_supprimee(base, monkeypatch):
    monkeypatch.setattr(fiches, "_demander_json", lambda consigne, delai_s=180: dict(FICHE))
    f = fiches.creer("pont du gard")
    assert f["titre"] == "Le pont du Gard" and f["categorie"] == "histoire" and f["sources"] == ["https://exemple.fr/pont"]
    assert f["contenu_md"].startswith("## En bref\nLe pont du Gard") and "- 48 mètres de haut" in f["contenu_md"]
    assert "## À retenir" in f["contenu_md"]
    # Meme sujet : la fiche est mise a jour, pas doublee.
    assert fiches.creer("le pont du Gard")["id"] == f["id"] and len(fiches.lister()) == 1
    assert [x["titre"] for x in fiches.lister("aqueduc")] == ["Le pont du Gard"] and fiches.lister("volcan") == []
    assert [x["titre"] for x in fiches.pertinentes("parle-moi du pont du Gard")] == ["Le pont du Gard"]
    assert fiches.pertinentes("bonjour, ça va ?") == []
    assert fiches.supprimer(f["id"]) and fiches.lire(f["id"]) is None and not fiches.supprimer(f["id"])


@pytest.mark.parametrize("reponse", [
    {}, {"fichable": True, "titre": "Quel est le cours du bitcoin ?", "en_bref": "x" * 80},
    {"fichable": True, "titre": "Sujet", "en_bref": "trop court"},
])
def test_redaction_inexploitable_pas_de_fiche(base, monkeypatch, reponse):
    monkeypatch.setattr(fiches, "_demander_json", lambda consigne, delai_s=180: reponse)
    assert "erreur" in fiches.creer("un sujet") and fiches.lister() == []


def test_fiche_automatique_seulement_si_le_sujet_le_merite(base, monkeypatch):
    monkeypatch.setattr(fiches, "_demander_json", lambda consigne, delai_s=180: {"fichable": False})
    assert fiches.depuis_reponse("Quel est le cours du bitcoin ?", "98 400 dollars") == ""
    monkeypatch.setattr(fiches, "_demander_json", lambda consigne, delai_s=180: dict(FICHE))
    assert fiches.depuis_reponse("C'est quoi le pont du Gard ?", "Un aqueduc. https://fr.wikipedia.org/wiki/Pont_du_Gard.") == (
        "FICHE : Le pont du Gard (histoire)")
    f = fiches.lister()[0]
    assert f["sources"] == ["https://fr.wikipedia.org/wiki/Pont_du_Gard"] and f["source"].startswith("conversation : ")


def test_categorie_inconnue_rangee_dans_autre(base, monkeypatch):
    monkeypatch.setattr(fiches, "_demander_json", lambda consigne, delai_s=180: {**FICHE, "categorie": "Licornes"})
    assert fiches.creer("x y")["categorie"] == "autre"


# ---- Filtres photo ----

@pytest.fixture
def photo(tmp_path):
    y, x = np.mgrid[0:120, 0:180]
    pixels = np.stack([x * 255 // 179, y * 255 // 119, (x + y) * 255 // 298], axis=2).astype(np.uint8)
    pixels[40:80, 60:120] = (230, 40, 40)            # un aplat a bords francs (pour la nettete)
    chemin = tmp_path / "paysage.png"
    Image.fromarray(pixels).save(chemin)
    return chemin


@pytest.mark.parametrize("filtre", [f[0] for f in filtres_photo.FILTRES])
def test_chaque_filtre_rend_une_image_differente(photo, filtre):
    original = Image.open(photo)
    rendu = filtres_photo.appliquer(original, filtre)
    assert rendu.size == original.size and rendu.mode == "RGB"
    assert np.abs(np.asarray(rendu, dtype=int) - np.asarray(original, dtype=int)).max() >= 4
    # Force 0 : l'original, au pixel pres.
    assert np.array_equal(np.asarray(filtres_photo.appliquer(original, filtre, 0)), np.asarray(original))


def test_noir_et_blanc_sans_couleur(photo):
    a = np.asarray(filtres_photo.appliquer(Image.open(photo), "noir_blanc"), dtype=int)
    assert np.abs(a[..., 0] - a[..., 1]).max() <= 1 and np.abs(a[..., 1] - a[..., 2]).max() <= 1


def test_apercus_puis_copie_sans_toucher_l_original(photo):
    avant = photo.read_bytes()
    d = filtres_photo.apercus(str(photo))
    assert (d["largeur"], d["hauteur"]) == (180, 120) and len(d["filtres"]) == len(filtres_photo.FILTRES)
    assert all(f["apercu"].startswith("data:image/jpeg;base64,") for f in d["filtres"])
    copie = filtres_photo.enregistrer(str(photo), "sepia", 0.8)
    assert copie.endswith("paysage_sepia.png") and Image.open(copie).size == (180, 120)
    assert filtres_photo.enregistrer(str(photo), "sepia").endswith("paysage_sepia_2.png")
    assert photo.read_bytes() == avant
    with pytest.raises(ValueError):
        filtres_photo.appliquer(Image.open(photo), "inconnu")


def test_apercu_du_studio_renvoie_a_son_original(tmp_path):
    maitre, apercu = tmp_path / "portrait.tif", tmp_path / "portrait_apercu.jpg"
    Image.new("RGB", (40, 30), (120, 80, 40)).save(maitre)
    Image.new("RGB", (20, 15), (120, 80, 40)).save(apercu)
    assert filtres_photo.source(str(apercu)) == maitre
    assert filtres_photo.source(str(tmp_path / "seul_apercu.jpg")).name == "seul_apercu.jpg"
