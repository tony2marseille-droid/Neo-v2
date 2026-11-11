# -*- coding: utf-8 -*-
"""Tests de securite des outils agent (backend/tools) : garde SSRF, sandbox
d'ecriture, recherche de fichiers, oubli memoire, Wikipedia, DOCX.

Aucun appel reseau, aucun sous-processus : les racines sandbox, les regles et
la base sont detournees vers des dossiers/bases temporaires."""
import sqlite3

import httpx
import pytest

from backend.tools import common, registry, t_docs, t_extra, t_files, t_wiki


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Workspace temporaire = seule racine d'ecriture et de lecture."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = {
        "root": ws, "read_roots": [ws],
        "cmd_allow": ["python", "python.exe", "dir", "type", "findstr"],
        "cmd_deny": ["cmd", "powershell"], "timeout_s": 5, "max_output": 2000,
    }
    regles = {"blocked_extensions_write": [".exe", ".bat", ".cmd", ".ps1"]}
    monkeypatch.setattr(common, "_sandbox_cfg", lambda: cfg)
    monkeypatch.setattr(registry, "_sandbox_cfg", lambda: cfg)
    monkeypatch.setattr(t_files, "_sandbox_cfg", lambda: cfg)
    monkeypatch.setattr(common, "charger_regles", lambda: regles)
    monkeypatch.setattr(common, "_dossier_produits", lambda: None)
    for module in (common, t_files):
        monkeypatch.setattr(module, "journaliser", lambda *a, **k: None)
    return ws


# ---------------------------------------------------------------- SSRF

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8320/api/settings",
    "http://10.1.2.3/",
    "http://192.168.1.10:8188/",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]:11434/api/tags",
    "http://[::ffff:127.0.0.1]/",
    "http://localhost:11434/",
    "http://0.0.0.0/",
    "ftp://93.184.216.34/",
    "pas une url",
])
def test_refus_url_adresses_non_publiques(url, monkeypatch):
    monkeypatch.setattr(common, "journaliser", lambda *a, **k: None)
    assert common.refus_url(url) is not None


def test_refus_url_ip_publique_autorisee():
    assert common.refus_url("https://93.184.216.34/page") is None
    assert common.refus_url("http://[2606:4700:4700::1111]/") is None


def test_redirection_vers_loopback_refusee(monkeypatch):
    """La garde s'applique a chaque saut de redirection (hook httpx)."""
    monkeypatch.setattr(common, "journaliser", lambda *a, **k: None)

    def repondre(request):
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8320/"})
        return httpx.Response(200, text="secret")

    with common.client_http(transport=httpx.MockTransport(repondre)) as client:
        with pytest.raises(common.UrlRefusee):
            client.get("http://93.184.216.34/")


# ---------------------------------------------------------------- write_file

@pytest.mark.parametrize("nom", [
    "x.bat", "X.BAT", "x.bat.", "x.bat ", "x.bat::$DATA", "x.txt:flux",
    "x.js", "x.hta", "x.lnk", "x.psm1", r"sous\..\x.ps1", r".git\config",
])
def test_write_file_extensions_interdites(sandbox, nom):
    resultat = t_files.tool_write_file(str(sandbox / nom), "contenu")
    assert "BLOQUEE" in resultat or resultat.startswith("refusé"), resultat
    assert not any(p.is_file() for p in sandbox.rglob("*"))


def test_write_file_hors_workspace_refuse(sandbox):
    dehors = sandbox.parent / "dehors.txt"
    assert t_files.tool_write_file(str(dehors), "x").startswith("refusé")
    assert t_files.tool_write_file(r"..\dehors.txt", "x").startswith("refusé")
    assert not dehors.exists()


def test_write_file_autorise_dans_workspace(sandbox):
    assert t_files.tool_write_file(str(sandbox / "notes.txt"), "ok").startswith("Ecrit:")
    assert (sandbox / "notes.txt").read_text(encoding="utf-8") == "ok"


def test_write_file_python_jamais_execute(sandbox):
    """write_file d'un .py : controle de syntaxe seulement, aucune execution."""
    temoin = sandbox / "execute.txt"
    code = f"open(r'{temoin}', 'w').write('x')\n"
    res = registry.execute_tool("write_file", {"path": str(sandbox / "s.py"), "content": code},
                                registry.PermissionManager("POWER"))
    assert "Syntaxe OK" in res
    assert not temoin.exists()


def test_execute_tool_arguments_non_dict():
    res = registry.execute_tool("calculator", "1+1", registry.PermissionManager("SAFE"))
    assert res.startswith("Erreur d'execution")


def test_ollama_tool_specs_ignore_outil_inconnu():
    specs = registry.ollama_tool_specs(["calculator", "outil_fantome"])
    assert [s["function"]["name"] for s in specs] == ["calculator"]


# ---------------------------------------------------------------- run_command

@pytest.mark.parametrize("commande", [
    "python -c print(1)",           # interpreteur refuse meme si cmd_allow l'autorise
    r"type C:\Windows\win.ini",      # lecture hors racines
    r"type ..\..\secret.txt",
    "dir %USERPROFILE%",              # expansion cmd.exe
    r"findstr /g:C:\x.txt motif a.txt",
])
def test_run_command_refus(sandbox, commande):
    assert t_files.tool_run_command(commande).startswith("refusé")


# ---------------------------------------------------------------- file_search

def test_file_search_trouve_et_elague(sandbox):
    (sandbox / "projet").mkdir()
    (sandbox / "projet" / "a.txt").write_text("ligne\nune aiguille ici\n", encoding="utf-8")
    (sandbox / "node_modules").mkdir()
    (sandbox / "node_modules" / "b.txt").write_text("aiguille cachee", encoding="utf-8")
    for chemin in (str(sandbox), "."):
        res = registry._tool_file_search("AIGUILLE", chemin)
        assert "a.txt:2:" in res, res
        assert "node_modules" not in res


def test_file_search_hors_racines_refuse(sandbox):
    assert registry._tool_file_search("x", str(sandbox.parent)).startswith("refusé")


# ---------------------------------------------------------------- memory_forget

class _BaseMemoire:
    def __init__(self):
        self.cnx = sqlite3.connect(":memory:")
        self.cnx.row_factory = sqlite3.Row
        self.cnx.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        for texte in ("J'aime le cafe", "Sur a 100% du resultat", "Mot_de_passe wifi"):
            self.cnx.execute("INSERT INTO memories (content) VALUES (?)", (texte,))

    def query_all(self, sql, params=()):
        return [dict(r) for r in self.cnx.execute(sql, params)]

    def execute(self, sql, params=()):
        return self.cnx.execute(sql, params).rowcount

    def contenus(self):
        return [r["content"] for r in self.query_all("SELECT content FROM memories")]


@pytest.fixture()
def base_memoire(monkeypatch):
    from backend.core import database

    base = _BaseMemoire()
    monkeypatch.setattr(database, "get_db", lambda: base)
    return base


@pytest.mark.parametrize("motif", ["%", "%%%", "_", "a", "  ", "%_%"])
def test_memory_forget_refuse_motifs_vagues(base_memoire, motif):
    t_extra.tool_memory_forget(motif)
    assert len(base_memoire.contenus()) == 3


def test_memory_forget_jokers_litteraux(base_memoire):
    assert t_extra.tool_memory_forget("100%").startswith("1 souvenir")
    assert base_memoire.contenus() == ["J'aime le cafe", "Mot_de_passe wifi"]
    # « _ » est litteral : « le_cafe » ne doit pas matcher « le cafe ».
    assert t_extra.tool_memory_forget("le_cafe").startswith("Aucun")
    assert t_extra.tool_memory_forget("mot_de").startswith("1 souvenir")
    assert base_memoire.contenus() == ["J'aime le cafe"]


def test_motif_like_echappe():
    assert t_extra._motif_like("a%b_c\\") == "%a\\%b\\_c\\\\%"


# ---------------------------------------------------------------- wikipedia

@pytest.mark.parametrize("brut, attendu", [
    ("en", "en"), ("EN", "en"), ("ast", "ast"), (None, "fr"), ("", "fr"),
    ("evil.com/x?", "fr"), ("fr.attaquant", "fr"), ("abcd", "fr"),
])
def test_wiki_langue(brut, attendu):
    assert t_wiki._langue(brut) == attendu


# ---------------------------------------------------------------- docx

def test_texte_riche_garde_tout_le_texte():
    from docx import Document

    paragraphe = Document().add_paragraph()
    t_docs._texte_riche(paragraphe, "A **B** C **D**")
    assert paragraphe.text == "A B C D"
    gras = [r.text for r in paragraphe.runs if r.bold]
    assert gras == ["B", "D"]
