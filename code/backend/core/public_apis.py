"""Catalogue public-apis/public-apis (annuaire d'APIs publiques).

Telecharge le README.md brut depuis GitHub, parse les tableaux Markdown par
categorie, met en cache le resultat dans data/public_apis_cache.json avec un
horodatage. Re-telecharge automatiquement si le cache est plus vieux que
CACHE_TTL (24 h) ou sur demande explicite.

Aucun code du depot public-apis n'est copie : on ne fait que lire son README
Markdown public, ce qui est son usage prevu.
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / "data" / "public_apis_cache.json"
README_URL = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
CACHE_TTL = timedelta(hours=24)

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _parse_link(cell: str) -> str:
    m = _LINK_RE.search(cell)
    return m.group(2).strip() if m else cell.strip()


def _cell_name(cell: str) -> str:
    m = _LINK_RE.search(cell)
    return m.group(1).strip() if m else cell.strip()


def _parse_readme(text: str) -> list[dict]:
    entries: list[dict] = []
    category = ""
    header: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("### "):
            category = s[4:].strip()
            header = None
            continue
        if "|" not in s:
            header = None
            continue
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        if not cells:
            continue
        # separateur de tableau
        if all(set(c.strip()) <= set("-: ") for c in cells):
            continue
        first = cells[0].lower().strip("`")
        # ligne d'en-tete
        if first in ("api", "name"):
            header = [c.lower().strip("`") for c in cells]
            continue
        if header is None:
            continue
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        name_cell = row.get("api") or row.get("name") or ""
        name = _cell_name(name_cell)
        if not name or name.lower() in ("api", "name"):
            continue
        auth = (row.get("auth", "") or "").strip()
        if not auth:
            auth = "No"
        is_mcp = "transport" in row
        entry = {
            "name": name,
            "description": row.get("description", ""),
            "auth": auth,
            "link": _parse_link(name_cell),
            "category": category,
            "kind": "mcp" if is_mcp else "api",
        }
        if "https" in row:
            entry["https"] = row["https"].lower() in ("yes", "true")
        if "cors" in row:
            entry["cors"] = row["cors"]
        if "transport" in row:
            entry["transport"] = row["transport"]
        entries.append(entry)
    return entries


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_cache() -> dict | None:
    try:
        if not CACHE_PATH.is_file():
            return None
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "entries" not in data:
            return None
        return data
    except Exception:
        return None


def _is_stale(cache: dict | None) -> bool:
    if not cache:
        return True
    try:
        updated = datetime.fromisoformat(cache["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - updated > CACHE_TTL
    except Exception:
        return True


def _fetch() -> list[dict]:
    req = urllib.request.Request(README_URL, headers={"User-Agent": "Neo/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return _parse_readme(text)


def get_catalog(force: bool = False) -> dict:
    cache = _read_cache()
    if force or _is_stale(cache):
        try:
            entries = _fetch()
            cache = {"updated_at": _now_iso(), "entries": entries}
            try:
                CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        except Exception as e:
            # Repli : cache perime mais present vaut mieux que rien.
            if cache is None:
                cache = {"updated_at": _now_iso(), "entries": [], "error": str(e)}
            else:
                cache = dict(cache)
                cache["error"] = str(e)
    return cache


if __name__ == "__main__":
    import sys
    force = "--refresh" in sys.argv
    c = get_catalog(force=force)
    print(json.dumps({"count": len(c.get("entries", [])), "updated_at": c.get("updated_at"),
                      "error": c.get("error")}, ensure_ascii=False))
