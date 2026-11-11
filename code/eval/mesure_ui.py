"""Couverture du theme dans l'interface : part des couleurs qui suivent le skin.

Une couleur ecrite en dur (#hex, rgb(), rgba(), hsl()) hors des regles propres a un
skin ne change pas quand l'utilisateur change de skin : elle « fuit ».
Ne fuient pas : les regles propres a un skin (definitions de jetons, decors de fond,
apercus de la galerie) et le noir/blanc translucides (ombres, voiles sur image),
neutres dans tous les skins.
Score = references a des variables / (references + couleurs qui fuient), en %.

Usage : python -I code/eval/mesure_ui.py [--src <dossier frontend/src>] [--json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
COULEUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\)")
VARIABLE = re.compile(r"var\(--[\w-]+")
# Regles propres a un skin (definitions et decors) : leurs couleurs sont legitimes.
PROPRE_SKIN = re.compile(r":root|skin-|data-skin|\.bg-|apercu|\.bulle|\.blob|\.cyber-|\.retro-|\.aurore-")
NEUTRE = re.compile(r"rgba\(\s*(0\s*,\s*0\s*,\s*0|255\s*,\s*255\s*,\s*255)\s*,")
REGLE = re.compile(r"([^{}]*)\{([^{}]*)\}")


def _sans_commentaires(texte: str) -> str:
    texte = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", texte)


def _couleurs_qui_fuient(texte: str) -> int:
    # rgba(var(--accent-rgb), a) ou rgba(${jeton}, a) : composees depuis un jeton, donc themees.
    return sum(1 for c in COULEUR.findall(texte)
               if not NEUTRE.match(c) and "var(" not in c and "${" not in c)


def _css_sans_regles_de_skin(texte: str) -> str:
    return REGLE.sub(lambda m: "" if PROPRE_SKIN.search(m.group(1)) else m.group(0), texte)


def mesurer(src: Path = SRC) -> dict:
    fichiers = sorted(p for p in src.rglob("*") if p.suffix in {".css", ".tsx", ".ts"} and ".test." not in p.name)
    total_dur, total_var, detail = 0, 0, {}
    for p in fichiers:
        texte = _sans_commentaires(p.read_text(encoding="utf-8"))
        if p.suffix == ".css":
            texte = _css_sans_regles_de_skin(texte)
        elif p.name in {"skins.ts", "SkinBackground.tsx"}:
            continue  # apercus et decors propres a chaque skin
        dur = _couleurs_qui_fuient(texte)
        var = len(VARIABLE.findall(texte))
        if dur or var:
            detail[p.relative_to(src).as_posix()] = {"en_dur": dur, "variables": var}
        total_dur += dur
        total_var += var
    score = round(100 * total_var / max(1, total_var + total_dur), 1)
    return {"score_couverture_theme": score, "couleurs_en_dur": total_dur,
            "references_variables": total_var, "fichiers": detail}


if __name__ == "__main__":
    src = Path(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv else SRC
    r = mesurer(src)
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(f"couverture du theme : {r['score_couverture_theme']} % "
              f"({r['references_variables']} variables, {r['couleurs_en_dur']} couleurs en dur)")
        for f, d in sorted(r["fichiers"].items(), key=lambda x: -x[1]["en_dur"])[:8]:
            print(f"  {d['en_dur']:4d} en dur  {d['variables']:4d} var  {f}")
