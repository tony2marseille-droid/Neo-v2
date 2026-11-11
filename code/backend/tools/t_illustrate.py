# -*- coding: utf-8 -*-
"""Outil illustration Unsplash : recherche d'images libres de droits."""

import httpx

from backend.core.api_keys import get_api_key


def tool_illustrer(sujet: str, nombre: int = 3) -> str:
    """Recherche des images sur Unsplash pour illustrer un sujet.

    Args:
        sujet: sujet de recherche (ex: "montagne", "chat", "ville")
        nombre: nombre d'images a retourner (defaut 3, max 10)

    Retourne une liste d'URLs d'images, ou un message si cle manquante.
    """
    sujet = (sujet or "").strip()[:200]
    if not sujet:
        return "Erreur: sujet vide"

    nombre = max(1, min(int(nombre or 3), 10))

    cle = get_api_key("unsplash")
    if not cle:
        return (
            "Clé Unsplash manquante. "
            "Configurez-la dans Réglages > Clés API (gratuit, inscription requise)."
        )

    try:
        url = "https://api.unsplash.com/search/photos"
        headers = {
            "Authorization": f"Client-ID {cle}",
        }
        params = {
            "query": sujet,
            "per_page": nombre,
            "orientation": "landscape",
        }

        resp = httpx.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 401:
            return "Erreur Unsplash: cle invalide (401 Unauthorized)"
        if resp.status_code == 403:
            return "Erreur Unsplash: cle invalide ou quota depasse (403)"
        resp.raise_for_status()
        data = resp.json()

        resultats = data.get("results", [])
        if not resultats:
            return "Aucune image Unsplash trouve pour : %s" % sujet

        lignes = ["Unsplash — %d image(s) pour : %s\n" % (len(resultats), sujet)]
        for i, img in enumerate(resultats, 1):
            titre = img.get("alt_description") or img.get("description") or "Sans titre"
            auteur = img.get("user", {}).get("name", "Inconnu")
            url_image = img.get("urls", {}).get("regular", "")
            url_page = img.get("links", {}).get("html", "")
            lignes.append(
                "%d. %s\n   Auteur: %s\n   Image: %s\n   Page: %s"
                % (i, titre, auteur, url_image, url_page)
            )

        return "\n".join(lignes)

    except httpx.HTTPStatusError as exc:
        return "Erreur Unsplash HTTP %s: %s" % (exc.response.status_code, exc)
    except Exception as exc:
        return "Erreur Unsplash: %s" % type(exc).__name__
