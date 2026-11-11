# -*- coding: utf-8 -*-
"""Acces centralise aux reglages persistes (table app_settings).

Ce module vit dans le coeur : ``backend.core`` et ``backend.inference`` ne
doivent pas dependre de ``backend.api`` (evite le cycle core -> api).
"""
from backend.core import secrets
from backend.core.database import get_db


def get_setting(key: str, default: str = "") -> str:
    """Lit un reglage (dechiffre si secret), ou ``default`` s'il est absent."""
    row = get_db().query_one("SELECT value FROM app_settings WHERE key=?", (key,))
    if not row:
        return default
    return secrets.dechiffrer(key, row["value"])


def set_setting(key: str, value: str) -> None:
    """Ecrit un reglage (chiffre si la cle est un secret connu)."""
    get_db().execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, secrets.chiffrer(key, value)),
    )
