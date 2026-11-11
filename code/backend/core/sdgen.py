"""Generation d'images locale via stable-diffusion.cpp.

Meme philosophie que TripoSR : binaire et modeles isoles dans un dossier
outils (defaut <racine>\\tools\\sd, surcharge par LOCALAI_SD_DIR), aucune
dependance python lourde dans l'application principale.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from backend.core import priorite as _priorite
from backend.core.security import extension_autorisee_ecriture as _extension_bloquee
from backend.core.security import verifier_acces as _verifier_acces
from backend.core import reglages

# Lance par une tache planifiee, ce processus herite d'une priorite basse
# (disque et memoire compris) : sd-cli chargeait ses poids 100 fois plus
# lentement. On revient a la normale avant de lancer quoi que ce soit.
_priorite.normale()

# Negatif par defaut (anglais : SD comprend mieux). Termes anti-« dessin »
# pour forcer le photorealisme (les modeles rapides donnent sinon un rendu
# enfantin/plastique).
NEGATIF_QUALITE = (
    "lowres, blurry, out of focus, watermark, signature, text, logo, "
    "jpeg artifacts, worst quality, low quality, deformed, disfigured, "
    "bad anatomy, bad hands, extra fingers, extra limbs, duplicate, cropped"
)
DEFAULT_NEGATIF = (
    NEGATIF_QUALITE + ", "
    "drawing, painting, sketch, cartoon, anime, illustration, comic, "
    "3d render, cgi, plastic, toy, kid drawing, childish, amateur"
)

# Suffixe de qualite ajoute au prompt (anglais) — vise un rendu haut de gamme.
SUFFIXE_QUALITE = ("masterpiece, best quality, ultra detailed, sharp focus, "
                   "vivid colors, professional, trending on artstation")

# Suffixe REALISTE : vise le photorealisme 2D (defaut d'une demande d'image).
SUFFIXE_REALISME = ("masterpiece, best quality, ultra detailed, photorealistic, "
                    "realistic photograph, cinematic lighting, hdr, 8k, "
                    "sharp focus, fine detail")
# FLUX lit le prompt avec T5 : il comprend des PHRASES, pas des listes de
# mots-cles. Les etiquettes heritees de SD 1.5 (« masterpiece, 8k, hdr,
# trending on artstation ») ne lui apportent rien et poussent vers un rendu
# « HDR artificiel ». Suffixe court et naturel, ajoute seulement s'il manque.
SUFFIXE_FLUX_PHOTO = ("Professional photograph with natural light, ultra sharp "
                      "focus and highly detailed, realistic textures.")
# Mots indiquant un STYLE graphique -> on n'impose pas le photorealisme.
STYLE_MOTS = ("cartoon", "manga", "anime", "dessin", "croquis", "aquarelle",
              "peinture", "pixel", "logo", "comics", "b d ", "stylise",
              "stylisé", "vectoriel", "abstrait", "cel-shad", "render 3d",
              "low poly", "affiche")


def _cfg(settings=None):
    """Retourne l'objet settings : celui fourni, sinon les settings de l'app.

    Tolerer `None` permet aux appelants historiques (pont Rust, outils Python)
    de continuer a appeler les fonctions sans argument.
    """
    if settings is not None:
        return settings
    try:
        from backend.core.config import settings as _s

        return _s
    except Exception:
        return None


def _dir() -> Path:
    """Dossier des OUTILS SD : binaire sd-cli.exe et modeles .gguf.

    Ne pas confondre avec `_dossier_sortie()` qui, lui, designe l'endroit ou
    les images generees sont ecrites.
    """
    return reglages.chemin("LOCALAI_SD_DIR", "tools", "sd")


def _dossier_sortie(settings=None) -> Path | None:
    """Dossier de sortie des images generees (reglage neo_documents_dir).

    Retourne None si le reglage n'est pas defini, pour laisser l'appelant
    decider (comportement historique : dossier `data/`).
    """
    cfg = _cfg(settings)
    valeur = getattr(cfg, "neo_documents_dir", None) if cfg is not None else None
    if not valeur:
        return None
    try:
        d = Path(valeur)
    except Exception:
        return None
    return d


def _exe() -> Path:
    d = _dir()
    candidats = list((d / "bin").glob("sd*.exe")) + list(d.glob("sd*.exe"))
    return candidats[0] if candidats else d / "bin" / "sd.exe"


_SAGE: dict[str, bool] = {}


def _attention() -> list[str]:
    """Options d'attention de sd-cli pour les passes de detail FLUX.2-klein
    (visage, yeux), dont seul le grain fin est garde a la fusion.

    `--sage-attn` (SageAttention, calcul d'attention accelere sur la carte)
    s'ajoute a l'attention « flash » quand le binaire le propose : passe visage
    12,6 s -> 9,9 s, passe yeux 10,3 s -> 8 s, image finale quasi identique
    (ecart moyen 0,8/255, nettete du visage -3 a -5 %, invisible a 100 %).

    PAS pour le generateur Z-Image : memes essais du 11/11/2026, 5,6 s gagnees
    mais un visage nettement plus lisse sur 2 portraits sur 2 (variance du
    laplacien -30 a -45 %, grain de peau et texture des levres perdus).
    Pas non plus pour la retouche demandee par l'utilisateur (non mesuree).
    NEO_SD_SAGE=0 coupe."""
    options = ["--diffusion-fa"]
    if os.environ.get("NEO_SD_SAGE", "1") == "0":
        return options
    exe = _exe()
    cle = str(exe)
    if cle not in _SAGE:
        try:
            aide = subprocess.run([cle, "--help"], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _SAGE[cle] = "--sage-attn" in (aide.stdout or "") + (aide.stderr or "")
        except Exception:
            _SAGE[cle] = False
    return options + ["--sage-attn"] if _SAGE[cle] else options


def _vram_go() -> float:
    """VRAM totale du GPU (Go), 0 si inconnue. Mise en cache (nvidia-smi ~0,2 s)."""
    global _VRAM_CACHE
    if _VRAM_CACHE is None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _VRAM_CACHE = float(out.stdout.split()[0]) / 1024.0
        except Exception:
            _VRAM_CACHE = 0.0
    return _VRAM_CACHE


_VRAM_CACHE: float | None = None


def _vram_utilisee_mio() -> float | None:
    """Memoire graphique occupee a l'instant (Mio), None si inconnue."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float(out.stdout.split()[0])
    except Exception:
        return None


def _attendre_vram_rendue(avant_mio: float | None, attente_max_s: float = 25.0) -> float:
    """Attend que la carte ait REELLEMENT rendu la memoire des modeles decharges.

    Ollama retire un modele de `/api/ps` tout de suite, mais son processus met
    encore plusieurs secondes a rendre ses ~9 Go (6 Go toujours occupes 4 s
    apres, mesure du 11/11/2026). sd-cli, lance dans cet intervalle, croit la
    carte pleine, range ses poids en memoire vive et devient 10 fois plus lent
    (chargement 218 s, echantillonnage 207 s, deux essais expires : une image
    en 27 minutes). On attend donc que l'occupation ait nettement baisse puis
    se stabilise. Retourne le temps attendu ; ne bloque jamais au-dela du
    delai (la carte peut etre prise par autre chose)."""
    if os.environ.get("NEO_SD_ATTENTE_VRAM", "1") == "0":
        return 0.0
    if avant_mio is None:
        time.sleep(3.0)  # pas de mesure possible : marge fixe
        return 3.0
    t0 = time.time()
    precedent = avant_mio
    while time.time() - t0 < attente_max_s:
        time.sleep(0.5)
        u = _vram_utilisee_mio()
        if u is None:
            break
        baisse = avant_mio - u
        stable = abs(precedent - u) < 150
        # Rendu : au moins 1 Go de moins qu'avant le dechargement, et stable.
        if baisse >= 1024 and stable:
            break
        # Rien a rendre (modele sur le processeur) : stable depuis 3 s.
        if stable and baisse < 1024 and time.time() - t0 >= 3.0:
            break
        precedent = u
    return round(time.time() - t0, 1)


# Au-dela, le FLUX Q8 (12 Go) tient en VRAM avec ses activations : il ne coute
# que ~2 s de plus que le Q4 par image (mesure RTX 5080 : 15,5 s contre 13 s
# en 1536x864) pour un rendu nettement plus fin. En dessous (carte 10 Go), le
# Q8 deborde en RAM et devient 18x plus lent : on garde le Q4.
VRAM_MIN_Q8_GO = 14.0
# Guidance distillee des modeles FLUX dev / Krea dev (choisie par essais).
GUIDANCE_DEV = float(os.environ.get("LOCALAI_SD_GUIDANCE", "3.5"))


def _dossiers_modeles() -> list[Path]:
    """Dossiers de modeles, le plus RAPIDE d'abord.

    Stockage hybride : le dossier principal (`<sd>/models`, jonction vers le
    disque d'archives I: pour menager le SSD) garde tous les modeles ; le jeu
    « chaud » relu a chaque image (FLUX par defaut + encodeurs, ~19 Go) vit
    sur le SSD dans `<tools>/sd_ssd/models` (LOCALAI_SD_RAPIDE). Mesure :
    118 Mo/s a froid sur I: contre ~500 Mo/s sur le SSD, soit ~108 s contre
    ~25 s pour charger un FLUX Q8 de 12,7 Go."""
    rapide = Path(os.environ.get("LOCALAI_SD_RAPIDE", str(_dir().parent / "sd_ssd" / "models")))
    return [d for d in (rapide, _dir() / "models") if d.is_dir()]


def _fichiers_modeles() -> list[Path]:
    """Tous les fichiers de modeles ; a nom egal, la copie du SSD l'emporte."""
    vus: dict[str, Path] = {}
    for d in _dossiers_modeles():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in (".gguf", ".safetensors"):
                vus.setdefault(f.name.lower(), f)
    return list(vus.values())


def _fichier_modele(nom: str) -> Path | None:
    """Fichier de modele par son nom (SSD d'abord), ou None."""
    for d in _dossiers_modeles():
        if (d / nom).is_file():
            return d / nom
    return None


def _est_zimage(m: Path | None) -> bool:
    n = m.name.lower() if m is not None else ""
    return "z_image" in n or "z-image" in n or "zimage" in n


def _encodeur_zimage() -> Path | None:
    """Encodeur de texte de Z-Image (Qwen3-4B en GGUF), ou None."""
    return next((f for f in _fichiers_modeles() if f.name.lower().startswith("qwen3-4b")), None)


def _modele_secondaire(principal: Path) -> str:
    """Second modele pour une seconde prise : FLUX schnell si le principal est
    Z-Image, Z-Image si le principal est FLUX. Deux modeles interpretent un
    meme brief differemment (Z-Image dessinait une planete a anneaux pour
    « trou noir », FLUX non). Nom de fichier, ou "" s'il n'y en a pas."""
    tous = _fichiers_modeles()
    if _est_zimage(principal):
        schnell = sorted((f for f in tous if f.name.lower().startswith("flux1-schnell")),
                         key=lambda f: ("q8" not in f.name.lower(), f.name))
        return schnell[0].name if schnell else ""
    if "flux" in principal.name.lower():
        z = next((f for f in tous if _est_zimage(f)), None)
        return z.name if z is not None and _encodeur_zimage() else ""
    return ""


def _modele() -> Path:
    # Surcharge explicite (ex. LOCALAI_SD_MODEL=flux1-schnell-Q8_0.gguf).
    force = os.environ.get("LOCALAI_SD_MODEL", "").strip()
    if force and _fichier_modele(force):
        return _fichier_modele(force)
    d = _dir() / "models"
    tous = _fichiers_modeles()
    noms = {f.name.lower() for f in tous}
    modeles = sorted(tous, key=lambda f: f.name.lower())

    def _rang(m: Path) -> int:
        n = m.name.lower()
        if n.startswith(("t5xxl", "clip_l", "clip_g", "ae.", "vae", "qwen")):
            return 99  # encodeurs/VAE seuls (Qwen3-4B : encodeur de Z-Image)
        if _est_zimage(m):
            # Z-Image-Turbo d'abord (comparatif du 09/11/2026 sur 5 demandes
            # reelles : rendu photographique nettement superieur a FLUX
            # schnell, 29 s au lieu de 45) ; utilisable seulement avec son
            # encodeur Qwen3-4B et le VAE ae.
            return -1 if (_encodeur_zimage() and any(x.startswith("ae.") for x in noms)) else 99
        if any(x in n for x in ("wan", "umt5", "vae", "ltx")):
            return 99  # modeles VIDEO et leurs encodeurs (models_video/)
        if "kontext" in n:
            # Exige une image de reference -> jamais choisi par defaut
            # (selectionne explicitement par generer(..., reference=...)).
            return 99
        if "flux" in n:
            # utilisable seulement si le t5xxl est present.
            t5 = any(x.startswith("t5xxl") for x in noms)
            if not t5:
                return 99
            leger = "q4" in n or "q5" in n
            # schnell d'abord. Les FLUX dev (dont Krea dev, essaye le 08/11) ne
            # sont choisis que si aucun schnell n'est installe ou sur demande
            # (LOCALAI_SD_MODEL) : dans stable-diffusion.cpp, Krea dev Q8
            # produisait un motif de « toile tissee » sur la peau et les
            # degrades, quels que soient guidance et attention.
            famille = 0 if "schnell" in n else 1
            if _vram_go() >= VRAM_MIN_Q8_GO:
                return famille * 2 + (1 if leger else 0)
            # Petite carte : schnell leger d'abord (un dev Q8 deborderait en RAM).
            return (0 if leger else 1) + (0 if "schnell" in n else 2)
        if "realvis" in n or "juggernaut" in n or "realistic" in n:
            # SDXL UNet seul : exige clip_l ET clip_g ET vae separees.
            complet = (any("clip_l" in x for x in noms)
                       and any("clip_g" in x for x in noms)
                       and any("vae" in x or x.startswith("ae.") for x in noms))
            return 2 if complet else 99
        if "xl" in n:
            return 3
        return 20 if ("turbo" in n or "lcm" in n or "lightning" in n) else 10

    modeles = [m for m in modeles if _rang(m) < 90]
    if not modeles:
        return d / "_absent.gguf"
    return sorted(modeles, key=_rang)[0]


def _type_modele(m: Path | None = None) -> str:
    """Classe le modele SD : 'flux', 'turbo', 'sdxl' ou 'sd15'."""
    m = m or _modele()
    n = m.name.lower()
    # Z-Image est un DiT a flux comme FLUX : meme chaine (cfg 1, passe haute
    # resolution a sigmas imposes, brief en phrases). Teste AVANT « turbo »,
    # que son nom contient.
    if "flux" in n or _est_zimage(m):
        return "flux"
    if "turbo" in n or "lcm" in n or "lightning" in n:
        return "turbo"
    if "xl" in n or "realvis" in n or "juggernaut" in n or "realistic" in n:
        return "sdxl"
    return "sd15"


def _steps_defaut(m: Path | None = None) -> int:
    """Nombre de steps adapte au modele (flux schnell=4, dev=28 ; sdxl=28…)."""
    n = (_modele() if m is None else m).name.lower()
    if "kontext" in n:
        return 20
    if "z_image" in n or "z-image" in n:
        return 8  # Z-Image-Turbo : distille pour 8 etapes
    if "flux" in n:
        return 4 if "schnell" in n else 28
    return {"turbo": 8, "sdxl": 28, "sd15": 28}.get(_type_modele(m), 28)


def _resolution_defaut(m: Path | None = None) -> int:
    """Cote par defaut adapte au modele (carre)."""
    return {"flux": 1024, "turbo": 768, "sdxl": 1024, "sd15": 512}.get(
        _type_modele(m), 512)


def _eta_fichier() -> Path:
    """Fichier des durees mesurees (mediane glissante par configuration)."""
    base = Path(os.environ.get("LOCALAI_DATA_DIR", str(_dir().parents[1] / "data")))
    return base / "sd_eta.json"


def _lire_eta() -> dict:
    try:
        return json.loads(_eta_fichier().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _enregistrer_duree(cle: str, secondes: float) -> None:
    """Memorise la duree reelle (8 dernieres) pour une ETA honnete."""
    try:
        data = _lire_eta()
        hist = [float(x) for x in data.get(cle, [])][-7:]
        hist.append(round(secondes, 1))
        data[cle] = hist
        f = _eta_fichier()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


def _nom_modele(modele) -> str:
    """Nom court du modele, pour une cle ETA qui distingue Q4 et Q8."""
    try:
        return Path(str(modele)).name or str(modele)
    except Exception:
        return str(modele)


def _cle_eta(t: str, modele, largeur: int, hauteur: int, steps: int,
             kontext_actif: bool, passe: str = "") -> str:
    """Cle de mesure : le MODELE et la passe haute resolution en font partie.

    Sans eux, les 774 s du Q8 sur l'ancienne carte 10 Go polluaient
    l'estimation (12 min 54 s annoncees pour 43 s reelles).
    """
    return (f"{t}:{_nom_modele(modele)}:{largeur}x{hauteur}:{steps}:"
            f"{'k' if kontext_actif else 's'}{':' + passe if passe else ''}")


def _estimer_temps_generation(t: str, resolution: str, largeur: int, hauteur: int,
                              steps: int, kontext_actif: bool,
                              modele=None, passe: str = "") -> str:
    """Estime le temps reel : mediane mesuree, sinon modele calibre.

    L'ancienne formule annoncait ~2 s pour une generation de 39 s. Les durees
    reelles sont desormais enregistrees dans data/sd_eta.json, par modele.
    """
    cle = _cle_eta(t, modele, largeur, hauteur, steps, kontext_actif, passe)
    hist = [float(x) for x in _lire_eta().get(cle, [])]
    if hist:
        hist.sort()
        est = hist[len(hist) // 2]
    else:
        pixels = (largeur * hauteur) / (1024 * 1024)  # Mpix
        # Secondes par Mpix et par step, calibrees sur les mesures reelles :
        # FLUX Q4 1344x768/4 = 38 s, FLUX Q4 1536x864/6 = 43 s (upscale 4K
        # compris). L'ancien coefficient (26) sur-estimait de 20x.
        base = {"flux": 1.4, "turbo": 0.5, "sdxl": 2.0, "sd15": 1.0}.get(t, 1.4)
        est = 26.0 + pixels * steps * base * (2.0 if kontext_actif else 1.0)
        if passe:
            est += 40.0  # passe 2560x1440 (mesure RTX 5080 : ~37 s)
        if resolution in ("4k_natif", "1024_upscale"):
            est += 12.0  # Real-ESRGAN / Lanczos
    if est < 90:
        return f"~{int(est)}s"
    return f"~{int(est // 60)}min {int(est % 60):02d}s"


def status(settings=None) -> dict:
    """Etat du generateur. `settings` est accepte pour compatibilite (inutile)."""
    exe = _exe()
    modele = _modele()
    t = _type_modele(modele)
    return {
        "dir": str(_dir()),
        "binaire": exe.exists(),
        "modele": str(modele) if modele.is_file() else "",
        "pret": exe.exists() and modele.is_file(),
        "type": t,
        "steps_defaut": _steps_defaut(modele),
        "resolution_defaut": _resolution_defaut(modele),
    }


def _liberer_vram(attente_max_s: float = 45.0) -> None:
    """Decharge les modeles Ollama pour liberer la VRAM avant la generation.

    Best-effort ; les 48 Go de RAM restent disponibles pour l'offload CPU des
    gros modeles (FLUX). Le LLM se rechargera a la prochaine requete.

    On ATTEND que /api/ps soit vide (max 45 s) : sans cette attente, sd-cli
    demarrait alors que le LLM occupait encore la carte et basculait sur le
    CPU (une passe FLUX passait de ~2 min a >20 min).
    """
    import httpx

    from backend.core.config import ollama_url

    base = ollama_url()
    # Le trieur du RAG peut occuper 1,1 Go de carte (une minute apres une
    # recherche documentaire) : il redescend en memoire vive.
    try:
        httpx.post("http://127.0.0.1:8322/liberer", timeout=5)
    except Exception:
        pass
    try:
        ps = httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])
        if not ps:
            return
        avant = _vram_utilisee_mio()
        for m in ps:
            nom = m.get("name", "")
            if nom:
                httpx.post(f"{base}/api/generate",
                           json={"model": nom, "keep_alive": 0}, timeout=10)
        t0 = time.time()
        while time.time() - t0 < attente_max_s:
            restants = httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])
            if not restants:
                # Sortis de la liste d'Ollama ne veut pas dire memoire rendue.
                _attendre_vram_rendue(avant)
                print(f"[sd] VRAM liberee en {time.time() - t0:.1f}s", flush=True)
                return
            time.sleep(1.0)
        print(f"[sd] VRAM toujours occupee apres {attente_max_s:.0f}s "
              f"({len(restants)} modele(s)) - generation en mode degrade",
              flush=True)
    except Exception as e:
        print(f"[sd] liberation VRAM : {e}", flush=True)


def _est_francais(texte: str) -> bool:
    """Heuristique : le prompt contient-il des marqueurs francais ?"""
    t = " " + texte.lower() + " "
    marqueurs = (
        " un ", " une ", " des ", " le ", " la ", " les ", " de ", " du ",
        " avec ", " dans ", " sur ", " couleur", " fond ", " vue ", " ciel ",
        " planete", " planetes", " forêt", " foret", " maison ", " chat ",
        "à ", "é", "è", "ê", "â", "î", "ô", "û", "ç", "œ",
    )
    return any(m in t for m in marqueurs)


def _traduire_enrichir(prompt: str) -> str:
    """Traduit (FR->EN) et enrichit le prompt via le LLM local. Best-effort :
    en cas d'echec, retourne le prompt original inchange.

    Modele : LOCALAI_SD_TRAD_MODELE, sinon le meilleur GENERALISTE installe.
    Comparaison sur une meme demande : gemma3:12b ecrit les descriptions les
    plus riches (matieres, optique, lumiere) ; le modele de chat fine-tune
    pour le code, souvent deja charge, est moins bon a cet exercice. Un
    generaliste deja charge est reutilise (zero chargement).
    """
    import httpx

    from backend.core.config import ollama_url

    base = ollama_url()
    modele = os.environ.get("LOCALAI_SD_TRAD_MODELE", "")
    if not modele:
        preferes = ("gemma3:12b", "qwen3:14b", "gemma3:27b", "qwen3:8b", "llama3.1:8b")
        try:
            charges = [m.get("name", "") for m in
                       httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])]
            installes = [m.get("name", "") for m in
                         httpx.get(f"{base}/api/tags", timeout=5).json().get("models", [])]
        except Exception:
            charges, installes = [], []
        modele = (next((m for m in charges if m in preferes), "")
                  or next((m for m in preferes if m in installes), "")
                  or "qwen3:8b")
    # Consigne pensee pour FLUX (encodeur T5) : une description riche en
    # phrases, sujet d'abord, avec les details qui font une belle image
    # (matieres, lumiere, optique). Le style demande prime sur la photo.
    inst = (
        "You write prompts for the FLUX text-to-image model. Rewrite the image "
        "request below as ONE detailed English description of 60 to 110 words. "
        "Start with the main subject and its precise physical details, then the "
        "setting, composition and camera angle, lens and depth of field, lighting "
        "(direction, quality, color temperature), materials and surface textures, "
        "color palette and atmosphere. Use natural sentences, never keyword lists, "
        "never quality tags such as 8k, masterpiece or trending. Every element of "
        "the request must be clearly visible and prominent in the image: describe "
        "each one concretely and never turn it into something faint, distant or "
        "implied. Invent nothing that contradicts the request. If the request "
        "names an artistic style (drawing, anime, painting, 3D render...), describe "
        "that style instead of a photograph. Always write in English, even when "
        "the request is in French. Reply with the description only, no quotes, "
        "no preamble.\n"
        f"Request: {prompt}"
    )

    def _appel(consigne: str) -> str:
        # 120 s : le modele peut devoir se charger (l'ancien delai de 25 s
        # expirait alors en silence et FLUX recevait le prompt en francais,
        # qu'il comprend mal).
        r = httpx.post(
            f"{base}/api/generate",
            json={
                "model": modele,
                "prompt": consigne,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.4, "num_predict": 320},
            },
            timeout=120,
        )
        txt = (r.json().get("response") or "").strip()
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip().strip('"').strip()
        return " ".join(ligne.strip() for ligne in txt.splitlines() if ligne.strip())

    try:
        txt = _appel(inst)
        if txt and _est_francais(txt):
            # Le modele a repondu dans la langue de la demande : traduction
            # seule, sans re-enrichir (le contenu est deja bon).
            txt = _appel("Translate the following image description into natural "
                         "English. Reply with the translation only.\n\n" + txt)
        if 3 <= len(txt) <= 1500 and not _est_francais(txt):
            return txt
        _journal_sd("avertissement", "enrichissement du prompt inexploitable",
                    modele=modele, longueur=len(txt))
    except Exception as exc:
        _journal_sd("avertissement", "enrichissement du prompt en echec",
                    modele=modele, erreur=str(exc)[:200])
    return prompt


def preparer_prompt(prompt: str, traduire: bool = True, reference_desc: str = "",
                    flux: bool = True) -> str:
    """Prepare le prompt final : traduction/enrichissement + suffixe.

    Par defaut, une image vise le PHOTOREALISME 2D. Si l'utilisateur demande un
    style (cartoon, dessin, logo...), on n'impose pas le realisme.

    FLUX (defaut) : une demande francaise OU courte (< 40 mots) est reecrite
    par le LLM local en description anglaise detaillee ; suffixe naturel court.
    SDXL / SD 1.5 (flux=False) : traduction seule si francais, etiquettes.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    bas = p.lower()
    stylise = any(w in bas for w in STYLE_MOTS)
    # Fidelite Kontext : le contexte de reference est deja en anglais (la
    # description vient du modele vision) — la traduction n'apporte rien et
    # coute un cycle de chargement LLM. On saute.
    a_enrichir = _est_francais(p) or (flux and len(p.split()) < 40)
    if traduire and a_enrichir and not reference_desc:
        p = _traduire_enrichir(p)
    if flux:
        if not stylise and "photograph" not in p.lower():
            p = f"{p.rstrip('. ')}. {SUFFIXE_FLUX_PHOTO}"
        return p
    suffixe = SUFFIXE_QUALITE if stylise else SUFFIXE_REALISME
    # Evite de dupliquer le suffixe si l'appelant l'a deja mis.
    if suffixe.split(",")[0] not in p.lower():
        p = f"{p}, {suffixe}"
    return p


# ---- Upscale 4K (Real-ESRGAN + repli Lanczos) ----


def _esrgan_exe() -> Path | None:
    d = reglages.chemin("LOCALAI_ESRGAN_DIR", "tools", "RealESRGAN")
    if not d.is_dir():
        return None
    for p in sorted(d.glob("realesrgan-ncnn-vulkan*.exe")):
        if p.is_file():
            return p
    return None


def _sauver_image(im, chemin: Path) -> None:
    """Ecrit une image PIL selon l'extension du fichier (point unique).

    JPEG q96 en 4:4:4 (le detail colore — feuillages, tissus, neons — survit
    a la compression), WebP q94, TIFF sans compression, sinon PNG.
    """
    suffixe = chemin.suffix.lower()
    if suffixe in (".jpg", ".jpeg"):
        im.save(chemin, "JPEG", quality=96, subsampling=0, optimize=True)
    elif suffixe == ".webp":
        im.save(chemin, "WEBP", quality=94, method=6)
    elif suffixe in (".tif", ".tiff"):
        im.save(chemin, "TIFF")
    else:
        im.save(chemin, "PNG")


# Version d'affichage : le fil de discussion montre l'image a ~720-1400 px. Le
# navigateur reduisait la 4K a la volee (bilineaire, moirage, aspect grossier) :
# une reduction Lanczos soignee + accentuation adaptee a l'echelle d'affichage
# donne un rendu net et leger. Le fichier complet reste la reference.
ECRAN_LARGEUR = 1920


def _version_ecran(chemin: Path) -> Path | None:
    """Ecrit `<nom>_ecran.jpg` (1920 px de large) a cote de l'image. None si echec."""
    try:
        from PIL import Image, ImageFilter

        with Image.open(chemin) as im:
            im = im.convert("RGB")
            if im.width <= ECRAN_LARGEUR:
                return None
            h = round(ECRAN_LARGEUR * im.height / im.width)
            petit = im.resize((ECRAN_LARGEUR, h), Image.LANCZOS, reducing_gap=3.0)
        petit = petit.filter(ImageFilter.UnsharpMask(radius=0.8, percent=45, threshold=2))
        cible = chemin.with_name(f"{chemin.stem}_ecran.jpg")
        petit.save(cible, "JPEG", quality=92, subsampling=0, optimize=True, progressive=True)
        return cible
    except Exception as e:
        _journal_sd("warning", "version ecran impossible", fichier=str(chemin), erreur=str(e)[:200])
        return None


_ETAPES_SD = re.compile(r"(loading tensors|sampling|decode_first_stage|generate_image)[^\n]*?taking ([\d.]+)\s*s",
                        re.I)


def _etapes_sd(sortie: str) -> dict:
    """Durees internes de sd-cli (chargement, echantillonnage, decodage), en s."""
    etapes: dict = {}
    for nom, s in _ETAPES_SD.findall(sortie or ""):
        cle = nom.lower().replace(" ", "_")
        etapes[cle] = round(etapes.get(cle, 0.0) + float(s), 1)
    return etapes


def _resize_lanczos(src: Path, dst: Path, largeur: int, hauteur: int) -> bool:
    try:
        from PIL import Image

        im = Image.open(src).convert("RGB")
        im.thumbnail((largeur, hauteur), Image.LANCZOS)
        _sauver_image(im, dst)
        return dst.is_file()
    except Exception:
        return False


def _agrandir_lanczos_net(src: Path, dst: Path) -> bool:
    """Agrandit vers le master (3840 px de grand cote) par Lanczos + accentuation douce.

    Depuis la passe haute resolution (2560x1440), le facteur n'est que 1,5 :
    Real-ESRGAN calculait une image x4 (10240 px) pour la reduire ensuite.
    """
    try:
        from PIL import Image, ImageFilter

        with Image.open(src) as im:
            grand = im.convert("RGB").resize(_cible_master(im.width, im.height), Image.LANCZOS)
        # Accentuation legere : la finition en ajoute une ; trop forte, la peau devient « croquante ».
        grand = grand.filter(ImageFilter.UnsharpMask(radius=1.2, percent=35, threshold=3))
        _sauver_image(grand, dst)
        return dst.is_file()
    except Exception as exc:
        _journal_sd("avertissement", "agrandissement Lanczos en echec", erreur=str(exc)[:200])
        return False


def _upscale_4k(src: Path, dst: Path) -> bool:
    """Agrandit vers 3840x2160 : moyenne Real-ESRGAN x4 / Lanczos.

    Real-ESRGAN seul donne des contours nets mais un aspect « peinture a
    l'huile » sur les textures fines (peau, tissus) ; Lanczos seul garde la
    texture generee mais reste doux. La moyenne 50/50 garde le meilleur des
    deux (comparaison a 100 % sur portrait et scene detaillee).

    Retourne False si l'upscaler n'est pas disponible/echec (l'appelant
    replie alors sur Lanczos). Le dossier temporaire est toujours supprime ;
    un echec est journalise (code + fin de stderr) pour savoir pourquoi le
    repli Lanczos a ete utilise.
    """
    # Defaut : Lanczos + accentuation (0,7 s contre 14 s, rendu de peau plus naturel).
    # LOCALAI_SD_AGRANDISSEMENT=esrgan retablit le melange Real-ESRGAN / Lanczos.
    if os.environ.get("LOCALAI_SD_AGRANDISSEMENT", "lanczos").lower() != "esrgan":
        return _agrandir_lanczos_net(src, dst)
    exe = _esrgan_exe()
    if not exe:
        return False
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="esrgan_"))
    try:
        tmpout = tmpdir / "out.png"
        cmd = [str(exe), "-i", str(src), "-o", str(tmpout),
               "-s", "4", "-n", "realesrgan-x4plus", "-f", "png"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not tmpout.is_file():
            _journal_sd("avertissement", "real-esrgan en echec, repli Lanczos",
                        code=proc.returncode,
                        stderr=(proc.stderr or proc.stdout or "")[-400:])
            return False
        from PIL import Image

        with Image.open(src) as _s:
            cible = _cible_master(_s.width, _s.height)
        with Image.open(tmpout) as gan, Image.open(src) as base:
            net = gan.convert("RGB").resize(cible, Image.LANCZOS)
            doux = base.convert("RGB").resize(cible, Image.LANCZOS)
            _sauver_image(Image.blend(net, doux, 0.5), dst)
        return dst.is_file()
    except Exception as exc:
        _journal_sd("avertissement", "real-esrgan en echec, repli Lanczos",
                    erreur=str(exc))
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _journal_sd(niveau: str, message: str, **champs) -> None:
    """Journal general (best-effort : ne leve jamais)."""
    try:
        from backend.core.logs import log

        log(niveau, "sd", message, **champs)
    except Exception:
        pass


# ---- Finition : colorimetrie, micro-contraste, nettete, grain ----

# Palettes sobres (jamais de virage criard) : gains par canal, saturation,
# contraste et releve des noirs. Choisies automatiquement d'apres le prompt,
# sinon par rotation deterministe pour garantir une colorimetrie variee.
_PALETTES = {
    "naturel": {"sat": 1.00, "contraste": 1.00, "r": 1.000, "v": 1.000,
                "b": 1.000, "lift": 0.000},
    "chaud": {"sat": 1.04, "contraste": 1.01, "r": 1.028, "v": 1.006,
              "b": 0.970, "lift": 0.004},
    "froid": {"sat": 1.03, "contraste": 1.02, "r": 0.974, "v": 1.000,
              "b": 1.030, "lift": 0.000},
    "cinema": {"sat": 0.98, "contraste": 1.06, "r": 1.012, "v": 0.998,
               "b": 1.006, "lift": -0.006},
    "vibrant": {"sat": 1.12, "contraste": 1.05, "r": 1.010, "v": 1.004,
                "b": 0.996, "lift": 0.000},
    "doux": {"sat": 1.05, "contraste": 0.955, "r": 1.014, "v": 1.006,
             "b": 1.004, "lift": 0.020},
    "mono": {"sat": 0.00, "contraste": 1.07, "r": 1.020, "v": 1.005,
             "b": 0.985, "lift": -0.004},
}

# Mots-cles qui imposent une palette (francais + anglais).
_PALETTE_MOTS = (
    ("mono", ("noir et blanc", "black and white", "monochrome", "n&b")),
    ("chaud", ("coucher de soleil", "sunset", "sunrise", "aube", "automne",
               "desert", "désert", "golden hour", "braise", "flamme",
               "doré", "dore", "chaleur", "crépuscule", "crepuscule")),
    ("froid", ("nuit", "night", "glace", "hiver", "winter", "neige",
               "snow", "ocean", "océan", "pluie", "rain", "brume",
               "fog", "banquise", "arctique", "givre")),
    ("cinema", ("cinematique", "cinématique", "cinematic", "film noir",
                "thriller", "drame", "science-fiction", "sci-fi", "polar")),
    ("doux", ("portrait", "bebe", "bébé", "mariage", "pastel", "doux",
              "reve", "rêve", "aquarelle", "aérien", "aerien")),
    ("vibrant", ("neon", "néon", "cyberpunk", "fete", "fête", "carnaval",
                 "tropical", "jungle", "fleur", "flower", "coloré", "colore",
                 "vif", "festival")),
)


def _palette_auto(prompt: str, graine: int = -1) -> str:
    """Palette deduite des mots du prompt ; sinon « naturel ».

    L'ancienne rotation « pour varier » recolorait au hasard des images que
    l'utilisateur n'avait pas demandees ainsi (split-toning cinema sur un
    portrait neutre...) : le modele choisit deja la lumiere decrite.
    """
    texte = (prompt or "").lower()
    for nom, mots in _PALETTE_MOTS:
        if any(mot in texte for mot in mots):
            return nom
    return "naturel"


def _finition(chemin: Path, palette: str = "auto", prompt: str = "",
              graine: int = -1, force: float = 1.0) -> bool:
    """Finalise une image : colorimetrie, micro-contraste, nettete, grain.

    Applique dans l'ordre (1) une palette douce, (2) un micro-contraste local
    qui revele les volumes, (3) un rehaussement de nettete a rayon court
    seuille pour ne pas amplifier le bruit, (4) une saturation adaptative
    (jamais terne, jamais fluo) et (5) un grain tres leger qui evite l'aspect
    plastique du sur-lissage. Le fichier est reecrit sur place ; en cas
    d'echec il reste intact et la fonction renvoie False.
    """
    suffixe = chemin.suffix.lower()
    if suffixe == ".exr":
        return False  # master HDR lineaire : on ne touche pas aux valeurs
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:
        return False
    try:
        source = Image.open(chemin).convert("RGB")
        arr = np.asarray(source, dtype=np.float32) / 255.0
        h, w = arr.shape[:2]
        if h < 64 or w < 64:
            return False

        nom = _palette_auto(prompt, graine) if palette in ("", "auto") else palette
        p = _PALETTES.get(nom, _PALETTES["naturel"])
        poids = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

        # 1. Colorimetrie : gains par canal, saturation, contraste, releve.
        out = arr * np.array([p["r"], p["v"], p["b"]], dtype=np.float32)
        if p["sat"] != 1.0:
            luma = out @ poids
            out = luma[..., None] + (out - luma[..., None]) * p["sat"]
        out = (out - 0.5) * p["contraste"] + 0.5 + p["lift"]
        if nom == "cinema":
            # Split-toning : ombres froides, hautes lumieres chaudes.
            luma = np.clip(out @ poids, 0.0, 1.0)
            ombres = (1.0 - luma) ** 2
            hautes = luma ** 2
            out[..., 0] += 0.030 * hautes - 0.010 * ombres
            out[..., 2] += 0.032 * ombres - 0.008 * hautes
        out = np.clip(out, 0.0, 1.0)
        del arr

        # 2. Micro-contraste : on retranche une version tres floue (clarity).
        #    Calcul sur une reduction /4 : meme effet, 10x plus rapide en 4K.
        petite = source.resize((max(64, w // 4), max(64, h // 4)), Image.LANCZOS)
        flou = petite.filter(ImageFilter.GaussianBlur(radius=2.4))
        flou = flou.resize((w, h), Image.BILINEAR)
        basses = np.asarray(flou, dtype=np.float32) / 255.0
        out = np.clip(out + 0.10 * force * (out - basses), 0.0, 1.0)
        del basses, flou, petite

        # 3. Nettete a rayon court, dosee selon la nettete deja presente :
        #    une image douce est reaccentuee, une image deja nette (passe hires)
        #    l'est a peine — sinon les contours deviennent croustillants.
        petit = source.resize((min(w, 768), min(h, 768)), Image.LANCZOS)
        gris = np.asarray(petit.convert("L"), dtype=np.float32) / 255.0
        lap = (-4.0 * gris + np.roll(gris, 1, 0) + np.roll(gris, -1, 0)
               + np.roll(gris, 1, 1) + np.roll(gris, -1, 1))
        variance = float(lap.var() * 1e4)
        # Doses reduites : la passe haute resolution apporte du VRAI detail,
        # une accentuation forte le rendait « croustillant ».
        dose = 0.35 if variance < 80 else max(0.12, 0.35 * (80.0 / variance) ** 0.5)
        del petit, gris, lap

        inter = Image.fromarray((out * 255.0 + 0.5).astype(np.uint8))
        doux = inter.filter(ImageFilter.GaussianBlur(radius=1.05))
        detail = out - np.asarray(doux, dtype=np.float32) / 255.0
        detail = np.where(np.abs(detail) > 0.004, detail, 0.0)
        out = np.clip(out + dose * force * detail, 0.0, 1.0)
        del detail, doux, inter

        # 4. Saturation adaptative : plancher doux sur les images ternes.
        luma = out @ poids
        maximum = out.max(axis=2)
        minimum = out.min(axis=2)
        sat = np.where(maximum > 1e-5,
                       (maximum - minimum) / np.maximum(maximum, 1e-5),
                       np.float32(0.0)).astype(np.float32, copy=False)
        moyenne = float(sat.mean())
        if moyenne < 0.30:
            k = min(1.22, 0.30 / max(moyenne, 0.05))
            out = np.clip(luma[..., None] + (out - luma[..., None]) * k, 0.0, 1.0)
            luma = out @ poids

        # 5. Grain tres leger, limite aux tons moyens.
        if force > 0:
            rng = np.random.default_rng(1234 + (graine if graine >= 0 else 0))
            bruit = rng.normal(0.0, 0.0035 * force, (h, w, 1)).astype(np.float32)
            masque = (1.0 - np.abs(2.0 * luma - 1.0))[..., None]
            out = np.clip(out + bruit * masque, 0.0, 1.0)
            del bruit

        final = Image.fromarray((out * 255.0 + 0.5).astype(np.uint8))
        _sauver_image(final, chemin)
        return True
    except Exception:
        return False


def installer(binaire_seulement: bool = False) -> dict:
    """Telecharge stable-diffusion.cpp (release Windows la plus recente)
    puis un modele SD 1.5 GGUF via les APIs GitHub/HuggingFace."""
    import zipfile

    import httpx

    d = _dir()
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "models").mkdir(parents=True, exist_ok=True)

    # 1. binaire : derniere release GitHub, asset windows (cuda > vulkan > cpu)
    rel = httpx.get(
        "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest",
        timeout=30,
    ).json()
    assets = [
        a
        for a in rel.get("assets", [])
        if "-win-" in a["name"].lower() and a["name"].endswith(".zip")
    ]
    asset = next((a for a in assets if "cuda12" in a["name"].lower()), None) or next(
        (a for a in assets if "vulkan" in a["name"].lower()), None
    ) or next((a for a in assets if "cpu" in a["name"].lower()), None)
    if not asset:
        return {"ok": False, "error": "Aucun binaire Windows trouve dans la release"}
    zip_path = d / asset["name"]
    with httpx.stream("GET", asset["browser_download_url"], timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        fait = 0
        with zip_path.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                fait += len(chunk)
                print(f"[sd-install] binaire {fait // 1048576}/{total // 1048576} Mo")
    extrait = d / "_extract"
    shutil.rmtree(extrait, ignore_errors=True)
    extrait.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extrait)
    for src in list(extrait.rglob("*.exe")) + list(extrait.rglob("*.dll")):
        shutil.copy2(src, d / "bin" / src.name)
    shutil.rmtree(extrait, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    # 1b. runtimes CUDA si variante cuda (DLLs separees dans la release)
    if asset and "cuda" in asset["name"].lower():
        cudart = next(
            (
                a
                for a in rel.get("assets", [])
                if a["name"].lower().startswith("cudart-sd-bin-win")
            ),
            None,
        )
        if cudart:
            zip_cudart = d / cudart["name"]
            with httpx.stream(
                "GET", cudart["browser_download_url"], timeout=900, follow_redirects=True
            ) as r:
                r.raise_for_status()
                with zip_cudart.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            extrait = d / "_extract"
            shutil.rmtree(extrait, ignore_errors=True)
            extrait.mkdir(parents=True)
            with zipfile.ZipFile(zip_cudart) as z:
                z.extractall(extrait)
            for src in list(extrait.rglob("*.dll")) + list(extrait.rglob("*.exe")):
                shutil.copy2(src, d / "bin" / src.name)
            shutil.rmtree(extrait, ignore_errors=True)
            zip_cudart.unlink(missing_ok=True)

    if binaire_seulement:
        return {"ok": True, "etape": "binaire installe"}

    # 2. modele : recherche HF API d'un checkpoint SD 1.5 fichier unique
    fichier_choisi = None
    repo_choisi = None
    try:
        req = httpx.get(
            "https://huggingface.co/api/models",
            params={"search": "v1-5-pruned-emaonly", "limit": 20, "full": "true"},
            timeout=30,
        ).json()
        motif = re.compile(r"(v1[-._]?5|sd.?1[.-]?5).*\.gguf$", re.I)
        for repo in req:
            for sib in repo.get("siblings", []):
                nom = sib.get("rfilename", "")
                if nom.endswith(".gguf") and motif.search(nom):
                    taille_preferee = "q8" in nom.lower() or "q5" in nom.lower() or "f16" in nom.lower()
                    if taille_preferee or not fichier_choisi:
                        fichier_choisi = nom
                        repo_choisi = repo.get("id")
                    if fichier_choisi:
                        break
            if fichier_choisi:
                break
    except Exception:
        pass
    if not fichier_choisi:
        # repli verifie (decouvert via API HF) : checkpoint officiel SD 1.5
        repo_choisi = "stable-diffusion-v1-5/stable-diffusion-v1-5"
        fichier_choisi = "v1-5-pruned-emaonly.safetensors"
    url_modele = f"https://huggingface.co/{repo_choisi}/resolve/main/{fichier_choisi}"
    dest = d / "models" / fichier_choisi
    with httpx.stream("GET", url_modele, timeout=3600, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        fait = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                fait += len(chunk)
                print(f"[sd-install] modele {fait // 1048576}/{max(total, 1) // 1048576} Mo")
    return {"ok": True, "binaire": str(_exe()), "modele": str(dest)}


# Nom appele par le serveur Rust (`from backend.core.sdgen import install`).
install = installer


def _modele_kontext() -> Path | None:
    """Modele FLUX Kontext (fidelite sujet via image de reference), ou None."""
    for f in _fichiers_modeles():
        if "kontext" in f.name.lower() and f.suffix.lower() == ".gguf":
            return f
    return None


def _prechauffer_cache(chemins: list[str], force: bool = False) -> None:
    """Prechauffe le cache fichier Windows pour les gros modeles.

    `force` : relire meme ce qui vient de l'etre. Le cache n'est pas une
    garantie : quand la memoire se remplit, Windows jette les donnees les plus
    anciennement lues. Une relecture juste avant l'usage les remet « en tete »
    et recharge ce qui a ete jete (voir `t_video._clip_wan`).

    stable-diffusion.cpp charge via mmap (faults 4K quasi-aleatoires) : sur
    un SSD QLC froid, cela tombe a ~20 Mo/s (10 min de chargement). Une
    lecture sequentielle prealable remplit le cache a ~290 Mo/s (~45 s pour
    12 Go), puis les faults de sd-cli touchent la RAM. Best-effort.
    """
    import time as _t

    for p in chemins:
        try:
            path = Path(p)
            if not path.is_file() or path.stat().st_size < 100 * 1024 * 1024:
                continue
            cle = str(path)
            # Deja lu a l'instant (pendant le brief, voir `prechauffer_studio`) :
            # le relire coute encore 3 s pour rien. En cours de lecture par un
            # autre fil : on attend ce fichier-la, sans le lire en double.
            with _CHAUDS_VERROU:
                recent = not force and _t.time() - _CHAUDS.get(cle, 0.0) < _CHAUD_S
                en_cours = _LECTURES.get(cle)
                if not recent and en_cours is None:
                    _LECTURES[cle] = threading.Event()
            if recent:
                continue
            if en_cours is not None:
                en_cours.wait(180)
                continue
            t0 = _t.time()
            try:
                with open(path, "rb") as f:
                    while f.read(8 * 1024 * 1024):
                        pass
                with _CHAUDS_VERROU:
                    _CHAUDS[cle] = _t.time()
                print(f"[sd-warm] {path.name} prechauffe en {_t.time() - t0:.0f}s")
            finally:
                with _CHAUDS_VERROU:
                    _LECTURES.pop(cle).set()
        except Exception:
            continue


# Fichiers lus recemment par ce processus (chemin -> heure de fin de lecture).
_CHAUDS: dict[str, float] = {}
_LECTURES: dict[str, threading.Event] = {}  # lectures en cours
_CHAUDS_VERROU = threading.Lock()
_CHAUD_S = 180.0


def prechauffer_studio() -> threading.Thread | None:
    """Lit en tache de fond les fichiers que le studio va charger : generateur
    par defaut, ses encodeurs, et FLUX.2-klein (passes visage et yeux).

    Appele au debut d'une demande d'image, pendant que le brief s'ecrit : la
    lecture (3 s quand les fichiers sont deja en cache, bien plus apres un
    redemarrage) ne s'ajoute plus a l'attente. `generer` ne relit pas ce qui
    vient de l'etre et n'attend que ses propres fichiers : klein, lu en
    dernier, finit d'etre lu pendant que l'image de base se calcule.
    Best-effort."""

    def _lire() -> None:
        try:
            modele = _modele()
            fichiers = [str(modele)]
            if _type_modele(modele) == "flux":
                fichiers += [str(f) for _, f in _encodeurs_modele(modele)]
            retouche = _modele_retouche()
            if retouche:
                fichiers += [str(retouche[0]), str(retouche[1])]
            _prechauffer_cache(fichiers)
        except Exception:
            pass

    try:
        fil = threading.Thread(target=_lire, name="prechauffe-studio", daemon=True)
        fil.start()
        return fil
    except Exception:
        return None


def _acquerir_verrou_gpu(attente_s: int = 3600):
    """Verrou GLOBAL inter-processus : une seule generation SD a la fois.

    Chaque generation peut charger ~10 Go (VRAM + RAM) ; deux demandes
    concurrentes finiraient en OOM. Le verrou est un fichier locke via
    msvcrt (Windows) / fcntl (POSIX) ; retourne le descripteur a garder
    ouvert (le verrou est libere a la fermeture).
    """
    import time

    lock = Path(
        os.environ.get("LOCALAI_LOCK_DIR", os.environ.get("TEMP", r"C:\Windows\Temp"))
    ) / "localai_sd.lock"
    debut = time.time()
    while True:
        try:
            f = open(lock, "a+")
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except (OSError, IOError):
            try:
                f.close()
            except Exception:
                pass
            if time.time() - debut > attente_s:
                return None
            time.sleep(4)


_JOB_SD = None


def _job_sd():
    """Job Windows « tuer a la fermeture » contenant chaque sd-cli lance.

    Si ce Python meurt (delai depasse du pont Rust, arret du service), le
    systeme ferme le job et tue sd-cli avec lui. Le 09/11/2026, un sd-cli
    survivant a garde 15 Go de VRAM pendant des heures : chaque generation
    suivante depassait 10 min. None hors Windows ou sans pywin32."""
    global _JOB_SD
    if _JOB_SD is None:
        _JOB_SD = False
        if os.name == "nt":
            try:
                import win32job

                job = win32job.CreateJobObject(None, "")
                info = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation)
                info["BasicLimitInformation"]["LimitFlags"] |= (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, info)
                _JOB_SD = job
            except Exception as exc:
                _journal_sd("avertissement", "job sd-cli indisponible", erreur=str(exc)[:200])
    return _JOB_SD or None


def _nettoyer_orphelins() -> int:
    """Arrete les sd-cli.exe dont le Python parent est mort.

    Appele SOUS le verrou GPU : aucune generation legitime de Neo ne tourne
    alors ; un sd-cli sans parent vivant est un reste de generation abandonnee
    qui occuperait la carte. Un sd-cli dont le parent vit (essai manuel,
    autre outil) n'est jamais touche."""
    try:
        import psutil
    except Exception:
        return 0
    arretes = 0
    for p in psutil.process_iter(["name", "ppid", "pid"]):
        try:
            if (p.info.get("name") or "").lower() != "sd-cli.exe":
                continue
            ppid = p.info.get("ppid") or 0
            parent_vivant = False
            if ppid and psutil.pid_exists(ppid):
                try:
                    parent = psutil.Process(ppid)
                    # PID reutilise par un autre programme : le vrai parent est mort.
                    parent_vivant = parent.create_time() <= p.create_time()
                except Exception:
                    parent_vivant = False
            if not parent_vivant:
                p.kill()
                arretes += 1
        except Exception:
            continue
    if arretes:
        _journal_sd("avertissement", "sd-cli orphelin arrete avant generation", nombre=arretes)
        time.sleep(1.5)  # le pilote libere la VRAM
    return arretes


def _executer_sd(cmd: list[str], delai_s: float) -> subprocess.CompletedProcess:
    """Lance sd-cli dans le job « tuer a la fermeture », avec un delai strict.

    Leve subprocess.TimeoutExpired apres avoir TUE sd-cli (jamais d'orphelin)."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    job = _job_sd()
    if job is not None:
        try:
            import win32job

            win32job.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        sortie, erreurs = proc.communicate(timeout=delai_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            sortie, erreurs = proc.communicate(timeout=30)
            # Ce que sd-cli a ecrit avant d'etre arrete : son plan de placement
            # (memoire graphique libre vue au lancement) et ses dernieres lignes.
            # Sans cela, une passe expiree ne laissait aucun indice (11/11/2026).
            texte = (sortie or "") + (erreurs or "")
            plan = [ligne.strip()[:160] for ligne in texte.splitlines() if "auto-fit" in ligne or "free" in ligne][:6]
            _journal_sd("avertissement", "sd-cli arrete au delai", delai_s=delai_s, plan=plan,
                        fin=texte[-400:])
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, sortie, erreurs)


# Formats : base FLUX (~1,3 Mpx), passe haute resolution (~3,7 Mpx), master.
CADRAGES = {
    "paysage": {"base": (1536, 864), "hires": (2560, 1440), "kontext": (1280, 720)},
    "portrait": {"base": (864, 1536), "hires": (1440, 2560), "kontext": (720, 1280)},
    "carre": {"base": (1152, 1152), "hires": (1920, 1920), "kontext": (1024, 1024)},
}


def _cible_master(largeur: int, hauteur: int) -> tuple[int, int]:
    """Taille du master : 3840 px sur le grand cote (4K UHD en 16:9 et 9:16),
    2880x2880 pour un carre (x1,5 de la passe 1920)."""
    if largeur == hauteur:
        return (2880, 2880)
    if largeur > hauteur:
        return (3840, round(3840 * hauteur / largeur))
    return (round(3840 * largeur / hauteur), 3840)


def _commande_secours(cmd: list[str], t: str) -> list[str] | None:
    """Commande FLUX economique apres une passe bloquee : modele Q4 (6,8 Go
    au lieu de 12,6), encodeur de texte sur le processeur, decodage par
    tuiles. None si rien ne peut etre allege (autre famille de modele)."""
    if t != "flux" or "--diffusion-model" not in cmd:
        return None
    c = list(cmd)
    i = c.index("--diffusion-model") + 1
    actuel = Path(c[i]).name.lower()
    leger = next((f for f in _fichiers_modeles()
                  if f.name.lower().startswith("flux1-schnell") and "q4" in f.name.lower()), None)
    if leger is not None and "schnell" in actuel:
        c[i] = str(leger)
    if "--backend" not in c:
        c += ["--backend", "te=cpu"]
    if "--vae-tiling" not in c:
        c.append("--vae-tiling")
    return c


def _encodeurs_modele(m: Path) -> list[tuple[str, Path]]:
    """Encodeurs a passer a sd-cli selon le modele : Qwen3-4B + VAE pour
    Z-Image, t5xxl + clip_l + VAE pour FLUX."""
    if _est_zimage(m):
        enc = _encodeur_zimage()
        vae = next((f for f in _fichiers_modeles() if f.name.lower().startswith("ae.")), None)
        return [(fl, f) for fl, f in (("--llm", enc), ("--vae", vae)) if f is not None]
    return _encodeurs_flux()


def _encodeurs_flux() -> list[tuple[str, Path]]:
    """Encodeurs separes de FLUX trouves dans models/ : (drapeau, fichier)
    pour t5xxl, clip_l et le VAE, le premier par ordre alphabetique.

    Correspondance par DEBUT de nom : un motif « contenu dans » faisait
    prendre « Wan2.2_VAE.safetensors » (contient « ae. », et trie avant
    « ae.safetensors ») comme VAE de FLUX -> images detruites."""
    trouves: list[tuple[str, Path]] = []
    tous = _fichiers_modeles()  # SSD d'abord, puis ordre alphabetique
    for flag, prefixe in (("--t5xxl", "t5xxl"), ("--clip_l", "clip_l"), ("--vae", "ae.")):
        f = next((f for f in tous if f.name.lower().startswith(prefixe)), None)
        if f:
            trouves.append((flag, f))
    return trouves


def _guidance_flux(m: Path) -> float:
    """Guidance distillee : 0 (non transmise) pour schnell, 3.5 pour
    Kontext, valeur reglee pour dev/Krea dev."""
    n = m.name.lower()
    if "schnell" in n or "flux" not in n:
        return 0.0
    if "kontext" in n:
        return 3.5
    return GUIDANCE_DEV


def _sans_hires(cmd: list[str]) -> list[str]:
    """Copie de la commande sans la passe hires : `--hires` seul, et chaque
    option `--hires-*` retiree AVEC sa valeur (par paire, jamais par valeur)."""
    garde, sauter = [], False
    for arg in cmd:
        if sauter:
            sauter = False
            continue
        if arg == "--hires":
            continue
        if arg.startswith("--hires-"):
            sauter = True
            continue
        garde.append(arg)
    return garde


_CONSIGNE_VISAGE = (
    "Make this a sharp, high-resolution professional portrait photograph with realistic natural skin "
    "texture and fine pores, sharp eyelashes and individual hair strands. Keep the same person, identity, "
    "eye color, skin tone, face shape, expression, makeup, lighting, colors and framing exactly identical.")


_CONSIGNE_YEUX = (
    "Sharpen this photograph: bring the eyes into perfect focus, with crisp detailed irises, round pupils "
    "and sharp eyelashes. Do not change anything else: same eye color, same gaze direction, same eye shape, "
    "same makeup, same skin tone, same lighting, same colors, same framing.")


def _recaler_dense(orig, neuf, borne: float):
    """Deforme `neuf` pour qu'il epouse la geometrie d'`orig` : flot optique DIS,
    lisse en ne faisant confiance qu'aux CONTOURS FRANCS de l'original, borne a
    `borne` px. klein redessine les contours a 1-3 px pres : sans recalage, la
    fusion doublait les bords (monture de lunettes, aile du nez). Un flot lisse
    uniformement suivait le bruit des aplats et etirait le grain de peau en
    ondulations (essai du 10/11/2026) : dans un aplat, le deplacement vient donc
    des contours voisins. RGB uint8 de meme taille ; en cas d'echec, `neuf` tel quel."""
    import cv2
    import numpy as np

    try:
        go, gn = (cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), (0, 0), 1.5) for a in (orig, neuf))
        flot = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(go, gn, None)
        pente = np.hypot(cv2.Sobel(go, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(go, cv2.CV_32F, 0, 1, ksize=3))
        confiance = np.clip(pente / 40.0, 0.0, 1.0) ** 2 + 1e-3
        hh, ww = go.shape
        lissage = max(8.0, min(hh, ww) / 60)
        flot = (cv2.GaussianBlur(flot * confiance[..., None], (0, 0), lissage)
                / cv2.GaussianBlur(confiance, (0, 0), lissage)[..., None])
        flot = np.clip(flot, -borne, borne)
        xs, ys = np.meshgrid(np.arange(ww, dtype=np.float32), np.arange(hh, dtype=np.float32))
        return cv2.remap(neuf, xs + flot[..., 0], ys + flot[..., 1], cv2.INTER_LANCZOS4,
                         borderMode=cv2.BORDER_REFLECT)
    except Exception:
        return neuf


def _fusion_structure(orig, neuf, sigma_l: float, sigma_c: float = 3.0,
                      gain_l: float = 1.0, gain_c: float = 0.6, saturation: float = 0.0):
    """Structure de `neuf`, couleurs d'`orig` (espace Lab, RGB uint8 en entree et
    en sortie). Luminance : volumes d'`orig` (au-dela de `sigma_l` px) MULTIPLIES
    par le dessin de `neuf` (en deca : pores, iris, pupille, cils). Chrominance :
    celle d'`orig`, plus le grain fin de `neuf` (`sigma_c`).

    Le detail est un RAPPORT (clair / moyenne locale) et non une difference : son
    amplitude suit la luminosite locale de l'original. En addition, klein, qui
    eclaircit tout, faisait ressortir un oeil a travers un verre de lunettes
    noires (essai du 10/11/2026). La teinte des yeux et l'eclaircissement de la
    peau propres a klein ne passent pas.

    `saturation` (0 a 1) : la couleur d'un point suit sa nouvelle clarte (les
    fibres claires d'un iris marron restent ambrees au lieu de virer au gris),
    sauf dans les tons clairs : un reflet ou un blanc d'oeil ne se colore pas."""
    import cv2
    import numpy as np

    o = cv2.cvtColor(orig.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    k = cv2.cvtColor(neuf.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    fond_o = cv2.GaussianBlur(o[..., 0], (0, 0), sigma_l) + 2.0
    fond_k = cv2.GaussianBlur(k[..., 0], (0, 0), sigma_l) + 2.0
    res = o.copy()
    res[..., 0] = np.clip(fond_o * ((k[..., 0] + 2.0) / fond_k) ** gain_l - 2.0, 0, 100)
    if saturation > 0:
        facteur = np.clip((res[..., 0] + 2.0) / (o[..., 0] + 2.0), 0.6, 1.7) ** saturation
        sombre = 1.0 - np.clip((res[..., 0] - 60.0) / 20.0, 0.0, 1.0)  # 1 sous L=60, 0 au-dela de 80
        facteur = 1.0 + (facteur - 1.0) * sombre
        res[..., 1] *= facteur
        res[..., 2] *= facteur
    # Grain de couleur : celui de `neuf` REMPLACE celui d'`orig` (a hauteur de
    # `gain_c`) ; l'ajouter par-dessus doublait le detail colore deja present
    # (pores rougis). Une image fusionnee avec elle-meme reste identique.
    echelle = np.clip(fond_o / fond_k, 0.0, 1.5) * gain_c
    for c in (1, 2):
        grain_o = o[..., c] - cv2.GaussianBlur(o[..., c], (0, 0), sigma_c)
        grain_k = k[..., c] - cv2.GaussianBlur(k[..., c], (0, 0), sigma_c)
        res[..., c] = res[..., c] - gain_c * grain_o + echelle * grain_k
    return np.clip(cv2.cvtColor(res, cv2.COLOR_Lab2RGB) * 255, 0, 255).astype(np.uint8)


def _yeux_visibles(img, reperes) -> bool:
    """Les deux yeux sont-ils reellement visibles ? Derriere des lunettes noires
    ou dans une ombre profonde, la zone d'un oeil n'a aucun point aussi clair que
    la peau (ni blanc de l'oeil, ni reflet) : klein y dessinerait un oeil qui
    n'existe pas. Critere : le 95e centile de luminosite autour de chaque oeil
    atteint au moins la moitie de la luminosite mediane de la peau du nez."""
    import cv2
    import numpy as np

    gris = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gris.shape
    d_io = float(np.hypot(*(reperes[1] - reperes[0])))

    def zone(cx, cy, rx, ry):
        return gris[int(max(0, cy - ry)):int(min(h, cy + ry)) + 1, int(max(0, cx - rx)):int(min(w, cx + rx)) + 1]

    peau = zone(reperes[2][0], reperes[2][1], 0.18 * d_io, 0.18 * d_io)
    if peau.size == 0:
        return False
    seuil = 0.5 * float(np.median(peau))
    return all(z.size > 0 and float(np.percentile(z, 95)) >= seuil
               for z in (zone(x, y, 0.2 * d_io, 0.11 * d_io) for x, y in (reperes[0], reperes[1])))


# En deca, klein a change la structure d'ensemble de la zone des yeux (yeux
# dessines sur des lunettes noires, regard deplace...) : on n'applique rien.
_YEUX_CORRELATION_MIN = 0.80


def _detail_yeux(source, dest, reperes, chemin: Path, retouche, indice: int = 0) -> tuple[bool, float]:
    """Passe « yeux » d'un visage : la bande des deux yeux est agrandie (jusqu'a
    1 536 px de large), redessinee par FLUX.2-klein, recalee, puis fusionnee dans
    deux ellipses centrees sur les yeux : dessin de klein, couleurs d'origine.

    La passe « visage » ne garde de klein que le grain fin : un iris flou au
    depart restait baveux a 100 % (pupille indecise, bord de paupiere brouillon,
    constat du 10/11/2026). La bande est lue dans `source`, l'image AVANT la
    passe visage : relue apres, klein accentuait le grain de peau que cette
    passe depose sur le blanc de l'oeil. Le resultat est pose dans `dest` (RGB
    uint8, modifie sur place). `reperes` : les 5 points YuNet, en pixels.
    Retour : (appliquee, correlation des grandes masses entre l'original et
    klein dans la zone des yeux)."""
    import cv2
    import numpy as np
    from PIL import Image

    if not _yeux_visibles(source, reperes):
        return False, 0.0
    (xd, yd), (xg, yg) = reperes[0], reperes[1]
    d_io = float(np.hypot(xg - xd, yg - yd))
    h, w = source.shape[:2]
    cx, cy = (xd + xg) / 2, (yd + yg) / 2
    bw, bh = 2.3 * d_io, 1.0 * d_io + abs(yg - yd)
    x0, y0 = int(max(0, cx - bw / 2)), int(max(0, cy - bh / 2))
    x1, y1 = int(min(w, cx + bw / 2)), int(min(h, cy + bh / 2))
    if x1 - x0 < 64 or y1 - y0 < 32:
        return False, 0.0
    k = min(1536 / (x1 - x0), 3.0)
    cw, ch = int((x1 - x0) * k) // 16 * 16, int((y1 - y0) * k) // 16 * 16
    if cw < 256 or ch < 256:
        return False, 0.0
    bande = source[y0:y1, x0:x1].copy()
    ent = cv2.resize(bande, (cw, ch), interpolation=cv2.INTER_LANCZOS4 if k > 1 else cv2.INTER_AREA)
    entree = chemin.with_name(f"_{chemin.stem}_yeux{indice}.png")
    retour = chemin.with_name(f"_{chemin.stem}_yeux{indice}_k.png")
    modele, vae, encodeur = retouche
    Image.fromarray(ent).save(entree)
    try:
        proc = _executer_sd([str(_exe()), "--diffusion-model", str(modele), "--llm", str(encodeur),
                             "--vae", str(vae), "-r", str(entree), "-p", _CONSIGNE_YEUX,
                             "--steps", "4", "--cfg-scale", "1.0", "-W", str(cw), "-H", str(ch),
                             *_attention(), "-o", str(retour), "-s", "11"], 120.0)
        if proc.returncode != 0 or not retour.is_file():
            return False, 0.0
        with Image.open(retour) as r:
            kl = np.array(r.convert("RGB"))
    except subprocess.TimeoutExpired:
        return False, 0.0
    finally:
        entree.unlink(missing_ok=True)
        retour.unlink(missing_ok=True)
    if kl.shape[:2] != (ch, cw):
        kl = cv2.resize(kl, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
    kl = _recaler_dense(ent, kl, borne=0.04 * d_io * k)
    sigma = max(4.0, d_io * k / 30)
    # Deux ellipses (inclinees comme la tete) centrees sur les yeux, bord fondu.
    masque = np.zeros((y1 - y0, x1 - x0), np.float32)
    angle = float(np.degrees(np.arctan2(yg - yd, xg - xd)))
    for ex, ey in ((xd, yd), (xg, yg)):
        cv2.ellipse(masque, (int(ex - x0), int(ey - y0)), (max(2, int(0.36 * d_io)), max(2, int(0.24 * d_io))),
                    angle, 0, 360, 1.0, -1)
    zone = cv2.resize(masque, (cw, ch), interpolation=cv2.INTER_LINEAR) > 0.5
    masses = [cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), sigma)[zone]
              for a in (ent, kl)]
    correlation = float(np.corrcoef(masses[0], masses[1])[0, 1]) if zone.any() else 0.0
    if not correlation >= _YEUX_CORRELATION_MIN:
        return False, correlation
    fus = _fusion_structure(ent, kl, sigma_l=sigma, saturation=1.0)
    fus = cv2.resize(fus, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA if k > 1 else cv2.INTER_LANCZOS4)
    alpha = cv2.GaussianBlur(masque, (0, 0), max(1.0, 0.05 * d_io))[..., None]
    fond = dest[y0:y1, x0:x1].astype(np.float32)
    dest[y0:y1, x0:x1] = np.clip(fus.astype(np.float32) * alpha + fond * (1 - alpha), 0, 255).astype(np.uint8)
    return True, correlation


def _detail_visages(chemin: Path, prompt: str = "", max_visages: int = 4) -> int:
    """Passe « detail du visage » (equivalent ADetailer) sur l'image finale.

    Le generateur rend une peau lisse et cireuse a 100 % (image de 2 560 px
    agrandie en 4K). Chaque visage detecte (YuNet) est recadre, retravaille a
    1 536 px par FLUX.2-klein (pores, cils, meches), puis reintegre par
    SEPARATION DE FREQUENCES : couleurs, volumes et teinte des yeux viennent de
    l'original, seul le grain fin vient de klein (sans cela, klein changeait la
    couleur des yeux et ajoutait des taches, essai du 10/11/2026). Fondu
    elliptique, gain de detail 0,8. Puis passe « yeux » (`_detail_yeux`) pour
    les visages de face, sauf NEO_IMAGE_DETAIL_YEUX=0. Ignore : styles dessines,
    visages de moins de 160 px, klein absent, NEO_IMAGE_DETAIL_VISAGES=0.
    Retourne le nombre de visages traites ; tout echec laisse l'image intacte."""
    if os.environ.get("NEO_IMAGE_DETAIL_VISAGES", "1") == "0":
        return 0
    if any(m in (prompt or "").lower() for m in STYLE_MOTS + ("illustration", "painting", "3d render")):
        return 0
    retouche = _modele_retouche()
    if not retouche:
        return 0
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter

        from backend.tools.t_visage import _modele as _yunet

        yunet = _yunet()
        if yunet is None:
            return 0
        with Image.open(chemin) as im:
            img = np.array(im.convert("RGB"))
        h, w = img.shape[:2]
        echelle = 1280 / max(w, h)
        petit = cv2.resize(img, (int(w * echelle), int(h * echelle)))
        det = cv2.FaceDetectorYN.create(str(yunet), "", (petit.shape[1], petit.shape[0]), 0.7)
        _, faces = det.detect(cv2.cvtColor(petit, cv2.COLOR_RGB2BGR))
        if faces is None:
            return 0
        faces = sorted(faces, key=lambda f: -f[2] * f[3])[:max_visages]
        modele, vae, encodeur = retouche
        sortie, n = img.copy(), 0
        yeux_actifs = os.environ.get("NEO_IMAGE_DETAIL_YEUX", "1") != "0"
        n_yeux, correlations = 0, []
        for i, f in enumerate(faces):
            x, y, fw, fh = (f[:4] / echelle).astype(int)
            if max(fw, fh) < 160:
                continue
            c = max(fw, fh) * 2.0
            cx, cy = x + fw / 2, y + fh / 2
            x0, y0 = int(max(0, cx - c / 2)), int(max(0, cy - c / 2))
            x1, y1 = int(min(w, cx + c / 2)), int(min(h, cy + c / 2))
            crop = img[y0:y1, x0:x1]
            k = 1536 / max(crop.shape[:2])
            cw, ch = max(256, int(crop.shape[1] * k) // 16 * 16), max(256, int(crop.shape[0] * k) // 16 * 16)
            entree = chemin.with_name(f"_{chemin.stem}_visage{i}.png")
            retour = chemin.with_name(f"_{chemin.stem}_visage{i}_k.png")
            Image.fromarray(crop).resize((cw, ch), Image.LANCZOS).save(entree)
            try:
                proc = _executer_sd([str(_exe()), "--diffusion-model", str(modele), "--llm", str(encodeur),
                                     "--vae", str(vae), "-r", str(entree), "-p", _CONSIGNE_VISAGE,
                                     "--steps", "4", "--cfg-scale", "1.0", "-W", str(cw), "-H", str(ch),
                                     *_attention(), "-o", str(retour), "-s", "11"], 120.0)
                if proc.returncode != 0 or not retour.is_file():
                    continue
                with Image.open(retour) as r:
                    neuf = np.array(r.convert("RGB").resize((x1 - x0, y1 - y0), Image.LANCZOS))
            except subprocess.TimeoutExpired:
                continue
            finally:
                entree.unlink(missing_ok=True)
                retour.unlink(missing_ok=True)
            ancien = crop.astype(np.float32)
            # Recalage local (klein decale legerement les contours), puis grain
            # fin de klein sur les volumes et les couleurs de l'original.
            rayon = max(2.0, (x1 - x0) / 400)
            neuf = _recaler_dense(crop, neuf, borne=max(4.0, (x1 - x0) / 150))
            neuf = _fusion_structure(crop, neuf, sigma_l=rayon, sigma_c=rayon,
                                     gain_l=1.0, gain_c=0.8).astype(np.float32)
            masque = Image.new("L", (x1 - x0, y1 - y0), 0)
            m = int(min(x1 - x0, y1 - y0) * 0.12)
            ImageDraw.Draw(masque).ellipse((m, m, x1 - x0 - m, y1 - y0 - m), fill=255)
            alpha = np.array(masque.filter(ImageFilter.GaussianBlur(m * 0.8))).astype(np.float32)[..., None] / 255
            sortie[y0:y1, x0:x1] = np.clip(neuf * alpha + ancien * (1 - alpha), 0, 255).astype(np.uint8)
            n += 1
            # Yeux : seulement de face (les deux yeux visibles et assez ecartes),
            # et pour les deux plus grands visages (~11 s chacun).
            reperes = (f[4:14] / echelle).reshape(5, 2)
            ecart = float(np.hypot(*(reperes[1] - reperes[0])))
            if yeux_actifs and i < 2 and ecart >= 0.28 * fw:
                fait, correlation = _detail_yeux(img, sortie, reperes, chemin, retouche, i)
                n_yeux += int(fait)
                correlations.append(round(correlation, 3))
        if n:
            _journal_sd("info", "detail visages", visages=n, yeux=n_yeux, correlations_yeux=correlations)
            if chemin.suffix.lower() in (".jpg", ".jpeg"):
                Image.fromarray(sortie).save(chemin, quality=95, subsampling=0)
            else:
                Image.fromarray(sortie).save(chemin)
        return n
    except Exception as exc:
        _journal_sd("avertissement", "detail visages impossible", erreur=str(exc)[:200])
        return 0


def _modele_retouche() -> tuple[Path, Path, Path] | None:
    """FLUX.2-klein-4B pour la retouche : (modele, vae, encodeur Qwen3-4B) ou None.

    Banc du 09/11/2026 (`eval/bench_retouche.py`, 3 retouches) : klein fait la
    retouche demandee en gardant visage, pose et cadrage, en 8 a 16 s ; FLUX
    Kontext (2 passes) recreait la scene (autre visage, autre cadrage, galaxie
    disparue) en 140 a 218 s. Dossier a part (`models_edit`) : ces fichiers ne
    doivent jamais etre pris pour un modele de generation.

    Comme les modeles de generation, la copie du SSD passe d'abord
    (`<tools>/sd_ssd/models_edit`, 4,5 Go) : klein sert a chaque portrait (passes
    visage et yeux) et un premier chargement depuis le disque d'archives coute
    ~35 s. `NEO_SD_RETOUCHE` impose un dossier.
    """
    force = os.environ.get("NEO_SD_RETOUCHE", "").strip()
    if force:
        dossiers = [Path(force)]
    else:
        rapide = Path(os.environ.get("LOCALAI_SD_RAPIDE", str(_dir().parent / "sd_ssd" / "models")))
        dossiers = [rapide.parent / "models_edit", _dir() / "models_edit"]
    encodeur = _encodeur_zimage()
    for dossier in dossiers:
        if not dossier.is_dir() or not encodeur:
            continue
        modele = next(iter(sorted(dossier.glob("flux-2-klein*.gguf"))), None)
        vae = dossier / "flux2-vae.safetensors"
        if modele and vae.is_file():
            return modele, vae, encodeur
    return None


def _retoucher_klein(consigne: str, sortie: str, reference: str, cote: int = 1536) -> str:
    """Retouche en une passe FLUX.2-klein (4 etapes), au format de la reference.

    La consigne est passee telle quelle (l'encodeur Qwen3 comprend le francais),
    avec la demande de garder le reste identique. Appele sous le verrou GPU."""
    modele, vae, encodeur = _modele_retouche()
    debut = time.time()
    ref = Path(reference).resolve()
    out = Path(sortie)
    if out.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        out = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with Image.open(ref) as im:
            w, h = im.size
    except Exception as exc:
        return f"Erreur: image de reference illisible ({exc})"
    k = cote / max(w, h)
    largeur, hauteur = max(256, int(w * k) // 16 * 16), max(256, int(h * k) // 16 * 16)
    texte = consigne.strip().rstrip(".")
    if not re.search(r"identique|identical|reste|unchanged", texte, re.I):
        texte += ". Garde tout le reste exactement identique."
    _liberer_vram()
    cmd = [str(_exe()), "--diffusion-model", str(modele), "--llm", str(encodeur), "--vae", str(vae),
           "-r", str(ref), "-p", texte, "--steps", "4", "--cfg-scale", "1.0",
           "-W", str(largeur), "-H", str(hauteur), "--diffusion-fa", "--vae-tiling", "-o", str(out)]
    try:
        proc = _executer_sd(cmd, float(os.environ.get("NEO_SD_DELAI_S", "300")))
    except subprocess.TimeoutExpired:
        return "Erreur: la retouche a depasse le delai (sd-cli arrete)."
    if proc.returncode != 0 or not out.is_file():
        return f"Erreur: retouche impossible ({(proc.stderr or proc.stdout or '')[-300:]})"
    _version_ecran(out)
    duree = round(time.time() - debut, 1)
    _journal_sd("info", "image retouchee", duree_s=duree, modele="flux2-klein",
                largeur=largeur, hauteur=hauteur, fichier=str(out))
    ko = out.stat().st_size // 1024
    return (f"Image generee: {out} ({ko} Ko) [{modele.name}, retouche 4 steps, {largeur}x{hauteur}] "
            f"[temps reel: {duree}s]")


def generer(
    prompt: str,
    sortie: str,
    negatif: str = "",
    largeur: int = 0,
    hauteur: int = 0,
    steps: int = 0,
    graine: int = -1,
    traduire: bool = True,
    resolution: str = "1024_upscale",  # "1024_upscale" (défaut, <60s) | "4k_natif" (choix explicite) | "hd"
    reference: str = "",
    reference_desc: str = "",
    mode: str = "creation",
    settings=None,
    modele_prefere: str = "",
    base_max: tuple[int, int] | None = None,
    cadrage: str = "paysage",
    apres_gpu=None,
    brut: bool = False,
) -> str:
    """Genere une image (defaut : 4K 3840x2160, photorealiste). Retourne un
    message outil (chemin PNG ou erreur).

    cadrage : "paysage" (16:9), "portrait" (9:16) ou "carre".
    Une seule generation a la fois (verrou global) : file d'attente implicite,
    evite les OOM quand deux demandes d'image arrivent en parallele.
    mode="edit" : modifie l'image de reference (img2img Kontext) au lieu de
    recréer une scene autour de son sujet.
    apres_gpu : fonction appelee des que la carte graphique ne sert plus
    (avant la finition), pour y recharger autre chose sans attendre.
    brut : image de travail (premiere image d'un plan video) : ni passes
    visage/yeux, ni finition (grain), ni version ecran.
    """
    lock = _acquerir_verrou_gpu()
    if lock is None:
        return (
            "Erreur: le generateur d'images est occupe par une autre demande "
            "depuis plus d'une heure. Reessayez plus tard."
        )
    try:
        _nettoyer_orphelins()
        if mode == "edit" and reference and _modele_retouche():
            return _retoucher_klein(prompt, sortie, reference)
        return _generer_nolock(
            prompt, sortie, negatif=negatif, largeur=largeur, hauteur=hauteur,
            steps=steps, graine=graine, traduire=traduire, resolution=resolution,
            reference=reference, reference_desc=reference_desc, mode=mode,
            settings=settings, modele_prefere=modele_prefere, base_max=base_max,
            cadrage=cadrage, apres_gpu=apres_gpu, brut=brut,
        )
    finally:
        try:
            lock.close()
        except Exception:
            pass


def _generer_nolock(
    prompt: str,
    sortie: str,
    negatif: str = "",
    largeur: int = 0,
    hauteur: int = 0,
    steps: int = 0,
    graine: int = -1,
    traduire: bool = True,
    resolution: str = "1024_upscale",  # "1024_upscale" (défaut, <60s) | "4k_natif" (choix explicite) | "hd"
    reference: str = "",
    reference_desc: str = "",
    mode: str = "creation",
    settings=None,
    modele_prefere: str = "",
    base_max: tuple[int, int] | None = None,
    cadrage: str = "paysage",
    apres_gpu=None,
    brut: bool = False,
) -> str:
    """Genere une image (defaut : 4K 3840x2160, photorealiste). Retourne un
    message outil (chemin PNG ou erreur).

    Pipeline : generation SD en base 16:9 (1024x576, cout VRAM identique a
    768x768) puis upscale x4 Real-ESRGAN (repli : Lanczos) vers 3840x2160.
    resolution="hd" desactive l'upscale (image base directe).

    reference : chemin d'une image locale de reference. Si un modele FLUX
    Kontext est installe, il est utilise (le sujet de la reference est
    preserve : forme, couleurs, proportions fidele au vrai sujet).
    """
    st = status()
    if not st["pret"]:
        return (
            "Generateur d'images non installe. Installez-le depuis "
            "Reglages > Generation d'images (bouton Installer), ou definissez "
            "LOCALAI_SD_DIR vers un dossier contenant bin/sd.exe et models/*.gguf."
        )
    debut = time.time()
    refus_ext = _extension_bloquee(sortie)
    if refus_ext:
        return refus_ext
    refus = _verifier_acces(sortie, ecriture=True)
    if refus:
        return refus
    out_final = Path(sortie).expanduser().resolve()
    out_final.parent.mkdir(parents=True, exist_ok=True)
    out = out_final

    modele = _modele()
    # Mode qualite max : forcer le modele le plus precis disponible (Q8 > Q4).
    if modele_prefere:
        cand = _fichier_modele(modele_prefere)
        if cand:
            modele = cand
    t = _type_modele(modele)
    ref_ok = Path(reference).is_file() if reference else False
    if ref_ok:
        # Fidelite sujet : FLUX Kontext conditionne par l'image de reference.
        kontext = _modele_kontext()
        if kontext:
            modele = kontext
            t = "flux"
    if largeur <= 0:
        largeur = _resolution_defaut(modele)
    if hauteur <= 0:
        hauteur = _resolution_defaut(modele)
    if steps <= 0:
        steps = _steps_defaut(modele)
    # Garde-fou : un modele Turbo n'a pas besoin de >12 steps (20-50 degrade
    # le rendu et ralentit inutilement).
    if t == "turbo":
        steps = min(steps, 12)
    if not negatif:
        # Style graphique demande (dessin, manga...) : on ne combat pas ce
        # style avec le negatif anti-dessin du photorealisme.
        stylise = any(w in (prompt or "").lower() for w in STYLE_MOTS)
        negatif = NEGATIF_QUALITE if stylise else DEFAULT_NEGATIF
    prompt_final = preparer_prompt(prompt, traduire=traduire, reference_desc=reference_desc,
                                   flux=(t == "flux"))

    kontext_actif = ref_ok and t == "flux" and "kontext" in modele.name.lower()
    format_cadrage = CADRAGES.get(cadrage, CADRAGES["paysage"])

    # Pipeline selon resolution:
    # - "1024_upscale" (défaut) : base 1024/16:9 -> upscale x4 Real-ESRGAN -> 3840x2160 (<60s)
    # - "4k_natif" : generation directe 3840x2160 (150-400s, choix explicite)
    # - "hd" : base native sans upscale (compatibilité)
    do_upscale = False
    if resolution == "1024_upscale":
        do_upscale = True
        if base_max:
            largeur, hauteur = base_max
        elif t in ("sdxl", "flux"):
            # Base exacte du cadrage : 1536x864 en 16:9 (l'ancien 1344x768
            # sortait en 3780x2160), 864x1536 en portrait, 1152 en carre.
            largeur, hauteur = format_cadrage["base"]
        else:
            largeur, hauteur = (1024, 576) if cadrage == "paysage" else (
                (576, 1024) if cadrage == "portrait" else (768, 768))
        if kontext_actif:
            largeur, hauteur = format_cadrage["kontext"]
        out = out_final.with_name(out_final.stem + "_base.png")
    elif resolution == "4k_natif":
        # Meme chaine que le defaut pour FLUX (base 1536x864 + passe 2560x1440 :
        # une generation directe en 4K est plus douce, FLUX lissant au-dela de
        # ~4 Mpx) ; plus grande base 16:9 pour les autres modeles.
        largeur, hauteur = (format_cadrage["base"] if t == "flux" and not kontext_actif
                            else (2048, 1152))
        do_upscale = True
        out = out_final.with_name(out_final.stem + "_base.png")
    # sinon "hd" ou autre : resolution native du modele sans upscale


    # Passe haute resolution FLUX, dans le MEME processus (un seul chargement) :
    # la base 1536x864 est agrandie (Lanczos) en 2560x1440 puis re-debruitee
    # sur l'image ENTIERE. C'est ce qui ajoute du VRAI detail (pores, fils,
    # panneaux de coque) ; l'upscaler seul ne fait qu'inventer de la texture.
    #  - Sigmas IMPOSES (0,30 -> 0) : FLUX decale son calendrier de bruit avec
    #    la resolution ; une « force 0,32 » valait un bruit quasi total en
    #    2560x1440 et la scene etait reinventee (mesure : ecart moyen 84/255
    #    contre 6-8/255 avec les sigmas imposes).
    #  - Image entiere et non par tuiles : chaque tuile inventait son propre
    #    sujet (visages en double dans un portrait).
    #  - 2560x1440 et pas 3840x2160 : au-dela de ~4 Mpx FLUX lisse au lieu
    #    d'affiner (passe 4K testee : plus douce, +110 s).
    _hires_env = os.environ.get("LOCALAI_SD_HIRES", "1").strip().lower()
    # Passe reservee a schnell : sur les FLUX dev (Krea compris) elle cree un
    # quadrillage visible a partir de ~2 Mpx.
    hires_flux = bool(t == "flux" and do_upscale
                      and ("schnell" in modele.name.lower() or _est_zimage(modele))
                      and not kontext_actif
                      and _hires_env not in ("0", "off", "non", "false", "no"))
    hires_cible = format_cadrage["hires"]
    # 2 etapes (0,30 -> 0,15 -> 0) : rendu identique a 3 etapes a 100 %, 8 s de moins (A/B du 09/11/2026).
    hires_sigmas = os.environ.get("LOCALAI_SD_HIRES_SIGMAS", "0.30,0.15,0.0")
    # ETA calibree sur la configuration REELLE (base adaptee + passe).
    passe = "h2560" if hires_flux else ""
    temps_estime = _estimer_temps_generation(
        t, resolution, largeur, hauteur, steps, kontext_actif, modele, passe)

    def _cmd_flux(modele_p: Path, prompt_p: str, sortie_p: Path,
                  reference_p: str = "", guidance_p: float = 0.0) -> list[str]:
        cmd = [str(_exe())]
        if reference_p:
            cmd += ["-r", reference_p]
        cmd += [
            "--diffusion-model", str(modele_p),
            "-p", prompt_p,
            "--width", str(max(256, min(largeur, 2048))),
            "--height", str(max(256, min(hauteur, 2048))),
            "--steps", str(max(2, min(steps, 60))),
            "-o", str(sortie_p),
        ]
        for flag, f in _encodeurs_modele(modele_p):
            cmd += [flag, str(f)]
        # schnell/kontext : guidance distillée -> cfg-scale 1.0 (le prompt
        # negatif est alors sans effet : on ne le transmet pas).
        cmd += ["--sampling-method", "euler", "--cfg-scale", "1.0", "--diffusion-fa"]
        if hires_flux:
            cmd += ["--hires", "--hires-width", str(hires_cible[0]),
                    "--hires-height", str(hires_cible[1]),
                    "--hires-upscaler", "Lanczos", "--hires-sigmas", hires_sigmas,
                    # Decodage par tuiles : 37 s au lieu de 46 s et moins de VRAM
                    # en 2560x1440, sans couture visible (mesure du 09/11/2026).
                    "--vae-tiling"]
        if guidance_p > 0:
            cmd += ["--guidance", str(guidance_p)]
        if graine >= 0:
            cmd += ["--seed", str(graine)]
        return cmd

    kontext_chain = False
    cmds: list[list[str]] = []
    if t == "flux":
        if kontext_actif:
            # Fidelite au sujet reel : chaine Kontext en 2 passes —
            # 1) le sujet de la reference est preserve, la scene s'introduit ;
            # 2) la scene se finalise, le sujet reste identique (derive minime).
            if mode == "edit":
                # img2img : l'image de reference EST le sujet a modifier.
                p1 = f"Edit this image: {prompt_final}"
                p2 = f"Refine the edit, keep everything else identical: {prompt_final}"
            else:
                desc = (reference_desc or "").strip()[:300]
                if desc:
                    p1 = (f"the same subject as in the reference image: {desc}, "
                          f"{prompt_final}")
                    p2 = (f"keep the subject exactly identical to the reference: "
                          f"{desc}, {prompt_final}")
                else:
                    p1 = f"the same subject as in the reference image, {prompt_final}"
                    p2 = (f"keep the subject exactly identical to the reference image, "
                          f"{prompt_final}")
            tmp1 = out.with_name(out.stem + "_k1.png")
            cmds = [
                _cmd_flux(modele, p1, tmp1, str(Path(reference).resolve()), 3.5),
                _cmd_flux(modele, p2, out, str(tmp1), 3.5),
            ]
            kontext_chain = True
        else:
            cmds = [_cmd_flux(modele, prompt_final, out, guidance_p=_guidance_flux(modele))]
    else:
        cmd = [
            str(_exe()),
            "-m", str(modele),
            "-p", prompt_final,
            "--width", str(max(256, min(largeur, 2048))),
            "--height", str(max(256, min(hauteur, 2048))),
            "--steps", str(max(2, min(steps, 60))),
            "-o", str(out),
        ]
        cmds = [cmd]
    hires = False
    if t == "turbo":
        cmds[0] += ["--cfg-scale", "1.0"]
    elif t != "flux":
        # SDXL/SD1.5 photorealiste : dpm++2m karras + hires fix (2e passe
        # haute resolution qui ajoute le vrai detail « comme devant soi »).
        cmds[0] += ["--sampling-method", "dpm++2m", "--scheduler", "karras",
                    "--cfg-scale", "5.5", "--vae-tiling"]
        if t == "sdxl":
            cmds[0] += ["--hires", "--hires-scale", "1.5",
                        "--hires-denoising-strength", "0.4",
                        "--hires-steps", "10"]
            hires = True
    if t != "flux":
        # FLUX recoit deja le negatif via _cmd_flux ; SDXL/SD1.5/Turbo ici
        # (l'ancien test `if not negatif` etait toujours faux : jamais passe).
        cmds[0] += ["--negative-prompt", negatif]
    if graine >= 0 and t != "flux":
        cmds[0] += ["--seed", str(graine)]

    # VRAM : on decharge le LLM avant la generation (les 48 Go de RAM restent
    # dispo pour l'offload CPU des gros modeles FLUX).
    _liberer_vram()

    # Prechauffage cache : lecture sequentielle des modeles (mmap de sd-cli
    # est lente a froid sur QLC ; le cache Windows rend les faults instantanees).
    fichiers_chauds = [str(modele)]
    if t == "flux":
        fichiers_chauds += [str(f) for _, f in _encodeurs_modele(modele)]
    if kontext_chain:
        fichiers_chauds.append(str(Path(reference).resolve()))
    _prechauffer_cache(fichiers_chauds)

    # Delai par passe sd-cli : bien en dessous des 600 s du pont Rust, pour
    # qu'un essai bloque laisse le temps d'un essai de secours. Une passe
    # saine dure 30-60 s ; au-dela de 300 s, la carte deborde en memoire
    # partagee et l'attente ne sert plus a rien.
    delai_passe = float(os.environ.get("NEO_SD_DELAI_S", "300"))

    def _run(c: list[str]):
        return _executer_sd(c, delai_passe)

    proc = None
    if kontext_chain:
        tmp1 = Path(cmds[0][cmds[0].index("-o") + 1])
        try:
            p1_proc = _run(cmds[0])
        except subprocess.TimeoutExpired:
            return f"Erreur: generation trop longue (>{int(delai_passe)} s)"
        if p1_proc.returncode != 0 or not tmp1.exists():
            # Passe 1 impossible : repli sur le modele FLUX par defaut
            # (schnell), sans reference. Relancer Kontext sans image de
            # reference n'avait aucun sens.
            repli = _modele()
            if _type_modele(repli) != "flux":
                proc = p1_proc
            else:
                try:
                    proc = _run(_cmd_flux(repli, prompt_final, out,
                                          guidance_p=_guidance_flux(repli)))
                except subprocess.TimeoutExpired:
                    return f"Erreur: generation trop longue (>{int(delai_passe)} s)"
        else:
            try:
                proc = _run(cmds[1])
            except subprocess.TimeoutExpired:
                proc = None
            if proc is None or proc.returncode != 0 or not out.exists():
                # Passe 2 ratee : on livre la passe 1 (sujet fidele).
                shutil.copy2(tmp1, out)
                proc = p1_proc
            tmp1.unlink(missing_ok=True)
    else:
        try:
            proc = _run(cmds[0])
        except subprocess.TimeoutExpired:
            # Carte saturee (autre programme, memoire partagee) : essai de
            # secours economique — modele Q4, encodeur de texte sur le CPU.
            secours = _commande_secours(cmds[0], t)
            if not secours:
                return f"Erreur: generation trop longue (>{int(delai_passe)} s), carte graphique saturee"
            _journal_sd("avertissement", "passe sd trop longue, essai de secours",
                        delai_s=delai_passe)
            _liberer_vram()
            try:
                proc = _executer_sd(secours, 200)
            except subprocess.TimeoutExpired:
                return ("Erreur: generation trop longue meme en mode economique "
                        "(carte graphique saturee par un autre programme ?)")
        if (proc.returncode != 0 or not out.exists()) and (hires_flux or hires):
            # Repli : meme commande sans la passe haute resolution (build qui
            # refuse --hires sur FLUX, ou OOM en SDXL). Les options sont
            # retirees par paires drapeau+valeur : l'ancien filtre par VALEUR
            # supprimait aussi « --steps 10 ».
            try:
                proc = _run(_sans_hires(cmds[0]))
            except subprocess.TimeoutExpired:
                return f"Erreur: generation trop longue (>{int(delai_passe)} s)"
    if proc.returncode != 0 or not out.exists():
        err = (proc.stderr or proc.stdout or "")[-800:]
        return f"Erreur generation image (code {proc.returncode}): {err}"

    etapes = _etapes_sd((proc.stdout or "") + (proc.stderr or "")) if proc else {}
    etapes["sd_total"] = round(time.time() - debut, 1)
    t_etape = time.time()

    # Upscale 4K : Real-ESRGAN x4 (sinon repli Lanczos vers 3840x2160).
    if do_upscale and out != out_final:
        if _upscale_4k(out, out_final) or _resize_lanczos(out, out_final, 3840, 3840):
            out.unlink(missing_ok=True)
            out = out_final

    # Finition sur le master final : colorimetrie, micro-contraste, nettete,
    # saturation adaptative et grain. Un echec laisse le fichier intact.
    etapes["agrandissement"] = round(time.time() - t_etape, 1)
    t_etape = time.time()
    if mode != "edit" and not brut:
        n_visages = _detail_visages(out, prompt)
        if n_visages:
            etapes["detail_visages"] = round(time.time() - t_etape, 1)
            print(f"[sd] detail visages : {n_visages} visage(s) en {etapes['detail_visages']}s", flush=True)
        t_etape = time.time()
    # La carte ne sert plus : finition et version ecran sont du calcul
    # ordinaire. L'appelant peut deja y remettre le modele de conversation.
    if apres_gpu is not None:
        try:
            apres_gpu()
        except Exception:
            pass
    if not brut:
        _finition(out, palette="auto", prompt=prompt, graine=graine)
        etapes["finition"] = round(time.time() - t_etape, 1)
        t_etape = time.time()
        _version_ecran(out)
        etapes["version_ecran"] = round(time.time() - t_etape, 1)

    try:
        from PIL import Image

        with Image.open(out) as im:
            dims = f"{im.width}x{im.height}"
    except Exception:
        dims = f"{largeur}x{hauteur}"
    ko = out.stat().st_size // 1024
    duree = round(time.time() - debut, 1)
    _enregistrer_duree(
        _cle_eta(t, modele, largeur, hauteur, steps, kontext_actif, passe), duree)
    _journal_sd("info", "image generee", duree_s=duree, modele=t,
                steps=steps, largeur=largeur, hauteur=hauteur, fichier=str(out), etapes=etapes)
    return (
        f"Image generee: {out} ({ko} Ko) "
        f"[{modele.name}, {steps} steps"
        f"{', passe {}x{}'.format(*hires_cible) if hires_flux else ''}, {dims}] "
        f"[temps reel: {duree}s / estimation: {temps_estime}]"
    )


def generer_auto(
    prompt: str,
    negatif: str = "",
    largeur: int = 0,
    hauteur: int = 0,
    steps: int = 0,
    graine: int = -1,
    resolution: str = "1024_upscale",
    traduire: bool = True,
    reference: str = "",
    reference_desc: str = "",
    mode: str = "creation",
    settings=None,
    save_path: str = "",
    qualite: str = "standard",
    format_sortie: str = "png",
) -> dict:
    """Genere une image et la range dans le dossier Documents de Neo.

    Point d'entree des routes HTTP (Rust et Python) : il choisit le dossier
    (reglage `neo_documents_dir`, repli `data/`), fabrique un nom horodate,
    appelle `generer()` puis renvoie un dictionnaire pret a serialiser.

    qualite="standard" (defaut) : FLUX schnell (Q8 si la carte a >= 14 Go,
    sinon Q4), base 1536x864, passe haute resolution 2560x1440 a sigmas
    imposes, 4K par moyenne Real-ESRGAN/Lanczos, finition legere.
    Mesure RTX 5080 : ~75 s de bout en bout, modele en cache.
    qualite="rapide" : force FLUX schnell en 6 etapes, meme si un modele
    dev a ete impose par LOCALAI_SD_MODEL.
    LOCALAI_SD_HIRES=0 desactive la passe haute resolution.

    Retour : {ok, message, path, url, duree_s, nom, dossier}.
    """
    from urllib.parse import quote

    debut = time.time()
    cfg = _cfg(settings)
    dossier = _dossier_sortie(cfg)
    if dossier is None:
        # Repli historique : data/ a cote du projet.
        base = os.environ.get("LOCALAI_DATA_DIR", "")
        dossier = Path(base) if base else Path(_dir().parents[1]) / "data"
    try:
        dossier.mkdir(parents=True, exist_ok=True)
    except OSError:
        dossier = Path(os.environ.get("TEMP", ".")) / "neo_images"
        dossier.mkdir(parents=True, exist_ok=True)

    if save_path:
        sortie = Path(save_path)
        if sortie.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".exr"):
            sortie = sortie.with_suffix(".png")
        # Controle d'acces AVANT toute creation de dossier : un chemin refuse
        # ne doit laisser aucune trace sur le disque.
        refus = _verifier_acces(sortie, ecriture=True)
        if refus:
            return {"ok": False, "message": refus, "path": "", "nom": sortie.name,
                    "dossier": str(sortie.parent), "duree_s": 0.0, "url": ""}
        sortie.parent.mkdir(parents=True, exist_ok=True)
        nom = sortie.name
        dossier = sortie.parent
    else:
        nom = f"neo_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.png"
        sortie = dossier / nom

    modele_prefere = ""
    base_max = None
    qual = (qualite or "").lower()
    if qual in ("max", "ultra"):
        base_max = (1536, 864)
    if qual == "rapide":
        # Brouillon : FLUX schnell (6 etapes au lieu de 28) quand la qualite
        # maximale n'est pas demandee.
        schnell = sorted((f for f in _fichiers_modeles()
                          if f.name.lower().startswith("flux1-schnell")),
                         key=lambda f: ("q8" not in f.name.lower(), f.name))
        if schnell:
            modele_prefere = schnell[0].name
            steps = steps or 6

    message = generer(
        prompt, str(sortie), negatif, largeur, hauteur, steps, graine,
        traduire, resolution, reference, reference_desc, mode, cfg,
        modele_prefere=modele_prefere, base_max=base_max,
    )
    # Format de sortie demande par l'utilisateur (modal "choisir le format").
    # La generation ecrit toujours un PNG ; on convertit ensuite sans perte
    # de qualite visible (voir _sauver_image).
    fmt = (format_sortie or "png").lower().lstrip(".")
    if fmt in ("jpg", "jpeg", "webp", "tiff", "tif") and sortie.is_file():
        cible = sortie.with_suffix("." + ("jpg" if fmt == "jpeg" else fmt))
        if cible.suffix.lower() != sortie.suffix.lower():
            original = sortie
            try:
                from PIL import Image as _Image

                with _Image.open(original) as source:
                    _sauver_image(source.convert("RGB"), cible)
                if cible.is_file():
                    sortie = cible
                    nom = cible.name
                    try:
                        original.unlink()
                    except OSError:
                        pass
            except Exception:
                # Repli explicite : on garde le PNG si la conversion echoue.
                pass

    duree = round(time.time() - debut, 1)
    ok = sortie.is_file()
    chemin = str(sortie.resolve()) if ok else ""
    return {
        "ok": ok,
        "message": message,
        "path": chemin,
        "nom": nom,
        "dossier": str(dossier),
        "duree_s": duree,
        "url": f"/api/sd/file?path={quote(chemin)}" if ok else "",
    }
