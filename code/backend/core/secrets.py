"""Secrets au repos cote Python (miroir de oracle-core/secrets.rs).

Meme algorithme : ChaCha20-Poly1305, nonce 12 octets, format
``enc:v1:`` + base64(nonce || ciphertext + tag). La cle machine est le
fichier ``.neo_keystore`` dans le dossier de la base (cree par le serveur
Rust). Les valeurs en clair (anciennes versions) passent telles quelles.
"""
import base64
import os
from pathlib import Path

from backend.core.config import settings

PREFIX = "enc:v1:"


def db_path() -> Path:
    return settings.db_path


def keystore_path() -> Path:
    return db_path().parent / ".neo_keystore"


def est_secret(cle: str) -> bool:
    """Meme regle que `est_secret` de localai-core/secrets.rs (a garder alignee).

    Seuls les `provider_*_key` sont des secrets (l'ancienne version Python
    traitait tout `provider_*` comme secret, ce qui divergeait du Rust).
    """
    cles_explicites = {
        "api_token", "webui_pass", "telegram_token", "livekit_api_key",
        "livekit_api_secret", "connecteur_meshy_token",
        "connecteur_telegram_token", "connecteur_discord_token",
    }
    if cle in cles_explicites:
        return True
    if cle.startswith("pubapi_key."):
        return True
    if cle.startswith("provider_") and cle.endswith("_key"):
        return True
    minuscule = cle.lower()
    return any(
        motif in minuscule
        for motif in ("token", "secret", "password", "passwd", "_pass", "apikey", "api_key", "cle_api")
    )


def _lire_cle() -> bytes | None:
    try:
        data = keystore_path().read_bytes()
    except FileNotFoundError:
        return None
    if len(data) != 32:
        return None
    return data


def chiffrer(cle_setting: str, valeur: str) -> str:
    if not est_secret(cle_setting) or not valeur or valeur.startswith(PREFIX):
        return valeur
    cle = _lire_cle()
    if cle is None:
        return valeur  # repli : base pas encore initialisee
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(cle).encrypt(nonce, valeur.encode("utf-8"), None)
    return PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def dechiffrer(cle_setting: str, valeur: str) -> str:
    # Decide par le PREFIXE seul (pas par est_secret) : une valeur deja
    # chiffree sous l'ancienne regle (ex. un `provider_*` non `_key`) reste
    # lisible apres l'alignement sur le Rust. Echec -> valeur brute.
    if not valeur.startswith(PREFIX):
        return valeur
    cle = _lire_cle()
    if cle is None:
        return valeur
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    try:
        raw = base64.b64decode(valeur[len(PREFIX):])
        if len(raw) < 12 + 16:
            return valeur
        nonce, ct = raw[:12], raw[12:]
        return ChaCha20Poly1305(cle).decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        return valeur
