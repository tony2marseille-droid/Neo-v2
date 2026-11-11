"""Dictee : Parakeet-TDT-0.6B-v3 (defaut), Whisper en secours.

Banc du 09/11/2026 (`eval/bench_dictee.py`, 60 enregistrements, deux voix, propres et
bruites) : erreurs sur les mots 7,1 % avec Whisper small -> 4,7 % avec Parakeet v3,
0,24 s par phrase sur le processeur (la carte reste a Gemma). Reglage `stt_model` :
« parakeet » ou une taille Whisper (« small », « medium »...).
"""
from pathlib import Path
from backend.core import reglages

PARAKEET = reglages.chemin("NEO_PARAKEET_DIR", "models", "parakeet-tdt-0.6b-v3-onnx")
DUREE_MAX_PARAKEET_S = 20 * 60  # au-dela : Whisper (decoupage par activite vocale)

_engine = None
_engine_size = ""
_parakeet = None


def _size() -> str:
    row = None
    try:
        from backend.core.database import get_db

        row = get_db().query_one(
            "SELECT value FROM app_settings WHERE key='stt_model'"
        )
    except Exception:
        pass
    return (row["value"] if row and row["value"] else "parakeet").strip()


def _load() -> object:
    global _engine, _engine_size
    size = _size()
    if size == "parakeet":
        size = "small"
    if _engine is None or _engine_size != size:
        _engine = None
        from faster_whisper import WhisperModel

        _engine = WhisperModel(size, device="auto", compute_type="int8")
        _engine_size = size
    return _engine


def parakeet_disponible() -> bool:
    try:
        import onnx_asr  # noqa: F401

        return (PARAKEET / "encoder-model.onnx").exists()
    except Exception:
        return False


def _charger_parakeet():
    global _parakeet
    if _parakeet is None:
        import onnx_asr

        _parakeet = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", PARAKEET)
    return _parakeet


_SALUT_LEO = None


def corriger_nom(texte: str) -> str:
    """« Bonjour Léo », « Salut Léo » -> Néo : Parakeet entend parfois le nom de
    l'assistant comme le prenom courant (banc du 09/11/2026). Seulement juste
    apres une salutation ou une interpellation, pour ne jamais toucher a un Leo
    dont on parle."""
    import re

    global _SALUT_LEO
    if _SALUT_LEO is None:
        _SALUT_LEO = re.compile(r"^(\s*(?:bonjour|bonsoir|salut|coucou|hey|h[ée]|dis|ok|merci|alors)[\s,!]+)l[ée]o\b",
                                re.IGNORECASE)
    return _SALUT_LEO.sub(lambda m: m.group(1) + "Néo", texte or "")


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def _transcrire_whisper(path: Path) -> dict:
    segments, info = _load().transcribe(
        str(path), language=None, vad_filter=True, beam_size=5
    )
    parts = [seg.text.strip() for seg in segments]
    return {
        "text": " ".join(p for p in parts if p),
        "language": getattr(info, "language", "") or "",
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "moteur": f"whisper-{_engine_size}",
    }


def transcribe(path: Path) -> dict:
    """Transcrit un fichier audio (wav, webm, mp3...). Renvoie {text, language, duration, moteur}."""
    if not path.exists():
        return {"text": "", "language": "", "duration": 0.0}
    if _size() == "parakeet" and parakeet_disponible():
        try:
            from faster_whisper import decode_audio

            audio = decode_audio(str(path), sampling_rate=16000)
            duree = len(audio) / 16000
            if duree <= DUREE_MAX_PARAKEET_S:
                texte = _charger_parakeet().recognize(audio, sample_rate=16000) if duree > 0.2 else ""
                return {"text": corriger_nom((texte or "").strip()), "language": "", "duration": round(duree, 2),
                        "moteur": "parakeet-v3"}
        except Exception as exc:  # secours : la dictee ne doit jamais tomber
            print(f"[stt] parakeet indisponible ({exc}) : secours Whisper", flush=True)
    return _transcrire_whisper(path)
