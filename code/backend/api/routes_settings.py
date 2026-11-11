from fastapi import APIRouter
from pydantic import BaseModel

from backend.core import secrets
# Re-export de compatibilite : les modules API qui importent get_setting depuis
# routes_settings continuent de fonctionner. La persistance vit dans le coeur
# pour que backend.core et backend.inference ne dependent pas de backend.api.
from backend.core.app_settings import get_setting, set_setting  # noqa: F401
from backend.core.database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])

ALLOWED_KEYS = {
    "tool_level",
    "tts_voice",
    "tts_rate",
    "default_model",
    "inference_backend",
    "auto_learn",
    "tts_autoplay",
    "tts_followup",
    "theme",
    "reflection_mode",
    "voice_model",
    "stt_model",
    "telegram_token",
    "telegram_enabled",
    "telegram_model",
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret",
    "style_auto",
    "api_key_elevenlabs",
    "api_key_deepl",
    "api_key_unsplash",
}


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.get("")
async def get_settings() -> dict:
    rows = get_db().query_all("SELECT key, value FROM app_settings")
    resultat = {}
    for r in rows:
        cle = r["key"]
        valeur = r["value"]
        resultat[cle] = secrets.dechiffrer(cle, valeur)
    return {"settings": resultat, "secrets_masques": True}


class InterestsUpdate(BaseModel):
    themes: list[str]


@router.get("/interests")
async def get_interests() -> dict:
    from backend.core import interests

    selection = interests.selection()
    return {
        "catalogue": interests.catalogue(),
        "selection": selection,
        "configura": bool(selection),
    }


@router.put("/interests")
async def put_interests(req: InterestsUpdate) -> dict:
    from backend.core import interests

    valides = interests.definir(req.themes)
    return {"ok": True, "selection": valides}


@router.put("")
async def maj_setting(req: SettingUpdate) -> dict:
    """Enregistre un reglage autorise (validation des cles cote API)."""
    if req.key not in ALLOWED_KEYS:
        return {"ok": False, "error": f"Cle non autorisee: {req.key}"}
    # mindful: if UI sends the ciphertext placeholder, do not overwrite
    if secrets.est_secret(req.key) and req.value in ("********", "********"):
        return {"ok": True, "conserve": True}
    set_setting(req.key, req.value)
    return {"ok": True}
