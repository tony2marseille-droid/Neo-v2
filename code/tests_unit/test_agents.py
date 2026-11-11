"""Table des agents : coherence entre consignes, outils et registre."""
from backend.core import agents


def test_table_des_agents_coherente():
    assert agents.incoherences() == []


def test_chaque_agent_a_sa_consigne_dans_un_fichier():
    for a in agents.BUILTIN_AGENTS:
        if a.id == "general":
            continue
        fichier = agents._CONSIGNES / f"{a.id}.md"
        assert fichier.is_file(), a.id
        assert a.system_prompt == agents._lire(fichier) and len(a.system_prompt) > 200, a.id


def test_general_est_neo_lui_meme():
    g = agents.get_agent("general")
    assert g.tools == [] and "{nom}" in g.system_prompt  # nom et utilisateur : poses par le serveur
    assert agents._PERSONA.is_file() and g.system_prompt == agents._lire(agents._PERSONA)


def test_incoherence_detectee(monkeypatch):
    faux = agents.Agent(id="essai", name="x", description="x", tools=["outil_fantome"],
                        system_prompt="Utilise web_search puis calculator.")
    monkeypatch.setattr(agents, "BUILTIN_AGENTS", [faux])
    defauts = agents.incoherences()
    assert any("outil inconnu" in d for d in defauts)
    assert any("web_search" in d for d in defauts) and any("calculator" in d for d in defauts)


def test_agent_inconnu():
    assert agents.get_agent("") is None and agents.get_agent("inexistant") is None
    assert {a["id"] for a in agents.list_agents()} >= {"general", "dev", "docs", "image", "bureau",
                                                       "systeme", "cad3d", "depot"}
