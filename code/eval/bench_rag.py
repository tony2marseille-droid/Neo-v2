"""Banc de la recherche documentaire : plongement (embedding) + tri (reranker).

Corpus : la documentation du projet (docs/*.md, README, fiche de Neo) decoupee comme en
production (800 caracteres, recouvrement 100), noyee parmi les passages deja indexes dans
la base de Neo (leurres : jamais affiches ni envoyes ailleurs). 30 questions reformulees ;
un passage est pertinent s'il contient l'un des reperes de la question.

Mesures : passage pertinent dans les 4 retenus pour le chat (hit@4), rang moyen inverse
(MRR@10), temps. Configurations : chaque plongement seul, puis top 20 -> tri -> top 4.

Usage : python -I code/eval/bench_rag.py [--sans-leurres] [--sortie dossier]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import reglages  # noqa: E402

ICI = Path(__file__).resolve().parent
DEPOT = ICI.parent.parent
OLLAMA = "http://127.0.0.1:11434"
BASE = str(reglages.chemin("NEO_DB_PATH", "data", "localai.db"))
RERANK_BGE = "http://127.0.0.1:8322/rerank"
QWEN_RERANKER = str(reglages.chemin("NEO_QWEN_RERANKER", "models", "Qwen3-Reranker-0.6B"))
CONSIGNE_QWEN = "Given a question in French, retrieve the passage of documentation that answers it"

QUESTIONS = [
    ("Quel est le délai maximum d'une passe de génération d'image avant l'essai de secours ?", ["NEO_SD_DELAI_S"]),
    ("Pourquoi a-t-on renoncé à affiner l'image morceau par morceau ?", ["chaque tuile inventait"]),
    ("Quel modèle d'image a pris la place de FLUX schnell comme modèle principal ?", ["Z-Image-Turbo Q8"]),
    ("Comment le studio a-t-il fini par comprendre le mot lazar dans une scène spatiale ?", ["-> blazar"]),
    ("Sur quel disque garde-t-on tous les modèles de diffusion pour épargner le SSD ?", ["Neo-Archive-2026"]),
    ("Quelle voix française neuronale a été installée pour Néo ?", ["fr_FR-tom-medium"]),
    ("Combien de temps prend le décodage d'une vidéo avec le petit décodeur ?", ["720 s → 3 s"]),
    ("Pourquoi a-t-on renoncé au gros modèle Qwen à 35 milliards de paramètres ?", ["écrans bleus"]),
    ("Quelle note Gemma 4 a-t-il obtenue sur le banc complet une fois déployé ?", ["96,7"]),
    ("Que faire quand une mise à jour d'Ollama a effacé ses moteurs ?", ["reparer_ollama.ps1", "binary not found"]),
    ("Quel modèle contrôle que les éléments demandés figurent bien sur l'image générée ?", ["direction.verifier"]),
    ("Pourquoi les agents qui utilisent des outils ne tournent-ils pas avec Gemma ?", ["en boucle"]),
    ("Quels programmes la commande système a-t-elle le droit d'exécuter ?", ["liste blanche d'exécutables"]),
    ("Combien de requêtes par minute sont autorisées avant d'être freiné ?", ["(défaut 300"]),
    ("Quelles adresses de l'API restent ouvertes sans jeton ?", ["restent publiques"]),
    ("Pourquoi recommander des exclusions dans l'antivirus ?", ["Exclusions recommandées"]),
    ("Dans quel fichier sont consignés les accès refusés ?", ["security_log.jsonl"]),
    ("Quel est le niveau de permission des outils si on ne change rien ?", ["défaut `USER`"]),
    ("Que fait le superviseur quand un service ne répond plus du tout pendant deux minutes ?", ["pendant 2 min"]),
    ("À partir de quel espace libre restant le superviseur prévient-il ?", ["< 30 Go"]),
    ("Comment couper un service sans redémarrer le superviseur ?", ["relu à chaud"]),
    ("Quels réglages d'environnement faut-il donner à Ollama ?", ["OLLAMA_KV_CACHE_TYPE"]),
    ("Pourquoi les notes techniques des agents n'apparaissent-elles plus dans les conversations ?",
     ["erreurs d'outillage"]),
    ("Quand le coach d'auto-amélioration se lance-t-il tout seul ?", ["dimanche 05:00"]),
    ("La passerelle compatible OpenAI profite-t-elle des documents et des outils ?", ["Pas de RAG, pas d'outils"]),
    ("Combien d'outils le serveur MCP met-il à disposition ?", ["`tools/list`"]),
    ("Selon quels paliers l'installation adapte-t-elle les modèles au matériel ?", ["Paliers"]),
    ("Sous quelle licence est la voix Piper de Néo ?", ["jeu de données AGPL-3"]),
    ("Quel service gratuit fournit les cours des devises ?", ["Frankfurter"]),
    ("La lecture de secours des pages web passe-t-elle outre les sites qui bloquent les robots ?",
     ["jamais contourné"]),
]


def decouper(texte: str, taille: int = 800, recouvrement: int = 100) -> list[str]:
    """Comme `chunk_text` cote Rust : fenetres de caracteres avec recouvrement."""
    car = list(texte)
    sortie, i = [], 0
    while i < len(car):
        sortie.append("".join(car[i:i + taille]).strip())
        if i + taille >= len(car):
            break
        i += taille - recouvrement
    return [s for s in sortie if s]


def corpus() -> list[str]:
    fichiers = sorted((DEPOT / "docs").glob("*.md")) + [DEPOT / "README.md", DEPOT / "code" / "config" / "neo_persona.md"]
    passages = []
    for f in fichiers:
        passages += decouper(f.read_text(encoding="utf-8"))
    return passages


def leurres() -> list[str]:
    c = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
    return [r[0] for r in c.execute("SELECT content FROM document_chunks") if r[0]]


def _post(url: str, corps: dict, delai: int = 600) -> dict:
    req = urllib.request.Request(url, json.dumps(corps).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=delai).read())


def plonger(modele: str, textes: list[str], requete: bool = False) -> list[list[float]]:
    if requete and modele.startswith("qwen3-embedding"):
        textes = [f"Instruct: {CONSIGNE_QWEN}\nQuery: {t}" for t in textes]
    vecteurs = []
    for i in range(0, len(textes), 32):
        vecteurs += _post(f"{OLLAMA}/api/embed", {"model": modele, "input": textes[i:i + 32],
                                                   "keep_alive": "10m"})["embeddings"]
    return vecteurs


def _norme(v):
    import math
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def classer(q, docs):
    import numpy as np
    d = np.asarray(docs, dtype=np.float32)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    qv = np.asarray(_norme(q), dtype=np.float32)
    return list(np.argsort(-(d @ qv)))


class QwenTrieur:
    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(QWEN_RERANKER, padding_side="left")
        self.mod = AutoModelForCausalLM.from_pretrained(QWEN_RERANKER, torch_dtype=torch.float16).cuda().eval()
        self.oui = self.tok.convert_tokens_to_ids("yes")
        self.non = self.tok.convert_tokens_to_ids("no")
        self.avant = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
                      "and the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n"
                      "<|im_start|>user\n")
        self.apres = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def scores(self, question: str, docs: list[str]) -> list[float]:
        textes = [f"{self.avant}<Instruct>: {CONSIGNE_QWEN}\n<Query>: {question}\n<Document>: {d}{self.apres}"
                  for d in docs]
        with self.torch.no_grad():
            enc = self.tok(textes, padding=True, truncation=True, max_length=1024, return_tensors="pt").to("cuda")
            logits = self.mod(**enc).logits[:, -1, :]
            paire = self.torch.stack([logits[:, self.non], logits[:, self.oui]], dim=1).float()
            return self.torch.softmax(paire, dim=1)[:, 1].tolist()


def trier_bge(question: str, docs: list[str]) -> list[float]:
    return _post(RERANK_BGE, {"query": question, "docs": docs}, 120)["scores"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sans-leurres", action="store_true")
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    args = ap.parse_args()
    docs = corpus()
    nb_docs = len(docs)
    for q, reperes in QUESTIONS:
        if not any(any(r.lower() in d.lower() for r in reperes) for d in docs):
            raise SystemExit(f"repere introuvable dans le corpus : {reperes}")
    if not args.sans_leurres:
        docs += leurres()
    pertinents = [{i for i, d in enumerate(docs[:nb_docs]) if any(r.lower() in d.lower() for r in rep)}
                  for _, rep in QUESTIONS]
    questions = [q for q, _ in QUESTIONS]
    res = {"passages": len(docs), "passages_documentation": nb_docs, "configurations": {}}

    def noter(nom, rangs, duree):
        hit4 = sum(bool(set(r[:4]) & p) for r, p in zip(rangs, pertinents)) / len(rangs)
        mrr = sum(next((1 / (k + 1) for k, i in enumerate(r[:10]) if i in p), 0) for r, p in zip(rangs, pertinents))
        res["configurations"][nom] = {"hit@4": round(100 * hit4, 1), "mrr@10": round(mrr / len(rangs), 3),
                                      "s_par_question": round(duree / len(rangs), 2)}
        print(nom, res["configurations"][nom], flush=True)

    candidats = {}
    for modele in ("nomic-embed-text:latest", "qwen3-embedding:4b"):
        t0 = time.time()
        vd = plonger(modele, docs)
        res[f"indexation_{modele}_s"] = round(time.time() - t0, 1)
        t0 = time.time()
        vq = plonger(modele, questions, requete=True)
        rangs = [classer(q, vd) for q in vq]
        noter(modele, rangs, time.time() - t0)
        candidats[modele] = rangs

    trieurs = {"bge-reranker-v2-m3": trier_bge}
    qwen = QwenTrieur()
    trieurs["Qwen3-Reranker-0.6B"] = qwen.scores
    for modele, rangs in candidats.items():
        for nom_t, trier in trieurs.items():
            t0 = time.time()
            finaux = []
            for q, r in zip(questions, rangs):
                top = [int(i) for i in r[:20]]
                s = trier(q, [docs[i][:2000] for i in top])
                finaux.append([top[k] for k in sorted(range(len(top)), key=lambda k: -s[k])])
            noter(f"{modele} + {nom_t}", finaux, time.time() - t0)
    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / "rag.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RESUME", json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
