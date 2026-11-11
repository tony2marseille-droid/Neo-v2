"""Donnees publiques gratuites, sans cle : taux de change (BCE) et adresses francaises (IGN).

- Frankfurter (https://frankfurter.dev) : taux de reference publies chaque jour ouvre par la
  Banque centrale europeenne ; aucune cle, aucun quota annonce.
- Geoplateforme de l'IGN (https://data.geopf.fr/geocodage) : service officiel qui remplace
  api-adresse.data.gouv.fr ; aucune cle, 50 requetes/s par adresse IP.
Seul le texte de la question (montant, devises, adresse) quitte la machine.
"""
from __future__ import annotations

import re

_UA = {"User-Agent": "Neo/1.0 (assistant local)"}

# Noms courants -> codes ISO 4217 (la BCE publie une trentaine de devises).
_DEVISES = {
    "euro": "EUR", "euros": "EUR", "€": "EUR",
    "dollar": "USD", "dollars": "USD", "$": "USD", "usd": "USD", "dollar americain": "USD",
    "livre": "GBP", "livres": "GBP", "livre sterling": "GBP", "£": "GBP",
    "franc suisse": "CHF", "francs suisses": "CHF", "yen": "JPY", "yens": "JPY",
    "yuan": "CNY", "yuans": "CNY", "dollar canadien": "CAD", "dollars canadiens": "CAD",
    "dollar australien": "AUD", "dollars australiens": "AUD", "couronne suedoise": "SEK",
    "couronnes suedoises": "SEK", "couronne norvegienne": "NOK", "couronne danoise": "DKK",
    "zloty": "PLN", "zlotys": "PLN", "roupie": "INR", "roupies": "INR", "real": "BRL",
    "reais": "BRL", "won": "KRW", "lire turque": "TRY", "livre turque": "TRY",
    "peso mexicain": "MXN", "rand": "ZAR", "forint": "HUF", "couronne tcheque": "CZK",
}


def _sans_accents(t: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def code_devise(texte: str) -> str:
    """« dollars », « usd », « $ » -> « USD » ; chaine vide si inconnue."""
    t = _sans_accents((texte or "").strip().lower())
    if re.fullmatch(r"[a-z]{3}", t):
        return t.upper()
    return _DEVISES.get(t, "")


def tool_taux_change(montant: float = 1.0, de: str = "EUR", vers: str = "USD") -> str:
    import httpx

    a, b = code_devise(de), code_devise(vers)
    if not a or not b:
        return f"Erreur: devise inconnue ({de!r} -> {vers!r}). Utilise un code ISO (EUR, USD, GBP, CHF, JPY...)."
    try:
        montant = float(str(montant).replace(",", ".").replace(" ", ""))
    except ValueError:
        return f"Erreur: montant illisible ({montant!r})"
    if a == b:
        return f"{montant:g} {a} = {montant:g} {b}"
    try:
        r = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": a, "symbols": b},
                      headers=_UA, timeout=10)
        r.raise_for_status()
        d = r.json()
        taux = float(d["rates"][b])
    except Exception as exc:
        return f"Erreur: taux indisponible ({exc})"
    return (f"{_fr(montant)} {a} = {_fr(montant * taux)} {b} (taux BCE du {d.get('date', '?')} : "
            f"1 {a} = {_fr(taux, 4)} {b}). Taux de reference, hors commission bancaire.")


def _fr(n: float, decimales: int = 2) -> str:
    """1234.5 -> « 1 234,50 » (espace insecable fine evitee : espace simple)."""
    return f"{n:,.{decimales}f}".replace(",", " ").replace(".", ",")


def tool_adresse(recherche: str, nombre: int = 3) -> str:
    import httpx

    recherche = (recherche or "").strip()[:200]
    if len(recherche) < 3:
        return "Erreur: adresse trop courte"
    n = max(1, min(int(nombre or 3), 5))
    # Deux index : adresses (« 10 rue de la Paix ») et lieux nommes
    # (« mairie d'Annecy », « gare Saint-Charles ») ; meilleurs scores d'abord.
    resultats = []
    for index in ("address", "poi"):
        try:
            r = httpx.get("https://data.geopf.fr/geocodage/search",
                          params={"q": recherche, "limit": n, "index": index}, headers=_UA, timeout=10)
            r.raise_for_status()
            resultats += r.json().get("features", [])
        except Exception as exc:
            erreur = str(exc)
    if not resultats:
        return (f"Erreur: geocodage indisponible ({erreur})" if "erreur" in locals()
                else f"Aucune adresse trouvee pour « {recherche} » (service limite a la France).")
    resultats.sort(key=lambda f: -float(f.get("properties", {}).get("score", 0)))

    def _un(v):
        return v[0] if isinstance(v, list) and v else v

    lignes = []
    for f in resultats[:n]:
        p = f.get("properties", {})
        lon, lat = (f.get("geometry", {}).get("coordinates") or [None, None])[:2]
        nom = p.get("label") or f"{_un(p.get('toponym') or p.get('name'))}, {_un(p.get('postcode'))} {_un(p.get('city'))}"
        genre = f" [{_un(p.get('category'))}]" if p.get("category") else ""
        lignes.append(f"- {nom}{genre} (code postal {_un(p.get('postcode'))}, code INSEE {_un(p.get('citycode'))}, "
                      f"GPS {lat}, {lon})")
    return "Adresses (IGN, Geoplateforme) :\n" + "\n".join(lignes)
