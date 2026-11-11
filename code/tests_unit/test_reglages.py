"""neo.toml : lecture Python, parite avec Rust, et garde-fou anti-chemins de machine."""
import re
import subprocess
from pathlib import Path

import pytest

from backend.core import reglages

CODE = Path(__file__).resolve().parents[1]
DEPOT = CODE.parent


def _trouve(vars_, nom):
    return next((v for n, v in vars_ if n == nom), None)


def test_racine_vide_donne_le_dossier_du_fichier():
    v = reglages.variables('[chemins]\nracine = ""\n', Path("/opt/neo"))
    assert _trouve(v, "LOCALAI_ROOT") == str(Path("/opt/neo"))


def test_correspondances_espace_reserve_et_variables():
    toml = '''
[chemins]
racine = "D:/Neo"
sd = "{racine}/outils/sd"
cache_hf = "F:/hf"
[reseau]
port = 9000
[variables]
NEO_IMAGE_VERIF = "0"
'''
    v = reglages.variables(toml, Path("/ailleurs"))
    assert _trouve(v, "LOCALAI_ROOT") == "D:/Neo"
    assert _trouve(v, "LOCALAI_SD_DIR") == "D:/Neo/outils/sd"
    assert _trouve(v, "HF_HOME") == "F:/hf"
    assert _trouve(v, "LOCALAI_PORT") == "9000"
    assert _trouve(v, "NEO_IMAGE_VERIF") == "0"
    assert _trouve(v, "LOCALAI_BLENDER") is None


def test_fichier_invalide():
    with pytest.raises(Exception):
        reglages.variables("[chemins\nracine=", Path("/x"))


def test_meme_table_que_rust():
    """neoconf.rs et reglages.py doivent alimenter exactement les memes variables."""
    rs = (CODE / "localai-rs" / "localai-core" / "src" / "neoconf.rs").read_text(encoding="utf-8")
    bloc = rs.split("pub const CORRESPONDANCES")[1].split("];")[0]
    cote_rust = [
        (s, c, tuple(re.findall(r'"([A-Z0-9_]+)"', v)))
        for s, c, v in re.findall(r'\("([a-z_]+)",\s*"([a-z_]+)",\s*&\[([^\]]*)\]\)', bloc)
    ]
    assert cote_rust == reglages.CORRESPONDANCES


def test_exemple_versionne_valide():
    exemple = CODE / "config" / "neo.exemple.toml"
    v = reglages.variables(exemple.read_text(encoding="utf-8"), Path("/racine"))
    assert _trouve(v, "LOCALAI_ROOT") == str(Path("/racine"))


# Chemins propres a une machine : ils ne doivent plus jamais revenir dans le code.
_INTERDIT = re.compile(
    r"[A-Za-z]:[\\/]+(ia[\\/]|ia\b|localai-rs|LocalAI-|Neo-Archive|neo-repo|huggingface_cache|OrcaSlicer|FlashForge|ComfyUI)"
    r"|Users[\\/]+(?!(?:Camille|Alice|Public|Default|x)\b)[A-Za-z]",
    re.IGNORECASE,
)
_EXCLUS = ("code/eval/resultats/", ".md")


def test_aucun_chemin_de_machine_dans_le_code():
    fichiers = subprocess.run(
        ["git", "ls-files", "code", "site", ".github"], cwd=DEPOT, capture_output=True, text=True, check=True
    ).stdout.split()
    fautes = []
    for rel in fichiers:
        if rel.endswith(_EXCLUS) or any(e in rel for e in _EXCLUS[:1]):
            continue
        if not rel.endswith((".py", ".rs", ".ts", ".tsx", ".ps1", ".bat", ".json", ".toml", ".yml", ".html", ".psm1")):
            continue
        try:
            texte = (DEPOT / rel).read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        for n, ligne in enumerate(texte.splitlines(), 1):
            if _INTERDIT.search(ligne):
                fautes.append(f"{rel}:{n}: {ligne.strip()[:100]}")
    assert not fautes, "chemins de machine trouves :\n" + "\n".join(fautes[:40])
