"""Direction artistique de Neo : comprendre une demande d'image avant de la peindre.

Le generateur (FLUX) ne comprend ni les fautes (« lazar », qui designait un quasar,
« science diction »), ni les references (« type Interstellar »), ni les
intentions de mise en scene (« effet 3D plongeant »). Le 09/11/2026, ces
demandes ont donne un vaisseau dans un hangar sans laser, puis un trou noir
plat sans le trou de ver demande.

`brief()` confie la demande au meilleur generaliste local, qui rend un brief
STRUCTURE (sortie JSON contrainte par schema) :
- ce qu'il a compris, en francais, pour que Neo le dise a l'utilisateur ;
- les fautes corrigees ;
- le style et le cadrage (paysage, portrait, carre) ;
- les elements qui DOIVENT etre visibles (controles ensuite par la vision) ;
- le prompt FLUX : anglais, phrases naturelles, sujet d'abord, plans
  avant/milieu/fond, optique, lumiere, matieres.

`verifier()` demande au modele de vision si chaque element est visible.
Tout est best-effort : en cas d'echec, la generation continue avec la
demande d'origine (enrichie par l'ancien chemin de sdgen).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time

from backend.core.config import contexte_pour
from pathlib import Path

# Le premier installe fait le brief. gemma4:12b d'abord : c'est le modele de
# conversation, DEJA en VRAM quand l'agent Image appelle l'outil. Mesure du
# 10/11/2026 : qwen3:14b coutait 35 s par image dont 24 s de chargement (il
# chassait Gemma de la carte, qui devait revenir en 26 s pour la verification).
# Banc `eval/bench_brief.py` (12 demandes dont 7 pieges) : gemma4 sans reflexion
# 12/12 en 3,8 s ; gemma4 avec reflexion 12/12 en 8,7 s ; qwen3:14b avec
# reflexion 12/12 en 10,8 s, chargement non compris.
MODELES_BRIEF = ("gemma4:12b", "qwen3:14b", "gemma3:12b", "qwen3:8b", "llama3.1:8b")
# Vision : gemma4:12b (29/29 elements, aucun piege accepte, ~1 s par image,
# deja charge pour la conversation), sinon qwen2.5vl:7b. NEO_IMAGE_VISION impose.
MODELES_VISION = ("gemma4:12b", "qwen2.5vl:7b")
MODELE_VISION = os.environ.get("NEO_IMAGE_VISION", "")
CADRAGES = ("paysage", "portrait", "carre")
STYLES = ("photo", "cinema", "illustration", "peinture", "anime", "3d", "pixel", "logo", "autre")

SCHEMA_BRIEF = {
    "type": "object",
    "properties": {
        # Ecrite EN PREMIER : le modele raisonne avant de remplir le reste
        # (les champs sont generes dans l'ordre du schema).
        "analyse": {"type": "string"},
        "ambiguites": {"type": "array", "items": {"type": "string"}},
        "comprehension": {"type": "string"},
        "corrections": {"type": "array", "items": {"type": "string"}},
        "style": {"type": "string", "enum": list(STYLES)},
        "cadrage": {"type": "string", "enum": list(CADRAGES)},
        "elements": {"type": "array", "items": {"type": "string"}},
        "prompt": {"type": "string"},
        "titre": {"type": "string"},
    },
    "required": ["analyse", "ambiguites", "comprehension", "corrections", "style", "cadrage", "elements", "prompt", "titre"],
}

CONSIGNE = """You are the art director of Neo, a local AI that creates stunning images with the FLUX model.
The user writes in French, often quickly, with typos and phonetic spellings. Your job: understand exactly what they want, then write the brief that makes FLUX produce a breathtaking, faithful image.

UNDERSTAND: reason before you write (field "analyse", in English, 4 to 10 sentences)
1. THEME: name the semantic field of the whole request (astronomy, fantasy war, barbershop, cars, food...). Every word is read inside this theme.
2. WORDS: find each word that is misspelled, phonetic, rare or ambiguous. For each, list 2 to 4 candidates that are close in spelling OR sound (French pronunciation), then pick the one that fits the THEME and the other words of the sentence. A candidate that belongs to the theme beats a more common word that does not. Example of the method (not a rule): "un vaisseau qui apercoit un pulsart" -> candidates pulsar / pulse / poussiere; theme astronomy -> pulsar. The same misspelled word can mean different things in different requests; never apply a fixed translation.
3. INTENT: what must the viewer feel and see first? Which element is the hero, which ones support it?
4. CHECK: re-read the request word by word and make sure every element is in your plan, with the meaning chosen in step 2.
- "ambiguites": one entry per word you had to interpret, in French: "« mot » : choix retenu (raison) — autres lectures possibles : x, y". Empty list if the request is unambiguous.
- "corrections": the plain fixes as "mot -> correction" (empty list if none). Only words that really appear in the request.
- In science and astronomy, keep the real scientific meaning and look: a quasar is an extremely bright galactic core with two opposite relativistic jets and a glowing accretion disk; a pulsar is a spinning neutron star with two narrow beams of light; a wormhole is a spherical lens-like tunnel that shows another starfield; a black hole is a black shadow ringed by a thin glowing accretion disk bent by gravitational lensing.
- Read French slang and trade words in context: on a person, "un dégradé" is a fade haircut ("dégradé au laser" = a sharp skin fade with laser-precise razor lines); "une meuf/un mec" = a woman/a man; "stylé" = stylish.
- Keep EVERY element the user asked for. Never drop one, never replace it with something else.
- A reference to a film, game, series or artist ("du type Interstellar", "facon Blade Runner") means its visual signature: translate it into concrete visuals (for Interstellar: Gargantua, a black hole whose glowing accretion disk bends above and below its black shadow by gravitational lensing, the ring-shaped Endurance spacecraft, vast scale, IMAX realism). You may also name the reference.
- "3D", "effet 3D", "plongeant", "profondeur", "immersif" mean a strong sense of depth, not a 3D render: very wide lens (14-24 mm), dramatic perspective, one element very close to the camera in the foreground, leading lines to a vanishing point, layered atmosphere and volumetric light.
- "photo", "photoreal", "realiste", "4K" or no style at all: a real photograph (style "photo"), or a film still (style "cinema") for epic, sci-fi, fantasy or movie-like scenes. Only use another style when the user names one (dessin, cartoon, manga, peinture, pixel art, logo, rendu 3D).
- Keep it tasteful: "sexy" means attractive and confident, never nudity; violence stays cinematic, never gore close-ups.
- In a photograph, eye, hair and skin colours stay NATURAL: write "green eyes", "red hair" — never "vibrant", "vivid", "piercing", "glowing" or "emerald" eyes, which the generator paints as unreal coloured lenses. Only an explicitly unreal request (fantasy creature, glowing eyes, neon look) gets saturated colours.
- If CONTEXT gives the previous image brief, the request is a change to it (variation, darker, other angle...): start from the previous brief and apply the change.

FRAMING ("cadrage")
- "portrait" (vertical) for a single person, face, character standing, phone wallpaper, tall subject.
- "carre" for a logo, icon, avatar, isolated object or product.
- "paysage" (16:9) for scenes, landscapes, vehicles, space, groups, anything cinematic.

ELEMENTS
- 2 to 6 short English noun phrases: the things that MUST be clearly visible (they will be checked by a vision model). Concrete and visual: "rusty iron sword", "black hole with glowing accretion disk".

PROMPT (English, 90 to 170 words, natural sentences, never keyword lists)
- Start with the main subject and what it is doing, with precise physical details.
- Then place every requested element LARGE and clearly visible, organised in foreground, middle ground and background.
- Then camera: shot type, angle, lens in mm, depth of field.
- Then light: direction, quality, colour temperature, atmosphere; then materials and textures; then colour palette and mood.
- For photo/cinema: write it as a real photograph or film still ("photograph", "film still shot on ..."), with natural imperfections; no plastic CGI look.
- No quality tags (8k, masterpiece, trending, best quality). No text, letters or watermark in the image unless the user asks for text.
- The image fills the whole frame: never letterbox bars or borders, even for a film still.

COMPREHENSION (French, one or two sentences, addressed to the user with "tu")
- Say plainly what you will show and how you interpreted any doubtful word, with the reason ("J'ai lu « X » comme un Y, parce que ..."). If another reading was plausible, say it in a few words so the user can correct you.

TITRE: 2 to 5 French words, lowercase, words separated by underscores, no accents (e.g. "vaisseau_quasar_trou_noir").

LANGUAGE: "comprehension" and "ambiguites" are in French; "analyse" in English. "prompt" and "elements" are ALWAYS in English, even though the request is in French.

Reply with the JSON object only."""


# Lexique par theme : les mots rares qu'un LLM generaliste ne retrouve pas a
# partir d'une faute. Le 09/11/2026, « lazar » (blazar ou quasar, au milieu
# d'une constellation, d'un trou noir et d'un trou de ver) devenait un laser,
# meme en mode reflexion : le modele ne proposait que des voisins
# orthographiques courants (lazare, lasser). Le rapprochement approximatif
# ci-dessous lui fournit les candidats ; le choix reste le sien, selon le theme.
LEXIQUE = {
    "astronomie": (
        "blazar quasar pulsar magnétar magnetar nébuleuse nebula supernova hypernova galaxie "
        "constellation comète astéroïde exoplanète météore météorite "
        "naine géante neutrons accrétion gravitationnelle horizon singularité trou ver "
        "wormhole cosmos univers lactée andromède orion pléiades saturne jupiter mars "
        "vénus neptune uranus mercure pluton titan europa aurore éclipse zodiaque "
        "télescope observatoire orbite satellite sonde station navette fusée vaisseau "
        "astronaute cosmonaute spationaute gargantua endurance"),
    "science-fiction": (
        "laser plasma hologramme holographique cyborg androïde robot mecha exosquelette "
        "blaster sabre téléportation hyperespace distorsion propulseur réacteur cyberpunk "
        "dystopie mutant clone drone"),
    "fantasy": (
        "dragon wyverne griffon licorne pégase phénix hydre kraken golem liche nécromancien "
        "elfe nain orque orc troll gobelin minotaure centaure sirène vampire loup-garou "
        "paladin chevalier sorcier mage druide barbare samouraï ninja viking spartiate "
        "gladiateur valkyrie"),
    "coiffure et mode": (
        "dégradé fade undercut tresses dreadlocks locks chignon frange barbe moustache "
        "bouc tatouage piercing"),
    "photographie": (
        "bokeh contre-jour macro panoramique fisheye grand-angle téléobjectif longue "
        "exposition sépia monochrome argentique polaroid silhouette"),
}
_MOTS_LEXIQUE = {m: theme for theme, mots in LEXIQUE.items() for m in mots.split()}


def _sans_accents(t: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()


# Mots courants qui ressemblent par hasard a un mot du lexique (« voiture » /
# « voie », « centre » / « centaure ») : jamais proposes comme fautes.
_MOTS_COURANTS = frozenset(_sans_accents(m) for m in (
    "voir vers voiture centre barbu barbus barbe avec dans pour sous sur entre contre "
    "comme plus moins tres trop bien beau belle grand grande petit petite rouge rouges "
    "bleu bleue vert verte noir noire blanc blanche jaune image images photo photos "
    "fait faire fais veux voudrais vois voit voici leur leurs elle elles nous vous "
    "homme femme enfant jeune vieux ville route maison monde terre ciel nuit jour "
    "soleil lune etoile etoiles mer ocean foret montagne guerre guerrier guerriere "
    "visage tete corps main mains yeux regard style effet effets fond plan premier "
    "dernier autre autres tout toute tous toutes rien encore aussi alors donc "
    "quand comment pourquoi laser lasers vaisseau vaisseaux spatial spatiale "
    # Couleurs et descriptions de personnes : « cheveux roux » etait rapproche de
    # « trou (noir) », « yeux verts » de « ver (de terre) » (10/11/2026).
    "roux rousse rousses brun brune bruns brunes blond blonde blonds blondes chatain "
    "chatains gris grise verts vertes bleus bleues noirs noires blancs blanches "
    "cheveux peau barbe moustache souriant souriante portrait ans age agee").split())


def _phonetique(t: str) -> str:
    """Forme sonore francaise grossiere : « lazar » et « quasar » deviennent
    « lasar » et « kasar » (proches), ce que l'orthographe seule ne voit pas."""
    t = _sans_accents(t)
    for a, b in (("qu", "k"), ("ph", "f"), ("ch", "x"), ("ck", "k"), ("ss", "s"),
                 ("z", "s"), ("y", "i"), ("h", ""), ("ou", "u")):
        t = t.replace(a, b)
    t = re.sub(r"c(?=[aou])", "k", t)
    return re.sub(r"[tdx]$", "", t)


def indices_lexicaux(demande: str) -> list[str]:
    """Mots de la demande proches d'un mot rare du lexique sans lui etre egaux.

    Ex. « lazar » -> « « lazar » ressemble a : blazar (astronomie), quasar
    (astronomie) ». Rapprochement sur l'orthographe ET sur une forme sonore
    (difflib) ; les mots courants sont ignores ; vide quand rien n'est proche."""
    import difflib

    lexique = {_sans_accents(m): m for m in _MOTS_LEXIQUE}
    sons = {}
    for nu in lexique:
        sons.setdefault(_phonetique(nu), nu)
    indices: list[str] = []
    for mot in dict.fromkeys(re.findall(r"[a-zA-ZÀ-ÿ-]{4,}", demande)):
        n = _sans_accents(mot).strip("-")
        if len(n) < 4 or n in lexique or n in _MOTS_COURANTS or n.rstrip("s") in lexique:
            continue
        score: dict[str, float] = {}
        for p in difflib.get_close_matches(n, list(lexique), n=4, cutoff=0.75):
            score[p] = difflib.SequenceMatcher(None, n, p).ratio()
        ph = _phonetique(n)
        for s in difflib.get_close_matches(ph, list(sons), n=4, cutoff=0.75):
            p = sons[s]
            score[p] = max(score.get(p, 0.0), difflib.SequenceMatcher(None, ph, s).ratio())
        if score:
            meilleurs = sorted(score, key=score.get, reverse=True)[:3]
            liste = ", ".join(f"{lexique[p]} ({_MOTS_LEXIQUE[lexique[p]]})" for p in meilleurs)
            indices.append(f"« {mot} » ressemble à : {liste}")
    return indices[:8]


def _ollama() -> str:
    from backend.core.config import ollama_url

    return ollama_url()


def _modele_vision(base: str) -> str:
    """Modele de verification : NEO_IMAGE_VISION, sinon le premier installe."""
    if MODELE_VISION.strip():
        return MODELE_VISION.strip()
    import httpx

    try:
        installes = [m.get("name", "") for m in httpx.get(f"{base}/api/tags", timeout=5).json().get("models", [])]
    except Exception:
        return MODELES_VISION[-1]
    return next((m for m in MODELES_VISION if m in installes), MODELES_VISION[-1])


def _fichiers_modele(base: str, modele: str) -> list[Path]:
    """Fichiers d'un modele Ollama (poids, projecteur de vision), lus dans les
    lignes FROM de sa fiche."""
    import httpx

    fiche = httpx.post(f"{base}/api/show", json={"model": modele}, timeout=10).json()
    chemins = []
    for ligne in str(fiche.get("modelfile", "")).splitlines():
        if ligne.startswith("FROM "):
            p = Path(ligne[5:].strip())
            if p.is_absolute() and p.is_file():
                chemins.append(p)
    return chemins


def prechauffer_modele(modele: str = "") -> dict:
    """Relit en tache de fond les fichiers du modele qui verifiera l'image.

    Le studio decharge le modele de conversation pour liberer la carte ; a la
    verification, Ollama le recharge. Sous Windows il le lit alors hors cache :
    17 a 26 s. Si ses fichiers viennent d'etre lus (7,6 Go en 2 a 4 s sur le
    SSD), le meme rechargement prend 6 a 9 s. La lecture se fait pendant que
    l'image se fabrique : elle ne coute rien a l'attente.

    Retour immediat : un dictionnaire que le fil complete ({go, duree_s} ou
    {saute}), avec le fil sous la cle « fil ». NEO_IMAGE_PRECHAUFFE=0 coupe.
    """
    import threading

    etat: dict = {}
    if os.environ.get("NEO_IMAGE_PRECHAUFFE", "1") == "0":
        etat["saute"] = "desactive"
        return etat

    def _lire() -> None:
        debut = time.time()
        try:
            base = _ollama()
            chemins = _fichiers_modele(base, modele or _modele_vision(base))
            taille = sum(p.stat().st_size for p in chemins)
            if not taille:
                etat["saute"] = "aucun fichier"
                return
            try:
                import psutil

                # Jamais au prix de la memoire : le studio en a besoin.
                if psutil.virtual_memory().available < taille + 6 * 1024**3:
                    etat["saute"] = "memoire vive trop juste"
                    return
            except ImportError:
                pass
            for p in chemins:
                with open(p, "rb") as f:
                    while f.read(32 * 1024 * 1024):
                        pass
            etat.update(go=round(taille / 1e9, 1), duree_s=round(time.time() - debut, 1))
        except Exception as exc:
            etat["saute"] = str(exc)[:120]

    fil = threading.Thread(target=_lire, name="prechauffe-modele", daemon=True)
    etat["fil"] = fil
    fil.start()
    return etat


def precharger_modele(modele: str = ""):
    """Demande a Ollama, sans attendre, de remettre sur la carte le modele qui
    verifiera l'image (celui de la conversation).

    Appele quand le studio n'a plus besoin de la carte, alors qu'il lui reste
    deux a trois secondes de finition : le rechargement (5 a 9 s) avance
    d'autant. Meme taille de contexte que `verifier` et que le chat, sinon
    Ollama rechargerait le modele une seconde fois. Retourne le fil lance.
    NEO_IMAGE_PRECHARGE=0 coupe."""
    import threading

    if os.environ.get("NEO_IMAGE_PRECHARGE", "1") == "0":
        return None

    def _charger() -> None:
        try:
            import httpx

            base = _ollama()
            m = modele or _modele_vision(base)
            httpx.post(f"{base}/api/generate", json={
                "model": m, "keep_alive": "60m", "options": {"num_ctx": contexte_pour(m, 8192)},
            }, timeout=120)
        except Exception:
            pass

    fil = threading.Thread(target=_charger, name="precharge-modele", daemon=True)
    fil.start()
    return fil


def _sait_reflechir(modele: str) -> bool:
    """Le modele a-t-il un mode « reflexion » (champ `think` d'Ollama) ?
    Meme liste que `persona::sait_reflechir` cote Rust."""
    m = (modele or "").lower()
    return m.startswith(("qwen3", "gemma4", "gpt-oss")) or "deepseek-r1" in m or "magistral" in m


def _choisir_modele(base: str) -> str:
    """Generaliste a utiliser : NEO_IMAGE_BRIEF, sinon le plus fin installe."""
    force = os.environ.get("NEO_IMAGE_BRIEF", "").strip()
    if force:
        return force
    import httpx

    try:
        installes = [m.get("name", "") for m in httpx.get(f"{base}/api/tags", timeout=5).json().get("models", [])]
    except Exception:
        return MODELES_BRIEF[0]
    # Le plus fin INSTALLE, meme s'il faut le charger (~5 s depuis le SSD) :
    # qwen3:8b, souvent deja charge, comprenait moins bien (Interstellar sans
    # Gargantua, « degrade » pris pour un degrade de lumiere).
    return next((m for m in MODELES_BRIEF if m in installes), MODELES_BRIEF[-1])


_MOTS_FR = frozenset("le la les un une des du de et avec dans sur sous est sont pour par au aux "
                     "son sa ses leur qui que vers une".split())


def _slug(texte: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return "_".join(t.split("_")[:6])[:60] or "image"


# Qualificatifs qui saturent la couleur des yeux (groupe 2 : ce qui est garde).
_YEUX_SATURES = re.compile(
    r"\b(vibrant|vivid|bright|brilliant|piercing|striking|glowing|intense|intensely|luminous|"
    r"electric|emerald|sapphire|jewel-toned|deep)[ -]+((?:[a-z-]+ ){0,2}eyes)\b",
    re.IGNORECASE,
)
_YEUX_IRREELS_DEMANDES = re.compile(
    r"\b(?:[ée]clatants?|brillants?|lumineux|luisants?|per[çc]ants?|vifs?|intenses?|fluo\w*|"
    r"[ée]meraude|saphir|[ée]lectriques?|n[ée]on|surnaturels?|magiques?|d[ée]mon\w*|vampire\w*|"
    r"elfe\w*|cyborg\w*|robot\w*|extraterrestre\w*|alien\w*|qui brillent|rouges?|violets?|jaunes?|dor[ée]s?)\b",
    re.IGNORECASE,
)


def _nettoyer(brut: dict, demande: str) -> dict:
    """Valide et normalise le brief rendu par le modele."""
    prompt = re.sub(r"\s+", " ", str(brut.get("prompt", ""))).strip().strip('"')
    if len(prompt.split()) < 12:
        raise ValueError("prompt du brief trop court")
    mots = re.findall(r"[a-zà-ÿ']+", prompt.lower())
    francais = sum(m in _MOTS_FR for m in mots)
    if mots and francais / len(mots) > 0.08:
        raise ValueError("prompt du brief en francais")  # FLUX (T5) lit mal le francais
    style = brut.get("style") if brut.get("style") in STYLES else "photo"
    cadrage = brut.get("cadrage") if brut.get("cadrage") in CADRAGES else "paysage"
    elements = [re.sub(r"\s+", " ", str(e)).strip() for e in (brut.get("elements") or [])]
    elements = [e for e in elements if 2 <= len(e) <= 80][:6]
    # Yeux naturels en photo : « vibrant green eyes » donnait des iris de
    # lentilles colorees (portrait du 11/11/2026). Sauf si la demande veut
    # justement un regard irreel.
    if style in ("photo", "cinema") and not _YEUX_IRREELS_DEMANDES.search(demande):
        prompt = _YEUX_SATURES.sub(r"\2", prompt)
        elements = [_YEUX_SATURES.sub(r"\2", e) for e in elements]
    corrections = []
    for c in (brut.get("corrections") or [])[:6]:
        c = str(c).strip()
        cotes = re.split(r"\s*(?:->|→|=>)\s*", c)
        # Une « correction » identique des deux cotes n'en est pas une, ni une
        # faute absente de la demande (le modele recopiait « lazar -> laser »
        # de la consigne).
        if (c and len(cotes) == 2 and cotes[0].lower() != cotes[1].lower()
                and cotes[0].strip(" «»\"'").lower() in demande.lower()):
            corrections.append(c)
    comprehension = re.sub(r"\s+", " ", str(brut.get("comprehension", ""))).strip()
    ambiguites = [re.sub(r"\s+", " ", str(a)).strip() for a in (brut.get("ambiguites") or [])]
    ambiguites = [a for a in ambiguites if a][:6]
    return {
        "ok": True,
        "demande": demande,
        "analyse": re.sub(r"\s+", " ", str(brut.get("analyse", ""))).strip()[:2000],
        "ambiguites": ambiguites,
        "comprehension": comprehension[:500],
        "corrections": corrections,
        "style": style,
        "cadrage": cadrage,
        "elements": elements,
        "prompt": prompt[:1600],
        "titre": _slug(str(brut.get("titre", "")) or demande),
    }


def brief(demande: str, contexte: str = "", modele: str = "",
          reflexion: bool | None = None) -> dict:
    """Brief structure d'une demande d'image. Ne leve jamais : `ok` False si echec.

    reflexion : mode « thinking » de qwen3 avant le JSON (defaut :
    NEO_IMAGE_REFLEXION, actif). Sans lui, « lazar » au milieu d'une
    constellation, d'un trou noir et d'un trou de ver devenait un laser au
    lieu d'un quasar."""
    import httpx

    debut = time.time()
    base = _ollama()
    modele = modele or _choisir_modele(base)
    if reflexion is None:
        # gemma4 : le champ « analyse » du schema, ecrit en premier, lui suffit
        # (12/12 sans reflexion separee, deux fois plus vite). qwen3 en a besoin.
        choix = os.environ.get("NEO_IMAGE_REFLEXION", "").strip()
        reflexion = (choix != "0") if choix else not modele.lower().startswith("gemma4")
    message = f"REQUEST (French): {demande.strip()}\n\n"
    indices = indices_lexicaux(demande)
    if indices:
        message += ("DICTIONARY HINTS (words of the request close to rare words, with their "
                    "theme; consider them as candidates in step 2, pick by the THEME, they may "
                    "also be irrelevant):\n- " + "\n- ".join(indices) + "\n\n")
    message += "Write prompt and elements in English, comprehension in French."
    if contexte.strip():
        message = f"CONTEXT (previous image brief): {contexte.strip()[:1500]}\n\n{message}"
    corps = {
        "model": modele,
        "messages": [{"role": "system", "content": CONSIGNE}, {"role": "user", "content": message}],
        "stream": False,
        "keep_alive": "60m",
        "format": SCHEMA_BRIEF,
        "options": {"temperature": 0.4, "num_predict": 4096 if reflexion else 1400,
                    "num_ctx": contexte_pour(modele, 8192)},
    }
    # Un modele qui sait reflechir reflechit PAR DEFAUT chez Ollama : le choix
    # doit lui etre dit explicitement (les autres refusent le parametre).
    if _sait_reflechir(modele):
        corps["think"] = bool(reflexion)
    erreur = ""
    for _ in range(2):
        try:
            r = httpx.post(f"{base}/api/chat", json=corps, timeout=150)
            rep = r.json()
            msg = rep.get("message") or {}
            texte = re.sub(r"<think>.*?</think>", "", msg.get("content", ""), flags=re.S).strip()
            res = _nettoyer(json.loads(texte), demande)
            res.update(modele=modele, duree_s=round(time.time() - debut, 1),
                       chargement_s=round((rep.get("load_duration") or 0) / 1e9, 1),
                       reflexion=bool(reflexion),
                       pensee=re.sub(r"\s+", " ", msg.get("thinking") or "").strip()[:3000])
            return res
        except Exception as exc:  # JSON invalide, delai, modele absent : un second essai
            erreur = str(exc)[:200]
    return {"ok": False, "demande": demande, "erreur": erreur, "modele": modele,
            "duree_s": round(time.time() - debut, 1), "titre": _slug(demande),
            "cadrage": "paysage", "style": "photo", "elements": [], "corrections": [],
            "comprehension": "", "prompt": ""}


def _image_b64(chemin: Path, largeur_max: int = 1344) -> str:
    """Image reduite en JPEG base64 (la vision n'a pas besoin de la 4K)."""
    from io import BytesIO

    from PIL import Image

    with Image.open(chemin) as im:
        im = im.convert("RGB")
        if im.width > largeur_max:
            im = im.resize((largeur_max, round(largeur_max * im.height / im.width)), Image.LANCZOS)
        tampon = BytesIO()
        im.save(tampon, "JPEG", quality=90)
    return base64.b64encode(tampon.getvalue()).decode("ascii")


SCHEMA_VERIF = {
    "type": "object",
    "properties": {
        "elements": {"type": "array", "items": {"type": "object", "properties": {
            "element": {"type": "string"}, "visible": {"type": "boolean"}},
            "required": ["element", "visible"]}},
        "note": {"type": "integer"},
    },
    "required": ["elements", "note"],
}


def verifier(chemin: str | Path, elements: list[str]) -> dict:
    """Chaque element attendu est-il clairement visible ? Note globale /10.

    Retour : {ok, presents, manquants, note, duree_s}. ok False si la vision
    est indisponible (la generation n'est alors jamais bloquee)."""
    import httpx

    debut = time.time()
    if not elements:
        return {"ok": False, "presents": [], "manquants": [], "note": 0, "duree_s": 0.0}
    liste = "\n".join(f"- {e}" for e in elements)
    question = (
        "Look carefully at this image. For each element below, answer whether it is CLEARLY visible "
        "(true) or absent, tiny or ambiguous (false). Then rate the image from 0 to 10 for visual "
        "quality and realism (sharpness, anatomy, coherent lighting, no artifacts).\n" + liste
    )
    try:
        vision = _modele_vision(_ollama())
        r = httpx.post(f"{_ollama()}/api/chat", json={
            "model": vision,
            "messages": [{"role": "user", "content": question, "images": [_image_b64(Path(chemin))]}],
            # Pas de reflexion : 29/29 avec ou sans, et 2,5 fois plus rapide.
            "stream": False, "format": SCHEMA_VERIF, "think": False,
            "options": {"temperature": 0.0, "num_predict": 400, "num_ctx": contexte_pour(vision, 8192)},
            "keep_alive": "60m",
        }, timeout=150)
        rep = json.loads((r.json().get("message") or {}).get("content", "{}"))
        vus = {str(e.get("element", "")).strip().lower(): bool(e.get("visible"))
               for e in rep.get("elements", []) if isinstance(e, dict)}
        presents, manquants = [], []
        for e in elements:
            # Correspondance tolerante : le modele recopie parfois l'element en l'abregeant.
            v = vus.get(e.lower())
            if v is None:
                v = next((ok for k, ok in vus.items() if k and (k in e.lower() or e.lower() in k)), True)
            (presents if v else manquants).append(e)
        note = max(0, min(10, int(rep.get("note", 0))))
        return {"ok": True, "presents": presents, "manquants": manquants, "note": note,
                "duree_s": round(time.time() - debut, 1)}
    except Exception as exc:
        return {"ok": False, "presents": [], "manquants": [], "note": 0, "erreur": str(exc)[:200],
                "duree_s": round(time.time() - debut, 1)}


def lire_brief(image: str | Path) -> dict | None:
    """Brief enregistre a cote d'une image (`<nom>.brief.json`), ou None."""
    try:
        p = Path(image)
        for cand in (p.with_suffix(".brief.json"),
                     p.with_name(re.sub(r"(_apercu|_ecran)$", "", p.stem) + ".brief.json")):
            if cand.is_file():
                return json.loads(cand.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def ecrire_brief(image: str | Path, donnees: dict) -> None:
    try:
        Path(image).with_suffix(".brief.json").write_text(
            json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
