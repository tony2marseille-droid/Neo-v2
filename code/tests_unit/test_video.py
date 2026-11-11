# -*- coding: utf-8 -*-
"""Logique pure de la chaine video (aucun appel GPU, ffmpeg ni LLM)."""
from backend.tools import t_video as tv


def test_mouvement_reconnu():
    assert tv._mouvement("The camera slowly pulls back") == "slow zoom out"
    assert tv._mouvement("He walks, camera pan left") == "pan left"
    assert tv._mouvement("panoramique vers la droite") == "pan right"
    assert tv._mouvement("rain falls, static shot") == "static"
    assert tv._mouvement("") == "slow zoom in"


def test_filtre_ken_burns_une_image_cadence_native():
    f = tv._filtre_mouvement("slow zoom in", 5.0, fps=24, taille=(1920, 1080))
    # 5 s a 24 i/s tirees d'UNE image (d=120), sur-echantillonnage x2, courbe en S.
    assert "d=120" in f and "fps=24" in f and "scale=3840:2160" in f and "cos(PI" in f


def test_fondus_enchaines_offsets_et_duree():
    filtre, total = tv._filtre_montage([5.0, 5.0, 4.0])
    # deux fondus de 0,6 s : 5 + 5 + 4 - 1,2
    assert abs(total - 12.8) < 1e-6
    assert "offset=4.400" in filtre and "offset=8.800" in filtre
    assert filtre.endswith("[v]") and "fade=t=out" in filtre
    seul, t1 = tv._filtre_montage([3.0])
    assert t1 == 3.0 and "xfade" not in seul


def test_prompt_plan_garde_personnage_et_style():
    data = {"characterDescription": "an old fisherman with a white beard and blue eyes",
            "visualStyle": "moody overcast light, muted teal palette"}
    p = tv._prompt_plan(data, {"imagePrompt": "Close-up on the harbor at dawn."})
    assert "white beard" in p and "muted teal" in p
    # deja presents : pas de duplication
    deja = "Close-up of an old fisherman with a white beard and blue eyes. moody overcast light, muted teal palette"
    assert tv._prompt_plan(data, {"imagePrompt": deja}).count("white beard") == 1


# ---- Un plan : image de travail quand Wan anime, image 4K pour le Ken Burns ----

import json  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def plan(tmp_path, monkeypatch):
    """Storyboard d'un plan, studio et ffmpeg simules ; journal des appels."""
    from backend.core import sdgen

    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({"seed": 10, "shots": [{"id": 1, "durationSeconds": 3,
                                                     "imagePrompt": "A harbor at dawn. Boats.",
                                                     "videoPrompt": "slow zoom in"}]}), encoding="utf-8")
    appels = {"generer": [], "wan": [], "ffmpeg": [], "prechauffe": 0}

    def generer(prompt, sortie, **k):
        appels["generer"].append(k)
        open(sortie, "wb").write(b"png")
        return f"Image generee: {sortie} (1 Ko) [essai]"

    monkeypatch.setattr(sdgen, "generer", generer)
    monkeypatch.setattr(tv, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(tv, "IMAGE_LEGERE", True)
    monkeypatch.setattr(tv, "_wan", lambda: {"modele": "m", "t5": "t", "vae": "v", "tae": None, "exe": "sd"})
    monkeypatch.setattr(tv, "_prechauffer_wan", lambda: appels.__setitem__("prechauffe", appels["prechauffe"] + 1))
    monkeypatch.setattr(tv, "_run_cmd", lambda cmd, timeout=0: (appels["ffmpeg"].append(cmd) or (0, "", 0.1)))
    return sb, appels


def test_plan_anime_par_wan_image_de_travail(plan, monkeypatch):
    sb, appels = plan
    monkeypatch.setattr(tv, "_clip_wan", lambda *a, **k: appels["wan"].append(a) or None)
    rep = tv.tool_video_clip(str(sb), 1)
    assert "Wan 2.2" in rep and appels["prechauffe"] == 1
    # Une seule image, la base du studio, sans passes visage ni finition.
    assert len(appels["generer"]) == 1
    k = appels["generer"][0]
    assert (k["largeur"], k["hauteur"]) == tv.IMAGE_TRAVAIL and k["resolution"] == "hd" and k["brut"] is True
    assert not any("zoompan" in " ".join(c) for c in appels["ffmpeg"])  # pas de Ken Burns


def test_wan_en_echec_ken_burns_sur_l_image_4k(plan, monkeypatch):
    sb, appels = plan
    monkeypatch.setattr(tv, "_clip_wan", lambda *a, **k: "Wan 2.2 en echec (code 1)")
    rep = tv.tool_video_clip(str(sb), 1)
    assert "Ken Burns" in rep
    # L'image de travail, puis l'image complete (meme graine) pour le zoom.
    assert len(appels["generer"]) == 2 and "brut" not in appels["generer"][1]
    assert appels["generer"][0]["graine"] == appels["generer"][1]["graine"] == 11
    assert "zoompan" in " ".join(appels["ffmpeg"][-1])  # (un essai de l'encodeur peut preceder)


def test_sans_wan_image_4k_directe(plan, monkeypatch):
    sb, appels = plan
    monkeypatch.setattr(tv, "_wan", lambda: None)
    rep = tv.tool_video_clip(str(sb), 1)
    assert "Ken Burns" in rep and appels["prechauffe"] == 0
    assert len(appels["generer"]) == 1 and "brut" not in appels["generer"][0]


def test_wan_refuse_si_la_carte_est_prise(tmp_path, monkeypatch):
    from backend.core import sdgen

    image = tmp_path / "i.png"
    from PIL import Image

    Image.new("RGB", (1536, 864), (90, 120, 150)).save(image)
    monkeypatch.setattr(tv, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(tv, "_wan", lambda: {"modele": "m", "t5": "t", "vae": "v", "tae": None, "exe": "sd"})
    monkeypatch.setattr(sdgen, "_acquerir_verrou_gpu", lambda *a, **k: None)
    lances = []
    monkeypatch.setattr(sdgen, "_executer_sd", lambda cmd, delai: lances.append(cmd))
    echec = tv._clip_wan(image, "x", 3.0, tmp_path / "c.mp4", 1)
    assert "occupee" in echec and lances == []


def test_wan_delai_depasse_rend_un_motif_clair(tmp_path, monkeypatch):
    import subprocess

    from backend.core import sdgen
    from PIL import Image

    image = tmp_path / "i.png"
    Image.new("RGB", (1536, 864), (90, 120, 150)).save(image)

    class _Verrou:
        def close(self):
            pass

    def trop_long(cmd, delai):
        assert delai == tv.WAN_DELAI_S < 1800  # sous le delai du pont Rust
        raise subprocess.TimeoutExpired(cmd, delai)

    monkeypatch.setattr(tv, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(tv, "_wan", lambda: {"modele": "m", "t5": "t", "vae": "v", "tae": None, "exe": "sd"})
    monkeypatch.setattr(sdgen, "_acquerir_verrou_gpu", lambda *a, **k: _Verrou())
    monkeypatch.setattr(sdgen, "_liberer_vram", lambda *a, **k: None)
    monkeypatch.setattr(sdgen, "_prechauffer_cache", lambda chemins, force=False: None)
    monkeypatch.setattr(sdgen, "_executer_sd", trop_long)
    traces = []
    monkeypatch.setattr(tv, "_journal_wan", lambda *args: traces.append(args))
    echec = tv._clip_wan(image, "x", 3.0, tmp_path / "c.mp4", 1)
    assert "delai" in echec and "Wan" in echec
    assert traces and traces[0][0] == -1 and traces[0][2] == 0   # echec trace, aucune image


# ---- Montage : seulement les clips du projet en cours, plans manquants signales ----

def test_montage_ne_melange_pas_les_projets(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(tv, "VIDEO_DIR", tmp_path)
    t0 = 1_700_000_000
    # Projet precedent : plans 1 a 3. Projet en cours (storyboard a t0+1000) : plans 1 et 3 rendus, 2 en echec.
    for nom, quand in (("shot1_a.mp4", t0), ("shot2_a.mp4", t0 + 10), ("shot3_a.mp4", t0 + 20),
                       ("shot1_b.mp4", t0 + 1100), ("shot3_b.mp4", t0 + 1300)):
        f = tmp_path / nom
        f.write_bytes(b"clip")
        os.utime(f, (quand, quand))
    sb = tmp_path / f"storyboard_{t0 + 1000}.json"
    sb.write_text(json.dumps({"shots": [{"id": 1}, {"id": 2}, {"id": 3}]}), encoding="utf-8")
    assert tv._dernier_storyboard() == (float(t0 + 1000), [1, 2, 3])

    commandes = []

    def run(cmd, timeout=0):
        commandes.append(cmd)
        if "-filter_complex" in cmd:
            (tmp_path / "essai_final.mp4").write_bytes(b"film")
        return 0, "", 0.1

    monkeypatch.setattr(tv, "_run_cmd", run)
    monkeypatch.setattr(tv, "_duree_media", lambda p: 3.0)
    rep = tv.tool_video_render("essai")
    montage = next(c for c in commandes if "-filter_complex" in c)
    entrees = [Path_(c).name for c in montage if str(c).endswith(".mp4") and "final" not in str(c)]
    assert entrees == ["shot1_b.mp4", "shot3_b.mp4"]          # rien du projet precedent
    assert "2 plans" in rep and "plan(s) 2 absent(s)" in rep  # l'echec du plan 2 est dit
    assert (tmp_path / "shot2_a.mp4").exists()                # l'ancien projet n'est pas touche


def test_sans_storyboard_pas_de_filtre(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "VIDEO_DIR", tmp_path)
    assert tv._dernier_storyboard() == (0.0, [])


from pathlib import Path as Path_  # noqa: E402


def test_commande_wan_cache_et_lanceur_du_studio(tmp_path, monkeypatch):
    from backend.core import sdgen
    from PIL import Image

    image = tmp_path / "i.png"
    Image.new("RGB", (1536, 864), (90, 120, 150)).save(image)

    class _Verrou:
        def close(self):
            pass

    class _Proc:
        returncode, stdout, stderr = 1, "", "arret"

    lances = []
    monkeypatch.setattr(tv, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(tv, "WAN_CACHE", 0.05)
    monkeypatch.setattr(tv, "_wan", lambda: {"modele": "m", "t5": "t", "vae": "v", "tae": None, "exe": "sd"})
    monkeypatch.setattr(sdgen, "_acquerir_verrou_gpu", lambda *a, **k: _Verrou())
    monkeypatch.setattr(sdgen, "_liberer_vram", lambda *a, **k: None)
    monkeypatch.setattr(sdgen, "_prechauffer_cache",
                        lambda chemins, force=False: lances.append(("lecture", chemins, force)))
    monkeypatch.setattr(sdgen, "_executer_sd", lambda cmd, delai: lances.append(("sd", cmd)) or _Proc())
    monkeypatch.setattr(tv, "_journal_wan", lambda *args: None)
    echec = tv._clip_wan(image, "x", 3.0, tmp_path / "c.mp4", 1)
    assert echec and "Wan 2.2 en echec" in echec
    # Fichiers RELUS juste avant le lancement (force), le modele en dernier :
    # lus plus tot, Windows les jetait du cache avant que sd-cli les charge.
    assert lances[0] == ("lecture", ["v", "t", "m"], True)
    cmd = lances[1][1]
    i = cmd.index("--cache-mode")
    assert cmd[i:i + 4] == ["--cache-mode", "easycache", "--cache-option", "threshold=0.05"]
    assert "--sage-attn" in cmd and cmd[cmd.index("--video-frames") + 1] == "73"   # 3 s a 24 i/s, 4k+1
    monkeypatch.setattr(tv, "WAN_CACHE", 0.0)
    lances.clear()
    tv._clip_wan(image, "x", 3.0, tmp_path / "c.mp4", 1)
    assert "--cache-mode" not in lances[1][1]


def test_journal_d_un_clip_wan(monkeypatch):
    from backend.core import sdgen

    ecrit = []
    monkeypatch.setattr(sdgen, "_journal_sd", lambda niveau, message, **champs: ecrit.append((niveau, message, champs)))
    sortie = "\n".join([
        "[I] auto-fit plan: --- backend_fit.cpp:323",
        "[I]     CUDA0  free  14987 MiB, budget  14475 MiB",
        '[I] auto-fit: --backend "diffusion=CUDA0,te=CUDA0,vae=CUDA0" --- backend_fit.cpp:477',
        "[I] sampling completed, taking 50.03s --- image.cpp:899",
    ])
    tv._journal_wan(0, sortie, 73, 92.5, 6.1)
    niveau, message, champs = ecrit[0]
    assert (niveau, message) == ("info", "clip wan")
    assert champs["images"] == 73 and champs["duree_s"] == 92.5 and champs["lecture_fichiers_s"] == 6.1
    assert champs["etapes"].get("sampling") == 50.0 and any("te=CUDA0" in l for l in champs["plan"])
    tv._journal_wan(-1, "delai depasse", 0, 1500.0, 0.0)
    assert ecrit[1][0] == "avertissement"
