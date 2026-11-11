# -*- coding: utf-8 -*-
import re

from backend.tools.common import UrlRefusee, _llm_rapide, client_http, html_unescape, refus_url


def _fetch_page(url: str, max_chars: int = 6000) -> tuple[str, str, list[str]]:
    """Recupere une page web et en extrait le titre, le texte propre et les liens.

    Retourne (titre, texte, liens). Utilise httpx + lxml (deja presents), sans
    navigateur : rapide et leger. Gere les pages HTML classiques ; les pages
    100% JavaScript renvoient un texte vide (a traiter par l'appelant).
    """
    import lxml.html

    url = (url or "").strip()
    if not url or not re.match(r"^https?://", url):
        return "", "Erreur: URL invalide (http/https uniquement)", []
    # Garde SSRF : refuse loopback/LAN, y compris apres redirection.
    refus = refus_url(url)
    if refus:
        return "", refus, []

    try:
        with client_http(timeout=20) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                },
            )
    except UrlRefusee as exc:
        return "", str(exc), []
    except Exception as exc:
        return "", f"Erreur de recuperation: {exc}", []

    if resp.status_code >= 400:
        return "", f"Erreur HTTP {resp.status_code} pour {url}", []

    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return "", f"Contenu non HTML ({ctype}) : {url}", []

    try:
        doc = lxml.html.fromstring(resp.text)
    except Exception:
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html_unescape(text)).strip()
        return "", text[:max_chars], []

    # titre
    title = ""
    try:
        title = (doc.findtext(".//title") or "").strip()
    except Exception:
        title = ""

    # suppression des zones bruyantes
    for tag in ("script", "style", "noscript", "nav", "footer", "header", "aside", "form"):
        for node in doc.xpath(f"//{tag}"):
            try:
                node.getparent().remove(node)
            except Exception:
                pass

    # texte principal : preferer <main>/<article> si present
    main = None
    for xp in ("//main", "//article", "//div[@role='main']"):
        found = doc.xpath(xp)
        if found:
            main = found[0]
            break
    root = main if main is not None else doc.body if doc.body is not None else doc

    text = " ".join(t.strip() for t in root.itertext() if t.strip())
    text = re.sub(r"\s+", " ", html_unescape(text)).strip()
    text = text[:max_chars]

    # liens (absolus)
    links: list[str] = []
    seen: set[str] = set()
    base = str(resp.url)
    from urllib.parse import urljoin

    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        full = urljoin(base, href)
        # ne garder que http(s)
        if not full.startswith("http"):
            continue
        # ignorer les assets
        if any(full.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".pdf", ".zip")):
            continue
        if full not in seen:
            seen.add(full)
            anchor = " ".join(t.strip() for t in a.itertext() if t.strip())[:80]
            links.append(f"{anchor or '(lien)'} -> {full}")
    links = links[:40]

    return title, text, links


def tool_web_search(query: str) -> str:
    import time

    import httpx

    query = (query or "").strip()[:300]
    if not query:
        return "Erreur: requete vide"
    ua = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Referer": "https://html.duckduckgo.com/",
    }

    def parse_ddg(text: str) -> list[str]:
        out: list[str] = []
        pairs = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S
        )
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
        for i, (url, title) in enumerate(pairs[:6]):
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                from urllib.parse import unquote

                url = unquote(m.group(1))
            clean = html_unescape(re.sub(r"<[^>]+>", "", title)).strip()
            snippet = (
                html_unescape(re.sub(r"<[^>]+>", "", snips[i])).strip()[:220]
                if i < len(snips)
                else ""
            )
            out.append(f"{i+1}. {clean}\n   URL: {url}\n   {snippet}")
        return out

    results: list[str] = []
    statuses: list[str] = []
    try:
        time.sleep(1.0)
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=ua,
            timeout=14,
            follow_redirects=True,
        )
        statuses.append(f"POST {resp.status_code}/{len(resp.text)}o")
        results = parse_ddg(resp.text)
        if not results:
            time.sleep(2.5)
            resp = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=ua,
                timeout=14,
                follow_redirects=True,
            )
            statuses.append(f"GET {resp.status_code}/{len(resp.text)}o")
            results = parse_ddg(resp.text)
    except Exception as exc:
        statuses.append(f"EXC {type(exc).__name__}: {exc}")
    if not results:
        short = " ".join(query.split()[:3])
        try:
            resp = httpx.get(
                "https://fr.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": short, "limit": 4, "format": "json"},
                headers={"User-Agent": ua["User-Agent"]},
                timeout=10,
            )
            data = resp.json()
            for title, link in zip(data[1], data[3]):
                results.append(f"- {title}\n   URL: {link}")
        except Exception as exc:
            return f"Aucun resultat web ({exc}) [{'; '.join(statuses)}]"
    return (
        "\n\n".join(results)
        if results
        else f"Aucun resultat web [{'; '.join(statuses)}]"
    )



def _lecture_externe(url: str, max_chars: int) -> tuple[str, str, list[str]]:
    """Secours Jina Reader (r.jina.ai, gratuit, sans cle, 20 requetes/min) : rend
    le texte des pages construites en JavaScript (vides sans navigateur).

    Seule l'URL (deja publique : la garde SSRF est passee) quitte la machine.
    NEO_LECTEUR_EXTERNE=0 le desactive.
    """
    import os

    if os.environ.get("NEO_LECTEUR_EXTERNE", "1") == "0" or refus_url(url):
        return "", "", []
    try:
        with client_http(timeout=30) as client:
            r = client.get(f"https://r.jina.ai/{url}",
                           headers={"User-Agent": "Neo/1.0 (assistant local)", "X-Retain-Images": "none"})
        if r.status_code >= 400:
            return "", "", []
        brut = r.text
    except Exception:
        return "", "", []
    titre = (re.search(r"^Title:\s*(.+)$", brut, re.M) or [None, ""])[1].strip()
    corps = brut.split("Markdown Content:", 1)[-1].strip()
    # Page de verification anti-robot (Cloudflare...) : pas un contenu.
    if re.search(r"just a moment|security verification|verify you are (?:not a bot|human)|"
                 r"enable javascript and cookies|attention required", brut[:1500], re.I):
        return "", "", []
    liens = [f"{t[:80]} -> {u}" for t, u in re.findall(r"\[([^\]]{2,})\]\((https?://[^)\s]+)\)", corps)][:40]
    return titre, corps[:max_chars], liens


def _page(url: str, max_chars: int) -> tuple[str, str, list[str]]:
    """Lecture locale d'abord ; lecture externe seulement si la page s'est ouverte
    normalement mais que son texte est presque vide (page construite en
    JavaScript). Un site qui refuse (403, 429, anti-robot) n'est jamais
    contourne : son refus est rapporte tel quel."""
    titre, texte, liens = _fetch_page(url, max_chars=max_chars)
    pauvre = len(texte) < 300 and not texte.startswith("Erreur") and not texte.startswith(
        ("Contenu non HTML", "refusé"))
    if pauvre:
        t2, x2, l2 = _lecture_externe(url, max_chars)
        if len(x2) > len(texte if not texte.startswith("Erreur") else ""):
            return t2 or titre, x2, l2 or liens
    return titre, texte, liens


def tool_web_fetch(url: str) -> str:
    title, text, links = _page(url, max_chars=4000)
    if not text:
        return title or text or f"Erreur de recuperation: {url}"
    head = f"[{url}]"
    if title:
        head += f" — {title}"
    out = f"{head}\n{text}"
    if links:
        out += "\n\nLIENS SUR LA PAGE:\n" + "\n".join(links[:15])
    return out[:5000]


def tool_web_browse(url: str) -> str:
    """Navigation legere : recupere une page, extrait le texte propre et les liens
    cliquables. Permet au LLM de 'naviguer' par liens sans navigateur lourd.
    """
    title, text, links = _page(url, max_chars=5000)
    if not text:
        return title or text or f"Page vide ou erreur: {url}"
    out = []
    if title:
        out.append(f"TITRE: {title}")
    out.append(f"URL: {url}")
    out.append(f"TEXTE:\n{text}")
    if links:
        out.append("\nLIENS (pour naviguer):\n" + "\n".join(links))
    return "\n".join(out)[:7000]



def tool_deep_research(sujet: str, profondeur: int = 2) -> str:
    """Agent de recherche : multi-requetes web + lecture de pages + synthese."""
    sujet = (sujet or "").strip()[:300]
    if not sujet:
        return "Erreur: sujet vide"
    profondeur = max(1, min(int(profondeur or 2), 3))

    brut = _llm_rapide(
        f"Genere exactement {profondeur + 1} requetes de recherche web courtes et "
        f"complementaires (une par ligne, sans numeros ni tirets) pour repondre a : {sujet}",
        90,
    )
    requetes = [l.strip(" -*0123456789.") for l in brut.splitlines() if l.strip()]
    requetes = [r for r in requetes if len(r) > 5][: profondeur + 1] or [sujet]
    if sujet not in requetes:
        requetes.insert(0, sujet)

    resultats_bruts = []
    urls_vedettes = []
    import re as _re

    motif_url = _re.compile(r"https?://[^\s)\]>\"]+")
    import time as _time

    for i, req in enumerate(requetes):
        if i:
            _time.sleep(1.5)
        res = tool_web_search(req)
        resultats_bruts.append(f"### Requete: {req}\n{res[:2500]}")
        trouvées = motif_url.findall(res)
        if trouvées:
            for u in trouvées:
                if "duckduckgo" in u:
                    continue
                # nettoyer la ponctuation finale
                u = u.rstrip(".,;:!?")
                if u not in urls_vedettes:
                    urls_vedettes.append(u)

    # lecture des pages les plus pertinentes (dedupliquees)
    nb_pages = max(2, profondeur + 1)
    pages = []
    lus: set[str] = set()
    for url in urls_vedettes[: nb_pages * 2]:
        if len(pages) >= nb_pages:
            break
        if url in lus:
            continue
        lus.add(url)
        title, text, links = _fetch_page(url, max_chars=3500)
        if not text or len(text) < 200:
            continue
        bloc = f"### Source: {url}"
        if title:
            bloc += f" — {title}"
        bloc += f"\n{text}"
        # ajoute 1-2 liens internes prometteurs (1 niveau de plus)
        internes = [l.split(" -> ")[-1] for l in links if url.split("/")[2] in l][:2]
        for iu in internes:
            if iu in lus or len(pages) >= nb_pages + 1:
                continue
            lus.add(iu)
            t2, txt2, _ = _fetch_page(iu, max_chars=2000)
            if txt2 and len(txt2) >= 200:
                b2 = f"### Source (lien): {iu}"
                if t2:
                    b2 += f" — {t2}"
                b2 += f"\n{txt2}"
                pages.append(b2)
        pages.append(bloc)

    synthese_in = "\n\n".join(resultats_bruts + pages)[:12000]
    synthese = _llm_rapide(
        f"Synthetise en francais (10-15 phrases max, points cles structurés) la reponse "
        f"a cette question a partir des extraits suivants. Cite les URLs utiles.\n\n"
        f"Question: {sujet}\n\nExtraits:\n{synthese_in}",
        240,
    )
    if not synthese:
        synthese = "(Synthese LLM indisponible - resultats bruts ci-dessous)"

    return (
        f"[deep_research | {len(requetes)} requetes | {len(pages)} pages lues]\n\n"
        f"SYNTHESE:\n{synthese}\n\nSOURCES PRINCIPALES:\n"
        + "\n".join(f"- {u}" for u in dict.fromkeys(urls_vedettes))
    )[:6000]
