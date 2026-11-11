"""Banc de la comprehension des demandes d'image (le « brief » du studio).

Cas pieges releves en production (mot mal ecrit a comprendre selon le contexte) et
demandes ordinaires. Pour chaque cas : le brief contient-il le bon sujet (et pas le
mauvais), le cadrage attendu, un prompt en anglais ; combien de temps, dont combien a
charger le modele.

Usage : python -I code/eval/bench_brief.py <modele> [--reflexion 0|1] [--sortie dossier]
Le modele deja charge dans Ollama n'est pas decharge : on mesure la situation reelle
(celle ou l'agent Image vient de parler avec le modele de conversation).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))
from backend.core import direction  # noqa: E402

# (demande, attendus : au moins un par groupe « a|b », interdits, cadrage attendu ou "")
CAS = [
    ("vaisseau spaciaux qui s'approche de constellation. je veux voir un lazar, un trou noir et un trou de "
     "vers avec des effets plongeant 3d. en photoréaliste", ["quasar|blazar", "black hole", "wormhole"], [], ""),
    ("un vaisseau spatial qui apercoit un lazar avec un effet 3d plongeant photoréaliste", ["quasar|blazar"], [], ""),
    ("un chat qui court après le point rouge d'un lazar dans le salon", ["laser", "cat"], ["quasar", "blazar"], ""),
    ("un soldat du futur qui tire au fusil lazar dans une ruine", ["laser", "soldier"], ["quasar", "blazar"], ""),
    ("Fait moi un visage d’un jeune avec un dégradé au laser", ["fade"], ["laser beam", "gradient of light"], "portrait"),
    ("une nébuleuse avec un pulsart au centre", ["pulsar", "nebula"], [], ""),
    ("film de science diction du type interstellar", ["black hole|gargantua"], [], "paysage"),
    ("Fais-moi le portrait d'une femme de 35 ans aux cheveux roux, en extérieur, lumière naturelle, très réaliste",
     ["red hair|redhead|ginger|auburn|copper", "woman"], ["black hole", "hole"], "portrait"),
    ("un dragon sanguinaire effrayant et qui crache du feu en embrasant un chateau",
     ["dragon", "fire|flame", "castle"], [], ""),
    ("la plus jolie vue sous-marine tropicale avec des coraux et des poissons", ["coral", "fish"], [], ""),
    ("un homme brun aux yeux verts qui boit un café en terrasse à Marseille", ["green eyes", "coffee"], ["worm"], ""),
    ("un logo minimaliste pour une boulangerie qui s'appelle Le Fournil", ["logo", "bakery|bread|wheat"], [], "carre"),
]


def juger(b: dict, attendus: list[str], interdits: list[str], cadrage: str) -> tuple[bool, str]:
    if not b.get("ok"):
        return False, "brief en echec : " + str(b.get("erreur", ""))[:80]
    texte = (b.get("prompt", "") + " " + " ".join(b.get("elements", []))).lower()
    manquants = [g for g in attendus if not any(a in texte for a in g.split("|"))]
    presents = [i for i in interdits if i in texte]
    raisons = []
    if manquants:
        raisons.append("manque " + ", ".join(manquants))
    if presents:
        raisons.append("contresens " + ", ".join(presents))
    if cadrage and b.get("cadrage") != cadrage:
        raisons.append(f"cadrage {b.get('cadrage')} au lieu de {cadrage}")
    return not raisons, " ; ".join(raisons)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("modele")
    ap.add_argument("--reflexion", type=int, default=1)
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    args = ap.parse_args()
    lignes, bons, t0 = [], 0, time.time()
    for demande, attendus, interdits, cadrage in CAS:
        b = direction.brief(demande, modele=args.modele, reflexion=bool(args.reflexion))
        ok, pourquoi = juger(b, attendus, interdits, cadrage)
        bons += ok
        lignes.append({"demande": demande, "ok": ok, "pourquoi": pourquoi, "s": b.get("duree_s"),
                       "chargement_s": b.get("chargement_s"), "cadrage": b.get("cadrage"),
                       "comprehension": b.get("comprehension"), "elements": b.get("elements"),
                       "prompt": b.get("prompt"), "mots_prompt": len((b.get("prompt") or "").split())})
        print(("OK " if ok else "XX ") + f"{b.get('duree_s')}s (charge {b.get('chargement_s')}s) | {demande[:58]}"
              + (f" | {pourquoi}" if pourquoi else ""), flush=True)
    durees = sorted(l["s"] or 0 for l in lignes)
    resume = {"modele": args.modele, "reflexion": bool(args.reflexion), "bons": f"{bons}/{len(CAS)}",
              "s_median": durees[len(durees) // 2], "s_total": round(time.time() - t0, 1), "cas": lignes}
    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    nom = f"brief_{args.modele.replace(':', '_')}_{'reflexion' if args.reflexion else 'direct'}.json"
    (sortie / nom).write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RESUME", json.dumps({k: v for k, v in resume.items() if k != "cas"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
