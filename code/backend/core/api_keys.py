# -*- coding: utf-8 -*-
"""Coffre-fort local des cles API externes.

Les cles sont stockee dans la table app_settings (meme mecanisme que les
reglages existants), chiffrees automatiquement par backend.core.secrets
quand la cle contient "api_key" (ex: api_key_elevenlabs).
"""

from backend.core.app_settings import get_setting, set_setting


def get_api_key(service: str) -> str | None:
    """Recupere la cle API d'un service externe.

    Args:
        service: nom du service (ex: 'elevenlabs', 'deepl', 'unsplash')

    Retourne la cle en clair, ou None si non configuree.
    """
    valeur = get_setting(f"api_key_{service}", "")
    if not valeur:
        return None
    return valeur


def set_api_key(service: str, cle: str) -> None:
    """Enregistre une cle API pour un service (chiffree au repos)."""
    set_setting(f"api_key_{service}", cle)


def supprimer_api_key(service: str) -> None:
    """Supprime une cle API (remplace par chaine vide)."""
    set_setting(f"api_key_{service}", "")


def est_configue(service: str) -> bool:
    """Indique si une cle est configuree pour le service."""
    return get_api_key(service) is not None
