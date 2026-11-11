"""Priorite du processus remise a la normale (essai dans un processus a part)."""
import json
import subprocess
import sys

import pytest

SCRIPT = r"""
import ctypes, ctypes.wintypes as wt, json, sys
sys.path.insert(0, sys.argv[1])
from backend.core import priorite
k32, ntdll = ctypes.WinDLL("kernel32"), ctypes.WinDLL("ntdll")
k32.GetCurrentProcess.restype = wt.HANDLE
moi = wt.HANDLE(k32.GetCurrentProcess())
k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
k32.SetPriorityClass(moi, 0x4000)                       # comme une tache planifiee de priorite 7
for classe, valeur in ((33, 1), (39, 2)):
    v = ctypes.c_ulong(valeur)
    ntdll.NtSetInformationProcess(moi, classe, ctypes.byref(v), 4)
bas = priorite.etat()
apres = priorite.normale()
print(json.dumps({"bas": bas, "apres": apres, "second_appel": priorite.normale()}))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="priorites Windows")
def test_remontee_a_la_priorite_normale():
    from pathlib import Path

    racine = str(Path(__file__).resolve().parents[1])
    r = subprocess.run([sys.executable, "-c", SCRIPT, racine], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert d["bas"] == {"classe": "inferieure", "disque": 1, "memoire": 2}
    assert d["apres"] == {"classe": "normale", "disque": 2, "memoire": 5}
    assert d["second_appel"] == {}                     # une seule fois par processus


def test_coupe_par_l_environnement(monkeypatch):
    from backend.core import priorite

    monkeypatch.setenv("NEO_PRIORITE_NORMALE", "0")
    monkeypatch.setattr(priorite, "_FAIT", False)
    assert priorite.normale() == {} and priorite._FAIT is False
