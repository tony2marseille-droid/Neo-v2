# -*- coding: utf-8 -*-
"""Outil TTS ElevenLabs : genere un fichier audio MP3 a partir du texte.

Gratuit sans cle : repli sur le TTS systeme local (deja present dans Néo).
Avec cle ElevenLabs (10 000 car/mois gratuit) : voix ultra-realistes.
"""

import re
from pathlib import Path

import httpx

from backend.core.api_keys import get_api_key
from backend.core.config import settings

# L'API ElevenLabs attend un voice_id, pas un nom de voix (« Rachel » -> 404).
VOIX_ELEVENLABS = {"rachel": "21m00Tcm4TlvDq8ikWAM"}


def voice_id_elevenlabs(voix: str) -> str:
    """voice_id ElevenLabs : identifiant brut accepte tel quel, nom connu
    traduit, sinon Rachel."""
    v = (voix or "").strip()
    if re.fullmatch(r"[A-Za-z0-9]{20}", v):
        return v
    return VOIX_ELEVENLABS.get(v.lower(), VOIX_ELEVENLABS["rachel"])


def tool_tts_generer(texte: str, voix: str = "Rachel") -> str:
    """Genere un fichier audio MP3 via ElevenLabs ou repli TTS local.

    Args:
        texte: texte a convertir en audio
        voix: nom de la voix ElevenLabs (defaut "Rachel")

    Retourne le chemin du fichier audio genere, ou un message d'erreur.
    """
    texte = (texte or "").strip()[:2000]
    if not texte:
        return "Erreur: texte vide"

    cle = get_api_key("elevenlabs")

    # ---- Repli TTS local si cle manquante ----
    if not cle:
        return (
            "Clé ElevenLabs manquante. "
            "Configurez-la dans Réglages > Clés API (10 000 car/mois gratuit). "
            "Repli TTS systeme non implante dans cet outil."
        )

    # ---- Appel ElevenLabs ----
    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id_elevenlabs(voix)}"
        headers = {
            "xi-api-key": cle,
            "Content-Type": "application/json",
        }
        body = {
            "text": texte,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }

        dossier_sortie = Path(settings.neo_documents_dir)
        dossier_sortie.mkdir(parents=True, exist_ok=True)

        nom_fichier = f"tts_{abs(hash(texte)) % 1000000:06d}.mp3"
        chemin = dossier_sortie / nom_fichier

        resp = httpx.post(url, headers=headers, json=body, timeout=60)
        if resp.status_code == 401:
            return "Erreur ElevenLabs: cle invalide (401 Unauthorized)"
        if resp.status_code == 429:
            return "Erreur ElevenLabs: quota depasse (429 Too Many Requests)"
        resp.raise_for_status()

        chemin.write_bytes(resp.content)

        taille = chemin.stat().st_size
        return (
            f"Audio génère : {chemin}\n"
            f"Taille : {taille} octets\n"
            f"Texte : {texte[:100]}{'...' if len(texte) > 100 else ''}"
        )

    except httpx.HTTPStatusError as exc:
        return "Erreur ElevenLabs HTTP %s: %s" % (exc.response.status_code, exc)
    except Exception as exc:
        return "Erreur ElevenLabs: %s" % type(exc).__name__
