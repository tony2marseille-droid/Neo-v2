"""Banc de la dictee : Whisper (actuel) contre Parakeet-TDT-0.6B-v3.

Audio de test synthetise (aucun enregistrement de l'utilisateur) : 15 phrases du
quotidien, deux voix (Piper fr_FR-tom-medium, masculine ; SAPI Hortense, feminine),
propres et avec un bruit de fond a 10 dB. Mesure : taux d'erreur sur les mots (WER,
apres normalisation), temps de calcul.

Usage : python -I code/eval/bench_dictee.py [--sortie dossier]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import unicodedata
import wave
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import reglages  # noqa: E402

import numpy as np

ICI = Path(__file__).resolve().parent
VOIX_PIPER = reglages.chemin("NEO_VOIX_DIR", "tools", "voix") / "fr_FR-tom-medium.onnx"
PARAKEET = reglages.chemin("NEO_PARAKEET_DIR", "models", "parakeet-tdt-0.6b-v3-onnx")
TRAVAIL = Path(__import__("tempfile").gettempdir()) / "bench_dictee"

PHRASES = [
    "Bonjour Néo, peux-tu me rappeler d'acheter du pain en rentrant ce soir ?",
    "Génère une image d'un vaisseau spatial qui s'approche d'une nébuleuse violette.",
    "Quel temps va-t-il faire demain matin à Annecy ?",
    "Explique-moi simplement la différence entre un processus et un thread.",
    "J'ai rendez-vous chez le dentiste jeudi prochain, n'oublie pas de me le rappeler.",
    "Traduis cette phrase en anglais : le chat dort paisiblement sur le canapé.",
    "Écris une fonction Python qui vérifie si un mot est un palindrome.",
    "Je voudrais une recette de tarte aux pommes facile pour le dessert de dimanche.",
    "Ouvre le bloc-notes et baisse un peu le volume, s'il te plaît.",
    "Résume-moi le dernier document que je t'ai envoyé sur la sécurité du serveur.",
    "Combien coûte un aller-retour entre Marseille et Paris en train cette semaine ?",
    "Mon fils a de la fièvre depuis hier soir, que dois-je surveiller ?",
    "Montre-moi une photo de la tour Eiffel illuminée la nuit.",
    "Ajoute du lait, des œufs et de la farine à la liste de courses.",
    "Peux-tu corriger les fautes d'orthographe de ce courriel avant que je l'envoie ?",
]


def normaliser(t: str) -> list[str]:
    t = t.lower().replace("œ", "oe").replace("’", "'")
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9' ]+", " ", t.replace("-", " ").replace("'", "' "))
    return t.split()


def wer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = normaliser(ref), normaliser(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prec, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prec, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prec + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


def lire(chemin: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(chemin)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return a, w.getframerate()


def ecrire(chemin: Path, a: np.ndarray, sr: int) -> None:
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())


def en_16k(a: np.ndarray, sr: int) -> np.ndarray:
    if sr == 16000:
        return a
    x = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
    return np.interp(x, np.arange(len(a)), a).astype(np.float32)


def synthetiser() -> list[tuple[str, Path, str]]:
    from piper import PiperVoice

    TRAVAIL.mkdir(parents=True, exist_ok=True)
    voix = PiperVoice.load(str(VOIX_PIPER))
    rng = np.random.default_rng(7)
    sortie = []
    for i, p in enumerate(PHRASES):
        brut = TRAVAIL / f"piper_{i}.wav"
        with wave.open(str(brut), "wb") as w:
            voix.synthesize_wav(p, w)
        sapi = TRAVAIL / f"sapi_{i}.wav"
        texte = p.replace("'", "''")
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        "$s.SelectVoice('Microsoft Hortense Desktop'); "
                        f"$s.SetOutputToWaveFile('{sapi}'); $s.Speak('{texte}'); $s.Dispose()"],
                       check=True, capture_output=True)
        for nom, f in (("piper", brut), ("hortense", sapi)):
            a, sr = lire(f)
            a = en_16k(a, sr)
            propre = TRAVAIL / f"{nom}_{i}_propre.wav"
            ecrire(propre, a, 16000)
            # Bruit rose leger a 10 dB (ventilation, piece) : plus realiste qu'un micro de studio.
            blanc = rng.standard_normal(len(a)).astype(np.float32)
            rose = np.convolve(blanc, np.ones(8) / 8, mode="same")
            p_sig, p_bruit = np.mean(a ** 2), np.mean(rose ** 2)
            bruite = a + rose * np.sqrt(p_sig / (p_bruit * 10 ** (10 / 10)))
            chemin_b = TRAVAIL / f"{nom}_{i}_bruit.wav"
            ecrire(chemin_b, bruite, 16000)
            sortie += [(p, propre, f"{nom}/propre"), (p, chemin_b, f"{nom}/bruit")]
    return sortie


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    args = ap.parse_args()
    audios = synthetiser()

    from faster_whisper import WhisperModel
    import onnx_asr

    moteurs = {}
    w = WhisperModel("small", device="auto", compute_type="int8")
    moteurs["whisper-small (actuel)"] = lambda f: " ".join(
        s.text for s in w.transcribe(str(f), language=None, vad_filter=True, beam_size=5)[0])
    for quant in (None, "int8"):
        m = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", PARAKEET, quantization=quant)
        moteurs[f"parakeet-v3 {'int8' if quant else 'fp32'} (CPU)"] = lambda f, m=m: m.recognize(str(f))

    res = {"audios": len(audios), "moteurs": {}}
    for nom, transcrire in moteurs.items():
        transcrire(audios[0][1])  # chauffe
        erreurs = mots = 0
        par_cond, exemples, t0 = {}, [], time.time()
        for ref, f, cond in audios:
            hyp = transcrire(f)
            e, n = wer(ref, hyp)
            erreurs, mots = erreurs + e, mots + n
            c = par_cond.setdefault(cond, [0, 0])
            c[0] += e
            c[1] += n
            if e:
                exemples.append({"condition": cond, "attendu": ref, "entendu": hyp.strip()})
        duree = time.time() - t0
        res["moteurs"][nom] = {"wer_%": round(100 * erreurs / mots, 1),
                               "par_condition": {k: round(100 * v[0] / v[1], 1) for k, v in par_cond.items()},
                               "s_par_phrase": round(duree / len(audios), 2), "erreurs": exemples[:12]}
        print(nom, {k: v for k, v in res["moteurs"][nom].items() if k != "erreurs"}, flush=True)
    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / "dictee.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
