# -*- coding: utf-8 -*-
import os
import re

from backend.core.config import contexte_pour
from pathlib import Path

from backend.tools.common import (UrlRefusee, _find_vision_model, chemin_ecriture,
    chemin_lecture, client_http, refus_url)


def _qc_vision(image_path: str, prompt: str) -> tuple[int, bool]:
    """Controle qualite d'une image generee : note /10 + fidelite a la demande.

    Retourne (note, fidele). En cas d'echec du modele vision, (5, True)
    (aucun blocage).
    """
    q = (
        f"Image demandee : « {prompt[:600]} ». Note cette image sur 10 "
        "(realisme, details, coherence avec la demande) et juge si elle est "
        "FIDELE ou PAS FIDELE a la demande. Reponds exactement : "
        "NOTE: X/10 puis FIDELE ou PAS FIDELE."
    )
    r = _analyser_image(image_path, q)
    note, fidele = 5, True
    if not r or "Erreur" in r[:40] or "vide" in r.lower():
        return note, fidele
    m = re.search(r"(\d{1,2})\s*/\s*10", r)
    if m:
        note = max(0, min(10, int(m.group(1))))
    haut = r[:400].upper()
    if "PAS FIDELE" in haut:
        fidele = False
    elif "FIDELE" in haut:
        fidele = True
    return note, fidele


def tool_edit_image(image_path: str, prompt: str, save_path: str = "") -> str:
    """Modifie une image existante (FLUX.2-klein, sinon FLUX Kontext) : recoloration,
    changement de decor/ciel, style, suppression d'elements...

    L'image d'entree est conservee ; une nouvelle image modifiee est creee.
    """
    from backend.core.sdgen import _modele_kontext, _modele_retouche, generer

    p = Path(str(image_path).strip())
    if not p.is_file():
        return f"Erreur: image introuvable {image_path}"
    if not _modele_retouche() and not _modele_kontext():
        return (
            "Erreur: un modele de retouche est requis (FLUX.2-klein dans tools/sd/models_edit, "
            "ou flux1-kontext-dev-Q6_K.gguf dans les modeles SD)."
        )
    if not save_path:
        import time as _t

        save_path = str(p.parent / f"edit_{p.stem}_{int(_t.time())}.png")
    if not re.search(r"\.(png|jpe?g|webp)$", save_path.lower()):
        save_path += ".png"
    return generer(prompt, save_path, reference=str(p), mode="edit")


def _vers_maitre(src: Path, dst: Path) -> bool:
    """Convertit le PNG genere en master TIFF (PIL) ou EXR (OpenCV)."""
    try:
        if dst.suffix.lower() in (".tiff", ".tif"):
            from PIL import Image

            im = Image.open(src)
            im.save(dst, format="TIFF", compression="tiff_deflate")
            return dst.is_file()
        # EXR : OpenCV d'abord (si la wheel embarque OpenEXR), puis
        # imageio + FreeImage (telechargement unique au premier appel).
        import os as _os

        _os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        try:
            import cv2  # type: ignore
            import numpy as _np

            bgr = cv2.imdecode(
                _np.fromfile(str(src), dtype=_np.uint8), cv2.IMREAD_COLOR)
            if bgr is not None:
                ok, buf = cv2.imencode(".exr", bgr.astype(_np.float32))
                if ok:
                    buf.tofile(str(dst))
                    return dst.is_file()
        except Exception:
            pass
        try:
            import imageio.v2 as _imageio
            import numpy as _np2

            from PIL import Image as _Image

            im = _Image.open(src)
            arr = _np2.asarray(im, dtype=_np2.float32)
            _imageio.imwrite(str(dst), arr)
            return dst.is_file()
        except Exception:
            return False
    except Exception:
        return False


def _apercu_jpg(src: Path, dst: Path) -> None:
    """Apercu JPEG du master (affichage navigateur + Telegram)."""
    try:
        from PIL import Image

        im = Image.open(src).convert("RGB")
        im.save(dst, format="JPEG", quality=90, optimize=True)
    except Exception:
        pass


def _finaliser_maitre(src: str, ext: str) -> str | None:
    """PNG -> master TIFF/EXR + apercu JPEG ; retourne le chemin master."""
    p = Path(src)
    cible = p.with_suffix(ext)
    if not _vers_maitre(p, cible):
        return None
    _apercu_jpg(p, cible.with_name(f"{cible.stem}_apercu.jpg"))
    try:
        p.unlink()
    except Exception:
        pass
    return str(cible)


def _format_sortie(format: str) -> tuple[str, str]:
    """(format, extension) : argument explicite, sinon reglage image_format, sinon TIFF."""
    if not format:
        try:
            from backend.core.database import get_db

            row = get_db().query_one(
                "SELECT value FROM app_settings WHERE key=?", ("image_format",))
            format = (row["value"] if row else "").strip()
        except Exception:
            format = ""
    fmt = (format or "tiff").lower()
    if fmt in ("jpg", "jpeg"):
        return "jpeg", ".jpg"
    if fmt in ("webp", "exr", "png"):
        return fmt, "." + fmt
    if fmt in ("tif", "tiff"):
        return "tiff", ".tiff"
    return "png", ".png"


def _dossier_images() -> Path:
    """Dossier des images de Neo : <racine>/workspace/images (cf. {WORKSPACE_IMAGES})."""
    from backend.tools.common import _default_root

    return Path(_default_root()) / "workspace" / "images"


def _chemin_de_sortie(save_path: str, titre: str, ext_gen: str) -> str:
    """Chemin d'ecriture de la generation (PNG pour un master TIFF/EXR)."""
    import time as _t

    # Neo tourne sous le compte systeme : « Documents » y designe
    # C:\Windows\System32\config\systemprofile\Documents, invisible pour
    # l'utilisateur. Sans chemin (gemma4 n'en passe pas, 10/11/2026), ou avec un
    # chemin dans ce profil, l'image va dans workspace\images, le dossier que
    # l'interface et l'agent connaissent.
    if not save_path or "systemprofile" in save_path.lower():
        nom = f"{titre or 'image'}_{int(_t.time())}{ext_gen}"
        if save_path:
            nom = re.sub(r"\.(png|jpe?g|webp|tiff?|exr)$", "", Path(save_path).name, flags=re.I) + ext_gen
        dossier = _dossier_images()
        try:
            dossier.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return str(dossier / nom)
    save_path = re.sub(r"\.(png|jpe?g|webp|tiff?|exr)$", "", save_path, flags=re.I)
    return save_path + ext_gen


def _infos_verif(v: dict) -> tuple[int, int]:
    """Cle de tri d'une verification : (elements manquants, -note)."""
    if not v or not v.get("ok"):
        return (99, 0)
    return (len(v.get("manquants", [])), -int(v.get("note", 0)))


def tool_generate_image(
    prompt: str, save_path: str = "", largeur: int = 0, hauteur: int = 0,
    negatif: str = "", steps: int = 0, reference: str = "",
    reference_desc: str = "", variantes: int = 1, format: str = "",
    qc: bool | None = None, precedent: str = "", cadrage: str = "",
) -> str:
    """Studio image de Neo : comprendre, peindre, verifier.

    1. Direction artistique (backend.core.direction.brief) : la demande, en
       francais et telle quelle, devient un brief structure — fautes
       corrigees, references traduites en visuels, cadrage (paysage /
       portrait / carre), elements obligatoires, prompt FLUX anglais.
    2. Generation FLUX dans le cadrage choisi (base ~1,3 Mpx, passe haute
       resolution ~3,7 Mpx, master 3840 px).
    3. Verification par la vision : chaque element obligatoire est-il
       visible ? S'il en manque un essentiel, une seconde prise (autre
       graine, elements manquants en tete du prompt) ; la meilleure gagne.

    precedent : chemin d'une image deja produite dont on veut une suite
    (variation, plus sombre, autre angle...) — son brief sert de contexte.
    reference : image d'un sujet reel (FLUX Kontext) ; la direction artistique
    est alors sautee (le prompt vient de la description du sujet).
    variantes : 1 a 3 images, graines differentes, triees de la meilleure a
    la moins bonne. format : tiff (defaut), jpeg, png, webp, exr.
    qc : conserve pour compatibilite (NEO_IMAGE_VERIF=0 coupe la verification).
    """
    import json as _json
    import random
    import time as _time

    from backend.core import direction
    from backend.core.logs import log as _log
    from backend.core.sdgen import (_modele, _modele_secondaire, _steps_defaut,
                                     _version_ecran, generer, prechauffer_studio)

    debut = _time.time()
    demande = (prompt or "").strip()
    if not demande:
        return "Erreur: demande d'image vide"
    # Les fichiers du studio sont lus pendant que le brief s'ecrit.
    if not reference:
        prechauffer_studio()

    # 1. Comprendre.
    b: dict = {"ok": False}
    if not reference and os.environ.get("NEO_IMAGE_DIRECTION", "1") != "0":
        contexte = ""
        if precedent:
            ancien = direction.lire_brief(precedent)
            if ancien:
                contexte = _json.dumps({k: ancien.get(k) for k in
                                        ("comprehension", "prompt", "style", "cadrage", "elements")},
                                       ensure_ascii=False)
        b = direction.brief(demande, contexte=contexte)
    prompt_gen = b["prompt"] if b.get("ok") else demande
    cadre = cadrage if cadrage in ("paysage", "portrait", "carre") else b.get("cadrage", "paysage")

    # FLUX schnell : 6 etapes au lieu de 4 (plus de detail, ~4 s).
    if steps <= 0:
        try:
            if _steps_defaut(_modele()) == 4:
                steps = 6
        except Exception:
            pass

    fmt, ext = _format_sortie(format)
    maitre = ext in (".tiff", ".exr")
    ext_gen = ".png" if maitre else ext
    racine = Path(_chemin_de_sortie(save_path, b.get("titre", ""), ext_gen))
    chemin_v = lambda i: racine if i == 0 else racine.with_name(f"{racine.stem}_v{i}{racine.suffix}")

    def _peindre(i: int, texte: str, graine: int, modele_prefere: str = "",
                 etapes: int | None = None, derniere: bool = True) -> str | None:
        msg = generer(texte, str(chemin_v(i)), largeur=largeur, hauteur=hauteur,
                      negatif=negatif, steps=steps if etapes is None else etapes,
                      reference=reference, reference_desc=reference_desc, graine=graine,
                      traduire=not b.get("ok"), cadrage=cadre, modele_prefere=modele_prefere,
                      # Derniere prise : des que la carte est libre, le modele de
                      # conversation y remonte pendant la finition.
                      apres_gpu=direction.precharger_modele if derniere else None)
        m = re.match(r"Image generee: (.+?) \(\d+ Ko\)", msg)
        if m and Path(m.group(1)).is_file():
            return m.group(1)
        _log("warning", "image", "generation en echec", message=msg[-400:])
        return None

    # 2. Peindre. Pendant ce temps, les fichiers du modele de conversation
    # sont relus : il sera recharge trois fois plus vite pour verifier l'image
    # puis pour repondre.
    chauffe = direction.prechauffer_modele()
    variantes = max(1, min(3, int(variantes or 1)))
    graine0 = random.randint(1, 2**31 - 1000)
    sorties: list[str] = []
    for i in range(variantes):
        p = _peindre(i, prompt_gen, graine0 + i * 7919, derniere=i == variantes - 1)
        if p:
            sorties.append(p)
    if not sorties:
        # Message adresse au modele : l'ancien « Reessaie dans un instant » lui
        # faisait relancer l'outil aussitot — trois essais de neuf minutes le
        # 11/11/2026, 27 minutes d'attente pour l'utilisateur.
        return ("Erreur: aucune image generee (generateur indisponible ou carte "
                "graphique saturee). NE RELANCE PAS generate_image maintenant : "
                "dis a l'utilisateur que la generation n'a pas abouti et qu'il "
                "peut redemander l'image dans une minute.")

    # 3. Verifier, et reprendre une fois si un element essentiel manque.
    elements = b.get("elements") or []
    verif_active = (bool(elements) and os.environ.get("NEO_IMAGE_VERIF", "1") != "0"
                    and qc is not False)
    verifs: dict[str, dict] = {}
    if verif_active:
        for p in sorties:
            verifs[p] = direction.verifier(p, elements)
        v0 = verifs[sorties[0]]
        essentiels = [e for e in v0.get("manquants", []) if e in elements[:3]]
        budget = float(os.environ.get("NEO_IMAGE_BUDGET_S", "330"))
        if (variantes == 1 and v0.get("ok") and essentiels
                and _time.time() - debut < budget - 120):
            renfort = (f"The image must clearly and prominently show {', '.join(essentiels)}. "
                       + prompt_gen)
            # Seconde prise avec L'AUTRE modele (Z-Image <-> FLUX) : une autre
            # lecture du meme brief, plutot qu'une simple autre graine.
            autre = _modele_secondaire(_modele())
            etapes2 = 6 if autre.lower().startswith("flux1-schnell") else 0
            p2 = _peindre(1, renfort, graine0 + 104729, modele_prefere=autre, etapes=etapes2)
            if p2:
                sorties.append(p2)
                verifs[p2] = direction.verifier(p2, elements)
        sorties.sort(key=lambda p: _infos_verif(verifs.get(p, {})))
        # La meilleure prend le nom principal (celui que l'agent a annonce).
        if sorties[0] != str(chemin_v(0)) and Path(str(chemin_v(0))).is_file():
            gagnante, perdante = Path(sorties[0]), Path(str(chemin_v(0)))
            tmp = perdante.with_name(perdante.stem + "_tmp" + perdante.suffix)
            for a, c in ((perdante, tmp), (gagnante, perdante), (tmp, gagnante)):
                a.replace(c)
            for p in (gagnante, perdante):
                _version_ecran(p)
            verifs[str(perdante)], verifs[str(gagnante)] = verifs[str(gagnante)], verifs[str(perdante)]
            sorties = [str(perdante)] + [str(gagnante) if s == str(perdante) else s
                                         for s in sorties[1:]]

    # 4. Masters TIFF/EXR (+ apercu JPEG) et brief a cote de chaque image.
    finales: list[str] = []
    for p in sorties:
        final = _finaliser_maitre(p, ext) if maitre else p
        if final:
            if final != p:
                verifs[final] = verifs.pop(p, {})
            finales.append(final)
            direction.ecrire_brief(final, {**{k: v for k, v in b.items() if k != "ok"},
                                           "demande": demande, "cadrage": cadre,
                                           "verification": verifs.get(final, {})})
    if not finales:
        return ("Erreur: conversion TIFF/EXR impossible (PIL/OpenCV). "
                "Reessaie avec format png, jpeg ou webp.")

    total = round(_time.time() - debut, 1)
    lignes: list[str] = []
    if b.get("ok") and b.get("comprehension"):
        lignes.append(f"COMPRIS: {b['comprehension']}")
    if b.get("corrections"):
        lignes.append("CORRECTIONS: " + "; ".join(b["corrections"]))
    lignes.append(f"IMAGE: {finales[0]}")
    v = verifs.get(finales[0], {})
    if v.get("ok"):
        # Le modele de vision rend parfois 0 faute de noter : une note nulle
        # n'est pas affichee (elle ferait croire a une image ratee).
        note = f" (note {v['note']}/10)" if v.get("note") else ""
        if v.get("manquants"):
            lignes.append("VERIFICATION: peu visibles ou absents : " + ", ".join(v["manquants"])
                          + f"{note} — dis-le honnetement a l'utilisateur.")
        else:
            lignes.append(f"VERIFICATION: tous les elements demandes sont visibles{note}.")
    for autre in finales[1:]:
        lignes.append(f"Variante: {autre}")
    lignes.append(f"[{len(finales)} image(s), cadrage {cadre}, {total}s au total]")
    try:
        _log("info", "image", "generation terminee", duree_s=total, images=finales,
             cadrage=cadre, brief_ok=bool(b.get("ok")), brief_s=b.get("duree_s"),
             verification=v, variantes=len(finales),
             prechauffe={k: x for k, x in chauffe.items() if k != "fil"})
    except Exception:
        pass
    return "\n".join(lignes)



def tool_image_search(query: str) -> str:
    """Recherche d'images de reference : Wikipedia (personnes/sujets reels),
    Wikimedia Commons, Openverse, DuckDuckGo.

    L'UA doit etre CONFORME a la politique Wikimedia (identite + contact),
    sinon reponse 403 silencieuse. Retourne titres + URL d'images
    telechargeables pour le modele vision ou fetch_image.
    """
    import html as _html

    import httpx

    query = (query or "").strip()[:200]
    if not query:
        return "Erreur: requete vide"
    ua = {"User-Agent": "NeoLocalAI/1.0 (assistant IA personnel; contact: neo@oracle.local)"}
    out: list[str] = []

    # 0. Wikipedia pageimages : LA source fiable pour les personnes et
    #    sujets notoires (photo officielle de l'article). fr puis en.
    for lang in ("fr", "en"):
        try:
            r = httpx.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query", "format": "json", "redirects": 1,
                    "prop": "pageimages", "piprop": "thumbnail",
                    "pithumbsize": 800, "titles": query,
                },
                headers=ua, timeout=12,
            )
            if r.status_code != 200:
                continue
            pages = r.json().get("query", {}).get("pages", {})
            for p in pages.values():
                src = p.get("thumbnail", {}).get("source", "")
                if src:
                    titre = _html.unescape(p.get("title", query))[:70]
                    out.append(f"{len(out)+1}. [wikipedia] {titre}\n   IMAGE: {src}")
                    break
            if out:
                break
        except Exception:
            continue

    try:
        r = httpx.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrlimit": 5,
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
            headers=ua,
            timeout=15,
        )
        pages = list(r.json().get("query", {}).get("pages", {}).values())
        pages.sort(key=lambda p: p.get("index", 99))
        for p in pages[:4]:
            ii = p.get("imageinfo", [{}])[0]
            url = ii.get("url", "")
            if url:
                title = p.get("title", "").replace("File:", "")[:70]
                out.append(f"{len(out)+1}. [wikimedia] {title}\n   IMAGE: {url}")
    except Exception:
        pass

    try:
        r = httpx.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": 3},
            headers=ua,
            timeout=15,
        )
        if r.status_code == 200:
            for i, x in enumerate(r.json().get("results", [])[:2]):
                title = _html.unescape(str(x.get("title", "")))[:70]
                url = x.get("url") or x.get("thumbnail")
                if url:
                    out.append(f"{len(out)+1}. [openverse] {title}\n   IMAGE: {url}")
    except Exception:
        pass

    try:
        client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
            timeout=14,
            follow_redirects=True,
        )
        home = client.get(
            "https://duckduckgo.com/",
            params={"q": query, "iax": "images", "ia": "images"},
        )
        m = re.search(r"vqd=['\"]?([\d-]+)", home.text)
        if m:
            r2 = client.get(
                "https://duckduckgo.com/i.js",
                params={"l": "fr-fr", "o": "json", "q": query, "vqd": m.group(1), "f": ",,,", "p": "1"},
                headers={"Referer": "https://duckduckgo.com/"},
            )
            for item in r2.json().get("results", [])[:3]:
                title = _html.unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))[:70]
                if item.get("image"):
                    out.append(f"{len(out)+1}. [ddg] {title}\n   IMAGE: {item['image']}")
    except Exception:
        pass

    return "\n\n".join(out) if out else (
        "Aucune image trouvee pour cette recherche. Essaie : (1) une "
        "formulation plus precise du sujet, (2) web_search pour trouver "
        "manuellement l'URL d'une image, puis fetch_image dessus, ou "
        "(3) decris ce que tu cherches si c'est un concept abstrait."
    )



def tool_fetch_image(url: str, save_path: str = "") -> str:
    """Telecharge une image (adresse publique uniquement) dans le workspace
    et retourne le chemin local."""
    import time

    url = (url or "").strip()
    refus = refus_url(url)
    if refus:
        return refus
    try:
        # Garde SSRF re-appliquee a chaque redirection (hook du client).
        with client_http(timeout=20) as client:
            resp = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
                "Referer": "https://duckduckgo.com/",
            })
        ctype = resp.headers.get("content-type", "").lower()
        # Une page d'erreur HTML ne doit jamais etre enregistree en .jpg.
        if resp.status_code != 200:
            return f"Erreur HTTP {resp.status_code} pour {url}"
        if not ctype.startswith("image/") or len(resp.content) < 1000:
            return f"Erreur: contenu non image ({ctype or 'type inconnu'}, {len(resp.content)} octets)"
        ext = ".jpg"
        m = re.search(r"image/(\w+)", ctype)
        if m:
            ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(m.group(1), ".jpg")
        cible = save_path or f"images/ref_{int(time.time() * 1000)}{ext}"
        # Ecriture confinee au workspace, extension verifiee sur le chemin resolu.
        dest, refus = chemin_ecriture(cible, "fetch_image")
        if refus:
            return refus
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        kb = len(resp.content) // 1024
        return f"Image telechargee: {dest} ({kb} Ko)"
    except UrlRefusee as exc:
        return str(exc)
    except Exception as exc:
        return f"Erreur telechargement: {exc}"


def tool_analyze_image(image_path: str, question: str) -> str:
    """Analyse une image locale avec un modele vision (outil du modele :
    limite aux racines de lecture et aux images generees)."""
    p, refus = chemin_lecture(str(image_path or ""), "analyze_image")
    if refus:
        return refus
    return _analyser_image(str(p), question)


def _analyser_image(image_path: str, question: str) -> str:
    """Analyse vision sans controle de chemin : appels internes uniquement
    (QC des images generees, captures, rendus temporaires)."""
    import base64 as b64
    import tempfile
    import time

    import httpx

    p = Path(str(image_path).strip())
    if not p.exists():
        return f"Erreur: image introuvable {image_path}"
    if not question:
        question = "Decris cette image en detail."
    model = _find_vision_model()
    if not model:
        return (
            "Erreur: aucun modele vision installe. Installez par exemple "
            "`ollama pull gemma4:12b` (cerveau et vision de Neo) ou qwen2.5vl:7b."
        )
    data = b64.b64encode(p.read_bytes()).decode()
    # Les images 4K (>1500px) depassent le budget vision : on reduit.
    try:
        from PIL import Image as _Image

        with _Image.open(p) as im:
            if max(im.size) > 1536:
                im.thumbnail((1536, 1536), _Image.LANCZOS)
                with tempfile.TemporaryDirectory(prefix="vis_") as tmp:
                    vignette = Path(tmp) / "v.png"
                    im.save(vignette, format="PNG")
                    data = b64.b64encode(vignette.read_bytes()).decode()
    except Exception:
        pass
    base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": question[:1500], "images": [data]}],
        "stream": False,
        # Meme contexte que le chat (8192) : gemma4:12b sert aussi a la
        # conversation, un autre num_ctx forcerait Ollama a le recharger.
        "keep_alive": "60m",
        "options": {"temperature": 0.2, "num_ctx": contexte_pour(model, 8192)},
    }
    text = ""
    for tentative in range(3):
        try:
            resp = httpx.post(f"{base}/api/chat", json=payload, timeout=240)
            text = resp.json().get("message", {}).get("content", "") or ""
        except Exception:
            text = ""
        if text.strip():
            return f"[{model}] {text.strip()[:1200]}"
        # Swap de modeles VRAM en cours (le LLM principal est charge) :
        # on recharge et on reessaie apres un court delai.
        try:
            httpx.post(f"{base}/api/generate",
                       json={"model": model, "keep_alive": 0}, timeout=10)
        except Exception:
            pass
        time.sleep(6 + 6 * tentative)
    return f"[{model}] Reponse vide du modele vision (apres 3 essais)."



# Mots a ignorer pour extraire le SUJET d'une requete utilisateur (« genere
# moi une image photorealiste 4k d un avion SR71... » -> « avion sr71 ... »).
_STOPWORDS_REF = {
    "genere", "generez", "genere-moi", "generez-moi", "creer", "cree", "crée",
    "dessine", "dessiner", "fais", "fait", "faire", "montre", "moi", "me", "je",
    "tu", "il", "elle", "on", "nous", "vous", "veux", "voudrais", "peux", "peut",
    "aimerais", "jaimerais", "une", "un", "des", "d", "du", "de", "la", "le",
    "les", "l", "a", "au", "aux", "avec", "dans", "sur", "sous", "et", "ou",
    "que", "qui", "est", "sont", "en", "au", "pour", "pas", "ne", "plus",
    "moins", "image", "images", "photo", "photos", "photorealiste", "realiste",
    "réaliste", "4k", "hd", "hdr", "8k", "2k", "volant", "qui", "style",
    "couleur", "couleurs", "fond", "beau", "belle", "joli", "jolie",
    "magnifique", "superbe", "petit", "petite", "grand", "grande", "tres",
    "très", "super", "quelque", "comme", "aujourdhui", "aujourd'hui",
}


def _extraire_sujet(requete: str) -> list[str]:
    """Extraire les mots-clefs du sujet : stopwords retires, ordre conserve.

    Retourne une liste de requetes candidates, de la plus complete a la plus
    courte (degradation progressive pour la recherche d'images).
    """
    import re

    mots = re.findall(r"[a-z0-9àâçéèêëîïôùûüÿ'’-]+", requete.lower())
    mots = [m for m in mots if m not in _STOPWORDS_REF]
    if not mots:
        return [requete.strip()]
    sujets: list[str] = []
    # Candidats : requete complete puis noyau du sujet (3/2/1 premiers mots).
    for n in (len(mots), 3, 2, 1):
        if n <= len(mots):
            s = " ".join(mots[:n])
            if s.lower() not in [x.lower() for x in sujets]:
                sujets.append(s)
    # Degradations intermediaires (fin retiree mot a mot).
    for n in range(len(mots) - 1, 3, -1):
        s = " ".join(mots[:n])
        if s.lower() not in [x.lower() for x in sujets]:
            sujets.append(s)
    return sujets


def _liberer_pour_vision() -> None:
    """Libere la carte pour le modele de vision, sauf s'il y est deja.

    Le modele de vision est aujourd'hui celui de la conversation (gemma4:12b),
    donc deja charge quand l'agent appelle l'outil : le decharger pour le
    recharger aussitot coutait 17 a 26 s. On ne decharge que si un AUTRE
    modele occupe la carte."""
    try:
        import httpx

        base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
        charges = [m.get("name", "") for m in
                   httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])]
        if not charges or charges == [_find_vision_model()]:
            return
        from backend.core.sdgen import _liberer_vram

        _liberer_vram()
    except Exception:
        pass


def tool_find_reference_image(object_query: str) -> str:
    """Agent de recherche d'image intelligent : degrade progressivement la requete
    (description complete -> sujet court) jusqu'a trouver une photo validee par le
    modele vision. Retourne le chemin de la meilleure reference + sa description.
    """
    object_query = (object_query or "").strip()[:200]
    if not object_query:
        return "Erreur: description d'objet vide"
    _liberer_pour_vision()
    sujets = _extraire_sujet(object_query)
    report: list[str] = []
    validations = 0
    MAX_VALIDATIONS = 4
    best = None
    best_desc = ""
    for sujet in sujets:
        if best or validations >= MAX_VALIDATIONS:
            break
        search = tool_image_search(sujet)
        urls = [
            line.replace("   IMAGE: ", "").split("?utm_source")[0]
            for line in search.split("\n")
            if line.strip().startswith("IMAGE:")
        ]
        if not urls:
            continue
        for url in urls[:4]:
            if best or validations >= MAX_VALIDATIONS:
                break
            fetched = tool_fetch_image(url)
            m = re.match(r"Image telechargee: (.+?) \((\d+) Ko\)", fetched)
            if not m:
                continue
            path = m.group(1)
            # Reference legere (max 1024 px) : moins de charge vision/Kontext.
            try:
                from PIL import Image as _Image

                with _Image.open(path) as im:
                    if max(im.size) > 1024:
                        im.thumbnail((1024, 1024), _Image.LANCZOS)
                        im.save(path)
            except Exception:
                pass
            validations += 1
            desc = _analyser_image(
                path,
                f"L'utilisateur cherche des references visuelles de : « {sujet} ».\n"
                "IMPORTANT : reponds NON-PERTINENT si le sujet principal est AUTRE CHOSE "
                "(personne differente, objet different, lieu different, scene sans le sujet). "
                "Si le sujet principal de l'image EST bien ce sujet, reponds PERTINENT.\n"
                "Reponds sur la premiere ligne par exactement PERTINENT ou NON-PERTINENT, puis "
                "decris ses caracteristiques visuelles distinctives en ANGLAIS (une phrase : "
                "forme, proportions, couleurs, details). Si le sujet est une PERSONNE, une "
                "photo de son visage de face ou de profil est PERTINENTE.",
            )
            # Verdict = premiere ligne de la reponse, sans le prefixe « [modele] ».
            # « NON PERTINENT » / « NON-PERTINENT » / « NOT PERTINENT » = rejet.
            tete = re.sub(r"^\[[^\]]*\]\s*", "", desc.strip())
            premiere = (tete.splitlines() or [""])[0].upper()
            premiere = re.sub(r"[^A-Z ]", " ", premiere.replace("-", " "))
            ok = premiere.split()[:1] == ["PERTINENT"]
            tag = "OK " if ok else "-- "
            report.append(f"[{tag}] {Path(path).name} ({sujet}): {desc[:300]}")
            if ok:
                best = path
                best_desc = desc[:600]
    if best:
        return "\n".join(
            [
                f"MEILLEURE REFERENCE: {best}",
                f"Description: {best_desc}",
                "Pour une forme organique (visage, personnage, sculpture), utilise "
                "MAINTENANT generate_mesh_from_image sur cette reference (3D). "
                "Pour une IMAGE 2D fidele au sujet (avion, vehicule, animal...), "
                "utilise MAINTENANT generate_image avec reference=<ce chemin>.",
                "",
                "DETAILS DE LA RECHERCHE:",
                *report,
            ]
        )
    return "\n\n".join(
        report
        + [
            "\nAUCUNE image pertinente trouvee. Base-toi sur tes connaissances "
            "geometriques et precise les caracteristiques distinctives du sujet."
        ]
    )
