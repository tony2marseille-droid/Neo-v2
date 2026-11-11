"""Tableur de l'agent bureau : formules retrouvees, cellules a remplir, mise en page du texte."""
import pytest

from backend.tools import t_docs

FACTURE = (
    "# Facture\n\n"
    "| Description | Quantité | Prix unitaire HT | Total HT |\n| :--- | ---: | ---: | ---: |\n"
    "| Conseil | 3 | 450,00 € | 1 350,00 € |\n"
    "| Déplacement | 2 | 40,00 € | 80,00 € |\n"
    "| **Total HT** | | | **1 430,00 €** |\n"
    "| TVA (20 %) | | | 286,00 € |\n"
    "| **TOTAL TTC** | | | **1 716,00 €** |\n"
)
BUDGET_VIDE = (
    "# Budget\n\n"
    "| Poste | Prévu (€) | Réel (€) | Écart |\n| --- | --- | --- | --- |\n"
    "| Loyer | [Montant] | [Montant] | [Écart] |\n"
    "| Courses | [Montant] | [Montant] | [Écart] |\n"
    "| **TOTAL** | **0,00 €** | **0,00 €** | **0,00 €** |\n"
)


def _feuille(tmp_path, contenu):
    openpyxl = pytest.importorskip("openpyxl")
    sortie = tmp_path / "t.xlsx"
    t_docs._vers_xlsx("x", contenu, sortie)
    return openpyxl.load_workbook(str(sortie)).active


def test_facture_formules_verifiees_sur_les_nombres(tmp_path):
    ws = _feuille(tmp_path, FACTURE)
    # En-tete ligne 3, donnees lignes 4 et 5, Total HT 6, TVA 7, TTC 8.
    assert ws["D4"].value == "=B4*C4" and ws["D5"].value == "=B5*C5"
    assert ws["D6"].value == "=SUM(D4:D5)"
    assert ws["D7"].value == "=D6*20/100"                # TVA : 20 % du total HT (286 verifie)
    assert ws["D8"].value == "=D6+SUM(D7:D7)"            # TTC = HT + TVA
    assert ws["B6"].value is None                        # case vide du modele : laissee vide
    assert ws["D4"].number_format == "#,##0.00 €" and ws["B4"].number_format == "General"
    assert ws["A6"].font.bold and ws["A3"].fill.fgColor.rgb.endswith("E8E8E8")


def test_tva_calculee_et_ligne_vide_ignoree(tmp_path):
    devis = ("| Désignation | Quantité | Prix HT | Total HT |\n| --- | --- | --- | --- |\n"
             "| Peinture | 30 | 15 | 450 |\n| Plafond | 20 | 12 | 240 |\n| | | | |\n"
             "| TOTAL HT | | | 690 |\n| TVA (20%) | | | 138 |\n| TOTAL TTC | | | 828 |\n")
    ws = _feuille(tmp_path, devis)
    assert ws["D4"].value == "=B4*C4" and ws["D6"].value == "=SUM(D4:D5)"      # ligne vide retiree
    assert ws["D7"].value == "=D6*20/100" and ws["D8"].value == "=D6+SUM(D7:D7)"


def test_montants_presentes_a_la_francaise_dans_un_pdf_ou_un_word():
    affiche = t_docs._tableau_affiche([["Description", "Quantité (jours)", "Total HT (€)", "Année"],
                                       ["Conseil", "3", "1350", "2026"], ["**TOTAL**", "", "**1350**", ""]])
    espace = chr(160)
    assert affiche[1] == [("Conseil", False), ("3", True), (f"1{espace}350,00{espace}€", True), ("2026", True)]
    assert affiche[2][2] == (f"**1{espace}350,00{espace}€**", True) and affiche[2][1] == ("", False)


def test_tableau_a_remplir_cases_vides_et_formules(tmp_path):
    ws = _feuille(tmp_path, BUDGET_VIDE)
    assert ws["B4"].value is None and ws["C5"].value is None       # plus de « [Montant] »
    assert ws["A4"].value == "Loyer"
    assert ws["D4"].value == "=C4-B4" and ws["D5"].value == "=C5-B5"   # ecart = reel - prevu
    assert [ws[f"{c}6"].value for c in "BCD"] == ["=SUM(B4:B5)", "=SUM(C4:C5)", "=SUM(D4:D5)"]


def test_total_qui_ne_colle_pas_reste_un_nombre(tmp_path):
    ws = _feuille(tmp_path, "| Poste | Montant |\n| --- | --- |\n| A | 10 |\n| B | 20 |\n| Total | 99 |\n")
    assert ws["B6"].value == 99.0


def test_pas_de_formule_devinee_si_ambigu():
    def formules(entete, lignes):
        return t_docs._formules_table(entete, lignes, lambda i: i + 2, lambda c: "ABCD"[c])

    # 2 + 2 = 2 x 2 : deux explications, aucune formule.
    assert formules(["x", "a", "b", "Total"], [["l", 2.0, 2.0, 4.0]]) == {}
    assert formules(["x", "a", "b", "c"], [["l", 2.0, 2.0, 4.0], ["m", 3.0, 5.0, 15.0]]) == {
        (0, 3): "=B2*C2", (1, 3): "=B3*C3"}
    # Une seule ligne et un en-tete qui n'annonce pas un resultat : coincidence possible.
    assert formules(["Poste", "2025", "2026", "2027"], [["Ventes", 10.0, 20.0, 30.0]]) == {}
    assert formules(["Article", "Qté", "Prix", "Total HT"], [["Stylo", 3.0, 2.0, 6.0]]) == {(0, 3): "=B2*C2"}


def test_formule_ecrite_par_le_modele_reste_du_texte(tmp_path):
    ws = _feuille(tmp_path, "| A | B |\n| --- | --- |\n| x | =HYPERLINK(\"http://x\") |\n")
    assert ws["B4"].data_type == "s"


def test_retours_a_la_ligne_gardes():
    blocs = t_docs._md_to_blocks("**Date :** [Date]\n**Lieu :** [Salle]\n**Secrétaire :** [Nom]\n")
    assert blocs == [("para", "**Date :** [Date]\n**Lieu :** [Salle]\n**Secrétaire :** [Nom]")]
    # Une phrase coupee par un retour (la suite commence par une minuscule) reste une phrase.
    assert t_docs._md_to_blocks("Je résilie mon abonnement\nsouscrit en janvier.\nCordialement,\nJean")[0][1] == (
        "Je résilie mon abonnement souscrit en janvier.\nCordialement,\nJean")


@pytest.mark.parametrize("titre,nom", [
    ("Compte_Rendu_Lancement_Site_Web.pdf", "compte_rendu_lancement_site_web.pdf"),
    ("CV_Developpeur_Web_[TonNom]", "cv_developpeur_web.pdf"),
    ("Suivi de budget : mars 2027", "suivi_de_budget_mars_2027.pdf"),
])
def test_nom_de_fichier_tire_du_titre(tmp_path, monkeypatch, titre, nom):
    monkeypatch.setattr(t_docs, "_dossier_documents", lambda: tmp_path)
    assert t_docs._chemin_document("", titre, "pdf") == str(tmp_path / nom)
