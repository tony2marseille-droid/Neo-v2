"""Banc de la retouche d'image : FLUX Kontext (actuel) contre FLUX.2-klein-4B.

Trois retouches typiques (accessoire, decor, couleur) sur des images du studio. Mesures :
temps, et jugement de Gemma 4 sur la paire originale / retouchee (consigne appliquee ?
reste preserve ?). Une planche est produite pour l'oeil.

Usage : python -I code/eval/bench_retouche.py <dossier_images> [--sortie dossier]
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import reglages  # noqa: E402
SD = reglages.chemin("LOCALAI_SD_DIR", "tools", "sd") / "bin" / "sd-cli.exe"
KLEIN = reglages.chemin("NEO_SD_RETOUCHE", "tools", "sd", "models_edit") / "flux-2-klein-4b-Q8_0.gguf"
KLEIN_VAE = reglages.chemin("NEO_SD_RETOUCHE", "tools", "sd", "models_edit") / "flux2-vae.safetensors"
QWEN3_4B = reglages.chemin("NEO_QWEN3_4B_DIR", "tools", "sd_ssd", "models") / "Qwen3-4B-Instruct-2507-Q8_0.gguf"
OLLAMA = "http://127.0.0.1:11434"

RETOUCHES = [
    ("degrade_ecran.jpg", "Add a red baseball cap on his head. Keep his face, sunglasses, jacket, lighting and "
     "background exactly identical.", "a red baseball cap is on his head"),
    ("nain_ecran.jpg", "Replace the background with a snowy mountain landscape in daylight. Keep the warrior, his "
     "face, beard, armor and sword exactly identical.", "the background is a snowy mountain landscape"),
    ("lazar_ecran.jpg", "Make the spaceship hull bright red. Keep the galaxy, the blue light beam, the stars and "
     "the asteroids exactly identical.", "the spaceship hull is bright red"),
]


def _b64(p: Path, cote: int = 1024) -> str:
    im = Image.open(p).convert("RGB")
    im.thumbnail((cote, cote))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def juger(original: Path, retouche: Path, consigne: str) -> dict:
    schema = {"type": "object", "properties": {"consigne_appliquee": {"type": "boolean"},
                                               "reste_preserve": {"type": "integer"},
                                               "qualite": {"type": "integer"}},
              "required": ["consigne_appliquee", "reste_preserve", "qualite"]}
    q = (f"Image 1 is the original, image 2 is the edited version. Expected edit: {consigne}. "
         "Answer: consigne_appliquee = is the expected edit clearly visible in image 2; reste_preserve = 0-10, "
         "how well everything else (identity, composition, colors, details) is preserved from image 1; "
         "qualite = 0-10 visual quality of image 2 (sharpness, artifacts).")
    corps = {"model": "gemma4:12b", "stream": False, "think": False, "format": schema, "keep_alive": "5m",
             "messages": [{"role": "user", "content": q, "images": [_b64(original), _b64(retouche)]}],
             "options": {"temperature": 0, "num_ctx": 16384}}
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        OLLAMA + "/api/chat", json.dumps(corps).encode(), {"Content-Type": "application/json"}), timeout=600).read())
    return json.loads(r["message"]["content"])


def liberer_vram() -> None:
    ps = json.loads(urllib.request.urlopen(OLLAMA + "/api/ps", timeout=10).read()).get("models", [])
    for m in ps:
        urllib.request.urlopen(urllib.request.Request(
            OLLAMA + "/api/generate", json.dumps({"model": m["name"], "keep_alive": 0}).encode(),
            {"Content-Type": "application/json"}), timeout=30).read()
    time.sleep(3)


def klein(entree: Path, consigne: str, sortie: Path, etapes: int = 4) -> float:
    im = Image.open(entree)
    w, h = im.size
    k = 1024 / max(w, h)
    w, h = int(w * k) // 16 * 16, int(h * k) // 16 * 16
    cmd = [str(SD), "--diffusion-model", str(KLEIN), "--llm", str(QWEN3_4B), "--vae", str(KLEIN_VAE),
           "-r", str(entree), "-p", consigne, "--steps", str(etapes), "--cfg-scale", "1.0",
           "-W", str(w), "-H", str(h), "--diffusion-fa", "--vae-tiling", "-o", str(sortie), "-s", "7"]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0 or not sortie.exists():
        raise RuntimeError(p.stderr[-800:] or p.stdout[-800:])
    return time.time() - t0


def kontext(entree: Path, consigne: str, sortie: Path) -> float:
    from backend.core.sdgen import generer
    t0 = time.time()
    generer(consigne, str(sortie), reference=str(entree), mode="edit")
    return time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("images")
    ap.add_argument("--sortie", default=str(ICI / "resultats" / "retouche"))
    args = ap.parse_args()
    src, dst = Path(args.images), Path(args.sortie)
    dst.mkdir(parents=True, exist_ok=True)
    res = []
    for nom, consigne, attendu in RETOUCHES:
        for moteur, fonction in (("klein", klein), ("kontext", kontext)):
            liberer_vram()
            sortie = dst / f"{Path(nom).stem}_{moteur}.png"
            try:
                duree = fonction(src / nom, consigne, sortie)
                fichier = sortie if sortie.exists() else next(dst.glob(f"{Path(nom).stem}_{moteur}*"), sortie)
                note = juger(src / nom, fichier, attendu)
            except Exception as e:  # noqa: BLE001
                duree, note = 0.0, {"erreur": str(e)[:300]}
            ligne = {"image": nom, "moteur": moteur, "s": round(duree, 1), **note}
            print(json.dumps(ligne, ensure_ascii=False), flush=True)
            res.append(ligne)
    (dst / "retouche.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
