# -*- coding: utf-8 -*-
"""Outils video de Neo : storyboard, clips, montage.

Chaine (100 % locale, un seul modele lourd en VRAM a la fois) :
1. storyboard : un LLM local decoupe l'histoire en plans (JSON force), avec
   des prompts d'image descriptifs adaptes a FLUX et un personnage fige ;
2. clip       : une image 4K par plan (sdgen), puis du VRAI mouvement par
   Wan 2.2 TI2V 5B (image -> video, 24 i/s) si le modele est installe ; a
   defaut, un mouvement de camera fluide (Ken Burns sur-echantillonne,
   courbe d'acceleration douce) choisi d'apres le videoPrompt ;
3. render     : montage avec fondus enchaines, ouverture/fermeture au noir,
   narration (ElevenLabs si cle, sinon voix locale SAPI) normalisee, encodage
   GPU NVENC (repli libx264).

Interface publique stable (API video :8321, registre d'outils, serveur Rust) :
VIDEO_DIR, tool_video_storyboard, tool_video_clip, tool_video_clip_all,
tool_video_render.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from backend.core.config import contexte_pour

from backend.tools.common import _default_root

# ---- Configuration ---------------------------------------------------------

try:
    from backend.core.config import ollama_url as _ollama_url

    OLLAMA_URL = _ollama_url()
except Exception:
    OLLAMA_URL = "http://127.0.0.1:11434"

# Sortie video : 1080p par defaut (images sources en 4K : le zoom reste net
# jusqu'a x2). NEO_VIDEO_RES=4k pour une sortie 3840x2160.
# 24 i/s : cadence native de Wan 2.2 et du cinema ; tous les plans (Wan ou
# Ken Burns) partagent la meme cadence, condition des fondus enchaines.
FPS = 24
SORTIE_4K = os.environ.get("NEO_VIDEO_RES", "").lower() in ("4k", "2160", "2160p", "uhd")
TAILLE = (3840, 2160) if SORTIE_4K else (1920, 1080)
FONDU_S = 0.6        # fondu enchaine entre deux plans
NOIR_S = 0.5         # ouverture / fermeture au noir

# Dossier de sortie des videos (reutilise neo_documents_dir si dispo).
try:
    from backend.core.config import settings as _s
    VIDEO_DIR = Path(_s.neo_documents_dir) / "video"
except Exception:
    VIDEO_DIR = _default_root() / "data" / "documents" / "video"

# Jamais d'exception a l'import : le registre des outils importe ce module.
try:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# Mots de mouvement de camera reconnus dans le videoPrompt (anglais/francais).
MOUVEMENTS = ("slow zoom in", "slow zoom out", "pan left", "pan right",
              "tilt up", "tilt down", "static")

# Wan 2.2 TI2V 5B (stable-diffusion.cpp, -M vid_gen) : reglages officiels du
# modele en 720p (1280x704, cfg 5, decalage 5), 121 images = 5 s a 24 i/s.
WAN_TAILLE = (1280, 704)
WAN_IMAGES_MAX = 121
WAN_ETAPES = int(os.environ.get("NEO_WAN_STEPS", "20"))
# Seuil EasyCache de Wan (voir `_clip_wan`) : 0 = sans cache, 0.2 = le plus
# rapide mais plus mou.
try:
    WAN_CACHE = max(0.0, float(os.environ.get("NEO_WAN_CACHE", "0.05")))
except ValueError:
    WAN_CACHE = 0.05
# Delai d'un clip Wan : sous les 1 800 s du pont Rust (image du plan comprise).
WAN_DELAI_S = float(os.environ.get("NEO_WAN_DELAI_S", "1500"))
# Image d'un plan anime par Wan : la base du studio (1536x864) suffit, Wan
# n'en lit que 1280x704. NEO_VIDEO_IMAGE_COMPLETE=1 revient a l'image 4K.
IMAGE_TRAVAIL = (1536, 864)
IMAGE_LEGERE = os.environ.get("NEO_VIDEO_IMAGE_COMPLETE", "0").strip() not in ("1", "oui", "true")
WAN_NEGATIF = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，"
               "低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，"
               "毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")


# ---- Processus et ffmpeg ---------------------------------------------------

def _sans_fenetre() -> int:
    """Drapeau Windows CREATE_NO_WINDOW : ffmpeg ne doit jamais ouvrir de console."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_cmd(cmd, timeout=300):
    """Execute une commande sans fenetre ; (code, sortie, duree)."""
    t0 = time.time()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout,
                             creationflags=_sans_fenetre())
        return res.returncode, res.stdout + res.stderr, round(time.time() - t0, 2)
    except FileNotFoundError as exc:
        return -1, f"executable introuvable : {cmd[0]} ({exc})", round(time.time() - t0, 2)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT", round(time.time() - t0, 2)


def _ffmpeg() -> str:
    """ffmpeg : NEO_FFMPEG, sinon le PATH, sinon l'installation C:\\ffmpeg."""
    return (os.environ.get("NEO_FFMPEG") or shutil.which("ffmpeg")
            or r"C:\ffmpeg\bin\ffmpeg.exe")


def _ffprobe() -> str:
    voisin = Path(_ffmpeg()).with_name("ffprobe.exe")
    return str(voisin) if voisin.is_file() else (shutil.which("ffprobe") or "ffprobe")


_ENCODEUR: list[str] | None = None


def _encodeur() -> list[str]:
    """Options d'encodage video : NVENC (GPU, qualite constante elevee) si la
    carte l'accepte reellement, sinon libx264 lent/qualite haute. Teste une
    fois par processus (un encodeur liste peut echouer a l'ouverture)."""
    global _ENCODEUR
    if _ENCODEUR is None:
        nvenc = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr",
                 "-cq", "17", "-b:v", "0", "-profile:v", "high"]
        rc, _, _ = _run_cmd([_ffmpeg(), "-hide_banner", "-loglevel", "error",
                             "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                             *nvenc, "-f", "null", "-"], timeout=30)
        _ENCODEUR = nvenc if rc == 0 else ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]
    return _ENCODEUR + ["-pix_fmt", "yuv420p"]


def _duree_media(chemin: Path) -> float:
    rc, out, _ = _run_cmd([_ffprobe(), "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", str(chemin)], timeout=30)
    try:
        return float(out.strip().splitlines()[0]) if rc == 0 else 0.0
    except (ValueError, IndexError):
        return 0.0


# ---- LLM ---------------------------------------------------------------------

def _ollama_generate(model: str, prompt: str, timeout: int = 300, json_mode: bool = False) -> str:
    """Appelle Ollama /api/generate (sans « reflexion » qwen3) et retourne le texte."""
    corps = {"model": model, "prompt": prompt, "stream": False, "think": False,
             "options": {"temperature": 0.6, "num_ctx": contexte_pour(model, 8192), "num_predict": 4096}}
    if json_mode:
        corps["format"] = "json"
    r = httpx.post(f"{OLLAMA_URL}/api/generate", json=corps, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "")


def _ollama_list() -> list[str]:
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _choix_modele_video() -> str:
    """Meilleur modele TEXTE generaliste installe (jamais un modele vision ni
    le modele de code fine-tune) : la qualite du decoupage et des prompts
    d'image en depend directement."""
    installes = _ollama_list()
    # gemma4:12b d'abord : c'est le modele de conversation, deja charge (5 s par
    # storyboard contre 11 s pour qwen3:14b, plus 35 s pour le charger), et sur
    # trois histoires il respecte toutes les regles la ou qwen3:14b oublie le
    # mouvement de camera de deux plans (essai du 11/11/2026).
    for m in ("gemma4:12b", "qwen3:14b", "gemma3:12b", "qwen3:8b", "qwen2.5:14b",
              "llama3.1:8b", "qwen2.5:7b", "gemma3:4b"):
        if m in installes:
            return m
    return "qwen3:8b"


# ---- Outil 1 : Storyboard --------------------------------------------------

def tool_video_storyboard(histoire: str, modele: str = "", total_duration: int | None = None) -> str:
    """Decoupe une histoire en storyboard JSON via un LLM local.

    Args:
        histoire: texte de l'histoire a adapter
        modele: modele Ollama (defaut : le meilleur modele texte installe)
        total_duration: duree totale visee en secondes (optionnel)

    Retourne un resume ; le storyboard est ecrit dans VIDEO_DIR.
    """
    histoire = (histoire or "").strip()
    if not histoire:
        return "Erreur: histoire vide"
    modele = modele or _choix_modele_video()

    duree_instruction = ""
    if total_duration and total_duration > 0:
        nb = max(3, min(12, round(total_duration / 5)))
        par_plan = max(3, min(5, round(total_duration / nb)))
        duree_instruction = (
            f"\nCONTRAINTE DE DUREE (PRIORITAIRE) : environ {total_duration} s au total, "
            f"exactement {nb} plans de {par_plan} s chacun.")

    prompt = f"""Tu es realisateur et storyboarder professionnel. Adapte EXACTEMENT l'histoire fournie en plans de cinema. N'invente pas une autre histoire : chaque plan illustre un moment de celle-ci, dans l'ordre.

Reponds UNIQUEMENT avec un objet JSON valide respectant ce schema :
{{
  "title": "titre court",
  "language": "fr",
  "characterDescription": "description physique et vestimentaire FIGEE du ou des personnages, en anglais, tres precise (age, visage, cheveux, vetements, couleurs)",
  "visualStyle": "style visuel commun a tous les plans, en anglais (eclairage, palette, grain, ambiance)",
  "shots": [
    {{
      "id": 1,
      "setting": "lieu et atmosphere, en francais",
      "imagePrompt": "description de l'image en anglais, 60 a 100 mots",
      "videoPrompt": "le MOUVEMENT du plan en anglais : ce qui bouge et comment (personnage, elements, lumiere), puis un mouvement de camera parmi : slow zoom in, slow zoom out, pan left, pan right, tilt up, tilt down, static",
      "dialogue": {{"character": "nom", "text": "replique ou narration en francais"}},
      "durationSeconds": 5
    }}
  ]
}}

Regles pour imagePrompt (essentiel, il pilote directement le generateur d'images) :
- phrases naturelles en anglais, jamais de liste de mots-cles ni d'etiquettes (8k, masterpiece...)
- sujet principal d'abord, bien visible et grand dans le cadre, puis decor, cadrage et angle, objectif, lumiere, matieres, couleurs
- si un personnage apparait, recopie MOT POUR MOT sa characterDescription dans le prompt (coherence d'un plan a l'autre)
- termine par le visualStyle commun
- varie les cadrages d'un plan a l'autre (plan large, plan moyen, gros plan)
Regles pour videoPrompt (le modele d'animation est petit : il reussit les mouvements simples et rate le reste) :
- une ou deux phrases en anglais, un SEUL mouvement simple, lent et physiquement plausible du sujet principal DEJA visible dans l'image (geste, pas, tete qui tourne, cheveux ou vetements dans le vent, vagues, feuillage)
- interdit : objet, animal ou personnage qui apparait ou traverse le champ, brume, fumee ou lumiere qui se deplacent, changement de decor ou d'eclairage, transformation
- termine par un mouvement de camera lent parmi : slow zoom in, slow zoom out, pan left, pan right, tilt up, tilt down, static
Autres regles : 3 a 10 plans, durationSeconds entre 3 et 5 (un plan anime dure au plus 5 s), une action claire par plan.{duree_instruction}

Histoire a adapter :
{histoire}"""

    try:
        reponse = _ollama_generate(modele, prompt, json_mode=True)
    except Exception as exc:
        return f"Erreur Ollama ({modele}) : {exc}"

    debut, fin = reponse.find("{"), reponse.rfind("}") + 1
    if debut == -1 or fin == 0:
        return f"Erreur: pas de JSON dans la reponse du modele.\nReponse brute : {reponse[:500]}"
    try:
        data = json.loads(reponse[debut:fin])
    except json.JSONDecodeError as exc:
        return f"Erreur: JSON invalide ({exc}).\nBrute : {reponse[debut:fin][:500]}"
    if not isinstance(data.get("shots"), list) or not data["shots"]:
        return "Erreur: storyboard sans plans valides."

    for i, sh in enumerate(data["shots"], start=1):
        sh["id"] = _id_plan(sh.get("id")) or i
        # Un plan anime par Wan dure au plus 5 s (121 images a 24 i/s).
        plafond = 5 if _wan() else 10
        try:
            sh["durationSeconds"] = max(3, min(plafond, round(float(sh.get("durationSeconds", 5)))))
        except (TypeError, ValueError):
            sh["durationSeconds"] = 5
    if total_duration and total_duration > 0:
        cible = max(3, min(5 if _wan() else 10, round(total_duration / len(data["shots"]))))
        for sh in data["shots"]:
            sh["durationSeconds"] = cible
    # Graine commune au projet : rendu coherent d'un plan a l'autre.
    data.setdefault("seed", int(time.time()) % 2_000_000_000)

    chemin = VIDEO_DIR / f"storyboard_{int(time.time())}.json"
    chemin.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    resume = [f"Storyboard généré : {chemin.name}", f"Modèle : {modele}",
              f"Titre : {data.get('title', 'Sans titre')}", f"Plans : {len(data['shots'])}",
              "", "Aperçu des plans :"]
    for shot in data["shots"]:
        resume.append(f"  Plan {shot.get('id')} : {str(shot.get('setting', ''))[:60]}... "
                      f"({shot.get('durationSeconds', 0)}s, {shot.get('videoPrompt', '')})")
    return "\n".join(resume)


# ---- Outil 2 : Clip video --------------------------------------------------

def _id_plan(valeur) -> int | None:
    """Identifiant de plan normalise en entier (1, "1", 1.0 -> 1)."""
    try:
        return int(float(valeur))
    except (TypeError, ValueError):
        return None


def _mouvement(video_prompt: str) -> str:
    """Mouvement de camera normalise d'apres le videoPrompt (phrase naturelle :
    les formes conjuguees « pulls back », « panning left »... sont reconnues)."""
    vp = (video_prompt or "").lower()
    regles = (
        ("slow zoom out", r"zoom(s|ing)? out|pull(s|ing)? back|dolly(ing)? out|zoom arri|recul"),
        ("pan left", r"pan(s|ning)? (to the )?left|panoramique (vers la )?gauche|vers la gauche"),
        ("pan right", r"pan(s|ning)? (to the )?right|panoramique (vers la )?droite|vers la droite|tracking"),
        ("tilt up", r"tilt(s|ing)? up|crane(s|ing)? up|vers le haut"),
        ("tilt down", r"tilt(s|ing)? down|crane(s|ing)? down|vers le bas"),
        ("static", r"\bstatic\b|\bfixe\b|locked[- ]off"),
    )
    for nom, motif in regles:
        if re.search(motif, vp):
            return nom
    return "slow zoom in"


def _filtre_mouvement(video_prompt: str, duree: float, fps: int = FPS,
                      taille: tuple[int, int] = TAILLE) -> str:
    """Ken Burns FLUIDE : l'image source est sur-echantillonnee (x2 de la
    sortie) avant zoompan, ce qui supprime les sauts d'un pixel entiers ; la
    progression suit une courbe en S (depart et arrivee en douceur) ; zoompan
    produit directement `fps` images/s (plus de 25 -> 30 i/s saccade).
    Entree = UNE image : zoompan d=n en tire les n images du plan, l'image
    n'est donc decodee et agrandie qu'une seule fois."""
    w, h = taille
    n = max(2, int(round(duree * fps)))
    # p : progression 0 -> 1 avec acceleration/deceleration douces.
    p = f"(0.5-0.5*cos(PI*min(on/{n - 1},1)))"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    mvt = _mouvement(video_prompt)
    if mvt == "slow zoom out":
        z, x, y = f"1.18-0.18*{p}", cx, cy
    elif mvt == "pan left":
        z, x, y = "1.15", f"(iw-iw/zoom)*(1-{p})", cy
    elif mvt == "pan right":
        z, x, y = "1.15", f"(iw-iw/zoom)*{p}", cy
    elif mvt == "tilt up":
        z, x, y = "1.15", cx, f"(ih-ih/zoom)*(1-{p})"
    elif mvt == "tilt down":
        z, x, y = "1.15", cx, f"(ih-ih/zoom)*{p}"
    elif mvt == "static":
        z, x, y = f"1+0.03*{p}", cx, cy
    else:  # slow zoom in
        z, x, y = f"1+0.18*{p}", cx, cy
    return (f"scale={2 * w}:{2 * h}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={fps},"
            f"format=yuv420p")


def _wan() -> dict | None:
    """Fichiers Wan 2.2 (dossier models_video du generateur), ou None.
    NEO_VIDEO_WAN=0 force le Ken Burns."""
    if os.environ.get("NEO_VIDEO_WAN", "1").strip() in ("0", "off", "non", "false"):
        return None
    try:
        from backend.core.sdgen import _dir, _exe
    except Exception:
        return None
    d = _dir() / "models_video"
    trouves = {}
    for cle, motif in (("modele", "Wan2.2-TI2V-5B*.gguf"), ("vae", "Wan2.2_VAE*.safetensors"),
                       ("t5", "umt5-xxl*.gguf")):
        f = sorted(d.glob(motif)) if d.is_dir() else []
        if not f:
            return None
        trouves[cle] = f[0]
    # Decodeur leger TAEHV (optionnel) : 121 images decodees en ~3 s contre
    # ~720 s pour le VAE complet decoupe en tuiles (qualite equivalente a l'oeil).
    tae = sorted(d.glob("taew2_2*.safetensors"))
    trouves["tae"] = tae[0] if tae else None
    trouves["exe"] = _exe()
    return trouves


def _prechauffer_wan():
    """Lit les fichiers de Wan en tache de fond, pendant que l'image du plan
    se calcule. Ils sont sur le disque d'archives (118 Mo/s) : a froid, leur
    lecture prend pres de deux minutes, autant qu'elle ne s'ajoute pas a
    l'attente. Chaque plan est un nouveau processus : relire des fichiers deja
    en cache coute quelques secondes, en parallele de l'image. Best-effort."""
    f = _wan()
    if not f:
        return None
    try:
        import threading

        from backend.core.sdgen import _prechauffer_cache

        fil = threading.Thread(
            target=_prechauffer_cache,
            args=([str(f[c]) for c in ("modele", "t5", "vae", "tae") if f.get(c)],),
            name="prechauffe-wan", daemon=True)
        fil.start()
        return fil
    except Exception:
        return None


def _recadrer(image: Path, taille: tuple[int, int], sortie: Path) -> None:
    """Recadrage « cover » (centre) puis redimensionnement Lanczos."""
    from PIL import Image

    w, h = taille
    with Image.open(image) as im:
        im = im.convert("RGB")
        ratio = w / h
        cw = min(im.width, int(im.height * ratio))
        ch = int(cw / ratio)
        x, y = (im.width - cw) // 2, (im.height - ch) // 2
        im.crop((x, y, x + cw, y + ch)).resize((w, h), Image.LANCZOS).save(sortie)


def _journal_wan(rc: int, sortie_sd: str, images: int, duree_s: float, lecture_s: float) -> None:
    """Trace d'un clip Wan dans le journal : etapes internes de sd-cli, plan de
    placement (carte, memoire vive) et priorite du processus.

    En production un clip prend encore ~60 s de plus que depuis une console
    (170 s contre 110 s le 11/11/2026), dans une phase ou la carte ne fait
    rien ; la sortie de sd-cli etait perdue, donc la cause invisible."""
    try:
        from backend.core import priorite
        from backend.core.sdgen import _etapes_sd, _journal_sd

        plan = [l.strip()[:170] for l in sortie_sd.splitlines() if "auto-fit" in l or "backend" in l.lower()][:8]
        _journal_sd("info" if rc == 0 else "avertissement", "clip wan", code=rc, images=images,
                    duree_s=duree_s, lecture_fichiers_s=lecture_s, etapes=_etapes_sd(sortie_sd),
                    plan=plan, priorite=priorite.etat())
    except Exception:
        pass


def _clip_wan(image: Path, prompt: str, duree: float, sortie: Path, graine: int) -> str | None:
    """Anime l'image du plan avec Wan 2.2 (vrai mouvement). Retourne None si
    reussi, sinon le motif de l'echec (l'appelant replie sur le Ken Burns)."""
    f = _wan()
    if not f:
        return "Wan 2.2 non installe"
    import tempfile

    from backend.core.sdgen import (_acquerir_verrou_gpu, _executer_sd, _liberer_vram,
                                    _prechauffer_cache)

    n = min(WAN_IMAGES_MAX, 4 * max(1, round(duree * FPS / 4)) + 1)  # 4k+1 images
    with tempfile.TemporaryDirectory(prefix="wan_", dir=str(VIDEO_DIR)) as tmp:
        tmp = Path(tmp)
        init = tmp / "init.png"
        _recadrer(image, WAN_TAILLE, init)
        cmd = [str(f["exe"]), "-M", "vid_gen", "--diffusion-model", str(f["modele"]),
               "--vae", str(f["vae"]), "--t5xxl", str(f["t5"]), "-p", prompt, "-n", WAN_NEGATIF,
               "--cfg-scale", "5.0", "--sampling-method", "euler", "--steps", str(WAN_ETAPES),
               "-W", str(WAN_TAILLE[0]), "-H", str(WAN_TAILLE[1]), "--video-frames", str(n),
               "--flow-shift", "5.0", "-i", str(init),
               # SageAttention : echantillonnage 402 s -> ~160 s (meme rendu) ;
               # offload : la VRAM reste libre pour le decodage.
               "--sage-attn", "--offload-to-cpu",
               "-s", str(graine), "-o", str(tmp / "f_%03d.png")]
        # EasyCache : les etapes d'echantillonnage qui changent peu sont
        # reprises de la precedente. Seuil 0,05 : 6 etapes sur 20 sautees,
        # 114 s -> 86 s pour 3 s de video, image quasi identique (ecart 2/255,
        # nettete -3 a -5 %). Le seuil par defaut de sd-cli (0,2 : 10 etapes
        # sautees, 68 s) ramollit visiblement les textures.
        if WAN_CACHE > 0:
            cmd += ["--cache-mode", "easycache", "--cache-option", f"threshold={WAN_CACHE:g}"]
        cmd += (["--tae", str(f["tae"])] if f["tae"] else
                ["--vae-tiling", "--temporal-tiling", "--vae-tile-size", "256x256",
                 "--extra-tiling-args", "temporal_tile_frames=4,temporal_decode_frames=2"])
        verrou = _acquerir_verrou_gpu()  # une seule generation GPU a la fois
        if verrou is None:
            # Lancer Wan a cote d'une autre generation saturerait la carte.
            return "carte graphique occupee par une autre generation depuis plus d'une heure"
        debut_wan = time.time()
        lecture_s = 0.0
        try:
            _liberer_vram()
            # Fichiers de Wan (12,8 Go sur le disque d'archives) : TOUJOURS
            # relus ici, juste avant le lancement, meme s'ils viennent de
            # l'etre pendant l'image du plan (`_prechauffer_wan`). Mesure du
            # 11/11/2026 : lus trop tot, ils etaient les plus anciens du cache ;
            # l'image du plan puis les 11 Go que sd-cli reserve d'un coup en
            # faisaient jeter 5 Go, que sd-cli relisait sur le disque dur a
            # 42 Mo/s — 120 s sur les 210 du plan. Relus ici (4 s s'ils sont
            # la, 118 Mo/s en lecture suivie sinon), ils sont les plus recents
            # et ce sont les fichiers de l'image, devenus inutiles, qui cedent.
            _prechauffer_cache([str(f[c]) for c in ("tae", "vae", "t5", "modele") if f.get(c)], force=True)
            lecture_s = round(time.time() - debut_wan, 1)
            # Meme lanceur que le studio : sd-cli est tue avec ce processus
            # (plus d'orphelin gardant la carte), et le delai reste sous celui
            # du pont Rust (1 800 s), qui sinon tuait Python en laissant Wan.
            try:
                proc = _executer_sd(cmd, WAN_DELAI_S)
                rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                rc, out = -1, f"delai de {int(WAN_DELAI_S)} s depasse (Wan arrete)"
        finally:
            verrou.close()
        images = sorted(tmp.glob("f_*.png"))
        _journal_wan(rc, out, len(images), round(time.time() - debut_wan, 1), lecture_s)
        if rc != 0 or len(images) < 2:
            return f"Wan 2.2 en echec (code {rc}) : {out[-300:]}"
        w, h = TAILLE
        # 1280x704 -> sortie : recadrage cover + Lanczos, legere accentuation.
        filtre = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                  f"crop={w}:{h},unsharp=5:5:0.35:5:5:0,format=yuv420p")
        cmd2 = [_ffmpeg(), "-hide_banner", "-y", "-framerate", str(FPS),
                "-start_number", images[0].stem.split("_")[-1], "-i", str(tmp / "f_%03d.png"),
                "-vf", filtre, "-r", str(FPS), *_encodeur(), "-movflags", "+faststart", str(sortie)]
        rc2, out2, _ = _run_cmd(cmd2, timeout=600)
        if rc2 != 0:
            return f"encodage du clip Wan en echec : {out2[-300:]}"
    return None


def _prompt_plan(data: dict, shot: dict) -> str:
    """Prompt d'image d'un plan : personnage fige et style commun garantis
    meme si le LLM les a oublies dans imagePrompt."""
    p = str(shot.get("imagePrompt") or shot.get("setting") or "").strip()
    perso = str(data.get("characterDescription") or "").strip()
    style = str(data.get("visualStyle") or "").strip()
    if perso and perso[:40].lower() not in p.lower() and len(perso) < 500:
        p = f"{p} {perso}"
    if style and style[:30].lower() not in p.lower():
        p = f"{p.rstrip('. ')}. {style}"
    return p


def tool_video_clip(storyboard_path: str, shot_id: int = 1) -> str:
    """Genere le clip d'un plan : image 4K (sdgen) puis mouvement de camera.

    Args:
        storyboard_path: chemin du fichier JSON du storyboard
        shot_id: identifiant du plan a rendre

    Retourne le chemin du clip MP4.
    """
    p = Path(storyboard_path)
    if not p.exists():
        return f"Erreur: fichier introuvable : {storyboard_path}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Erreur lecture JSON : {exc}"

    shot_id = _id_plan(shot_id)
    shot = next((s for s in data.get("shots", []) if _id_plan(s.get("id")) == shot_id), None)
    if not shot or shot_id is None:
        return f"Erreur: plan {shot_id} non trouvé dans le storyboard."
    try:
        duree = max(1.0, min(30.0, float(shot.get("durationSeconds", 5))))
    except (TypeError, ValueError):
        duree = 5.0

    # 1. Image du plan, directement par sdgen (meme processus : pas d'aller-
    #    retour HTTP ni de delai d'attente arbitraire ; le verrou GPU de sdgen
    #    serialise les generations concurrentes).
    from backend.core.sdgen import generer

    horodatage = int(time.time())
    chemin_image = VIDEO_DIR / f"shot{shot_id}_{horodatage}.png"
    graine = (int(data.get("seed", 0)) + shot_id) % 2_000_000_000
    prompt_image = _prompt_plan(data, shot)

    def _image(travail: bool) -> Path | str:
        """Image du plan. `travail` : la base 1536x864 seule, sans passe
        2 560 px, agrandissement 4K, passes visage ni finition : Wan n'en lit
        que 1280x704. Sinon l'image 4K complete (zoom du Ken Burns)."""
        if travail:
            msg = generer(prompt_image, str(chemin_image), graine=graine, traduire=False,
                          largeur=IMAGE_TRAVAIL[0], hauteur=IMAGE_TRAVAIL[1],
                          resolution="hd", brut=True)
        else:
            msg = generer(prompt_image, str(chemin_image), graine=graine, traduire=False)
        m = re.match(r"Image generee: (.+?) \(\d+ Ko\)", msg)
        if not m or not Path(m.group(1)).is_file():
            return f"Erreur: image du plan {shot_id} impossible : {msg[:400]}"
        return Path(m.group(1))

    # Wan disponible : image de travail, et ses fichiers lus pendant ce temps.
    avec_wan = _wan() is not None
    if avec_wan:
        _prechauffer_wan()
    image = _image(travail=avec_wan and IMAGE_LEGERE)
    if isinstance(image, str):
        return image
    chemin_image = image

    # 2. Mouvement : Wan 2.2 (vrai mouvement) si installe, sinon Ken Burns.
    chemin_sortie = VIDEO_DIR / f"shot{shot_id}_{horodatage}.mp4"
    video_prompt = str(shot.get("videoPrompt", ""))
    prompt_wan = f"{str(shot.get('imagePrompt', '')).split('. ')[0]}. {video_prompt}".strip()
    echec_wan = _clip_wan(chemin_image, prompt_wan, duree, chemin_sortie, graine)
    methode = "Wan 2.2 (mouvement réel)"
    if echec_wan:
        methode = f"Ken Burns ({echec_wan[:120]})"
        if avec_wan and IMAGE_LEGERE:
            # Le zoom du Ken Burns demande l'image 4K complete (meme graine).
            image = _image(travail=False)
            if isinstance(image, str):
                return image
            chemin_image = image
        cmd = [_ffmpeg(), "-hide_banner", "-y", "-i", str(chemin_image),
               "-vf", _filtre_mouvement(video_prompt, duree),
               "-frames:v", str(max(2, int(round(duree * FPS)))), "-r", str(FPS), *_encodeur(),
               "-movflags", "+faststart", str(chemin_sortie)]
        rc, out, _ = _run_cmd(cmd, timeout=600)
        if rc != 0:
            return f"Erreur FFmpeg : {out[-500:]}"

    # La photo intermediaire ne sert plus, seul le clip est garde.
    for f in (chemin_image, chemin_image.with_name(chemin_image.stem + "_base.png"),
              chemin_image.with_name(chemin_image.stem + "_ecran.jpg")):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
    return f"Clip généré : {chemin_sortie} [{methode}]"


def tool_video_clip_all(storyboard_path: str) -> str:
    """Genere tous les clips d'un storyboard (sequentiel)."""
    p = Path(storyboard_path)
    if not p.exists():
        return f"Erreur: fichier introuvable : {storyboard_path}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Erreur lecture JSON : {exc}"
    shots = data.get("shots", [])
    if not shots:
        return "Erreur: storyboard sans plans valides."
    return "\n".join(f"Plan {s.get('id')}: {tool_video_clip(str(p), s.get('id', 0))}"
                     for s in shots)


# ---- Outil 3 : Montage -----------------------------------------------------

def _dernier_storyboard() -> tuple[float, list[int]]:
    """(horodatage, numeros des plans) du storyboard le plus recent ; (0, [])
    s'il n'y en a pas. L'horodatage est celui du nom du fichier."""
    try:
        fichiers = sorted(VIDEO_DIR.glob("storyboard_*.json"), key=lambda p: p.stat().st_mtime)
        if not fichiers:
            return 0.0, []
        dernier = fichiers[-1]
        m = re.search(r"storyboard_(\d+)", dernier.name)
        quand = float(m.group(1)) if m else dernier.stat().st_mtime
        plans = json.loads(dernier.read_text(encoding="utf-8")).get("shots", [])
        ids = [i for i in (_id_plan(p.get("id")) for p in plans if isinstance(p, dict)) if i is not None]
        return quand, ids
    except Exception:
        return 0.0, []


def _nettoyer_intermediaires(clips: list[Path], fichiers: list[Path], garder: Path,
                             since: float = 0.0) -> int:
    """Supprime clips montes et fichiers de travail, plus les images de plans
    residuelles DU PROJET (creees depuis `since`). Jamais le fichier final."""
    supprimes = 0
    garde = garder.resolve()
    for p in list(clips) + list(fichiers):
        try:
            if p.exists() and p.resolve() != garde:
                p.unlink()
                supprimes += 1
        except OSError:
            pass
    if not since or since <= 0:
        return supprimes
    for motif in ("shot*.png", "shot*_apercu.jpg", "placeholder_*.png"):
        for p in VIDEO_DIR.glob(motif):
            try:
                if p.stat().st_mtime >= since and p.resolve() != garde:
                    p.unlink()
                    supprimes += 1
            except OSError:
                pass
    return supprimes


def _filtre_montage(durees: list[float]) -> tuple[str, float]:
    """Graphe ffmpeg : fondus enchaines entre plans + ouverture/fermeture au
    noir. Retourne (filter_complex, duree totale)."""
    if len(durees) == 1:
        total = durees[0]
        return (f"[0:v]fade=t=in:st=0:d={NOIR_S},"
                f"fade=t=out:st={max(0.0, total - NOIR_S):.3f}:d={NOIR_S}[v]", total)
    parties, precedent, decalage = [], "[0:v]", 0.0
    for i in range(1, len(durees)):
        f = min(FONDU_S, durees[i - 1] / 3, durees[i] / 3)
        decalage += durees[i - 1] - f
        sortie = f"[x{i}]"
        parties.append(f"{precedent}[{i}:v]xfade=transition=fade:duration={f:.3f}:"
                       f"offset={decalage:.3f}{sortie}")
        precedent = sortie
    total = decalage + durees[-1]
    parties.append(f"{precedent}fade=t=in:st=0:d={NOIR_S},"
                   f"fade=t=out:st={max(0.0, total - NOIR_S):.3f}:d={NOIR_S},format=yuv420p[v]")
    return ";".join(parties), total


def tool_video_render(project_name: str, audio_texte: str = "", total_duration: int | None = None,
                      since: float = 0.0) -> str:
    """Montage final : fondus enchaines, narration normalisee, encodage GPU.

    Args:
        project_name: nom du projet (nom du fichier final)
        audio_texte: narration (ElevenLabs si cle configuree, sinon voix locale)
        total_duration: conserve pour compatibilite (les durees sont deja dans les clips)
        since: horodatage (s) du storyboard : seuls les clips posterieurs sont
            montes (evite de melanger plusieurs projets). 0 = pas de filtre.

    Retourne le chemin de la video finale.
    """
    nom = re.sub(r"[^\w-]", "_", project_name or "video").strip("_") or "video"
    tous = sorted(VIDEO_DIR.glob("shot*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Sans horodatage (appel par l'agent du chat) : celui du dernier storyboard,
    # sinon des clips d'un projet precedent, de meme numero, se melaient au montage.
    attendus: list[int] = []
    if not since or since <= 0:
        since, attendus = _dernier_storyboard()
    if since and since > 0:
        tous = [p for p in tous if p.stat().st_mtime >= since]
    if not tous:
        return f"Erreur: aucun clip (shot*.mp4) trouvé dans {VIDEO_DIR}" + (
            " (après le storyboard demandé)" if since and since > 0 else "")

    def numero(p: Path) -> int:
        m = re.search(r"shot(\d+)", p.name)
        return int(m.group(1)) if m else 999

    plus_recent: dict[int, Path] = {}
    for p in tous:  # du plus recent au plus ancien : on garde le dernier rendu
        plus_recent.setdefault(numero(p), p)
    clips = sorted(plus_recent.values(), key=numero)
    ecartes = len(clips) - 12 if len(clips) > 12 else 0
    clips = clips[:12]
    manquants = [i for i in attendus if i not in plus_recent]
    durees = [_duree_media(c) or 5.0 for c in clips]

    filtre, total = _filtre_montage(durees)
    final = VIDEO_DIR / f"{nom}_final.mp4"
    cmd = [_ffmpeg(), "-hide_banner", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    travail: list[Path] = []
    chemin_audio = _generer_audio(audio_texte, nom) if audio_texte else None
    if chemin_audio:
        travail.append(Path(chemin_audio))
        cmd += ["-i", chemin_audio]
        # Narration : volume normalise (EBU R128), prolongee par du silence ;
        # la video fixe la duree.
        filtre += (f";[{len(clips)}:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad,"
                   f"afade=t=out:st={max(0.0, total - NOIR_S):.3f}:d={NOIR_S}[a]")
        cmd += ["-filter_complex", filtre, "-map", "[v]", "-map", "[a]",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}"]
    else:
        cmd += ["-filter_complex", filtre, "-map", "[v]"]
    cmd += [*_encodeur(), "-r", str(FPS), "-movflags", "+faststart", str(final)]

    rc, out, _ = _run_cmd(cmd, timeout=1800)
    if rc != 0 or not final.exists() or final.stat().st_size == 0:
        return f"Erreur FFmpeg : {out[-600:]}"
    n = _nettoyer_intermediaires(clips, travail, final, since)
    note = "" if (chemin_audio or not audio_texte) else " (narration demandée mais indisponible)"
    # Un plan en echec ou ecarte ne disparait plus en silence.
    if manquants:
        note += f" | ATTENTION : plan(s) {', '.join(map(str, manquants))} absent(s) du montage (clip non généré)"
    if ecartes:
        note += f" | ATTENTION : {ecartes} plan(s) au-delà du 12e non monté(s)"
    return (f"Vidéo finale : {final} ({total:.1f} s, {len(clips)} plans, fondus enchaînés)"
            f"{note} | {n} fichier(s) intermédiaire(s) supprimé(s)")


def _generer_audio(texte: str, project_name: str) -> str | None:
    """Narration : ElevenLabs si une cle est configuree, sinon voix locale
    (SAPI, francaise si disponible). None si aucune voix n'est disponible."""
    texte = (texte or "").strip()
    if not texte:
        return None
    try:
        from backend.core.api_keys import get_api_key
        cle = get_api_key("elevenlabs")
    except Exception:
        cle = None
    if cle:
        try:
            from backend.tools.t_tts import voice_id_elevenlabs

            r = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id_elevenlabs('Rachel')}",
                headers={"xi-api-key": cle, "Content-Type": "application/json"},
                json={"text": texte, "model_id": "eleven_multilingual_v2",
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                timeout=120)
            if r.status_code == 200:
                chemin = VIDEO_DIR / f"{project_name}_narration.mp3"
                chemin.write_bytes(r.content)
                return str(chemin)
        except Exception:
            pass
    try:
        from backend.tts.engine import synthesize_to_wav, voix_feminine_fr

        chemin = VIDEO_DIR / f"{project_name}_narration.wav"
        synthesize_to_wav(texte, chemin, voice_id=voix_feminine_fr(), rate=170)
        return str(chemin) if chemin.is_file() and chemin.stat().st_size > 0 else None
    except Exception:
        return None
