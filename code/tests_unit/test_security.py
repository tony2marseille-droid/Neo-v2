# -*- coding: utf-8 -*-
"""Tests des regles de securite (motifs de commandes, extensions, sandbox de
chemins, journal). Tout est isole dans un dossier temporaire : aucune regle
reelle ni aucun journal de production n'est lu ou ecrit."""
import pytest

from backend.core import security


@pytest.fixture()
def securite_isolee(tmp_path, monkeypatch):
    """Detourne tous les chemins de regles/journal vers un dossier temporaire
    et coupe la lecture de la base (dossiers_autorises -> [])."""
    monkeypatch.setattr(security, "SECURITY_DIR", tmp_path)
    monkeypatch.setattr(security, "RULES_PATH", tmp_path / "security_rules.json")
    monkeypatch.setattr(security, "LOG_PATH", tmp_path / "security_log.jsonl")
    monkeypatch.setattr(security, "dossiers_autorises", lambda: [])
    monkeypatch.setattr(security, "HOME", lambda: tmp_path)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    return tmp_path


def test_est_sous(securite_isolee):
    racine = securite_isolee
    assert security._est_sous(racine / "a" / "b", racine)
    assert security._est_sous(racine, racine)
    assert not security._est_sous(racine.parent, racine)


def test_commande_autorisee_bloque_les_motifs_interdits(securite_isolee):
    for commande in ["format C:", "shutdown /s", "del /f /s C:\\x", "reg add HKLM"]:
        assert security.commande_autorisee(commande) is not None, commande
    assert security.commande_autorisee("echo bonjour") is None
    assert security.commande_autorisee("python train.py") is None


def test_extension_autorisee_ecriture(securite_isolee):
    for ext in [".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi"]:
        assert security.extension_autorisee_ecriture(f"x{ext}") is not None, ext
    assert security.extension_autorisee_ecriture("rapport.txt") is None
    assert security.extension_autorisee_ecriture("data.json") is None


def test_verifier_acces_dossier_installation_autorise(securite_isolee):
    assert security.verifier_acces(securite_isolee / "fichier.txt") is None
    assert security.verifier_acces(securite_isolee / "data" / "x") is None


def test_verifier_acces_hors_installation_refuse(securite_isolee):
    exterieur = securite_isolee.parent / "autre_dossier"
    exterieur.mkdir(exist_ok=True)
    message = security.verifier_acces(exterieur / "f.txt")
    assert message is not None
    assert "ACCES REFUSE" in message


def test_verifier_acces_chemin_relatif_autorise(securite_isolee):
    # Un nom relatif est resolu depuis le dossier d'installation (HOME).
    assert security.verifier_acces("data/relatif.txt") is None
    assert security.verifier_acces("relatif.txt", ecriture=True) is None


def test_verifier_acces_chemin_relatif_evasion_refusee(securite_isolee):
    for chemin in ["..\\..\\Users\\x\\secret.txt", "../dehors.txt", "data/../../x.txt"]:
        message = security.verifier_acces(chemin, ecriture=True)
        assert message is not None and "ACCES REFUSE" in message, chemin


def test_verifier_acces_chemin_relatif_lecteur_refuse(securite_isolee):
    # « C:foo » depend du dossier courant du lecteur : refuse.
    assert security.verifier_acces("C:foo.txt") is not None
    assert security.verifier_acces("\\racine_du_lecteur.txt") is not None


def test_charger_regles_corrompues_sauvegardees(securite_isolee):
    security.RULES_PATH.write_text("{pas du json", encoding="utf-8")
    regles = security.charger_regles()
    assert regles["blocked_command_patterns"]
    copie = security.RULES_PATH.with_name("security_rules.json.corrupt")
    assert copie.read_text(encoding="utf-8") == "{pas du json"


def test_verifier_acces_mode_sans_restriction(securite_isolee, monkeypatch):
    monkeypatch.setenv("LOCALAI_UNRESTRICTED", "1")
    assert security.verifier_acces("C:\\Windows\\System32\\cmd.exe") is None


def test_regles_roundtrip(securite_isolee):
    fusion = security.sauvegarder_regles({"max_write_mb": 900})
    assert fusion["max_write_mb"] == 900
    rechargees = security.charger_regles()
    assert rechargees["max_write_mb"] == 900
    # Les defauts restent presents apres fusion.
    assert rechargees["blocked_command_patterns"]
    assert rechargees["blocked_extensions_write"]


def test_journal_append_only(securite_isolee):
    security.journaliser("evenement_test", {"x": 1})
    security.journaliser("evenement_test2")
    lignes = security.lire_journal()
    assert len(lignes) == 2
    assert lignes[0]["event"] == "evenement_test"
    assert lignes[0]["x"] == 1
