#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lance l'API vidéo Néo sur un port dédié (FastAPI + uvicorn, port 8321).

Déployé en E:\\ia\\serve_video.py par deployer.ps1 (voir correspondances.psd1).

Sécurité, alignée sur le serveur Rust (main.rs, auth_middleware) :
- CORS limité aux pages servies par Néo (port 8320, quel que soit l'hôte) ;
- si le réglage `api_token` est défini, les clients NON locaux doivent
  présenter `Authorization: Bearer <token>` ou `?token=<token>` (les balises
  <img>/<video> ne peuvent pas envoyer d'en-tête). Les clients loopback
  passent toujours.
"""
import hmac
import ipaddress
import os
import sys
from pathlib import Path

# Racine du projet : dossier du script (E:\ia) ou son parent (code\scripts).
_ICI = Path(__file__).resolve().parent
RACINE = _ICI if (_ICI / "backend").is_dir() else _ICI.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
os.chdir(str(RACINE))

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.api.routes_neo import fiches as fiches_router  # noqa: E402
from backend.api.routes_neo import photo as photo_router  # noqa: E402
from backend.api.routes_video import router as video_router  # noqa: E402


def _est_local(hote: str | None) -> bool:
    if not hote:
        return False
    try:
        ip = ipaddress.ip_address(hote)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _jeton_attendu() -> str:
    """Jeton API (déchiffré si stocké en `enc:v1:`), "" s'il n'est pas défini."""
    from backend.core.app_settings import get_setting

    return (get_setting("api_token", "") or "").strip()


def verifier_jeton(request: Request) -> None:
    """Même règle que le Rust : loopback libre, sinon jeton si défini."""
    if _est_local(request.client.host if request.client else None):
        return
    attendu = _jeton_attendu()
    if not attendu:
        return  # pas de jeton défini : accès LAN autorisé (comme le Rust)
    entete = request.headers.get("authorization", "")
    fourni = entete[7:].strip() if entete.startswith("Bearer ") else entete.strip()
    if not fourni:
        fourni = request.query_params.get("token", "")
    if fourni and hmac.compare_digest(fourni.encode("utf-8"), attendu.encode("utf-8")):
        return
    raise HTTPException(status_code=401, detail="token API requis (Authorization: Bearer)")


app = FastAPI(title="Néo - API Vidéo", version="1.0.0")

# CORS : uniquement les pages de Néo (port 8320 de n'importe quel hôte :
# localhost, IP du LAN, nom Tailscale). Le préflight OPTIONS est traité par
# ce middleware, avant le contrôle du jeton.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://[^/]+:8320$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Origin", "Accept", "Authorization"],
    max_age=3600,
)

app.include_router(video_router, dependencies=[Depends(verifier_jeton)])
# Filtres photo et fiches de connaissances : meme service, meme controle.
app.include_router(photo_router, dependencies=[Depends(verifier_jeton)])
app.include_router(fiches_router, dependencies=[Depends(verifier_jeton)])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8321, log_level="info")
