# -*- coding: utf-8 -*-
"""Outil GitHub : recherche de depots et code (gratuit, sans cle, rate-limit 10 req/min)."""

import httpx


def tool_github(query: str) -> str:
    """Recherche des depots GitHub pertinents pour une requete.

    Args:
        query: sujet, langage ou mot-cle a rechercher sur GitHub

    Retourne les 5 premiers depots avec nom, description, langage, etoiles et URL.
    Necessite une connexion internet. Rate-limite GitHub : ~10 requetes/min sans cle.
    """
    query = (query or "").strip()[:200]
    if not query:
        return "Erreur: requete vide"

    try:
        url = "https://api.github.com/search/repositories"
        params = {
            "q": query,
            "per_page": 5,
            "sort": "stars",
            "order": "desc",
        }
        headers = {
            "User-Agent": "Neo/1.0 (local assistant)",
            "Accept": "application/vnd.github+json",
        }

        resp = httpx.get(url, params=params, headers=headers, timeout=15)

        if resp.status_code == 403:
            return (
                "GitHub API rate-limite ou bloquee. "
                "Attendez quelques minutes ou ajoutez une cle d'authentification."
            )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            return "Aucun depot GitHub trouve pour : %s" % query

        total = data.get("total_count", 0)
        lignes = ["GitHub — %d depot(s) total, affichage des 5 premiers pour : %s\n" % (total, query)]
        for i, item in enumerate(items, 1):
            nom = item.get("full_name", "?")
            desc = item.get("description") or "Pas de description"
            langage = item.get("language") or "Non specifie"
            etoiles = item.get("stargazers_count", 0)
            forks = item.get("forks_count", 0)
            url_depot = item.get("html_url", "")
            lignes.append(
                "%d. %s\n"
                "   %s\n"
                "   Langage: %s · Etoiles: %s · Forks: %s\n"
                "   URL: %s"
                % (i, nom, desc, langage, etoiles, forks, url_depot)
            )

        return "\n".join(lignes)

    except httpx.HTTPStatusError as exc:
        return "Erreur GitHub HTTP %s: %s" % (exc.response.status_code, exc)
    except Exception as exc:
        return "Erreur GitHub: %s" % type(exc).__name__
