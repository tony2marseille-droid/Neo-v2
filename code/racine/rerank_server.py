"""Service de reclassement cross-encoder Neo (port 8322, POST /rerank).

Implementation avec transformers pur (AutoModelForSequenceClassification) :
pas de sentence-transformers, pour rester compatible avec la stack
d'entrainement (transformers 5.5 / torch cu128 / unsloth).

Contrat : POST /rerank {"query": str, "docs": [str]} -> {"scores": [float]}
Score = sigmoid du logit de pertinence (0..1, 1 = pertinent).
Reglages : NEO_RERANK_MODEL, NEO_RERANK_DEVICE (auto par defaut),
NEO_RERANK_PORT, NEO_RERANK_MAXLEN, NEO_RERANK_REPOS_S.

Carte graphique a la demande (mesure du 09/11/2026, 20 passages) : 12,3 s sur le
processeur, 0,06 s sur la carte en fp16, scores identiques. Le modele reste en
memoire vive et ne monte sur la carte (1,1 Go, 0,15 s) que pour trier ; il en
redescend apres NEO_RERANK_REPOS_S secondes sans requete (60 par defaut) ou
sur POST /liberer (appele par le studio image avant une generation).
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.getenv("NEO_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
DEVICE = os.getenv("NEO_RERANK_DEVICE", "auto")  # auto | cuda | cpu
PORT = int(os.getenv("NEO_RERANK_PORT", "8322"))
MAX_LEN = int(os.getenv("NEO_RERANK_MAXLEN", "512"))
REPOS_S = float(os.getenv("NEO_RERANK_REPOS_S", "60"))

_etat = {"charge": False, "erreur": "", "requetes": 0, "sur_carte": False}
_lock = threading.Lock()
_tok = None
_model = None
_derniere = 0.0


def _carte_dispo() -> bool:
    if DEVICE == "cpu":
        return False
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


def _vers_carte() -> bool:
    """Monte le modele sur la carte (fp16) ; False si impossible (VRAM pleine...)."""
    if _etat["sur_carte"]:
        return True
    if not _carte_dispo():
        return False
    try:
        _model.half().to("cuda")
        _etat["sur_carte"] = True
        return True
    except Exception as e:  # noqa: BLE001
        _etat["erreur"] = f"carte indisponible : {e}"[:200]
        _vers_memoire()
        return False


def _vers_memoire() -> None:
    """Redescend en memoire vive (fp32 : calcul possible sur le processeur)."""
    import torch

    _model.float().to("cpu")
    _etat["sur_carte"] = False
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def liberer() -> bool:
    with _lock:
        if _model is not None and _etat["sur_carte"]:
            _vers_memoire()
            return True
    return False


def _surveiller_repos() -> None:
    import time

    while True:
        time.sleep(5)
        if _etat["sur_carte"] and time.time() - _derniere > REPOS_S and liberer():
            print("[rerank] repos : modele redescendu en memoire vive", flush=True)


def get_model():
    """Charge le cross-encoder une seule fois (appel bloquant au demarrage)."""
    global _tok, _model
    with _lock:
        if _model is not None:
            return
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        _tok = AutoTokenizer.from_pretrained(MODEL)
        _model = AutoModelForSequenceClassification.from_pretrained(MODEL)
        _model.eval()
        _etat["charge"] = True
        print(f"[rerank] {MODEL} charge en memoire vive (carte a la demande : {_carte_dispo()})", flush=True)


def score(query: str, docs: list[str]) -> list[float]:
    import torch

    get_model()
    if not docs:
        return []
    global _derniere
    import time

    with _lock:
        appareil = "cuda" if _vers_carte() else "cpu"
        paires = [[query, d] for d in docs]
        enc = _tok(
            paires, padding=True, truncation=True,
            max_length=MAX_LEN, return_tensors="pt",
        ).to(appareil)
        with torch.no_grad():
            logits = _model(**enc).logits.view(-1)
        probs = torch.sigmoid(logits.float()).tolist()
        _derniere = time.time()
    _etat["requetes"] += 1
    return [round(float(x), 4) for x in probs]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": _etat["charge"], "model": MODEL,
                         "device": DEVICE, "sur_carte": _etat["sur_carte"],
                         "requetes": _etat["requetes"], "erreur": _etat["erreur"]})

    def do_POST(self):
        if self.path == "/liberer":
            return self._send(200, {"ok": True, "libere": liberer()})
        if self.path != "/rerank":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n))
            self._send(200, {"scores": score(req["query"], req["docs"])})
        except Exception as e:  # noqa: BLE001
            _etat["erreur"] = str(e)[:200]
            self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"[rerank] demarrage {MODEL} sur {DEVICE} (port {PORT}), chargement...",
          flush=True)
    try:
        get_model()
        # Auto-test : un couple pertinent doit scorer plus haut qu'un autre.
        s = score("Quelle est la capitale de la France ?",
                  ["Paris est la capitale de la France.", "Un chat dort sur un tapis."])
        print(f"[rerank] auto-test : {s}", flush=True)
        ok = len(s) == 2 and s[0] > s[1]
        print(f"[rerank] pret sur http://127.0.0.1:{PORT} (auto-test {'OK' if ok else 'DOUTEUX'})",
              flush=True)
    except Exception as e:  # noqa: BLE001
        _etat["erreur"] = str(e)[:200]
        print(f"[rerank] ERREUR chargement : {e}", flush=True)
        raise
    threading.Thread(target=_surveiller_repos, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
