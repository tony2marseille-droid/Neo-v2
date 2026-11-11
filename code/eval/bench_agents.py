"""Banc conversationnel et raisonnement de Neo, mesure par l'API reelle (/api/chat).

Envoie chaque question comme le fait l'interface (modele « auto », agent « auto »,
temperature 0,5), recupere la reponse finale du flux SSE, puis :
- raisonnement / calcul : reponse attendue verifiee automatiquement ;
- code : la fonction produite est executee contre des tests ;
- conversation, explication, conseil, identite : note de 1 a 10 par un juge local
  (qwen3:14b, mode reflexion) selon une grille fixe ; le juge ne sait pas quelle
  version il note.
Les conversations creees par le banc sont supprimees a la fin.

Usage : python -I code/eval/bench_agents.py <etiquette> [--url http://127.0.0.1:8320] [--sortie dossier]
        [--modele qwen3.6:35b-a3b]   (sans --modele : modele « auto », comme l'interface)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ICI = Path(__file__).resolve().parent
OLLAMA = "http://127.0.0.1:11434"
JUGE = "qwen3:14b"

# (categorie, question, verification)
#   verification : ("nombre", valeur) | ("contient", [mots, ...]) | ("code", nom_fonction, tests) | ("juge", grille)
QUESTIONS = [
    # Raisonnement verifiable
    ("raisonnement", "Un train part à 14 h 10 et roule 2 h 55. Il a ensuite 35 minutes de retard. "
                     "À quelle heure arrive-t-il ? Réponds au format HH:MM.", ("contient", ["17:40", "17h40", "17 h 40"])),
    ("raisonnement", "Si 5 machines fabriquent 5 pièces en 5 minutes, combien de minutes faut-il à "
                     "100 machines pour fabriquer 100 pièces ?", ("nombre", 5)),
    ("raisonnement", "Une batte et une balle coûtent 1,10 € au total. La batte coûte 1 € de plus que la "
                     "balle. Combien coûte la balle, en centimes ?", ("nombre", 5)),
    ("raisonnement", "J'ai 3 frères. Chacun de mes frères a 2 sœurs. Combien d'enfants y a-t-il dans la "
                     "famille, sachant que je suis une fille ?", ("nombre", 5)),
    ("raisonnement", "Quel est le plus petit nombre entier positif divisible par 2, 3, 4, 5 et 6 ?", ("nombre", 60)),
    ("raisonnement", "Un escargot monte de 3 m le jour et redescend de 2 m la nuit dans un puits de 10 m. "
                     "Au bout de combien de jours sort-il ?", ("nombre", 8)),
    # Raisonnement difficile (plusieurs etapes, pieges)
    ("raisonnement", "Un prix augmente de 20 %, puis baisse de 20 %. Il vaut alors 96 €. Quel était le prix "
                     "initial, en euros ?", ("nombre", 100)),
    ("raisonnement", "Quel est le reste de la division euclidienne de 2 puissance 100 par 7 ?", ("nombre", 2)),
    ("raisonnement", "Un nénuphar double de surface chaque jour et couvre tout l'étang en 48 jours. "
                     "En combien de jours couvre-t-il la moitié de l'étang ?", ("nombre", 47)),
    ("raisonnement", "Combien d'entiers entre 1 et 1000 inclus sont divisibles par 3 ou par 5 ?", ("nombre", 467)),
    ("raisonnement", "Alice a deux fois l'âge qu'avait Bob quand Alice avait l'âge qu'a Bob aujourd'hui. "
                     "Alice a 40 ans. Quel âge a Bob ?", ("nombre", 30)),
    ("raisonnement", "Entre midi et minuit (bornes exclues), combien de fois l'aiguille des heures et celle "
                     "des minutes se superposent-elles exactement ?", ("nombre", 10)),
    # Code execute
    ("code", "Écris une fonction Python `est_palindrome(s)` qui renvoie True si la chaîne est un "
             "palindrome en ignorant la casse, les espaces et la ponctuation. Donne uniquement le code.",
     ("code", "est_palindrome", [("'Ésope reste ici et se repose'", False), ("'A man, a plan, a canal: Panama'", True),
                                 ("'abc'", False), ("''", True)])),
    ("code", "Écris une fonction Python `fusionner(a, b)` qui fusionne deux listes triées en une liste "
             "triée, en O(n). Donne uniquement le code.",
     ("code", "fusionner", [("[1,4,9], [2,3,10]", [1, 2, 3, 4, 9, 10]), ("[], [1]", [1]), ("[5], []", [5])])),
    ("code", "Écris une fonction Python `romain(n)` qui convertit un entier de 1 à 3999 en chiffres romains. "
             "Donne uniquement le code.",
     ("code", "romain", [("1994", "MCMXCIV"), ("58", "LVIII"), ("3999", "MMMCMXCIX"), ("4", "IV")])),
    ("code", "Écris une fonction Python `trier_par_frequence(s)` qui renvoie les caractères de s triés par "
             "fréquence décroissante, à égalité par ordre de code Unicode croissant, chaque caractère répété "
             "autant de fois qu'il apparaît. Donne uniquement le code.",
     ("code", "trier_par_frequence", [("'tree'", "eert"), ("'cccaaa'", "aaaccc"), ("'Aabb'", "bbAa")])),
    ("code", "Écris une fonction Python `bien_parenthese(s)` qui vérifie que (), [] et {} sont correctement "
             "imbriqués dans s (les autres caractères sont ignorés). Donne uniquement le code.",
     ("code", "bien_parenthese", [("'([]{})'", True), ("'([)]'", False), ("'a(b)c'", True), ("'(('", False),
                                  ("''", True)])),
    # Identite et personnage
    ("identite", "Salut ! Qui es-tu, au juste ?", ("juge", "identite")),
    ("identite", "Comment tu t'appelles ?", ("contient", ["Néo", "Neo"])),
    # Conversation naturelle
    ("conversation", "Salut, ça va ? J'ai eu une journée épuisante.", ("juge", "conversation")),
    ("conversation", "J'ai un entretien d'embauche demain pour un poste de développeur et je stresse. "
                     "Tu as des conseils ?", ("juge", "conseil")),
    ("conversation", "Tu préfères le thé ou le café ? Et pourquoi ?", ("juge", "conversation")),
    ("conversation", "Je n'arrive pas à me motiver pour reprendre le sport. Tu m'aides à trouver un plan "
                     "réaliste ?", ("juge", "conseil")),
    ("conversation", "Raconte-moi un truc surprenant que tu as appris récemment.", ("juge", "conversation")),
    # Explication
    ("explication", "Explique-moi simplement pourquoi le ciel est bleu.", ("juge", "explication")),
    ("explication", "Quelle est la différence entre un processus et un thread ? Sois concis.", ("juge", "explication")),
    ("explication", "Pourquoi la dette technique coûte-t-elle cher à long terme ? Donne un exemple concret.",
     ("juge", "explication")),
]

# Niveau expert (ajoute le 09/11/2026) : le banc de base etait sature (98,5/100
# pour qwen3:14b) et ne departageait plus deux cerveaux. `--niveau expert`.
QUESTIONS_EXPERT = [
    ("expert_raisonnement", "Trois boîtes sont étiquetées « pommes », « oranges » et « mélange », et toutes les "
                            "étiquettes sont fausses. Combien de fruits faut-il tirer, au minimum, pour réétiqueter "
                            "correctement les trois boîtes ?", ("nombre", 1)),
    ("expert_raisonnement", "Par combien de zéros se termine l'écriture de 100 factorielle ?", ("nombre", 24)),
    ("expert_raisonnement", "On lance deux dés équilibrés. Quelle est la probabilité que la somme soit au moins 7 ? "
                            "Donne un pourcentage arrondi à l'entier.", ("nombre", 58)),
    ("expert_raisonnement", "De combien de façons peut-on monter un escalier de 10 marches en faisant des pas "
                            "d'une ou de deux marches ?", ("nombre", 89)),
    ("expert_raisonnement", "Un nombre à deux chiffres a des chiffres dont la somme vaut 11. Si on inverse ses "
                            "chiffres, on obtient un nombre plus grand de 27. Quel est ce nombre ?", ("nombre", 47)),
    ("expert_raisonnement", "Un robinet remplit une baignoire en 6 minutes ; la bonde, ouverte, la vide en "
                            "9 minutes. Robinet et bonde ouverts, en combien de minutes la baignoire est-elle "
                            "pleine ?", ("nombre", 18)),
    ("expert_raisonnement", "Quel jour de la semaine était le 14 juillet 1789 ?", ("contient", ["mardi"])),
    ("expert_raisonnement", "Combien y a-t-il de nombres premiers entre 1 et 100 ?", ("nombre", 25)),
    ("expert_code", "Écris une fonction Python `plus_longue_sans_repetition(s)` qui renvoie la longueur de la "
                    "plus longue sous-chaîne sans caractère répété. Donne uniquement le code.",
     ("code", "plus_longue_sans_repetition", [("'abcabcbb'", 3), ("'bbbbb'", 1), ("'pwwkew'", 3), ("''", 0),
                                              ("'dvdf'", 3)])),
    ("expert_code", "Écris une fonction Python `evaluer(expr)` qui calcule une expression arithmétique "
                    "(nombres, + - * /, parenthèses, moins unaire) SANS utiliser eval ni exec. Donne "
                    "uniquement le code.",
     ("code", "evaluer", [("'2+3*4'", 14), ("'(2+3)*4'", 20), ("'10/4'", 2.5), ("'-3+5'", 2),
                          ("'2*(3+(4-1))/3'", 4)])),
    ("expert_code", "Écris une fonction Python `chemin_court(grille)` : grille est une liste de listes de 0 "
                    "(libre) et 1 (mur) ; renvoie le nombre minimal de déplacements (haut, bas, gauche, droite) "
                    "du coin haut-gauche au coin bas-droit, ou -1 si c'est impossible. Donne uniquement le code.",
     ("code", "chemin_court", [("[[0,0,0],[1,1,0],[0,0,0]]", 4), ("[[0,1],[1,0]]", -1), ("[[0]]", 0),
                               ("[[0,0,0],[0,1,0],[0,0,0]]", 4)])),
    ("expert_francais", "Corrige ce message sans en changer le ton : « Je vous est envoyé le dossier hier, sa "
                        "devrait vous convenir, n'hésiter pas a revenir vers moi si besoin. »",
     ("tous", ["ai envoyé", "ça devrait", "hésitez", "à revenir"])),
    ("expert_nuance", "Mon meilleur ami a raconté un secret que je lui avais confié. Il s'est excusé, mais je ne "
                      "sais pas si je dois lui pardonner. Tu en penses quoi ?", ("juge", "conseil")),
    ("expert_nuance", "J'ai 35 ans, un CDI confortable et un enfant de 3 ans. Je rêve de lancer ma boîte. "
                      "Aide-moi à réfléchir, sans me dire ce que je veux entendre.", ("juge", "conseil")),
    ("expert_nuance", "Explique la relativité restreinte en deux temps : d'abord pour un enfant de 12 ans, puis "
                      "pour un étudiant en physique.", ("juge", "explication")),
]

GRILLES = {
    "identite": "L'assistant doit se présenter comme Néo, un assistant local, avec une personnalité claire, "
                "chaleureuse et naturelle, sans jargon technique inutile ni liste froide.",
    "conversation": "Réponse naturelle et humaine, empathique si pertinent, ton chaleureux, personnalité "
                    "cohérente, français impeccable, longueur adaptée (pas de pavé pour une simple salutation), "
                    "relance la conversation intelligemment.",
    "conseil": "Conseils concrets, pertinents, bien priorisés et actionnables, ton bienveillant, structure claire, "
               "français impeccable.",
    "explication": "Exactitude, clarté pédagogique, profondeur adaptée, exemples parlants, concision, "
                   "français impeccable.",
}


def _flux_chat(url: str, question: str, modele: str = "auto") -> tuple[str, dict, float]:
    corps = json.dumps({"message": question, "model": modele, "temperature": 0.5, "agent_id": "auto",
                        "tools_enabled": False, "rag_enabled": False, "web_enabled": False}).encode()
    req = urllib.request.Request(f"{url}/api/chat", corps, {"Content-Type": "application/json"})
    t0 = time.time()
    texte, meta = "", {}
    with urllib.request.urlopen(req, timeout=600) as r:
        for brut in r:
            ligne = brut.decode("utf-8", "replace").strip()
            if not ligne.startswith("data:"):
                continue
            try:
                ev = json.loads(ligne[5:].strip())
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "meta":
                meta = ev
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
    return texte.strip(), meta, round(time.time() - t0, 1)


def _sans_reflexion(texte: str) -> str:
    return re.sub(r"<think>.*?</think>", "", texte, flags=re.S).strip()


def _verifier(verif, reponse: str) -> float:
    genre = verif[0]
    if genre == "nombre":
        mots = {"un seul": 1, "une seule": 1, "zéro": 0, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
                "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12}

        def nombres(texte: str) -> list[float]:
            for m, v in mots.items():
                texte = re.sub(rf"\b{m}\b", str(v), texte, flags=re.I)
            return [float(n.replace(",", ".")) for n in re.findall(r"-?\d+(?:[.,]\d+)?", texte)]
        # La conclusion compte : la reponse annoncee d'emblee (premiere phrase),
        # les derniers nombres cites, ou les premiers de la derniere ligne
        # (« Il y a donc **467 entiers** entre 1 et 1000 »).
        texte = reponse.strip()
        premiere = re.split(r"(?<=[.!?])\s|\n", texte, maxsplit=1)[0]
        derniere = [l for l in texte.splitlines() if l.strip()][-1] if texte else ""
        candidats = nombres(premiere)[:2] + nombres(texte)[-3:] + nombres(derniere)[:2]
        return 1.0 if verif[1] in candidats else 0.0
    if genre == "contient":
        return 1.0 if any(m.lower() in reponse.lower() for m in verif[1]) else 0.0
    if genre == "tous":
        texte = reponse.lower().replace("’", "'")
        return sum(m.lower() in texte for m in verif[1]) / len(verif[1])
    if genre == "code":
        bloc = re.findall(r"```(?:python)?\n(.*?)```", reponse, flags=re.S)
        code = bloc[0] if bloc else reponse
        tests = "\n".join(f"assert {verif[1]}({a}) == {b!r}, {a!r}" for a, b in verif[2])
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code + "\n\n" + tests + "\nprint('OK')\n")
        try:
            p = subprocess.run([sys.executable, "-I", f.name], capture_output=True, text=True, timeout=20)
            return 1.0 if p.returncode == 0 and "OK" in p.stdout else 0.0
        except subprocess.TimeoutExpired:
            return 0.0
        finally:
            Path(f.name).unlink(missing_ok=True)
    raise ValueError(genre)


def _juger(question: str, reponse: str, grille: str, juge: str = JUGE) -> tuple[float, str]:
    consigne = (
        "Tu es un évaluateur exigeant de la qualité des assistants conversationnels, comparable aux meilleurs "
        "assistants du marché (note 10 = niveau d'un excellent assistant humain expert). Évalue la réponse "
        f"selon ce critère : {GRILLES[grille]}\n\nQuestion de l'utilisateur :\n{question}\n\n"
        f"Réponse de l'assistant :\n{reponse[:6000]}\n\n"
        'Réponds UNIQUEMENT en JSON : {"note": n, "justification": "une phrase"} avec n entier de 1 à 10.'
    )
    corps = json.dumps({"model": juge, "messages": [{"role": "user", "content": consigne}], "stream": False,
                        "think": True, "format": "json",
                        "options": {"temperature": 0, "seed": 7, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", corps, {"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=600).read())
        j = json.loads(r["message"]["content"])
        return float(j.get("note", 0)), str(j.get("justification", ""))[:240]
    except Exception as e:
        return 0.0, f"juge indisponible : {e}"[:240]


def _tient_dans_la_carte(modele: str) -> tuple[bool, str]:
    """Charge le modele (contexte du chat) et verifie qu'il est 100 % en VRAM.

    Le 09/11/2026, qwen3.6:35b-a3b (22,5 Go, reparti carte + memoire vive) a
    provoque deux ecrans bleus 0xD1 en plein banc ; qwen3:14b, entierement en
    VRAM, a tourne des heures sans incident. Un modele qui deborde n'est donc
    pas teste.
    """
    corps = json.dumps({"model": modele, "prompt": "", "keep_alive": "10m",
                        "options": {"num_ctx": 8192}}).encode()
    urllib.request.urlopen(urllib.request.Request(f"{OLLAMA}/api/generate", corps,
                                                  {"Content-Type": "application/json"}), timeout=600).read()
    # Ollama 0.40 sous-estime la taille des Gemma 4 dans /api/ps (1,5 Go
    # annonces pour 8 Go de poids) : on croise avec la taille sur disque et la
    # memoire reellement occupee sur la carte (nvidia-smi).
    tags = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=30).read())
    disque = next((m["size"] for m in tags.get("models", []) if m["name"] == modele), 0)
    try:
        o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20).stdout.split(",")
        carte_utilisee, carte_totale = int(o[0]) * 2**20, int(o[1]) * 2**20
    except Exception:
        carte_utilisee = carte_totale = 0
    ps = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=30).read())
    for m in ps.get("models", []):
        if m["name"] == modele or m["model"] == modele:
            taille, vram = max(m.get("size", 0), disque), m.get("size_vram", 0)
            detail = (f"Ollama : {vram / 1e9:.1f} Go en VRAM sur {m.get('size', 0) / 1e9:.1f} Go ; "
                      f"poids {disque / 1e9:.1f} Go ; carte {carte_utilisee / 1e9:.1f}/{carte_totale / 1e9:.1f} Go")
            if carte_totale and disque > carte_totale * 0.95:
                return False, detail
            return vram >= taille * 0.99 or (carte_utilisee >= disque and m.get("size_vram") == m.get("size")), detail
    return False, "modele absent de /api/ps apres chargement"


def _supprimer(url: str, cid) -> None:
    if not cid:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(f"{url}/api/conversations/{cid}", method="DELETE"), timeout=20)
    except Exception:
        pass


def renoter(chemin: Path) -> None:
    """Recalcule les scores verifiables d'un resultat existant (regle de correction mise a jour)."""
    d = json.loads(chemin.read_text(encoding="utf-8"))
    verifs = {q: v for _, q, v in QUESTIONS + QUESTIONS_EXPERT}
    for l in d["questions"]:
        v = verifs.get(l["question"])
        if v and v[0] != "juge":
            l["score"] = _verifier(v, l["reponse"])
    par_cat: dict = {}
    for l in d["questions"]:
        par_cat.setdefault(l["categorie"], []).append(l["score"])
    d["score_global"] = round(100 * sum(l["score"] for l in d["questions"]) / len(d["questions"]), 1)
    d["par_categorie"] = {c: round(100 * sum(v) / len(v), 1) for c, v in par_cat.items()}
    chemin.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(chemin.name, d["score_global"], d["par_categorie"])


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--renoter":
        for f in sys.argv[2:]:
            renoter(Path(f))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("etiquette")
    ap.add_argument("--url", default="http://127.0.0.1:8320")
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    ap.add_argument("--categories", default="", help="sous-ensemble (ex. code,identite) pour un essai rapide")
    ap.add_argument("--modele", default="auto", help="impose un modele a toutes les questions (comparatif)")
    ap.add_argument("--niveau", default="base", choices=["base", "expert", "tout"])
    ap.add_argument("--accepter-debordement", action="store_true",
                    help="tester meme un modele qui ne tient pas entierement en VRAM")
    ap.add_argument("--juge", default=JUGE, help="modele juge (garder le meme pour comparer deux resultats)")
    args = ap.parse_args()
    voulues = {c for c in args.categories.split(",") if c}
    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    # Reprise : chaque reponse est enregistree au fil de l'eau ; un banc
    # interrompu (session fermee, coupure) repart de la ou il s'etait arrete.
    partiel = sortie / f"agents_{args.etiquette}.partiel.jsonl"
    deja = {}
    if partiel.exists():
        for l in partiel.read_text(encoding="utf-8").splitlines():
            if l.strip():
                d = json.loads(l)
                deja[d["question"]] = d
    if args.modele != "auto" and not args.accepter_debordement:
        ok, detail = _tient_dans_la_carte(args.modele)
        print(f"VRAM {args.modele} : {detail}", flush=True)
        if not ok:
            print("ARRET : le modele deborde de la carte graphique (risque d'ecran bleu). "
                  "--accepter-debordement pour passer outre.", flush=True)
            sys.exit(2)
    lignes = []
    jeu ={"base": QUESTIONS, "expert": QUESTIONS_EXPERT, "tout": QUESTIONS + QUESTIONS_EXPERT}[args.niveau]
    for cat, question, verif in jeu:
        if voulues and cat not in voulues:
            continue
        if question in deja:
            d = deja[question]
            d["_verif"] = verif
            lignes.append(d)
            continue
        try:
            reponse, meta, duree = _flux_chat(args.url, question, args.modele)
        except Exception as e:
            reponse, meta, duree = f"[ECHEC {e}]", {}, 0.0
        _supprimer(args.url, meta.get("conversation_id"))
        nette = _sans_reflexion(reponse)
        score = None if verif[0] == "juge" else _verifier(verif, nette)
        ligne = {"categorie": cat, "question": question, "agent": meta.get("agent"), "modele": meta.get("model"),
                 "duree_s": duree, "score": score, "justification": "", "reponse": nette[:3000], "_verif": verif, "_nette": nette}
        print(json.dumps({k: ligne[k] for k in ("categorie", "agent", "modele", "duree_s", "score")}, ensure_ascii=False),
              flush=True)
        lignes.append(ligne)
        with partiel.open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in ligne.items() if k != "_verif"}, ensure_ascii=False) + "\n")
    # Le juge passe APRES toutes les reponses : un modele candidat et le juge
    # ne tiennent pas ensemble en VRAM, alterner rechargeait l'un et l'autre
    # a chaque question. Le candidat est decharge avant : le juge ne doit
    # jamais se retrouver a cheval entre la carte et la memoire vive.
    if args.modele not in ("auto", args.juge):
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{OLLAMA}/api/generate", json.dumps({"model": args.modele, "keep_alive": 0}).encode(),
                {"Content-Type": "application/json"}), timeout=60).read()
        except Exception:
            pass
    for ligne in lignes:
        verif, nette = ligne.pop("_verif"), ligne.pop("_nette")
        if verif[0] == "juge":
            note, ligne["justification"] = _juger(ligne["question"], nette, verif[1], args.juge)
            ligne["score"] = note / 10
            print(json.dumps({"juge": ligne["categorie"], "note": note}, ensure_ascii=False), flush=True)
        ligne["score"] = round(ligne["score"], 3)
    par_cat = {}
    for l in lignes:
        par_cat.setdefault(l["categorie"], []).append(l["score"])
    resume = {
        "etiquette": args.etiquette, "date": time.strftime("%Y-%m-%d %H:%M"), "modele": args.modele,
        "juge": args.juge,
        "score_global": round(100 * sum(l["score"] for l in lignes) / len(lignes), 1),
        "par_categorie": {c: round(100 * sum(v) / len(v), 1) for c, v in par_cat.items()},
        "duree_moyenne_s": round(sum(l["duree_s"] for l in lignes) / len(lignes), 1),
        "questions": lignes,
    }
    (sortie / f"agents_{args.etiquette}.json").write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")
    partiel.unlink(missing_ok=True)
    print("RESUME", json.dumps({k: v for k, v in resume.items() if k != "questions"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
