"""Banc image : temps de generation et qualite percue, sur prompts et graines fixes.

Mesures par image :
- temps reel de bout en bout (ce que l'utilisateur attend) ;
- nettete a la taille d'affichage (1920 px, reduction Lanczos) : variance du
  laplacien et energie des hautes frequences (plus haut = plus de detail fin) ;
- note d'un modele de vision local (qwen2.5vl) : detail, realisme, absence
  d'artefacts, sur 10 ; il ne sait pas quelle version il juge.

Usage : E:\\ia\\.venv\\Scripts\\python.exe -I code/eval/bench_image.py <etiquette> [--code <dossier code>] [--sortie <dossier>]
Le pipeline teste est celui de --code (defaut : le code du depot).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ICI = Path(__file__).resolve().parent
PROMPTS = [
    ("portrait", "Portrait en gros plan d'un vieux pêcheur breton au visage buriné, ciré jaune, "
                 "lumière douce de fin de journée sur le port", 1234),
    ("paysage", "Un village de montagne dans les Alpes au lever du soleil, brume dans la vallée, "
                "chalets en bois, sommets enneigés", 4321),
    ("objet", "Une montre mécanique de luxe posée sur une table en noyer, cadran guilloché, "
              "photo produit en studio", 777),
]
VISION = "qwen2.5vl:7b"
CONSIGNE = ("Tu es un photographe expert et exigeant. Évalue cette image générée. Réponds UNIQUEMENT en JSON : "
            '{"detail": n, "realisme": n, "artefacts": n, "commentaire": "..."} avec n de 1 à 10 '
            "(detail = finesse des textures ; realisme = crédibilité photographique ; "
            "artefacts = 10 si aucun défaut visible, 1 si très dégradée).")


def nettete(chemin: Path) -> dict:
    img = Image.open(chemin).convert("L")
    img = img.resize((1920, round(1920 * img.height / img.width)), Image.LANCZOS)
    a = np.asarray(img, dtype=np.float32)
    lap = (a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1] - 4 * a[1:-1, 1:-1])
    gx, gy = np.diff(a, axis=1)[:-1, :], np.diff(a, axis=0)[:, :-1]
    return {"laplacien": round(float(lap.var()), 1),
            "tenengrad": round(float(np.mean(gx * gx + gy * gy)), 1)}


def note_vision(chemin: Path) -> dict:
    img = Image.open(chemin).convert("RGB")
    img.thumbnail((1344, 1344), Image.LANCZOS)
    tampon = io.BytesIO()
    img.save(tampon, "JPEG", quality=92)
    corps = json.dumps({"model": VISION, "prompt": CONSIGNE, "images": [base64.b64encode(tampon.getvalue()).decode()],
                        "stream": False, "format": "json", "options": {"temperature": 0, "seed": 1}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", corps, {"Content-Type": "application/json"})
    try:
        r = json.loads(json.loads(urllib.request.urlopen(req, timeout=300).read())["response"])
        notes = {k: float(r.get(k, 0)) for k in ("detail", "realisme", "artefacts")}
        notes["moyenne"] = round(sum(notes.values()) / 3, 2)
        notes["commentaire"] = str(r.get("commentaire", ""))[:200]
        return notes
    except Exception as e:  # le banc continue : la note manquante est signalee
        return {"erreur": str(e)[:200]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("etiquette")
    ap.add_argument("--code", default=str(ICI.parent))
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    args = ap.parse_args()
    sys.path.insert(0, args.code)
    from backend.core import sdgen  # noqa: E402

    dossier = Path(args.sortie) / f"image_{args.etiquette}"
    dossier.mkdir(parents=True, exist_ok=True)
    resultats = []
    for nom, prompt, graine in PROMPTS:
        sortie = dossier / f"{nom}.jpg"
        t0 = time.time()
        message = sdgen.generer(prompt, str(sortie), graine=graine)
        duree = round(time.time() - t0, 1)
        fichier = next((p for p in dossier.glob(f"{nom}.*") if "_apercu" not in p.name), None)
        r = {"nom": nom, "duree_s": duree, "message": message[:300]}
        if fichier:
            r.update({"fichier": str(fichier), "octets": fichier.stat().st_size,
                      "taille": list(Image.open(fichier).size), **nettete(fichier), "vision": note_vision(fichier)})
        print(json.dumps(r, ensure_ascii=False), flush=True)
        resultats.append(r)
    ok = [r for r in resultats if "laplacien" in r]
    resume = {
        "etiquette": args.etiquette, "date": time.strftime("%Y-%m-%d %H:%M"),
        "duree_moyenne_s": round(sum(r["duree_s"] for r in resultats) / len(resultats), 1),
        "laplacien_moyen": round(sum(r["laplacien"] for r in ok) / max(1, len(ok)), 1),
        "note_vision_moyenne": round(sum(r["vision"].get("moyenne", 0) for r in ok) / max(1, len(ok)), 2),
        "images": resultats,
    }
    (Path(args.sortie) / f"image_{args.etiquette}.json").write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RESUME", json.dumps({k: v for k, v in resume.items() if k != "images"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
