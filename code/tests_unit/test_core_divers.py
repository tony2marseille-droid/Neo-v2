# -*- coding: utf-8 -*-
"""Tests unitaires purs (sans base, sans reseau) des correctifs du coeur."""
from backend.core import hardware, sdgen


def test_resolve_plan_tag_latest_implicite():
    installes = ["qwen3:8b", "nomic-embed-text:latest", "QWEN2.5VL:7B"]
    plan = hardware.resolve_plan("T1", installes)
    assert plan["resolu"]["agent_brain"] == "qwen3:8b"
    # « nomic-embed-text » (plan) == « nomic-embed-text:latest » (Ollama)
    assert plan["resolu"]["embed"] == "nomic-embed-text:latest"
    assert plan["resolu"]["vision"] == "QWEN2.5VL:7B"


def test_sans_hires_retire_par_paires():
    cmd = ["sd", "--steps", "10", "--hires", "--hires-scale", "1.5",
           "--hires-denoising-strength", "0.4", "--hires-steps", "10",
           "--cfg-scale", "1.5"]
    # « --steps 10 » et « --cfg-scale 1.5 » survivent (l'ancien filtre par
    # valeur les supprimait).
    assert sdgen._sans_hires(cmd) == ["sd", "--steps", "10", "--cfg-scale", "1.5"]


def test_install_alias():
    assert sdgen.install is sdgen.installer


def test_sans_hires_retire_la_passe_haute_resolution():
    cmd = ["sd", "--steps", "6", "--hires", "--hires-width", "2560", "--hires-height", "1440",
           "--hires-upscaler", "Lanczos", "--hires-sigmas", "0.30,0.20,0.10,0.0", "-o", "x.png"]
    assert sdgen._sans_hires(cmd) == ["sd", "--steps", "6", "-o", "x.png"]


def test_prompt_flux_naturel_sans_etiquettes():
    p = sdgen.preparer_prompt("A lighthouse on a cliff at dusk", traduire=False)
    assert "masterpiece" not in p and "8k" not in p
    assert p.endswith(sdgen.SUFFIXE_FLUX_PHOTO)
    # style demande : pas de suffixe photo
    assert "Professional photograph" not in sdgen.preparer_prompt(
        "a cartoon cat", traduire=False)
    # SDXL : etiquettes conservees
    assert "masterpiece" in sdgen.preparer_prompt("a cat", traduire=False, flux=False)


def test_palette_par_defaut_naturelle():
    assert sdgen._palette_auto("un phare sur une falaise", 7) == "naturel"
    assert sdgen._palette_auto("rue cyberpunk aux neons") == "vibrant"


def test_choix_q8_selon_vram(monkeypatch, tmp_path):
    for nom in ("flux1-schnell-Q4_K_S.gguf", "flux1-schnell-q8_0.gguf", "t5xxl-q8_0.gguf"):
        (tmp_path / "models").mkdir(exist_ok=True)
        (tmp_path / "models" / nom).write_bytes(b"x")
    monkeypatch.setenv("LOCALAI_SD_DIR", str(tmp_path))
    monkeypatch.delenv("LOCALAI_SD_MODEL", raising=False)
    monkeypatch.setattr(sdgen, "_VRAM_CACHE", 16.0)
    assert sdgen._modele().name == "flux1-schnell-q8_0.gguf"
    monkeypatch.setattr(sdgen, "_VRAM_CACHE", 10.0)
    assert sdgen._modele().name == "flux1-schnell-Q4_K_S.gguf"
    # Krea dev present : schnell reste prefere (Krea ecarte apres essais) ;
    # les fichiers video (Wan, umt5, VAE Wan) ne sont jamais choisis.
    for nom in ("flux1-krea-dev-Q8_0.gguf", "Wan2.2-TI2V-5B-Q8_0.gguf",
                "umt5-xxl-encoder-Q8_0.gguf", "Wan2.2_VAE.safetensors", "ae.safetensors"):
        (tmp_path / "models" / nom).write_bytes(b"x")
    monkeypatch.setattr(sdgen, "_VRAM_CACHE", 16.0)
    assert sdgen._modele().name == "flux1-schnell-q8_0.gguf"
    assert dict(sdgen._encodeurs_flux())["--vae"].name == "ae.safetensors"
    monkeypatch.setenv("LOCALAI_SD_MODEL", "flux1-krea-dev-Q8_0.gguf")
    assert sdgen._modele().name == "flux1-krea-dev-Q8_0.gguf"
    assert sdgen._guidance_flux(sdgen._modele()) == sdgen.GUIDANCE_DEV
    monkeypatch.delenv("LOCALAI_SD_MODEL")
    monkeypatch.setattr(sdgen, "_VRAM_CACHE", 10.0)
    assert sdgen._modele().name == "flux1-schnell-Q4_K_S.gguf"


def test_stockage_hybride_ssd_prioritaire(monkeypatch, tmp_path):
    lent = tmp_path / "sd" / "models"
    rapide = tmp_path / "sd_ssd" / "models"
    lent.mkdir(parents=True)
    rapide.mkdir(parents=True)
    for nom in ("flux1-krea-dev-Q8_0.gguf", "t5xxl-q8_0.gguf", "ae.safetensors", "flux1-schnell-q8_0.gguf"):
        (lent / nom).write_bytes(b"x")
    for nom in ("flux1-krea-dev-Q8_0.gguf", "t5xxl-q8_0.gguf"):
        (rapide / nom).write_bytes(b"x")
    monkeypatch.setenv("LOCALAI_SD_DIR", str(tmp_path / "sd"))
    monkeypatch.delenv("LOCALAI_SD_RAPIDE", raising=False)
    monkeypatch.delenv("LOCALAI_SD_MODEL", raising=False)
    monkeypatch.setattr(sdgen, "_VRAM_CACHE", 16.0)
    monkeypatch.setenv("LOCALAI_SD_MODEL", "flux1-krea-dev-Q8_0.gguf")
    assert sdgen._modele() == rapide / "flux1-krea-dev-Q8_0.gguf"
    enc = dict(sdgen._encodeurs_flux())
    assert enc["--t5xxl"] == rapide / "t5xxl-q8_0.gguf"
    assert enc["--vae"] == lent / "ae.safetensors"  # absent du SSD : repli
    assert sdgen._fichier_modele("flux1-schnell-q8_0.gguf") == lent / "flux1-schnell-q8_0.gguf"


def test_texte_lu_a_voix_haute_sans_markdown():
    from backend.tts.engine import nettoyer_pour_voix

    texte = ("## Résultat\n**Bravo**, voici `x = 1` et [la doc](https://exemple.fr/doc).\n"
             "```python\nprint('hi')\n```\nImage : E:\ia\workspace\images\chat.jpg\n- point un")
    lu = nettoyer_pour_voix(texte)
    assert "#" not in lu and "*" not in lu and "`" not in lu and "https" not in lu
    assert "Bravo" in lu and "x = 1" in lu and "la doc" in lu and "point un" in lu
    assert "print" not in lu and "workspace" not in lu
