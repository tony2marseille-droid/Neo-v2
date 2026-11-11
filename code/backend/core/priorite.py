# -*- coding: utf-8 -*-
"""Priorite du processus : normale, quel que soit le lanceur.

Les services de Neo sont lances par des taches planifiees. Windows donne a une
tache la priorite 7 par defaut : le programme, et tout ce qu'il lance, tourne
alors en classe « inferieure a la normale », avec une priorite de lecture
disque BASSE et une priorite memoire de 2 sur 5. Mesure du 11/11/2026, meme
commande, meme graine : un clip Wan en 350 s ainsi (chargement des poids 262 s)
contre 92 s en priorite normale (chargement 1,8 s). En production : 410 a 700 s.

`normale()` remet le processus courant en priorite normale (processeur, disque,
memoire) ; les programmes qu'il lance ensuite (sd-cli, ffmpeg) en heritent.
Appele par le studio et par le worker. Le correctif de fond est le reglage des
taches elles-memes (`scripts/priorite_taches.ps1`), qui vaut aussi pour Ollama
et pour le serveur.
"""
from __future__ import annotations

import sys

_CLASSE_NORMALE = 0x20
_CLASSES = {0x40: "idle", 0x4000: "inferieure", 0x20: "normale", 0x8000: "superieure", 0x80: "haute",
            0x100: "temps reel"}
_DISQUE, _MEMOIRE = 33, 39          # ProcessIoPriority, ProcessPagePriority
_DISQUE_NORMAL, _MEMOIRE_NORMALE = 2, 5
_FAIT = False


def etat() -> dict:
    """{classe, disque, memoire} du processus courant ; {} hors Windows."""
    if sys.platform != "win32":
        return {}
    import ctypes
    import ctypes.wintypes as wt

    k32, ntdll = ctypes.WinDLL("kernel32"), ctypes.WinDLL("ntdll")
    k32.GetCurrentProcess.restype = wt.HANDLE
    moi = wt.HANDLE(k32.GetCurrentProcess())
    k32.GetPriorityClass.argtypes = [wt.HANDLE]
    res = {"classe": _CLASSES.get(k32.GetPriorityClass(moi), "?")}
    for cle, classe in (("disque", _DISQUE), ("memoire", _MEMOIRE)):
        v = ctypes.c_ulong(0)
        if ntdll.NtQueryInformationProcess(moi, classe, ctypes.byref(v), 4, None) == 0:
            res[cle] = int(v.value)
    return res


def normale(force: bool = False) -> dict:
    """Remet le processus courant en priorite normale. Sans effet s'il y est
    deja, hors Windows, ou si NEO_PRIORITE_NORMALE=0. Ne leve jamais.
    Retourne l'etat obtenu."""
    global _FAIT
    import os

    if sys.platform != "win32" or os.environ.get("NEO_PRIORITE_NORMALE", "1") == "0":
        return {}
    if _FAIT and not force:
        return {}
    _FAIT = True
    try:
        import ctypes
        import ctypes.wintypes as wt

        avant = etat()
        k32, ntdll = ctypes.WinDLL("kernel32"), ctypes.WinDLL("ntdll")
        k32.GetCurrentProcess.restype = wt.HANDLE
        moi = wt.HANDLE(k32.GetCurrentProcess())
        k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        # On ne fait que REMONTER vers la normale : une priorite plus haute,
        # choisie ailleurs, est laissee telle quelle.
        if avant.get("classe") in ("idle", "inferieure"):
            k32.SetPriorityClass(moi, _CLASSE_NORMALE)
        for cle, classe, normal in (("disque", _DISQUE, _DISQUE_NORMAL), ("memoire", _MEMOIRE, _MEMOIRE_NORMALE)):
            if avant.get(cle, normal) < normal:
                v = ctypes.c_ulong(normal)
                ntdll.NtSetInformationProcess(moi, classe, ctypes.byref(v), 4)
        return etat()
    except Exception:
        return {}
