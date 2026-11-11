# -*- coding: utf-8 -*-
"""Outil Wikipedia : recherche + resumé d'article (gratuit, sans cle)."""

import re

import httpx


def _langue(lang) -> str:
    """Code langue Wikipedia valide (2-3 lettres minuscules), sinon 'fr'."""
    lang = str(lang or "").strip().lower()
    return lang if re.fullmatch(r"[a-z]{2,3}", lang) else "fr"


def tool_wikipedia(query: str, lang: str = "fr") -> str:
    """Recherche un article Wikipedia et en retourne le resume.

    Args:
        query: sujet a rechercher sur Wikipedia
        lang: code langue (defaut "fr", ex: "en", "es", "de")

    Retourne le titre, l'URL et le resume de l'article le plus pertinent,
    ou un message d'erreur si rien n'est trouve.
    """
    query = (query or "").strip()[:200]
    if not query:
        return "Erreur: requete vide"
    # `lang` est insere dans le nom d'hote : on n'accepte qu'un code langue.
    lang = _langue(lang)

    try:
        # 1. recherche d'articles pertinents
        search_url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 5,
            "format": "json",
        }
        headers = {
            "User-Agent": "Neo/1.0 (https://neo.local; contact: admin@neo.local)"
        }

        resp = httpx.get(search_url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        hits = data.get("query", {}).get("search", [])
        if not hits:
            return "Aucun article Wikipedia trouve pour : %s" % query

        # 2. prendre le premier resultat et recupérer son extrait
        title = hits[0]["title"]
        page_id = hits[0]["pageid"]

        extract_url = f"https://{lang}.wikipedia.org/w/api.php"
        extract_params = {
            "action": "query",
            "pageids": str(page_id),
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "exlimit": 1,
            "format": "json",
        }
        resp2 = httpx.get(extract_url, params=extract_params, headers=headers, timeout=15)
        resp2.raise_for_status()
        data2 = resp2.json()

        pages = data2.get("query", {}).get("pages", {})
        page = pages.get(str(page_id), {})
        extract = page.get("extract", "")

        # nettoyage : enlever les retours a la ligne multiples
        extract = re.sub(r"\n+", "\n", extract).strip()
        if len(extract) > 1500:
            extract = extract[:1500] + "…"

        url = "https://%s.wikipedia.org/wiki/%s" % (lang, title.replace(" ", "_"))

        return (
            "Wikipedia (%s) — %s\n"
            "URL: %s\n\n"
            "%s"
        ) % (lang, title, url, extract)

    except httpx.HTTPStatusError as exc:
        return "Erreur Wikipedia HTTP %s: %s" % (exc.response.status_code, exc)
    except Exception as exc:
        return "Erreur Wikipedia: %s" % type(exc).__name__
