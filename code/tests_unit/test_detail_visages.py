"""Passes « visage » et « yeux » du studio : fusion, recalage, garde-fous (sans GPU)."""
import pytest

from backend.core import sdgen

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def _lab(rgb):
    return cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)


def test_fusion_d_une_image_avec_elle_meme_ne_change_rien():
    rng = np.random.default_rng(1)
    img = np.clip(rng.normal(120, 30, (96, 96, 3)), 0, 255).astype(np.uint8)
    res = sdgen._fusion_structure(img, img, sigma_l=4.0)
    assert np.abs(res.astype(int) - img.astype(int)).max() <= 2


def test_fusion_garde_la_couleur_d_origine():
    # Original : aplat marron. « klein » : meme scene eclaircie, bleutee, avec du grain.
    rng = np.random.default_rng(2)
    orig = np.full((96, 96, 3), (110, 70, 40), np.uint8)
    grain = rng.normal(0, 12, (96, 96, 1))
    neuf = np.clip(np.array((90, 120, 190)) + grain, 0, 255).astype(np.uint8)
    res = _lab(sdgen._fusion_structure(orig, neuf, sigma_l=6.0))
    o = _lab(orig)
    assert abs(res[..., 1].mean() - o[..., 1].mean()) < 2 and abs(res[..., 2].mean() - o[..., 2].mean()) < 2
    assert abs(res[..., 0].mean() - o[..., 0].mean()) < 2      # ni eclairci
    assert res[..., 0].std() > 1.0                              # mais le grain est passe


def test_fusion_ne_fait_rien_ressortir_d_une_zone_sombre():
    # Verre de lunettes noires : « klein » y dessine un detail contraste et clair.
    rng = np.random.default_rng(3)
    orig = np.full((96, 96, 3), 12, np.uint8)
    neuf = np.clip(rng.normal(110, 40, (96, 96, 3)), 0, 255).astype(np.uint8)
    sombre = sdgen._fusion_structure(orig, neuf, sigma_l=4.0).astype(np.float32)
    clair = sdgen._fusion_structure(np.full((96, 96, 3), 160, np.uint8), neuf, sigma_l=4.0).astype(np.float32)
    assert sombre.std() < 0.35 * clair.std()      # detail proportionnel a la luminosite locale
    assert sombre.mean() < 30


def test_recalage_supprime_un_decalage():
    img = np.full((160, 160, 3), 200, np.uint8)
    cv2.rectangle(img, (40, 40), (120, 120), (30, 30, 30), -1)
    cv2.circle(img, (80, 80), 22, (220, 220, 220), -1)
    decale = np.roll(img, 3, axis=1)
    avant = np.abs(decale.astype(int) - img.astype(int)).mean()
    apres = np.abs(sdgen._recaler_dense(img, decale, borne=8.0).astype(int) - img.astype(int)).mean()
    assert apres < 0.4 * avant


def test_recalage_laisse_un_aplat_tranquille():
    # Aucun contour : le grain de « klein » ne doit pas etre etire par un flot bruite.
    rng = np.random.default_rng(4)
    orig = np.full((128, 128, 3), 150, np.uint8)
    neuf = np.clip(150 + rng.normal(0, 10, (128, 128, 3)), 0, 255).astype(np.uint8)
    res = sdgen._recaler_dense(orig, neuf, borne=8.0)
    assert np.abs(res.astype(int) - neuf.astype(int)).mean() < 6


def _visage(yeux: int, peau: int = 180):
    """Visage schematique 400 x 400 : peau unie, zones des yeux a la valeur `yeux`."""
    img = np.full((400, 400, 3), peau, np.uint8)
    reperes = np.array([[140, 160], [260, 160], [200, 230], [160, 290], [240, 290]], np.float32)
    for x, y in reperes[:2]:
        cv2.ellipse(img, (int(x), int(y)), (26, 14), 0, 0, 360, (yeux, yeux, yeux), -1)
    return img, reperes


def test_yeux_visibles_avec_un_blanc_d_oeil():
    img, reperes = _visage(yeux=40)
    for x, y in reperes[:2]:                      # blanc de l'oeil de part et d'autre de l'iris
        cv2.circle(img, (int(x) - 14, int(y)), 6, (215, 215, 215), -1)
        cv2.circle(img, (int(x) + 14, int(y)), 6, (215, 215, 215), -1)
    assert sdgen._yeux_visibles(img, reperes)


def test_lunettes_noires_les_yeux_ne_sont_pas_visibles():
    img, reperes = _visage(yeux=25)
    cv2.rectangle(img, (95, 125), (305, 200), (25, 25, 25), -1)      # verres sombres
    assert not sdgen._yeux_visibles(img, reperes)


def test_retouche_prefere_la_copie_du_ssd(monkeypatch, tmp_path):
    monkeypatch.delenv("NEO_SD_RETOUCHE", raising=False)
    monkeypatch.delenv("LOCALAI_SD_RAPIDE", raising=False)
    archives, ssd = tmp_path / "sd" / "models_edit", tmp_path / "sd_ssd" / "models_edit"
    for d in (archives, ssd):
        d.mkdir(parents=True)
        (d / "flux-2-klein-4b-Q8_0.gguf").write_bytes(b"x")
        (d / "flux2-vae.safetensors").write_bytes(b"x")
    enc = tmp_path / "Qwen3-4B-Instruct-2507-Q8_0.gguf"
    enc.write_bytes(b"x")
    monkeypatch.setattr(sdgen, "_dir", lambda: tmp_path / "sd")
    monkeypatch.setattr(sdgen, "_fichiers_modeles", lambda: [enc])
    assert sdgen._modele_retouche()[0].parent == ssd
    # Copie SSD incomplete (VAE absent) : on retombe sur le disque d'archives.
    (ssd / "flux2-vae.safetensors").unlink()
    assert sdgen._modele_retouche()[0].parent == archives


def test_passe_yeux_ignoree_sans_yeux_visibles(monkeypatch, tmp_path):
    img, reperes = _visage(yeux=25)
    cv2.rectangle(img, (95, 125), (305, 200), (25, 25, 25), -1)
    monkeypatch.setattr(sdgen, "_executer_sd", lambda *a, **k: pytest.fail("klein ne doit pas etre lance"))
    dest = img.copy()
    assert sdgen._detail_yeux(img, dest, reperes, tmp_path / "x.png", (tmp_path, tmp_path, tmp_path)) == (False, 0.0)
    assert (dest == img).all()


class _Aide:
    def __init__(self, texte):
        self.stdout, self.stderr = texte, ""


def _binaire(monkeypatch, tmp_path, aide):
    appels = []
    monkeypatch.setattr(sdgen, "_SAGE", {})
    monkeypatch.setattr(sdgen, "_exe", lambda: tmp_path / "sd-cli.exe")
    monkeypatch.setattr(sdgen.subprocess, "run", lambda cmd, **k: appels.append(cmd) or _Aide(aide))
    monkeypatch.delenv("NEO_SD_SAGE", raising=False)
    return appels


def test_attention_acceleree_si_le_binaire_la_propose(monkeypatch, tmp_path):
    appels = _binaire(monkeypatch, tmp_path, "  --fa\n  --sage-attn   use native CUDA SageAttention\n")
    assert sdgen._attention() == ["--diffusion-fa", "--sage-attn"]
    assert sdgen._attention() == ["--diffusion-fa", "--sage-attn"]
    assert len(appels) == 1  # l'aide n'est lue qu'une fois
    monkeypatch.setenv("NEO_SD_SAGE", "0")
    assert sdgen._attention() == ["--diffusion-fa"]


def test_attention_classique_avec_un_ancien_binaire(monkeypatch, tmp_path):
    _binaire(monkeypatch, tmp_path, "  --fa\n  --diffusion-fa\n")
    assert sdgen._attention() == ["--diffusion-fa"]

    def panne(*a, **k):
        raise OSError("binaire absent")

    monkeypatch.setattr(sdgen, "_SAGE", {})
    monkeypatch.setattr(sdgen.subprocess, "run", panne)
    assert sdgen._attention() == ["--diffusion-fa"]


def test_generateur_principal_sans_attention_acceleree():
    """SageAttention lissait les visages de Z-Image : reservee aux passes klein."""
    import inspect

    source = inspect.getsource(sdgen._generer_nolock)
    assert "_attention(" not in source and '"--diffusion-fa"' in source
    assert "_attention(" not in inspect.getsource(sdgen._retoucher_klein)
    for passe in (sdgen._detail_visages, sdgen._detail_yeux):
        assert "*_attention()" in inspect.getsource(passe)


def test_attente_de_la_memoire_graphique(monkeypatch):
    """La carte est tenue pour libre quand son occupation a baisse puis ne bouge plus."""
    lectures = iter([11400.0, 6000.0, 1900.0, 1890.0, 1890.0])
    monkeypatch.setattr(sdgen, "_vram_utilisee_mio", lambda: next(lectures))
    monkeypatch.setattr(sdgen.time, "sleep", lambda s: None)
    monkeypatch.delenv("NEO_SD_ATTENTE_VRAM", raising=False)
    sdgen._attendre_vram_rendue(11400.0)
    assert next(lectures) == 1890.0   # arret a la premiere lecture stable apres la baisse (4 lectures)


def test_attente_coupee_ou_sans_mesure(monkeypatch):
    appels = []
    monkeypatch.setattr(sdgen, "_vram_utilisee_mio", lambda: appels.append(1) or 5000.0)
    monkeypatch.setattr(sdgen.time, "sleep", lambda s: appels.append("pause"))
    monkeypatch.setenv("NEO_SD_ATTENTE_VRAM", "0")
    assert sdgen._attendre_vram_rendue(9000.0) == 0.0 and appels == []
    monkeypatch.delenv("NEO_SD_ATTENTE_VRAM")
    assert sdgen._attendre_vram_rendue(None) == 3.0 and appels == ["pause"]   # marge fixe sans mesure


def test_echec_de_generation_ne_demande_pas_de_relancer():
    import inspect

    from backend.tools import t_image

    source = inspect.getsource(t_image.tool_generate_image)
    assert "NE RELANCE PAS generate_image" in source and "Reessaie dans un instant" not in source.split("# Message adresse")[1][400:]
