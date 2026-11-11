"""Banc des agents a outils : le bon outil est-il appele, la reponse est-elle juste ?

Passe par l'API reelle (/api/chat, agent « auto ») comme l'interface. Seuls des outils
sans effet de bord sont sollicites (change, adresse, meteo, heure, calcul, statistiques,
recherche web). Les valeurs attendues variables (taux du jour, heure) sont calculees au
moment du banc. Les conversations creees sont supprimees.

Usage : python -I code/eval/bench_outils.py <etiquette> [--modele gemma4:12b] [--sortie dossier]
        (sans --modele : modele des agents tel que configure, `default_model`)
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ICI = Path(__file__).resolve().parent


def _taux(de: str, vers: str) -> float:
    with urllib.request.urlopen(f"https://api.frankfurter.dev/v1/latest?base={de}&symbols={vers}", timeout=15) as r:
        return float(json.loads(r.read())["rates"][vers])


def _nombres(texte: str) -> list[float]:
    t = re.sub(r"(?<=\d)[\s  ](?=\d{3}\b)", "", texte)
    return [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", t)]


def _proche(valeur: float, tolerance: float = 0.01):
    return lambda rep: any(abs(n - valeur) <= max(tolerance * abs(valeur), 0.01) for n in _nombres(rep))


def _contient(*mots):
    return lambda rep: any(m.lower() in rep.lower() for m in mots)


AUCUN = "(aucun outil)"


def cas() -> list[tuple[str, set[str], object]]:
    usd, chf, jpy = _taux("EUR", "USD"), _taux("CHF", "EUR"), _taux("GBP", "JPY")
    heure = datetime.now()
    heures = {heure.strftime("%H"), str(heure.hour)}
    return [
        ("Combien font 250 euros en dollars ?", {"taux_change"}, _proche(250 * usd)),
        ("Convertis 1000 francs suisses en euros", {"taux_change"}, _proche(1000 * chf)),
        ("Combien vaut une livre sterling en yens aujourd'hui ?", {"taux_change"}, _proche(jpy)),
        ("Quel est le code postal d'Annecy ?", {"adresse"}, _contient("74000")),
        ("Où se trouve la mairie d'Annecy ?", {"adresse"}, _contient("74000", "45.89", "45,89", "45.90", "45,90")),
        ("Quel temps fait-il à Marseille en ce moment ?", {"meteo"}, _contient("°", "degré")),
        ("Va-t-il pleuvoir demain à Lyon ?", {"meteo"}, _contient("pluie", "pleuv", "averse", "précipitation", "mm", "sec")),
        # Depuis le 09/11/2026 l'heure est dans la fiche de Neo : repondre sans outil est juste.
        ("Quelle heure est-il ?", {"get_datetime", AUCUN}, lambda rep: any(h in rep for h in heures)),
        # Depuis le 09/11/2026 un calcul simple est resolu par Neo lui-meme (sans outil).
        ("Calcule 17,5 % de 2340", {"calculator", "execute_python", "run_python", AUCUN}, _proche(409.5, 0.001)),
        ("Combien de mémoire vive mon PC utilise-t-il en ce moment ?", {"system_stats"}, _contient("Go", "GB", "%")),
        ("Cherche sur le web quelle est la capitale de l'Australie", {"web_search", "deep_research", "wikipedia"},
         _contient("Canberra")),
    ]


def _chat(url: str, message: str, modele: str) -> dict:
    corps = json.dumps({"message": message, "model": modele, "agent_id": "auto", "temperature": 0.2,
                        "tools_enabled": False, "rag_enabled": False, "web_enabled": False}).encode()
    t0 = time.time()
    meta, texte, outils = {}, "", []
    req = urllib.request.Request(f"{url}/api/chat", corps, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        for brut in r:
            ligne = brut.decode("utf-8", "replace").strip()
            if not ligne.startswith("data:"):
                continue
            try:
                ev = json.loads(ligne[5:])
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "meta":
                meta = ev
            elif t == "tool_start":
                outils.append(ev.get("name"))
            elif t == "token":
                texte += ev.get("content", "")
            elif t == "replace":
                texte = ev.get("content", "")
            elif t == "done":
                texte = ev.get("content") or texte
                break
            elif t == "error":
                texte += f"\n[ERREUR {ev.get('error')}]"
                break
    cid = meta.get("conversation_id")
    if cid:
        try:
            urllib.request.urlopen(urllib.request.Request(f"{url}/api/conversations/{cid}", method="DELETE"),
                                   timeout=20)
        except Exception:
            pass
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.S).strip()
    return {"agent": meta.get("agent"), "modele": meta.get("model"), "outils": outils,
            "duree_s": round(time.time() - t0, 1), "reponse": texte}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("etiquette")
    ap.add_argument("--url", default="http://127.0.0.1:8320")
    ap.add_argument("--modele", default="auto")
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    args = ap.parse_args()
    lignes = []
    for message, attendus, juste in cas():
        try:
            r = _chat(args.url, message, args.modele)
        except Exception as e:
            r = {"agent": None, "modele": None, "outils": [], "duree_s": 0.0, "reponse": f"[ECHEC {e}]"}
        bon_outil = bool(attendus & set(r["outils"] or [AUCUN]))
        exacte = bool(juste(r["reponse"]))
        r.update({"message": message, "outil_attendu": sorted(attendus), "bon_outil": bon_outil,
                  "reponse_juste": exacte, "reponse": r["reponse"][:1500]})
        print(json.dumps({k: r[k] for k in ("message", "agent", "modele", "outils", "bon_outil", "reponse_juste",
                                            "duree_s")}, ensure_ascii=False), flush=True)
        lignes.append(r)
    n = len(lignes)
    resume = {
        "etiquette": args.etiquette, "modele": args.modele, "date": time.strftime("%Y-%m-%d %H:%M"),
        "bon_outil": f"{sum(l['bon_outil'] for l in lignes)}/{n}",
        "reponses_justes": f"{sum(l['reponse_juste'] for l in lignes)}/{n}",
        "duree_moyenne_s": round(sum(l["duree_s"] for l in lignes) / n, 1),
        "duree_mediane_s": sorted(l["duree_s"] for l in lignes)[n // 2],
        "questions": lignes,
    }
    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / f"outils_{args.etiquette}.json").write_text(json.dumps(resume, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
    print("RESUME", json.dumps({k: v for k, v in resume.items() if k != "questions"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
