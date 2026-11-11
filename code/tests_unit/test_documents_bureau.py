"""Agent bureau : un chemin invente par le modele ne bloque plus la creation du document."""
import pytest

from backend.tools import t_docs


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(t_docs, "_dossier_documents", lambda: tmp_path / "documents")
    # Bac a sable simule : seul E:\autorise est accepte.
    monkeypatch.setattr(t_docs, "_verifier_acces",
                        lambda chemin, ecriture=False: None if str(chemin).lower().startswith(r"e:\autorise") else "ACCES REFUSE")
    return tmp_path / "documents"


@pytest.mark.parametrize("propose", [
    "", "/home/user/lettre_resiliation_internet.pdf", "/home/sandbox/lettre.pdf", "lettre.pdf",
    "data/lettre.pdf", r"C:\Windows\System32\config\systemprofile\Documents\lettre.pdf",
    r"D:\ailleurs\lettre.pdf",
])
def test_chemin_invente_ou_refuse_va_dans_les_documents_de_neo(dossier, propose):
    chemin = t_docs._chemin_document(propose, "Lettre de résiliation", "pdf")
    assert chemin.startswith(str(dossier)) and chemin.endswith(".pdf")
    assert dossier.is_dir()


def test_nom_propose_garde_sinon_titre(dossier):
    assert t_docs._chemin_document("/home/user/lettre_resiliation_internet.pdf", "x", "pdf") == str(
        dossier / "lettre_resiliation_internet.pdf")
    assert t_docs._chemin_document("", "Suivi de budget : mars 2027", "xlsx") == str(
        dossier / "suivi_de_budget_mars_2027.xlsx")


def test_chemin_absolu_accepte_est_respecte(dossier):
    assert t_docs._chemin_document(r"E:\autorise\cv", "CV", "docx") == r"E:\autorise\cv.docx"


def test_deux_documents_du_meme_nom_ne_s_ecrasent_pas(dossier):
    premier = t_docs._chemin_document("", "Devis", "pdf")
    dossier.mkdir(parents=True, exist_ok=True)
    open(premier, "wb").write(b"pdf")
    second = t_docs._chemin_document("", "Devis", "pdf")
    assert second != premier and second.endswith(".pdf")
