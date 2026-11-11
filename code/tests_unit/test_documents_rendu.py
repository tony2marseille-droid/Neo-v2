"""Rendu des documents de l'agent bureau : Markdown -> PDF, Word, Excel."""
import pytest

from backend.tools import t_docs

LETTRE = (
    "# Lettre de résiliation d'abonnement internet\n\n"
    "**[Prénom] [Nom]**  \n[Adresse]  \n[Code postal] [Ville]\n\n"
    "**Objet : Résiliation du contrat n° [Numéro]**\n\n"
    "Madame, Monsieur,\n\nJe résilie mon abonnement,\nsouscrit en *janvier*.\n"
)
BUDGET = (
    "# Suivi de budget\n\n## Revenus\n\n"
    "| Source | Prévu (€) | Réel (€) |\n| --- | --- | --- |\n"
    "| Salaire | 2 400,50 € | 2400.5 |\n| **TOTAL** | **2 400,50 €** | [Montant] |\n"
)


def test_blocs_retours_forces_et_table():
    blocs = t_docs._md_to_blocks(LETTRE)
    assert blocs[0] == ("h1", "Lettre de résiliation d'abonnement internet")
    # Bloc d'adresse : trois lignes gardees ; lignes simples : un seul paragraphe.
    assert blocs[1] == ("para", "**[Prénom] [Nom]**\n[Adresse]\n[Code postal] [Ville]")
    assert blocs[-1] == ("para", "Je résilie mon abonnement, souscrit en *janvier*.")
    table = next(c for t, c in t_docs._md_to_blocks(BUDGET) if t == "table")
    assert table[0] == ["Source", "Prévu (€)", "Réel (€)"] and len(table) == 3   # separateur retire


def test_gras_et_italique():
    assert t_docs._morceaux("un **mot** et *un autre*") == [
        ("un ", False, False), ("mot", True, False), (" et ", False, False), ("un autre", False, True)]
    # Une asterisque isolee (multiplication, note) n'est pas de l'italique.
    assert t_docs._morceaux("3 * 4 = 12") == [("3 * 4 = 12", False, False)]
    assert t_docs._sans_marques("**TOTAL** : *net*") == "TOTAL : net"
    assert t_docs._html_riche("**A & B**\nsuite <ici>") == "<b>A &amp; B</b><br/>suite &lt;ici&gt;"


@pytest.mark.parametrize("titre,attendu", [
    ("Lettre_de_Resiliation_Internet.pdf", "Lettre de Resiliation Internet"),
    ("Suivi de budget", "Suivi de budget"), ("rapport.docx", "rapport"), ("", "Document"),
])
def test_titre_lisible(titre, attendu):
    assert t_docs._titre_propre(titre) == attendu


def test_titre_du_contenu_pas_ecrit_deux_fois():
    titre, blocs = t_docs._sans_doublon_de_titre("Lettre_x.pdf", t_docs._md_to_blocks(LETTRE))
    assert titre == "Lettre de résiliation d'abonnement internet" and blocs[0][0] == "para"
    titre, blocs = t_docs._sans_doublon_de_titre("Note_interne.pdf", [("para", "texte")])
    assert titre == "Note interne" and len(blocs) == 1


@pytest.mark.parametrize("texte,valeur,gras", [
    ("2 400,50 €", 2400.5, False), ("**2 400,50 €**", 2400.5, True), ("2400.5", 2400.5, False),
    ("-12", -12.0, False), ("1 200", 1200.0, False), ("[Montant]", "[Montant]", False),
    ("**TOTAL**", "TOTAL", True), ("13 rue de la Paix", "13 rue de la Paix", False),
    ("06 12 34 56 78", "06 12 34 56 78", False), ("2026", 2026.0, False),
])
def test_cellule_de_tableur(texte, valeur, gras):
    assert t_docs._valeur_cellule(texte) == (valeur, gras)


def test_pdf_rendu(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    sortie = tmp_path / "lettre.pdf"
    t_docs._vers_pdf("Lettre_x.pdf", LETTRE + "\n" + BUDGET.split("\n\n", 1)[1], sortie)
    texte = "\n".join(p.extract_text() for p in pypdf.PdfReader(str(sortie)).pages)
    assert "**" not in texte and "Lettre_x" not in texte and "| ---" not in texte
    assert texte.count("Lettre de résiliation d'abonnement internet") == 1
    lignes = [l.strip() for l in texte.splitlines()]
    assert "[Adresse]" in lignes and "[Code postal] [Ville]" in lignes     # une ligne chacune
    assert "Salaire" in texte and "TOTAL" in texte


def test_docx_rendu(tmp_path):
    docx = pytest.importorskip("docx")
    sortie = tmp_path / "budget.docx"
    t_docs._vers_docx("x", BUDGET + "\n**Note** : *à compléter*.\n", sortie)
    doc = docx.Document(str(sortie))
    assert doc.tables and doc.tables[0].cell(0, 0).text == "Source" and doc.tables[0].cell(2, 0).text == "TOTAL"
    textes = [p.text for p in doc.paragraphs]
    assert textes[0] == "Suivi de budget" and textes.count("Suivi de budget") == 1
    note = next(p for p in doc.paragraphs if p.text.startswith("Note"))
    assert note.runs[0].bold and any(r.italic for r in note.runs) and "*" not in note.text


def test_xlsx_rendu(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    sortie = tmp_path / "budget.xlsx"
    t_docs._vers_xlsx("Suivi_Budget.xlsx", BUDGET, sortie)
    ws = openpyxl.load_workbook(str(sortie)).active
    assert ws["A1"].value == "Suivi de budget"
    valeurs = [[c.value for c in r] for r in ws.iter_rows(min_row=3)]
    assert ["Source", "Prévu (€)", "Réel (€)"] in valeurs
    assert ["Salaire", 2400.5, 2400.5] in valeurs                 # de vrais nombres
    assert ["TOTAL", "=SUM(B5:B5)", "=SUM(C5:C5)"] in valeurs     # de vraies sommes
    entete = next(r for r in ws.iter_rows(min_row=3) if r[0].value == "Source")
    assert entete[0].font.bold and ws.column_dimensions["A"].width >= 10
