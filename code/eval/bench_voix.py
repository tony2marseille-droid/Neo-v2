"""Banc de la voix de Neo : Piper (actuel) contre Kyutai Pocket TTS.

Pour chaque phrase : temps de synthese (facteur temps reel) et intelligibilite, mesuree
en faisant retranscrire l'audio par Parakeet (taux d'erreur sur les mots). Le naturel ne
se mesure pas : les fichiers sont gardes pour l'ecoute.

Usage : python -I code/eval/bench_voix.py [--sortie dossier]
Cache Hugging Face sur I: (NEO_HF_CACHE) pour menager le SSD.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import wave
from pathlib import Path

os.environ.setdefault("HF_HUB_CACHE", os.environ.get("NEO_HF_CACHE", r"I:\Neo-Archive-2026\hf-cache"))

import numpy as np  # noqa: E402

ICI = Path(__file__).resolve().parent
PHRASES = [
    "Bonjour Camille, j'espère que ta journée s'est bien passée. Qu'est-ce que je peux faire pour toi ce soir ?",
    "Demain à Marseille, le ciel sera couvert le matin, avec des averses faibles l'après-midi et dix-neuf degrés.",
    "J'ai trouvé trois documents qui parlent de ton entraînement : le plus récent date du vingt octobre.",
    "Attention, la carte graphique atteint quatre-vingt-cinq degrés. Je mets la génération d'images en pause.",
    "Voilà ton image : un vaisseau spatial qui s'approche d'une nébuleuse violette, en format paysage.",
    "Je ne suis pas certain de ce chiffre. Veux-tu que je vérifie sur le web avant de te répondre ?",
]


def ecrire(chemin: Path, audio: np.ndarray, sr: int) -> None:
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sortie", default=str(ICI / "resultats"))
    args = ap.parse_args()
    sortie = Path(args.sortie) / "voix"
    sortie.mkdir(parents=True, exist_ok=True)

    import importlib.util
    sp = importlib.util.spec_from_file_location("d", ICI / "bench_dictee.py")
    d = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(d)
    import onnx_asr
    asr = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", d.PARAKEET)

    voix = {}
    from piper import PiperVoice
    piper = PiperVoice.load(r"E:\ia\tools\voix\fr_FR-tom-medium.onnx")

    def piper_tts(texte, chemin):
        with wave.open(str(chemin), "wb") as w:
            piper.synthesize_wav(texte, w)

    voix["piper tom (actuel)"] = piper_tts

    from pocket_tts import TTSModel
    for langue in ("french", "french_24l"):
        modele = TTSModel.load_model(language=langue)
        for nom_voix in ("estelle", "jean"):
            etat = modele.get_state_for_audio_prompt(nom_voix)

            def pocket(texte, chemin, m=modele, e=etat):
                audio = m.generate_audio(e, texte)
                ecrire(chemin, audio.numpy().astype(np.float32), m.sample_rate)

            voix[f"pocket {langue} {nom_voix}"] = pocket

    res = {}
    for nom, synth in voix.items():
        synth("Essai de chauffe.", sortie / "chauffe.wav")
        erreurs = mots = 0
        t_calc = t_audio = 0.0
        for i, p in enumerate(PHRASES):
            chemin = sortie / f"{nom.replace(' ', '_').replace('(', '').replace(')', '')}_{i}.wav"
            t0 = time.time()
            synth(p, chemin)
            t_calc += time.time() - t0
            with wave.open(str(chemin)) as w:
                t_audio += w.getnframes() / w.getframerate()
            e, n = d.wer(p, asr.recognize(str(chemin)))
            erreurs, mots = erreurs + e, mots + n
        res[nom] = {"wer_%": round(100 * erreurs / mots, 1), "temps_reel_x": round(t_audio / t_calc, 1),
                    "s_par_phrase": round(t_calc / len(PHRASES), 2)}
        print(nom, res[nom], flush=True)
    (Path(args.sortie) / "voix.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
