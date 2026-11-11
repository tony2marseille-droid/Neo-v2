"""Brief du studio : choix du modele et mode de reflexion (reseau simule)."""
import pytest

from backend.core import direction

BRIEF = {"analyse": "x", "ambiguites": [], "comprehension": "Un chat roux.", "corrections": [],
         "style": "photo", "cadrage": "paysage", "elements": ["ginger cat"], "titre": "chat",
         "prompt": ("A ginger cat sitting on a wooden table near a window, soft natural light, shallow depth "
                    "of field, shot on a 50 mm lens, detailed fur and whiskers, warm tones.")}


class _Rep:
    def __init__(self, corps):
        self._corps = corps

    def json(self):
        return self._corps


@pytest.fixture
def ollama(monkeypatch):
    import httpx

    etat = {"installes": ["gemma4:12b", "qwen3:14b"], "envois": []}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Rep({"models": [{"name": n} for n in etat["installes"]]}))

    def post(url, json=None, **k):
        etat["envois"].append(json)
        return _Rep({"message": {"content": __import__("json").dumps(BRIEF)}, "load_duration": 2_500_000_000})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.delenv("NEO_IMAGE_BRIEF", raising=False)
    monkeypatch.delenv("NEO_IMAGE_REFLEXION", raising=False)
    return etat


def test_brief_par_le_modele_de_conversation_sans_reflexion(ollama):
    b = direction.brief("un chat roux sur une table")
    envoi = ollama["envois"][0]
    assert b["ok"] and b["modele"] == "gemma4:12b" and b["chargement_s"] == 2.5
    assert envoi["model"] == "gemma4:12b" and envoi["think"] is False
    assert envoi["keep_alive"] == "60m" and envoi["options"]["num_ctx"] == 16384


def test_sans_gemma4_qwen3_reflechit(ollama):
    ollama["installes"] = ["qwen3:14b", "qwen3:8b"]
    b = direction.brief("un chat roux sur une table")
    envoi = ollama["envois"][0]
    assert b["modele"] == "qwen3:14b" and envoi["think"] is True and envoi["options"]["num_ctx"] == 8192


def test_reflexion_imposee_par_l_environnement(ollama, monkeypatch):
    monkeypatch.setenv("NEO_IMAGE_REFLEXION", "1")
    direction.brief("un chat roux sur une table")
    assert ollama["envois"][0]["think"] is True


def test_modele_sans_reflexion_ne_recoit_pas_le_parametre(ollama):
    ollama["installes"] = ["gemma3:12b"]
    direction.brief("un chat roux sur une table")
    assert "think" not in ollama["envois"][0]


def _fiche(monkeypatch, fichiers):
    """Ollama simule : la fiche du modele cite ces fichiers."""
    import httpx

    lignes = "\n".join(f"FROM {f}" for f in fichiers) + "\nTEMPLATE {{ .Prompt }}"
    demandes = []

    def post(url, json=None, **k):
        demandes.append((url, json))
        return _Rep({"modelfile": lignes})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Rep({"models": [{"name": "gemma4:12b"}]}))
    monkeypatch.delenv("NEO_IMAGE_PRECHAUFFE", raising=False)
    return demandes


def test_prechauffage_lit_les_fichiers_du_modele(monkeypatch, tmp_path):
    poids, projecteur = tmp_path / "poids.gguf", tmp_path / "projecteur.gguf"
    poids.write_bytes(b"a" * 3_000_000)
    projecteur.write_bytes(b"b" * 1_000_000)
    demandes = _fiche(monkeypatch, [poids, projecteur, tmp_path / "absent.gguf", "gemma4:12b"])
    etat = direction.prechauffer_modele()
    etat["fil"].join(10)
    assert etat["go"] == 0.0 and "duree_s" in etat and "saute" not in etat
    assert demandes[0][0].endswith("/api/show") and demandes[0][1] == {"model": "gemma4:12b"}
    assert direction._fichiers_modele("http://x", "gemma4:12b") == [poids, projecteur]


def test_prechauffage_sans_fichier_ou_coupe(monkeypatch, tmp_path):
    _fiche(monkeypatch, [])
    etat = direction.prechauffer_modele()
    etat["fil"].join(10)
    assert etat["saute"] == "aucun fichier"
    monkeypatch.setenv("NEO_IMAGE_PRECHAUFFE", "0")
    assert direction.prechauffer_modele() == {"saute": "desactive"}


def test_prechauffage_renonce_si_la_memoire_manque(monkeypatch, tmp_path):
    psutil = pytest.importorskip("psutil")
    poids = tmp_path / "poids.gguf"
    poids.write_bytes(b"a" * 1000)
    _fiche(monkeypatch, [poids])
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("M", (), {"available": 2 * 1024**3})())
    etat = direction.prechauffer_modele()
    etat["fil"].join(10)
    assert etat["saute"] == "memoire vive trop juste"


def test_prechauffage_ne_fait_jamais_echouer(monkeypatch):
    import httpx

    def panne(*a, **k):
        raise httpx.ConnectError("ollama arrete")

    monkeypatch.setattr(httpx, "post", panne)
    monkeypatch.setattr(httpx, "get", panne)
    monkeypatch.delenv("NEO_IMAGE_PRECHAUFFE", raising=False)
    etat = direction.prechauffer_modele("gemma4:12b")
    etat["fil"].join(10)
    assert "ollama arrete" in etat["saute"]


def test_prechargement_meme_contexte_que_la_verification(monkeypatch):
    import httpx

    envois = []
    monkeypatch.setattr(httpx, "post", lambda url, json=None, **k: envois.append((url, json)) or _Rep({}))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Rep({"models": [{"name": "gemma4:12b"}]}))
    monkeypatch.delenv("NEO_IMAGE_PRECHARGE", raising=False)
    direction.precharger_modele().join(10)
    url, corps = envois[0]
    assert url.endswith("/api/generate") and "prompt" not in corps
    assert corps == {"model": "gemma4:12b", "keep_alive": "60m", "options": {"num_ctx": 16384}}
    monkeypatch.setenv("NEO_IMAGE_PRECHARGE", "0")
    assert direction.precharger_modele() is None and len(envois) == 1


def test_studio_ne_relit_pas_un_fichier_qui_vient_de_l_etre(monkeypatch, tmp_path, capsys):
    from backend.core import sdgen

    gros = tmp_path / "modele.gguf"
    with open(gros, "wb") as f:
        f.truncate(101 * 1024 * 1024)
    monkeypatch.setattr(sdgen, "_CHAUDS", {})
    sdgen._prechauffer_cache([str(gros), str(tmp_path / "absent.gguf")])
    sdgen._prechauffer_cache([str(gros)])
    assert capsys.readouterr().out.count("[sd-warm] modele.gguf") == 1
    # Passe le delai, la lecture reprend.
    sdgen._CHAUDS[str(gros)] -= sdgen._CHAUD_S + 1
    sdgen._prechauffer_cache([str(gros)])
    assert capsys.readouterr().out.count("[sd-warm] modele.gguf") == 1
    # Un autre fil le lit deja : on attend sa fin, sans lire en double.
    import threading

    sdgen._CHAUDS.clear()
    fini = threading.Event()
    fini.set()
    monkeypatch.setattr(sdgen, "_LECTURES", {str(gros): fini})
    sdgen._prechauffer_cache([str(gros)])
    assert "[sd-warm]" not in capsys.readouterr().out


@pytest.mark.parametrize("charges,decharge", [
    (["gemma4:12b"], False),            # le modele de vision est deja la : on n'y touche pas
    ([], False),                        # carte vide : rien a liberer
    (["qwen3:14b"], True),              # un autre modele occupe la carte
    (["gemma4:12b", "qwen3:8b"], True),
])
def test_recherche_de_reference_ne_decharge_pas_le_modele_de_vision(monkeypatch, charges, decharge):
    import httpx

    from backend.core import sdgen
    from backend.tools import t_image

    appels = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Rep({"models": [{"name": n} for n in charges]}))
    monkeypatch.setattr(t_image, "_find_vision_model", lambda: "gemma4:12b")
    monkeypatch.setattr(sdgen, "_liberer_vram", lambda *a, **k: appels.append(1))
    t_image._liberer_pour_vision()
    assert bool(appels) is decharge


def test_consigne_reference_reservee_aux_sujets_nommes():
    from backend.core.agents import get_agent
    from backend.tools.registry import ollama_tool_specs

    agent = get_agent("image")
    consigne = agent.system_prompt
    assert "JAMAIS find_reference_image pour une personne" in consigne
    assert "NOMME" in consigne and "DIRECTEMENT generate_image" in consigne
    description = next(o["function"]["description"] for o in ollama_tool_specs(["generate_image"]))
    assert "SUJET REEL NOMME" in description and "Jamais pour une" in description


def test_generer_previent_quand_la_carte_est_libre():
    import inspect

    from backend.core import sdgen

    assert "apres_gpu" in inspect.signature(sdgen.generer).parameters
    source = inspect.getsource(sdgen._generer_nolock)
    assert source.index("apres_gpu()") < source.index("_finition(out")
    assert source.index("_detail_visages(out") < source.index("apres_gpu()")


@pytest.mark.parametrize("modele,attendu", [
    ("gemma4:12b", True), ("qwen3:14b", True), ("gpt-oss:20b", True), ("deepseek-r1:8b", True),
    ("gemma3:12b", False), ("llama3.1:8b", False), ("localai-prog-v10-q5km:latest", False),
])
def test_modeles_capables_de_reflexion(modele, attendu):
    assert direction._sait_reflechir(modele) is attendu
