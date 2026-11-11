# -*- coding: utf-8 -*-
"""Outil Open Library : recherche de livres (gratuit, sans cle)."""

import httpx


def tool_livre(query: str) -> str:
    """Recherche un livre sur Open Library et retourne ses infos.

    Args:
        query: titre, auteur ou mot-cle a rechercher

    Retourne le titre, l'auteur, l'annee de publication et une URL de couverture
    si disponible, pour les 5 premiers resultats.
    """
    query = (query or "").strip()[:200]
    if not query:
        return "Erreur: requete vide"

    try:
        url = "https://openlibrary.org/search.json"
        params = {
            "q": query,
            "limit": 5,
            "fields": "title,author_name,first_publish_year,cover_i,key,isbn",
        }
        headers = {"User-Agent": "Neo/1.0 (local assistant)"}

        resp = httpx.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        docs = data.get("docs", [])
        if not docs:
            return "Aucun livre trouve sur Open Library pour : %s" % query

        lignes = ["Open Library — %d resultat(s) pour : %s\n" % (len(docs), query)]
        for i, doc in enumerate(docs, 1):
            titre = doc.get("title", "Titre inconnu")
            auteurs = ", ".join(doc.get("author_name", [])[:3]) or "Auteur inconnu"
            annee = doc.get("first_publish_year", "?")
            key = doc.get("key", "")
            url_livre = "https://openlibrary.org%s" % key if key else ""
            isbn = doc.get("isbn", ["?"])[0] if doc.get("isbn") else "?"

            cover_i = doc.get("cover_i")
            cover_url = (
                "https://covers.openlibrary.org/b/id/%i-M.jpg" % cover_i
                if cover_i
                else ""
            )

            lignes.append(
                "%d. %s (%s)\n"
                "   Auteur: %s\n"
                "   ISBN: %s\n"
                "   URL: %s%s"
                % (i, titre, annee, auteurs, isbn, url_livre,
                   ("\n   Couverture: %s" % cover_url) if cover_url else "")
            )

        return "\n".join(lignes)

    except httpx.HTTPStatusError as exc:
        return "Erreur Open Library HTTP %s: %s" % (exc.response.status_code, exc)
    except Exception as exc:
        return "Erreur Open Library: %s" % type(exc).__name__
