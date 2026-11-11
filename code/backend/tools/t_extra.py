# -*- coding: utf-8 -*-
"""Outils supplementaires (2026-10-19) : memoire persistante, notes,
recherche de fichiers, presse-papiers, conversion d'image.

Meme philosophie que les autres outils : sandbox, chemins absolus sous le
workspace, dependances du venv uniquement.
"""
import re
from pathlib import Path

from backend.core.security import verifier_acces as _verifier_acces, charger_regles as _charger_regles
from backend.tools.common import (UrlRefusee, _sandbox_cfg, chemin_ecriture,
    chemin_lecture, client_http, refus_url)
from backend.core import reglages


def _racine_notes() -> str:
    try:
        return str(_charger_regles().get("sandbox_root", _espace_travail()))
    except Exception:
        return _espace_travail()


def _espace_travail() -> str:
    return str(reglages.chemin("NEO_ESPACE_TRAVAIL", "workspace"))


def _fichier_notes() -> Path:
    return Path(_racine_notes()) / "notes.md"


# ---- 1. Memoire persistante (table memories) ----
#
# Deux natures de lignes :
#   - memoire UTILISATEUR (tag user/fait/preference/projet) : injectee dans
#     le prompt de conversation par routes/chat.rs ;
#   - note TECHNIQUE (tag outil/agent/debug/trace) : stockee pour
#     memory_search, jamais injectee dans le prompt.
# Ne jamais ecrire une note d'echec en tag "user" : l'assistant la
# presenterait comme un fait sur l'utilisateur.

TAGS_MEMOIRE = {
    "user", "fait", "preference", "projet",
    "outil", "agent", "debug", "trace",
}


def _tag_memoire(tag: str) -> str:
    t = (tag or "user").strip().lower()
    alias = {
        "utilisateur": "user", "user": "user",
        "fait": "fait", "fact": "fait",
        "preference": "preference", "préférence": "preference", "pref": "preference",
        "projet": "projet", "project": "projet",
        "outil": "outil", "tool": "outil",
        "agent": "agent", "debug": "debug", "trace": "trace",
    }
    return alias.get(t, "user")


def tool_memoire_ajouter(contenu: str, tag: str = "user") -> str:
    """Ajoute une information durable a la memoire de l'assistant.

    A utiliser quand l'utilisateur partage une information a retenir
    (preference, fait, projet). Le tag "outil" est reserve aux notes
    techniques : elles ne sont PAS injectees dans les conversations.
    """
    contenu = " ".join((contenu or "").split())[:500]
    if not contenu:
        return "Erreur: contenu vide"
    tag = _tag_memoire(tag)
    if tag not in TAGS_MEMOIRE:
        tag = "user"
    try:
        from backend.core.database import get_db

        db = get_db()
        from datetime import datetime

        deja = db.query_one(
            "SELECT id FROM memories WHERE content = ? LIMIT 1", (contenu,)
        )
        if deja:
            return f"Deja en memoire (id {deja.get('id')}) : {contenu[:120]}"

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            "INSERT INTO memories (content, tag, created_at) VALUES (?,?,?)",
            (contenu, tag, now),
        )
        n = db.query_one(
            "SELECT COUNT(*) AS n FROM memories WHERE tag IN "
            "('user','fait','preference','projet')"
        )
        return (
            f"Memoire enregistree (tag {tag}) : {contenu[:120]} "
            f"({n.get('n', '?')} memoires utilisateur au total)"
        )
    except Exception as exc:
        return f"Erreur memoire: {exc}"


# ---- 2. Notes persistantes (workspace/notes.md) ----

def tool_notes(action: str = "lire", texte: str = "", titre: str = "") -> str:
    """Notes personnelles persistantes (<espace de travail>\\notes.md).

    action : 'ecrire' (ajoute titre + texte), 'lire' (les N dernieres), 'effacer'.
    """
    action = (action or "lire").strip().lower()
    p = Path(_fichier_notes())
    if action == "effacer":
        try:
            p.unlink(missing_ok=True)
            return "Notes effacees."
        except Exception as exc:
            return f"Erreur effacement: {exc}"
    if action == "ecrire":
        if not (texte or "").strip():
            return "Erreur: texte vide"
        refus = _verifier_acces(str(p), ecriture=True)
        if refus:
            return refus
        from datetime import datetime

        horodatage = datetime.now().strftime("%Y-%m-%d %H:%M")
        entete = f"\n## {titre.strip() or 'Note'} — {horodatage}\n" if titre else f"\n- [{horodatage}] "
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(entete + texte.strip() + "\n")
        return f"Note enregistree dans {p}"
    # lecture : les 30 dernieres lignes non vides
    if not p.is_file():
        return "(aucune note)"
    try:
        lignes = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        queue = lignes[-30:]
        return "\n".join(queue) or "(aucune note)"
    except Exception as exc:
        return f"Erreur lecture notes: {exc}"


# ---- 3. Recherche de fichiers locaux ----

def tool_rechercher_fichiers(motif: str, dossier: str = "") -> str:
    """Cherche des fichiers par nom (jokers * ?) dans le workspace et les
    documents. Retourne chemin + taille + date, max 25 resultats."""
    motif = (motif or "").strip()
    if not motif:
        return "Erreur: motif vide"
    if "*" not in motif and "?" not in motif:
        motif = f"*{motif}*"
    racines = [str(r) for r in _sandbox_cfg()["read_roots"]]
    if dossier:
        d, refus = chemin_lecture(dossier, "rechercher_fichiers")
        if refus:
            return refus
        racines = [str(d)]
    resultats: list[str] = []
    for racine in racines:
        r = Path(racine)
        if not r.is_dir():
            continue
        try:
            for f in sorted(r.rglob(motif), key=lambda x: x.stat().st_mtime, reverse=True):
                if not f.is_file():
                    continue
                ko = f.stat().st_size // 1024
                from datetime import datetime

                date = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
                resultats.append(f"{f} ({ko} Ko, {date})")
                if len(resultats) >= 25:
                    break
        except Exception:
            continue
        if len(resultats) >= 25:
            break
    if not resultats:
        return f"Aucun fichier trouvé pour « {motif} » dans {', '.join(racines)}"
    return "\n".join(resultats)


# ---- 4. Presse-papiers Windows ----

# PowerShell 5.1 lit/ecrit la console en page de code OEM : sans forcer
# UTF-8, les accents sont detruits dans les deux sens.
_PS_UTF8 = ("[Console]::InputEncoding = [Text.UTF8Encoding]::new($false); "
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ")


def tool_presse_papiers(action: str = "lire", texte: str = "") -> str:
    """Presse-papiers systeme : 'lire' ou 'ecrire' (texte)."""
    action = (action or "lire").strip().lower()
    import subprocess

    try:
        if action == "ecrire":
            if not (texte or "").strip():
                return "Erreur: texte vide"
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 _PS_UTF8 + "[Console]::In.ReadToEnd() | Set-Clipboard"],
                input=texte.encode("utf-8"), capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                return f"Erreur presse-papiers: {proc.stderr.decode(errors='replace')[:200]}"
            return f"Presse-papiers mis a jour ({len(texte)} caracteres)."
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_UTF8 + "Get-Clipboard -Raw"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            return f"Erreur presse-papiers: {proc.stderr.decode(errors='replace')[:200]}"
        contenu = proc.stdout.decode("utf-8", errors="replace").strip()
        return contenu[:2000] if contenu else "(presse-papiers vide)"
    except Exception as exc:
        return f"Erreur presse-papiers: {exc}"


# ---- 5. Conversion / redimensionnement d'image ----

def tool_convertir_image(source: str, largeur: int = 0, hauteur: int = 0,
                         format_sortie: str = "", destination: str = "") -> str:
    """Redimensionne/convertit une image (PIL). largeur/hauteur 0 = inchangé.
    format_sortie : png | jpg | webp (defaut = celui de la source)."""
    src, refus = chemin_lecture(str(source or ""), "convertir_image")
    if refus:
        return refus
    if not src.is_file():
        return f"Erreur: image introuvable {source}"
    try:
        from PIL import Image

        with Image.open(src) as im:
            w, h = im.size
            if largeur > 0 and hauteur > 0:
                im = im.resize((int(largeur), int(hauteur)), Image.LANCZOS)
            elif largeur > 0:
                h = max(1, int(h * largeur / w))
                im = im.resize((largeur, h), Image.LANCZOS)
            elif hauteur > 0:
                w = max(1, int(w * hauteur / h))
                im = im.resize((w, hauteur), Image.LANCZOS)
            fmt = (format_sortie or src.suffix.lstrip(".")).lower()
            fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(fmt, "PNG")
            nom = f"{src.stem}_conv.{fmt.lower()}"
            dest, refus = chemin_ecriture(destination or str(src.with_name(nom)),
                                          "convertir_image")
            if refus and not destination:
                # Source hors workspace (documents, images generees) : copie
                # convertie deposee a la racine du workspace.
                dest, refus = chemin_ecriture(nom, "convertir_image")
            if refus:
                return refus
            if fmt == "JPEG" and im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.save(str(dest), format=fmt)
            ko = dest.stat().st_size // 1024
            return f"Image convertie: {dest} ({ko} Ko, {im.size[0]}x{im.size[1]}, {fmt})"
    except Exception as exc:
        return f"Erreur conversion: {exc}"


# ---- 6. Telechargement de fichier (URL -> workspace/documents) ----

def tool_telecharger_fichier(url: str, destination: str = "") -> str:
    """Telecharge un fichier (PDF, archive, code, image...) vers le dossier
    sandbox. Non-image : contrairement a fetch_image, tout type est accepte."""
    try:
        import time

        url = (url or "").strip()
        refus = refus_url(url)
        if refus:
            return refus
        # Garde SSRF re-appliquee a chaque redirection (hook du client).
        with client_http(timeout=120) as client:
            resp = client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
        resp.raise_for_status()
        if len(resp.content) < 10:
            return "Erreur: contenu vide"
        nom = str(resp.url).rstrip("/").split("/")[-1].split("?")[0][:80]
        nom = re.sub(r"[^\w.-]", "_", nom).strip("._") or "fichier"
        ext = Path(nom).suffix or ""
        if not ext:
            ctype = resp.headers.get("content-type", "")
            m = re.search(r"/(\w+)", ctype)
            if m:
                ext = "." + m.group(1).replace("plain", "txt")
        cible = destination or f"{Path(nom).stem}_{int(time.time())}{ext}"
        # Ecriture confinee au workspace (un chemin relatif y est rattache),
        # extension verifiee sur le chemin resolu.
        dest, refus = chemin_ecriture(cible, "telecharger_fichier")
        if refus:
            return refus
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return f"Fichier telecharge: {dest} ({len(resp.content) // 1024} Ko)"
    except UrlRefusee as exc:
        return str(exc)
    except Exception as exc:
        return f"Erreur telechargement: {exc}"


# ---- 7. Envoi de message/image sur Telegram (bot configure) ----

def tool_memory_search(query: str, k: int = 5) -> str:
    """Recherche dans les souvenirs persistants de Neo (mémoire entre
    conversations). Retourne les faits pertinents avec leur date."""
    from backend.core.database import get_db

    q = (query or "").strip().lower()
    if not q:
        return "Erreur: requete vide."
    k = max(1, min(int(k or 5), 20))
    mots = [m for m in q.split() if len(m) >= 3][:5]
    db = get_db()
    lignes = []
    for m in mots[:3]:
        for r in db.query_all(
            "SELECT content, tag, created_at FROM memories WHERE LOWER(content) LIKE ? LIMIT ?",
            (f"%{m}%", k),
        ):
            lignes.append(f"- {r['content']} [{r['created_at'][:10]}]")
    if not lignes:
        return f"Aucun souvenir local pour '{query}'."
    vus = set()
    uniques = [l for l in lignes if not (l[:70] in vus or vus.add(l[:70]))]
    return f"SOUVENIRS PERTINENTS pour '{query}' :\n" + "\n".join(uniques[:k])


def _motif_like(texte: str) -> str:
    """Motif LIKE « contient » ou l'antislash, % et _ du texte restent
    litteraux (requete a ecrire avec ESCAPE antislash)."""
    echappe = texte.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{echappe}%"


def tool_memory_forget(contenu: str) -> str:
    """Supprime un souvenir (consigne §1 : oublie demande par l'utilisateur).
    Supprime tous les souvenirs qui contiennent le texte donne."""
    from backend.core.database import get_db

    q = (contenu or "").strip()
    if not q:
        return "Erreur: texte vide."
    # Garde-fou : un texte trop court (ou « % ») effacerait toute la memoire.
    if len(q.strip("%_ ")) < 3:
        return "Erreur: precisez au moins 3 caracteres du souvenir a oublier."
    db = get_db()
    rows = db.query_all(
        "SELECT id, content FROM memories WHERE LOWER(content) LIKE ? ESCAPE '\\'",
        (_motif_like(q.lower()),),
    )
    for r in rows:
        db.execute("DELETE FROM memories WHERE id=?", (r["id"],))
    if rows:
        return f"{len(rows)} souvenir(s) supprime(s) : " + " | ".join(
            str(r["content"])[:60] for r in rows[:5]
        )
    return f"Aucun souvenir ne contient '{contenu}'."


def tool_envoyer_telegram(texte: str = "", image_path: str = "") -> str:
    """Envoie un message et/ou une image sur le(s) chat(s) Telegram connectes
    au bot configure (token dans settings). Si aucun chat actif, retour erreur.

    texte : message a envoyer (max 4000 car). image_path : chemin absolu d'une
    image locale a joindre (optionnel). Au moins l'un des deux requis.
    """
    try:
        from backend.core.database import get_db
        import httpx

        db = get_db()
        token = db.get_setting("telegram_token", "").strip()
        if not token:
            return "Erreur: token Telegram non configure (Reglages > Connexions)"
        chat_ids = [r["chat_id"] for r in db.query_all(
            "SELECT chat_id FROM telegram_sessions")]
        if not chat_ids:
            return "Erreur: aucune conversation Telegram active (envoyez /start au bot)"
        texte = (texte or "").strip()
        image = None
        if (image_path or "").strip():
            # Seules les images des racines autorisees partent sur Telegram.
            image, refus = chemin_lecture(image_path, "envoyer_telegram")
            if refus:
                return refus
            if not image.is_file():
                return f"Erreur: image introuvable {image_path}"
        if not texte and image is None:
            return "Erreur: texte ou image_path requis"
        base = f"https://api.telegram.org/bot{token}"
        erreurs: list[str] = []

        def _verifier(r, cid) -> None:
            try:
                ok = r.status_code == 200 and r.json().get("ok")
            except ValueError:
                ok = False
            if not ok:
                erreurs.append(f"chat {cid}: HTTP {r.status_code} {r.text[:120]}")

        with httpx.Client(timeout=30) as client:
            for cid in chat_ids:
                if image is not None:
                    with open(image, "rb") as f:
                        _verifier(client.post(
                            f"{base}/sendPhoto",
                            data={"chat_id": cid, "caption": texte[:1024]},
                            files={"photo": (image.name, f, "image/jpeg")},
                        ), cid)
                    if len(texte) > 1024:
                        _verifier(client.post(f"{base}/sendMessage",
                                              json={"chat_id": cid, "text": texte[:4000]}), cid)
                elif texte:
                    _verifier(client.post(f"{base}/sendMessage",
                                          json={"chat_id": cid, "text": texte[:4000]}), cid)
        if erreurs:
            return (f"Erreur envoi Telegram ({len(erreurs)} echec(s)) : "
                    + " | ".join(erreurs[:3]))
        return f"Envoye a {len(chat_ids)} chat(s) Telegram"
    except Exception as exc:
        return f"Erreur envoi Telegram: {exc}"
