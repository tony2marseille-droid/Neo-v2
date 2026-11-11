r"""Synthese vocale de Neo.

Deux moteurs :
- Piper (voix neuronales ONNX, processeur seulement : ne prend rien au GPU de
  FLUX) quand un modele est installe dans le dossier des voix (NEO_VOIX_DIR,
  defaut <racine>\tools\voix) ; identifiants « piper:<nom> » ;
- SAPI (pyttsx3) sinon.
La voix de Neo par defaut est la premiere voix Piper installee.
Le texte est nettoye avant lecture : le Markdown, les blocs de code, les
chemins et les adresses ne sont jamais lus a voix haute.
"""
import importlib.util
import os
import re
import threading
import uuid
import wave
from pathlib import Path

import pyttsx3
from backend.core import reglages

DOSSIER_VOIX = reglages.chemin("NEO_VOIX_DIR", "tools", "voix")


class TTSEngineError(Exception):
    pass


_FEMININ = ("zira", "hortense", "julie", "virginie", "hazel", "susan", "heera",
            "eva", "catherine", "amelie", "amélie", "audrey", "aurelie", "aurélie",
            "chantal", "charlotte", "female", "femme")


def _genre(nom: str, vid: str) -> str:
    bas = f"{nom} {vid}".lower()
    return "F" if any(m in bas for m in _FEMININ) else "M"


def voix_piper() -> list[dict]:
    """Voix Piper installees (modele .onnx + .onnx.json, paquet piper-tts present)."""
    if not DOSSIER_VOIX.is_dir() or importlib.util.find_spec("piper") is None:
        return []
    voix = []
    for onnx in sorted(DOSSIER_VOIX.glob("*.onnx")):
        if not onnx.with_suffix(".onnx.json").is_file():
            continue
        langue = onnx.stem.split("-")[0].replace("_", "-")
        voix.append({"id": f"piper:{onnx.stem}", "name": f"Néo (neuronale) — {onnx.stem}",
                     "languages": [langue], "gender": "M" if any(m in onnx.stem for m in ("tom", "gilles", "pierre")) else "F"})
    return voix


def nettoyer_pour_voix(texte: str) -> str:
    """Texte prononcable : sans Markdown, code, chemins ni adresses."""
    t = re.sub(r"```.*?```", " (je t'ai mis le code à l'écran) ", texte, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " (lien à l'écran) ", t)
    t = re.sub(r"\b[A-Za-z]:\\\S+", " (fichier à l'écran) ", t)
    t = re.sub(r"^\s{0,3}(#{1,6}|[-*+]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"[*_~#>|]+", "", t)
    t = re.sub(r"\$+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _synthese_piper(texte: str, out_path: Path, nom: str, rate: int) -> Path:
    from piper import PiperVoice, SynthesisConfig

    modele = DOSSIER_VOIX / f"{nom}.onnx"
    if not modele.is_file():
        raise TTSEngineError(f"voix Piper absente : {modele}")
    voix = PiperVoice.load(str(modele))
    # Debit : 185 mots/min (SAPI) <-> vitesse naturelle du modele.
    echelle = max(0.6, min(1.6, 185 / rate)) if rate > 0 else 1.0
    with wave.open(str(out_path), "wb") as wav:
        voix.synthesize_wav(texte, wav, syn_config=SynthesisConfig(length_scale=echelle))
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise TTSEngineError("Synthèse Piper sans sortie audio")
    return out_path


def list_voices() -> list[dict]:
    engine = pyttsx3.init()
    try:
        voices = []
        for v in engine.getProperty("voices"):
            langs = []
            for attr in ("languages", "language"):
                raw = getattr(v, attr, None)
                if raw:
                    langs = list(raw) if isinstance(raw, (list, tuple)) else [str(raw)]
                    break
            voices.append(
                {
                    "id": v.id,
                    "name": v.name,
                    "languages": langs,
                    "gender": _genre(v.name, v.id),
                }
            )
        # voix feminines d'abord : la selection par defaut les privilegie
        voices.sort(key=lambda x: (x["gender"] != "F", x["name"]))
        # Voix neuronales de Neo en tete.
        return voix_piper() + voices
    finally:
        try:
            engine.stop()
        except Exception:
            pass


def voix_feminine_fr() -> str:
    """La meilleure voix feminine disponible pour le francais."""
    feminines = [v for v in list_voices() if v["gender"] == "F"]
    candidates = [
        v for v in feminines if any("fr" in l.lower() for l in v["languages"])
    ] or feminines
    return candidates[0]["id"] if candidates else ""


def synthesize_to_wav(text: str, out_path: Path, voice_id: str = "", rate: int = 0) -> Path:
    text = nettoyer_pour_voix(text)
    if not text:
        raise TTSEngineError("Texte vide")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Voix de Neo : la voix Piper choisie, sinon la premiere installee.
    if not voice_id:
        piper = voix_piper()
        voice_id = piper[0]["id"] if piper else ""
    if voice_id.startswith("piper:"):
        return _synthese_piper(text, out_path, voice_id.split(":", 1)[1], rate)

    result: dict = {"path": None, "error": None}

    def work() -> None:
        try:
            engine = pyttsx3.init()
            if rate > 0:
                engine.setProperty("rate", rate)
            if voice_id:
                for v in engine.getProperty("voices"):
                    if v.id == voice_id:
                        engine.setProperty("voice", voice_id)
                        break
            engine.save_to_file(text, str(out_path))
            engine.runAndWait()
            engine.stop()
            if not out_path.exists() or out_path.stat().st_size == 0:
                result["error"] = "Synthèse sans sortie audio"
            else:
                result["path"] = out_path
        except Exception as exc:
            result["error"] = str(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout=60)
    if thread.is_alive():
        raise TTSEngineError("Délai de synthèse dépassé")
    if result["error"]:
        raise TTSEngineError(result["error"])
    return Path(result["path"])


def cleanup_cache(cache_dir: Path, keep: int = 40) -> None:
    files = sorted(cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


def new_wav_path(cache_dir: Path) -> Path:
    return cache_dir / f"tts_{uuid.uuid4().hex}.wav"
