"""Studio image : brief du directeur artistique, formats, commande de secours."""
import pytest

from backend.core import direction, sdgen


def _brut(**k):
    base = {
        "comprehension": "J'ai compris « lazar » comme un laser.",
        "corrections": ["lazar -> laser"],
        "style": "photo", "cadrage": "paysage",
        "elements": ["spaceship", "red laser beam"],
        "prompt": ("A sleek spaceship fires a red laser beam across a nebula, shot on a 16 mm lens "
                   "with a low angle and deep perspective, volumetric light."),
        "titre": "Vaisseau laser !",
    }
    base.update(k)
    return base


def test_brief_valide_et_normalise():
    b = direction._nettoyer(_brut(), "un vaisseau qui tire un lazar")
    assert b["ok"] and b["cadrage"] == "paysage" and b["style"] == "photo"
    assert b["corrections"] == ["lazar -> laser"]
    assert b["titre"] == "vaisseau_laser"


def test_correction_absente_de_la_demande_ignoree():
    # Le modele recopiait l'exemple de la consigne (« lazar -> laser ») sur
    # une demande qui ne contenait pas « lazar ».
    b = direction._nettoyer(_brut(), "un visage avec un dégradé au laser")
    assert b["corrections"] == []


def test_prompt_en_francais_refuse():
    with pytest.raises(ValueError):
        direction._nettoyer(_brut(prompt=(
            "Un vaisseau spatial tire un laser rouge dans une nebuleuse, avec une "
            "perspective profonde et une lumiere volumetrique sur la coque.")), "x")


def test_valeurs_hors_liste_ramenees_au_defaut():
    b = direction._nettoyer(_brut(style="hyperreal", cadrage="vertical"), "x")
    assert b["style"] == "photo" and b["cadrage"] == "paysage"


@pytest.mark.parametrize("taille,attendu", [
    ((2560, 1440), (3840, 2160)),
    ((1440, 2560), (2160, 3840)),
    ((1920, 1920), (2880, 2880)),
])
def test_master_selon_le_cadrage(taille, attendu):
    assert sdgen._cible_master(*taille) == attendu


def test_cadrages_coherents():
    for nom, f in sdgen.CADRAGES.items():
        (bw, bh), (hw, hh) = f["base"], f["hires"]
        assert abs(bw / bh - hw / hh) < 0.01, nom
        # FLUX travaille par blocs de 16 px (latent /8, patchs 2x2).
        assert all(v % 16 == 0 for v in (bw, bh, hw, hh)), nom


def test_commande_de_secours_allege_flux(monkeypatch, tmp_path):
    q4 = tmp_path / "flux1-schnell-Q4_K_S.gguf"
    q4.write_bytes(b"x")
    monkeypatch.setattr(sdgen, "_fichiers_modeles", lambda: [q4])
    cmd = ["sd-cli", "--diffusion-model", "flux1-schnell-q8_0.gguf", "-p", "x"]
    c = sdgen._commande_secours(cmd, "flux")
    assert c[c.index("--diffusion-model") + 1] == str(q4)
    assert "te=cpu" in c and "--vae-tiling" in c
    assert sdgen._commande_secours(["sd-cli", "-m", "sd15.gguf"], "sd15") is None


def test_brief_lu_a_cote_de_l_image(tmp_path):
    img = tmp_path / "chat.tiff"
    img.write_bytes(b"x")
    direction.ecrire_brief(img, {"prompt": "a cat"})
    assert direction.lire_brief(img)["prompt"] == "a cat"
    assert direction.lire_brief(tmp_path / "chat_apercu.jpg")["prompt"] == "a cat"


def test_z_image_reconnu_et_prefere(monkeypatch, tmp_path):
    noms = ["ae.safetensors", "t5xxl-q8_0.gguf", "clip_l.safetensors",
            "flux1-schnell-q8_0.gguf", "z_image_turbo-Q8_0.gguf",
            "Qwen3-4B-Instruct-2507-Q8_0.gguf"]
    fichiers = []
    for n in noms:
        (tmp_path / n).write_bytes(b"x")
        fichiers.append(tmp_path / n)
    monkeypatch.setattr(sdgen, "_fichiers_modeles", lambda: fichiers)
    monkeypatch.setattr(sdgen, "_dir", lambda: tmp_path)
    monkeypatch.delenv("LOCALAI_SD_MODEL", raising=False)
    m = sdgen._modele()
    # « turbo » dans le nom ne doit pas en faire un SDXL Turbo, et
    # l'encodeur Qwen3 n'est jamais pris pour un modele d'image.
    assert m.name == "z_image_turbo-Q8_0.gguf"
    assert sdgen._type_modele(m) == "flux" and sdgen._steps_defaut(m) == 8
    assert [f for f, _ in sdgen._encodeurs_modele(m)] == ["--llm", "--vae"]
    assert sdgen._modele_secondaire(m) == "flux1-schnell-q8_0.gguf"


def test_sans_encodeur_z_image_reste_sur_flux(monkeypatch, tmp_path):
    fichiers = []
    for n in ["ae.safetensors", "t5xxl-q8_0.gguf", "flux1-schnell-q8_0.gguf",
              "z_image_turbo-Q8_0.gguf"]:
        (tmp_path / n).write_bytes(b"x")
        fichiers.append(tmp_path / n)
    monkeypatch.setattr(sdgen, "_fichiers_modeles", lambda: fichiers)
    monkeypatch.setattr(sdgen, "_dir", lambda: tmp_path)
    monkeypatch.delenv("LOCALAI_SD_MODEL", raising=False)
    assert sdgen._modele().name == "flux1-schnell-q8_0.gguf"


@pytest.mark.parametrize("demande,mot,candidat", [
    ("un vaisseau qui apercoit un lazar", "lazar", "blazar"),
    ("une galaxie avec un kazar brillant", "kazar", "quasar"),
    ("une nébuleuse avec un pulsart au centre", "pulsart", "pulsar"),
    ("un drakon qui crache du feu", "drakon", "dragon"),
])
def test_indices_lexicaux_proposent_le_mot_rare(demande, mot, candidat):
    indices = " ".join(direction.indices_lexicaux(demande))
    assert f"« {mot} »" in indices and candidat in indices


@pytest.mark.parametrize("demande", [
    "une voiture rouge sous la pluie",
    "un nain barbu guerrier",
    "un portrait au centre de la ville",
])
def test_pas_d_indice_sur_les_mots_courants(demande):
    assert direction.indices_lexicaux(demande) == []


def test_consigne_sans_traduction_figee():
    # « lazar = laser » ecrit dans la consigne faisait d'un blazar un laser.
    assert "lazar" not in direction.CONSIGNE.lower()


def test_vision_prefere_gemma4_sinon_qwen2_5vl(monkeypatch):
    import httpx

    class R:
        def __init__(self, noms):
            self.noms = noms

        def json(self):
            return {"models": [{"name": n} for n in self.noms]}

    monkeypatch.setattr(direction, "MODELE_VISION", "")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(["qwen2.5vl:7b", "gemma4:12b"]))
    assert direction._modele_vision("http://x") == "gemma4:12b"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(["qwen2.5vl:7b", "qwen3:14b"]))
    assert direction._modele_vision("http://x") == "qwen2.5vl:7b"
    monkeypatch.setattr(direction, "MODELE_VISION", "llava:13b")
    assert direction._modele_vision("http://x") == "llava:13b"


def test_retouche_klein_trouvee_et_prioritaire(monkeypatch, tmp_path):
    edit = tmp_path / "models_edit"
    edit.mkdir()
    (edit / "flux-2-klein-4b-Q8_0.gguf").write_bytes(b"x")
    (edit / "flux2-vae.safetensors").write_bytes(b"x")
    enc = tmp_path / "Qwen3-4B-Instruct-2507-Q8_0.gguf"
    enc.write_bytes(b"x")
    monkeypatch.setattr(sdgen, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sdgen, "_fichiers_modeles", lambda: [enc])
    m = sdgen._modele_retouche()
    assert m and m[0].name.startswith("flux-2-klein") and m[2] == enc


def test_sans_klein_pas_de_retouche_klein(monkeypatch, tmp_path):
    monkeypatch.setattr(sdgen, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sdgen, "_fichiers_modeles", lambda: [])
    assert sdgen._modele_retouche() is None


def test_sans_chemin_l_image_va_dans_workspace(monkeypatch, tmp_path):
    from backend.tools import t_image

    monkeypatch.setattr(t_image, "_dossier_images", lambda: tmp_path / "images")
    c = t_image._chemin_de_sortie("", "portrait_roux", ".png")
    assert c.startswith(str(tmp_path / "images")) and c.endswith(".png") and "portrait_roux" in c


def test_chemin_du_profil_systeme_redirige(monkeypatch, tmp_path):
    from backend.tools import t_image

    monkeypatch.setattr(t_image, "_dossier_images", lambda: tmp_path / "images")
    c = t_image._chemin_de_sortie(
        r"C:\Windows\System32\config\systemprofile\Documents\Neo\x.jpg", "x", ".png")
    assert c == str(tmp_path / "images" / "x.png")


def test_documents_hors_du_profil_systeme(monkeypatch):
    from pathlib import Path

    from backend.core import config

    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path(r"C:\Windows\System32\config\systemprofile")))
    d = config._dossier_documents_defaut()
    assert "systemprofile" not in str(d).lower() and d.parts[-2:] == ("workspace", "images")


def test_detail_visages_saute_les_styles_dessines(monkeypatch):
    appele = []
    monkeypatch.setattr(sdgen, "_modele_retouche", lambda: appele.append(1) or None)
    assert sdgen._detail_visages(None, "portrait anime d'une guerriere") == 0
    assert not appele  # style dessine : aucune recherche de modele


def test_detail_visages_desactivable(monkeypatch):
    monkeypatch.setenv("NEO_IMAGE_DETAIL_VISAGES", "0")
    assert sdgen._detail_visages(None, "portrait photo") == 0
