# -*- coding: utf-8 -*-
"""Outil traduction DeepL Free : 500 000 caracteres/mois gratuit."""

import httpx

from backend.core.api_keys import get_api_key


def tool_traduire(texte: str, cible: str = "EN", source: str = "FR") -> str:
    """Traduit un texte via DeepL API gratuite.

    Args:
        texte: texte a traduire
        cible: langue cible (ex: "EN", "FR", "ES", "DE")
        source: langue source (ex: "FR", "EN", "auto")

    Retourne le texte traduit, ou un message si cle manquante.
    """
    texte = (texte or "").strip()[:5000]
    if not texte:
        return "Erreur: texte vide"

    cle = get_api_key("deepl")
    if not cle:
        return (
            "Clé DeepL manquante. "
            "Configurez-la dans Réglages > Clés API (500 000 car/mois gratuit)."
        )

    try:
        url = "https://api-free.deepl.com/v2/translate"
        headers = {
            "Authorization": f"DeepL-Auth-Key {cle}",
            "Content-Type": "application/json",
        }
        body = {
            "text": [texte],
            "target_lang": cible,
            "source_lang": source if source != "auto" else None,
        }
        # supprimer source_lang si None
        body = {k: v for k, v in body.items() if v is not None}

        resp = httpx.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code == 403:
            return "Erreur DeepL: cle invalide ou quota depasse (403)"
        resp.raise_for_status()
        data = resp.json()

        traductions = data.get("translations", [])
        if not traductions:
            return "Erreur DeepL: aucune traduction retournée"

        texte_traduit = traductions[0].get("text", "")
        langue_detectee = traductions[0].get("detected_source_language", source)

        return (
            f"Traduction ({langue_detectee} → {cible}):\n{texte_traduit}"
        )

    except httpx.HTTPStatusError as exc:
        return "Erreur DeepL HTTP %s: %s" % (exc.response.status_code, exc)
    except Exception as exc:
        return "Erreur DeepL: %s" % type(exc).__name__
