"""Cle Meshy (3D en ligne) : lue dechiffree, moteur choisi par la demande."""
from backend.tools import t_mesh
from backend.tools.registry import ollama_tool_specs


def test_cle_meshy_lue_dechiffree(monkeypatch):
    from backend.core import app_settings

    lues = []
    monkeypatch.setattr(app_settings, "get_setting",
                        lambda cle, defaut="": lues.append(cle) or "  cle-en-clair  ")
    assert t_mesh._meshy_token() == "cle-en-clair"
    assert lues == ["connecteur_meshy_token"]


def test_cle_meshy_absente_ou_base_en_panne(monkeypatch):
    from backend.core import app_settings

    monkeypatch.setattr(app_settings, "get_setting", lambda cle, defaut="": "")
    assert t_mesh._meshy_token() == ""

    def panne(*a, **k):
        raise RuntimeError("base verrouillee")

    monkeypatch.setattr(app_settings, "get_setting", panne)
    assert t_mesh._meshy_token() == ""


def test_moteur_meshy_sans_cle_dit_ou_l_ajouter(monkeypatch, tmp_path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    monkeypatch.setattr(t_mesh, "_meshy_token", lambda: "")
    monkeypatch.setattr(t_mesh, "_verifier_acces", lambda *a, **k: None)
    monkeypatch.setattr(t_mesh, "_generer_mesh_meshy", lambda *a, **k: "ne doit pas etre appele")
    rep = t_mesh.tool_generate_mesh_from_image(str(image), str(tmp_path / "x.stl"), moteur="meshy")
    # Reponse immediate, avant le detourage et tout chargement de modele.
    assert "Reglages > Connexions" in rep and "ne doit pas" not in rep
    assert not (tmp_path / "x_ref.png").exists()


def test_schema_propose_le_moteur():
    spec = next(o["function"] for o in ollama_tool_specs(["generate_mesh_from_image"]))
    moteur = spec["parameters"]["properties"]["moteur"]
    assert "meshy" in moteur["enum"] and "" in moteur["enum"]
    assert "moteur" not in spec["parameters"]["required"]
