# -*- coding: utf-8 -*-
"""Outils Bureau : creation de documents (DOCX, PDF, XLSX) depuis Markdown.

Meme philosophie que les autres outils : bibliotheques legeres du venv
(python-docx, openpyxl, reportlab), aucune dependance systeme.

Markdown compris : titres #/##/###, listes - et 1., tableaux | a | b |,
**gras**, *italique*, retour a la ligne force (deux espaces ou une barre
oblique inverse en fin de ligne : le bloc d'adresse d'une lettre).
"""
import html
import re
from pathlib import Path

from backend.core.security import extension_autorisee_ecriture as _extension_bloquee
from backend.core.security import verifier_acces as _verifier_acces

_SEPARATEUR_TABLE = re.compile(r":?-{2,}:?")
_BARRE = chr(92)  # la barre oblique inverse, ecrite ainsi pour rester lisible


def _cellules(ligne: str) -> list[str]:
    return [c.strip() for c in ligne.strip().strip("|").split("|")]


def _est_ligne_table(ligne: str) -> bool:
    l = ligne.strip()
    return l.startswith("|") and l.count("|") >= 2


def _md_to_blocks(contenu: str) -> list[tuple[str, object]]:
    """Decoupe du Markdown simple en blocs (type, contenu).

    Types : h1, h2, h3, bullet, num, para (texte ; un saut de ligne y marque un
    retour force) et table (liste de lignes, chacune une liste de cellules).
    """
    blocs: list[tuple[str, object]] = []
    brutes = contenu.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(brutes):
        l = brutes[i].strip()
        if not l or re.fullmatch(r"[-*_]{3,}", l):   # ligne vide ou filet horizontal
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", l)
        if m:
            blocs.append((f"h{len(m.group(1))}", m.group(2).strip().strip("#").strip()))
            i += 1
            continue
        if _est_ligne_table(l):
            lignes = []
            while i < len(brutes) and _est_ligne_table(brutes[i]):
                cellules = _cellules(brutes[i])
                # Ni la ligne de separation (| --- |) ni une ligne entierement vide.
                if any(cellules) and not all(_SEPARATEUR_TABLE.fullmatch(c) for c in cellules):
                    lignes.append(cellules)
                i += 1
            if lignes:
                blocs.append(("table", lignes))
            continue
        if l.startswith(("- ", "* ")):
            items = []
            while i < len(brutes) and brutes[i].strip().startswith(("- ", "* ")):
                items.append(brutes[i].strip()[2:])
                i += 1
            blocs.append(("bullet", "\n".join(items)))
            continue
        if re.match(r"^\d+\.\s", l):
            items = []
            while i < len(brutes) and re.match(r"^\d+\.\s", brutes[i].strip()):
                items.append(re.sub(r"^\d+\.\s", "", brutes[i].strip()))
                i += 1
            blocs.append(("num", "\n".join(items)))
            continue
        # Paragraphe. Un modele n'ecrit pas ses phrases sur plusieurs lignes :
        # quand il va a la ligne, c'est voulu (« **Date :** … » puis
        # « **Lieu :** … », un bloc d'adresse). Le retour est donc garde, sauf
        # si la ligne suivante commence par une minuscule : la phrase continue
        # (texte colle par l'utilisateur, coupe a 80 colonnes). Avant, seuls
        # les retours « forces » du Markdown etaient gardes et l'en-tete d'un
        # compte rendu sortait en une seule ligne (essai du 11/11/2026).
        lignes_para: list[tuple[str, bool]] = []
        while i < len(brutes):
            brute = brutes[i]
            nl = brute.strip()
            if not nl or nl.startswith(("#", "- ", "* ")) or re.match(r"^\d+\.\s", nl) or _est_ligne_table(nl):
                break
            force = brute.endswith("  ") or nl.endswith(_BARRE)
            lignes_para.append((nl.rstrip(_BARRE).rstrip(), force))
            i += 1
        texte = ""
        for n, (ligne, force) in enumerate(lignes_para):
            texte += ligne
            if n + 1 < len(lignes_para):
                suite = lignes_para[n + 1][0]
                texte += " " if (not force and suite[:1].islower()) else "\n"
        blocs.append(("para", texte))
    return blocs


def _titre_propre(titre: str) -> str:
    """Titre lisible : le modele passe parfois un nom de fichier
    (« Lettre_de_Resiliation_Internet.pdf »)."""
    t = re.sub(r"\.(pdf|docx?|xlsx?|pptx?)$", "", (titre or "").strip(), flags=re.I)
    if "_" in t and " " not in t:
        t = t.replace("_", " ")
    return t.strip() or "Document"


def _sans_doublon_de_titre(titre: str, blocs: list) -> tuple[str, list]:
    """Si le contenu commence par son propre titre (# …), c'est lui le titre :
    on ne l'ecrit pas deux fois."""
    if blocs and blocs[0][0] == "h1":
        return _sans_marques(str(blocs[0][1])), blocs[1:]
    return _titre_propre(titre), blocs


_INLINE = re.compile(r"(\*\*.+?\*\*|(?<![\*\w])\*[^\*\s][^\*]*?\*(?![\*\w]))")


def _morceaux(texte: str) -> list[tuple[str, bool, bool]]:
    """(texte, gras, italique) pour **gras** et *italique*."""
    res = []
    for part in _INLINE.split(texte):
        if not part:
            continue
        if len(part) > 4 and part.startswith("**") and part.endswith("**"):
            res.append((part[2:-2], True, False))
        elif len(part) > 2 and part.startswith("*") and part.endswith("*") and not part.startswith("**"):
            res.append((part[1:-1], False, True))
        else:
            res.append((part, False, False))
    return res


def _sans_marques(texte: str) -> str:
    return "".join(t for t, _, _ in _morceaux(texte))


def _texte_riche(paragraphe, texte: str):
    """**gras**, *italique* et retours a la ligne forces, un run par morceau."""
    for n, ligne in enumerate(texte.split("\n")):
        if n:
            paragraphe.add_run().add_break()
        for part, gras, italique in _morceaux(ligne):
            run = paragraphe.add_run(part)
            run.bold = gras
            run.italic = italique


def _tableau_affiche(lignes: list[list[str]]) -> list[list[tuple[str, bool]]]:
    """Tableau pret a afficher dans un PDF ou un Word : (texte, numerique) par
    cellule. Un nombre s'aligne a droite ; dans une colonne en euros, un nombre
    nu prend sa forme francaise (« 1350 » -> « 1 350,00 € »)."""
    colonnes = max(len(l) for l in lignes)
    lignes = [l + [""] * (colonnes - len(l)) for l in lignes]
    entete = lignes[0]
    insecable = chr(160)
    resultat = [[(c, False) for c in entete]]
    for ligne in lignes[1:]:
        affichee = []
        for c, texte in enumerate(ligne):
            valeur, gras = _valeur_cellule(texte)
            if not isinstance(valeur, float):
                affichee.append((texte, False))
                continue
            if _EURO.search(texte) or _EURO.search(entete[c]):
                montant = f"{valeur:,.2f}".replace(",", insecable).replace(".", ",") + insecable + "€"
                texte = f"**{montant}**" if gras else montant
            affichee.append((texte, True))
        resultat.append(affichee)
    return resultat


def _fond_gris(cellule) -> None:
    """Fond gris clair d'une cellule d'en-tete (python-docx n'a pas d'acces direct)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    fond = OxmlElement("w:shd")
    fond.set(qn("w:val"), "clear")
    fond.set(qn("w:color"), "auto")
    fond.set(qn("w:fill"), "E8E8E8")
    cellule._tc.get_or_add_tcPr().append(fond)


def _vers_docx(titre: str, contenu: str, sortie: Path) -> str:
    from docx import Document

    titre, blocs = _sans_doublon_de_titre(titre, _md_to_blocks(contenu))
    doc = Document()
    doc.add_heading(titre, 0)
    for typ, txt in blocs:
        if typ == "h1":
            doc.add_heading(_sans_marques(txt), 1)
        elif typ == "h2":
            doc.add_heading(_sans_marques(txt), 2)
        elif typ == "h3":
            doc.add_heading(_sans_marques(txt), 3)
        elif typ in ("bullet", "num"):
            style = "List Bullet" if typ == "bullet" else "List Number"
            for item in txt.split("\n"):
                p = doc.add_paragraph(style=style)
                _texte_riche(p, item)
        elif typ == "table":
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            affiche = _tableau_affiche(txt)
            table = doc.add_table(rows=len(affiche), cols=len(affiche[0]))
            table.style = "Table Grid"
            for r, ligne in enumerate(affiche):
                for c, (valeur, numerique) in enumerate(ligne):
                    cellule = table.cell(r, c)
                    cellule.text = ""
                    _texte_riche(cellule.paragraphs[0], valeur)
                    if numerique:
                        cellule.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    if r == 0:
                        for run in cellule.paragraphs[0].runs:
                            run.bold = True
                        _fond_gris(cellule)
            doc.add_paragraph()
        else:
            p = doc.add_paragraph()
            _texte_riche(p, txt)
    doc.save(str(sortie))
    return str(sortie)


def _html_riche(texte: str) -> str:
    """Mini-HTML de reportlab : echappement, puis <b>, <i> et <br/>."""
    parties = []
    for ligne in texte.split("\n"):
        parties.append("".join(
            f"<b>{html.escape(t, quote=False)}</b>" if gras else
            f"<i>{html.escape(t, quote=False)}</i>" if italique else html.escape(t, quote=False)
            for t, gras, italique in _morceaux(ligne)))
    return "<br/>".join(parties)


def _vers_pdf(titre: str, contenu: str, sortie: Path) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (ListFlowable, ListItem, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    titre, blocs = _sans_doublon_de_titre(titre, _md_to_blocks(contenu))
    styles = getSampleStyleSheet()
    a_droite = ParagraphStyle("Nombre", parent=styles["BodyText"], alignment=TA_RIGHT)
    doc = SimpleDocTemplate(
        str(sortie), pagesize=A4, title=titre,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    # Paragraph interprete un mini-HTML : <, > et & doivent etre echappes.
    story = [Paragraph(html.escape(titre, quote=False), styles["Title"]), Spacer(1, 0.5 * cm)]
    for typ, txt in blocs:
        if typ in ("h1", "h2", "h3"):
            story.append(Paragraph(html.escape(_sans_marques(txt), quote=False), styles[f"Heading{typ[1]}"]))
        elif typ in ("bullet", "num"):
            story.append(ListFlowable(
                [ListItem(Paragraph(_html_riche(t), styles["BodyText"])) for t in txt.split("\n")],
                bulletType="bullet" if typ == "bullet" else "1",
            ))
        elif typ == "table":
            affiche = _tableau_affiche(txt)
            colonnes = len(affiche[0])
            donnees = [[Paragraph(_html_riche(c), a_droite if numerique else styles["BodyText"])
                        for c, numerique in l] for l in affiche]
            table = Table(donnees, colWidths=[(A4[0] - 4 * cm) / colonnes] * colonnes, repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(table)
        else:
            story.append(Paragraph(_html_riche(txt), styles["BodyText"]))
        story.append(Spacer(1, 0.2 * cm))
    doc.build(story)
    return str(sortie)


# Nombre ecrit a la francaise ou a l'anglaise : 1200 ; 1 200,50 ; -3.5
_ESPACES = "   "
_NOMBRE = re.compile(rf"-?(?:\d{{1,3}}(?:[{_ESPACES}]\d{{3}})+|\d+)(?:[.,]\d+)?")


def _valeur_cellule(texte: str):
    """(valeur, gras) : un nombre ecrit a la francaise devient un vrai nombre
    (« 1 200,50 € » -> 1200.5), pour que les sommes du tableur fonctionnent."""
    gras = len(texte) > 4 and texte.startswith("**") and texte.endswith("**")
    t = _sans_marques(texte).strip()
    brut = re.sub(r"\s*(€|eur|euros?)\s*$", "", t, flags=re.I).strip()
    if brut and _NOMBRE.fullmatch(brut):
        try:
            return float(re.sub(rf"[{_ESPACES}]", "", brut).replace(",", ".")), gras
        except ValueError:
            pass
    return t, gras


_TOTAL = re.compile(r"^(?:sous[-\s]?)?tota(?:l|ux)\b|^somme\b", re.I)
_ATTENTE = re.compile(r"\[[^\]]*\]")          # champ a completer : [Montant]
_ECART = re.compile(r"[ée]cart|diff[ée]rence", re.I)
_PREVU = re.compile(r"pr[ée]vu|pr[ée]vision|budget|estim", re.I)
_REEL = re.compile(r"r[ée]el|r[ée]alis|d[ée]pens[ée]|effectif", re.I)
_EURO = re.compile(r"€|\beur(?:os?)?\b", re.I)
_RESULTAT = re.compile(r"tota(?:l|ux)|montant|[ée]cart|diff[ée]rence|solde|reste|\bttc\b", re.I)
_OPERATIONS = (("{a}*{b}", lambda p, q: p * q), ("{a}+{b}", lambda p, q: p + q),
               ("{a}-{b}", lambda p, q: p - q), ("{b}-{a}", lambda p, q: q - p))


def _proche(a: float, b: float) -> bool:
    return abs(a - b) <= 0.006 + 1e-9 * max(abs(a), abs(b))


def _formules_table(entete: list[str], val: list[list], rang, lettre) -> dict[tuple[int, int], str]:
    """Formules d'un tableau : {(ligne, colonne): « =… »}.

    Un tableur dont les totaux sont des nombres figes ne sert a rien : on
    change un montant, le total reste faux. Deux familles de formules,
    retrouvees a partir des nombres ecrits par le modele et VERIFIEES sur eux :
      - colonnes calculees (Total = Quantite x Prix, Ecart = Reel - Prevu) :
        retenues seulement si une seule operation entre deux colonnes explique
        toutes les lignes chiffrees ;
      - lignes « Total » : somme des lignes au-dessus, ou total precedent +
        ces lignes (TTC = HT + TVA). Un total qui ne colle a rien reste le
        nombre ecrit par le modele.
    `val[i][c]` est la valeur de la cellule (nombre, texte, « [champ] », vide) ;
    `rang(i)` la ligne Excel, `lettre(c)` la colonne.
    """
    n, ncol = len(val), len(entete)

    def genre(i: int, c: int) -> str:
        v = val[i][c]
        if isinstance(v, float):
            return "nombre"
        if v == "":
            return "vide"
        return "attente" if _ATTENTE.fullmatch(v) else "texte"

    total = [bool(_TOTAL.match(str(val[i][0]))) for i in range(n)]
    donnees = [i for i in range(n) if not total[i]]
    formules: dict[tuple[int, int], str] = {}

    # Colonnes calculees : toujours a partir de colonnes situees a leur gauche
    # (Total apres Quantite et Prix, Ecart apres Prevu et Reel), ce qui exclut
    # toute boucle. La relation doit expliquer toutes les lignes ou les trois
    # cases sont chiffrees : au moins deux, ou une seule si l'en-tete annonce un
    # resultat (une facture a souvent une seule ligne).
    for c in range(2, ncol):
        gauche = range(1, c)
        candidats = []
        for a in gauche:
            for b in range(a + 1, c):
                pleines = [i for i in donnees if genre(i, a) == genre(i, b) == genre(i, c) == "nombre"]
                if len(pleines) < (1 if _RESULTAT.search(entete[c]) else 2):
                    continue
                candidats += [(nom, a, b) for nom, f in _OPERATIONS
                              if all(_proche(val[i][c], f(val[i][a], val[i][b])) for i in pleines)]
        trouve = candidats[0] if len(candidats) == 1 else None
        if trouve is None and _ECART.search(entete[c]):
            # Tableau a remplir (pas de nombres) ou egalite ambigue : l'en-tete
            # tranche, ecart = reel - prevu.
            prevus = [k for k in gauche if _PREVU.search(entete[k])]
            reels = [k for k in gauche if _REEL.search(entete[k]) and k not in prevus]
            if len(prevus) == 1 and len(reels) == 1:
                trouve = ("{b}-{a}", prevus[0], reels[0])
        if trouve:
            nom, a, b = trouve
            for i in donnees:
                # Seulement la ou les deux operandes existent (nombre ou case a
                # remplir) : la ligne « TVA » d'une facture garde son montant.
                if genre(i, c) in ("nombre", "attente") and {genre(i, a), genre(i, b)} <= {"nombre", "attente"}:
                    formules[(i, c)] = "=" + nom.format(a=f"{lettre(a)}{rang(i)}", b=f"{lettre(b)}{rang(i)}")

    precedent, debut = None, 0
    for i in range(n):
        if not total[i]:
            # Ligne « TVA (20 %) » sous un total : ce pourcentage du total, si
            # le montant ecrit le confirme.
            taux = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(val[i][0]))
            if taux and precedent is not None:
                p = float(taux.group(1).replace(",", "."))
                for c in range(1, ncol):
                    if (genre(i, c) == "nombre" and genre(precedent, c) == "nombre"
                            and _proche(val[i][c], val[precedent][c] * p / 100)):
                        formules[(i, c)] = f"={lettre(c)}{rang(precedent)}*{p:g}/100"
            continue
        segment = [j for j in range(debut, i) if not total[j]]
        for c in range(1, ncol):
            g = genre(i, c)
            if g in ("vide", "texte") or not segment or any(genre(j, c) == "texte" for j in segment):
                continue
            col = lettre(c)
            somme = f"SUM({col}{rang(segment[0])}:{col}{rang(segment[-1])})"
            chiffres = [val[j][c] for j in segment if genre(j, c) == "nombre"]
            if g == "attente" or (not chiffres and val[i][c] == 0):
                formules[(i, c)] = "=" + somme
            elif chiffres and _proche(val[i][c], sum(chiffres)):
                formules[(i, c)] = "=" + somme
            elif (precedent is not None and genre(precedent, c) == "nombre"
                  and _proche(val[i][c], val[precedent][c] + sum(chiffres))):
                formules[(i, c)] = f"={col}{rang(precedent)}+{somme}"
        precedent, debut = i, i + 1
    return formules


def _ecrire_table(ws, lignes: list[list[str]], depart: int, largeurs: dict[int, int]) -> int:
    """Ecrit un tableau a partir de la ligne `depart` ; retourne la ligne suivante."""
    from openpyxl.styles import Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ncol = max(len(l) for l in lignes)
    lignes = [l + [""] * (ncol - len(l)) for l in lignes]
    entete, corps = lignes[0], lignes[1:]
    cellules = [[_valeur_cellule(c) for c in l] for l in corps]        # (valeur, gras)
    val = [[v for v, _ in l] for l in cellules]
    formules = _formules_table([_sans_marques(e) for e in entete], val, lambda i: depart + 1 + i,
                               lambda c: get_column_letter(c + 1))
    trait = Side(style="thin", color="B0B0B0")
    cadre = Border(left=trait, right=trait, top=trait, bottom=trait)

    def format_colonne(c: int) -> str | None:
        nombres = [v for v in (l[c] for l in val) if isinstance(v, float)]
        if _EURO.search(entete[c]) or any(_EURO.search(l[c]) for l in corps):
            return "#,##0.00 €"
        return "#,##0.00" if any(v != int(v) for v in nombres) else None

    formats = [format_colonne(c) for c in range(ncol)]
    for c, texte in enumerate(entete):
        cellule = ws.cell(row=depart, column=c + 1, value=_sans_marques(texte))
        cellule.font = Font(bold=True)
        cellule.fill = PatternFill("solid", fgColor="E8E8E8")
        cellule.border = cadre
        largeurs[c + 1] = max(largeurs.get(c + 1, 0), len(_sans_marques(texte)))
    for i, ligne in enumerate(cellules):
        est_total = bool(_TOTAL.match(str(val[i][0])))
        for c, (valeur, gras) in enumerate(ligne):
            formule = formules.get((i, c))
            # Un champ a completer dans une colonne de chiffres : cellule vide,
            # prete a etre remplie (« [Montant] » empechait toute somme).
            attente = c > 0 and isinstance(valeur, str) and bool(_ATTENTE.fullmatch(valeur))
            contenu = formule if formule else (None if attente or valeur == "" else valeur)
            cellule = ws.cell(row=depart + 1 + i, column=c + 1, value=contenu)
            if isinstance(contenu, str) and contenu.startswith("=") and not formule:
                cellule.data_type = "s"            # texte du modele, jamais execute
            if gras or est_total:
                cellule.font = Font(bold=True)
            if formats[c] and (formule or isinstance(contenu, float)):
                cellule.number_format = formats[c]
            cellule.border = cadre
            affiche = f"{valeur:,.2f} €" if isinstance(valeur, float) else str(valeur)
            largeurs[c + 1] = max(largeurs.get(c + 1, 0), len(affiche))
    return depart + 1 + len(corps)


def _vers_xlsx(titre: str, contenu: str, sortie: Path) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    titre, blocs = _sans_doublon_de_titre(titre, _md_to_blocks(contenu))
    wb = Workbook()
    ws = wb.active
    # Excel refuse / \ : ? * [ ] dans un nom de feuille (31 car. max).
    ws.title = (re.sub(r"[/\\:?*\[\]]", "_", titre or "").strip("' ")[:31]
                or "Feuille1")
    ws["A1"] = titre
    ws["A1"].font = Font(bold=True, size=14)
    ligne = 3
    largeurs: dict[int, int] = {}

    def ecrire(col: int, texte: str, gras_force: bool = False) -> None:
        valeur, gras = _valeur_cellule(texte)
        cellule = ws.cell(row=ligne, column=col, value=valeur)
        if isinstance(valeur, str) and valeur.startswith("="):
            cellule.data_type = "s"
        if gras or gras_force:
            cellule.font = Font(bold=True)
        # Un titre ou une phrase n'elargit pas la colonne A : il deborde a droite.
        if col > 1:
            largeurs[col] = max(largeurs.get(col, 0), len(str(valeur)))

    for typ, txt in blocs:
        if typ == "table":
            ligne = _ecrire_table(ws, txt, ligne, largeurs) + 1
        elif typ in ("h1", "h2", "h3"):
            ecrire(1, str(txt), gras_force=True)
            ligne += 1
        else:
            for item in str(txt).split("\n"):
                ecrire(1, item)
                ligne += 1
    for col, n in largeurs.items():
        ws.column_dimensions[get_column_letter(col)].width = min(60, max(10, n + 2))
    wb.save(str(sortie))
    return str(sortie)


def _diapositives(titre: str, blocs: list) -> list[dict]:
    """Decoupe les blocs en diapositives : {titre, blocs, notes}.

    Une diapositive par titre (# ou ##) ; ce qui precede le premier titre
    accompagne la page de titre. Un paragraphe « Note : … » devient une note
    de l'orateur. Plus de 8 lignes : la diapositive continue sur une « (suite) ».
    """
    est_note = re.compile(r"^\**\s*notes?\s*(de l['’]orateur)?\s*\**\s*:", re.I)
    pages = [{"titre": titre, "blocs": [], "notes": []}]
    for typ, contenu in blocs:
        if typ in ("h1", "h2"):
            pages.append({"titre": _sans_marques(str(contenu)), "blocs": [], "notes": []})
        elif typ == "para" and est_note.match(str(contenu)):
            pages[-1]["notes"].append(_sans_marques(str(contenu)).split(":", 1)[1].strip())
        elif typ in ("bullet", "num"):
            # Le modele glisse parfois la note parmi les puces (« - Note : … »).
            items = str(contenu).split("\n")
            pages[-1]["notes"] += [_sans_marques(i).split(":", 1)[1].strip() for i in items if est_note.match(i)]
            reste = [i for i in items if not est_note.match(i)]
            if reste:
                pages[-1]["blocs"].append((typ, "\n".join(reste)))
        else:
            pages[-1]["blocs"].append((typ, contenu))
    decoupees = [pages[0]]
    for page in pages[1:]:
        lignes: list[tuple[str, object]] = []
        for typ, contenu in page["blocs"]:
            if typ in ("bullet", "num"):
                lignes += [(typ, item) for item in str(contenu).split("\n")]
            else:
                lignes.append((typ, contenu))
        morceaux = [lignes[i:i + 8] for i in range(0, len(lignes), 8)] or [[]]
        for n, morceau in enumerate(morceaux):
            decoupees.append({"titre": page["titre"] + (" (suite)" if n else ""), "blocs": morceau,
                              "notes": page["notes"] if n == 0 else []})
    return decoupees


def _vers_pptx(titre: str, contenu: str, sortie: Path) -> str:
    """Presentation PowerPoint 16:9 : page de titre, puis une diapositive par
    titre du Markdown (puces, paragraphes, tableaux, notes de l'orateur)."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    titre, blocs = _sans_doublon_de_titre(titre, _md_to_blocks(contenu))
    pages = _diapositives(titre, blocs)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    vide = prs.slide_layouts[6]
    encre, accent, gris = RGBColor(0x1F, 0x29, 0x37), RGBColor(0x0E, 0x7C, 0xA8), RGBColor(0x6B, 0x72, 0x80)

    def texte(diapo, x, y, w, h):
        cadre = diapo.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        cadre.word_wrap = True
        return cadre

    def ecrire(paragraphe, contenu_txt, taille, couleur=encre, gras=False):
        for n, ligne in enumerate(str(contenu_txt).split("\n")):
            if n:
                paragraphe.add_line_break()
            for part, g, i in _morceaux(ligne):
                run = paragraphe.add_run()
                run.text = part
                run.font.size, run.font.bold, run.font.italic = Pt(taille), gras or g, i
                run.font.color.rgb = couleur

    def barre(diapo, x, y, w, h):
        forme = diapo.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        forme.fill.solid()
        forme.fill.fore_color.rgb = accent
        forme.line.fill.background()

    # Page de titre : le titre, puis le premier paragraphe en sous-titre.
    premiere = prs.slides.add_slide(vide)
    barre(premiere, 0, 0, 0.35, 7.5)
    ecrire(texte(premiere, 1.0, 2.3, 11.3, 1.6).paragraphs[0], titre, 40, gras=True)
    sous_titre = next((str(c) for t, c in pages[0]["blocs"] if t == "para"), "")
    if sous_titre:
        ecrire(texte(premiere, 1.0, 4.0, 11.3, 1.5).paragraphs[0], sous_titre, 20, couleur=gris)

    for numero, page in enumerate(pages[1:], 2):
        diapo = prs.slides.add_slide(vide)
        ecrire(texte(diapo, 0.7, 0.45, 11.9, 1.0).paragraphs[0], page["titre"], 30, gras=True)
        barre(diapo, 0.75, 1.42, 1.6, 0.06)
        lignes = [(t, c) for t, c in page["blocs"] if t != "table"]
        tableaux = [c for t, c in page["blocs"] if t == "table"]
        taille = 22 if len(lignes) <= 5 else 18
        if lignes:
            cadre = texte(diapo, 0.8, 1.75, 11.7, 2.4 if tableaux else 5.0)
            for n, (typ, contenu_bloc) in enumerate(lignes):
                p = cadre.paragraphs[0] if n == 0 else cadre.add_paragraph()
                p.space_after = Pt(10)
                if typ in ("bullet", "num"):
                    contenu_bloc = "•  " + str(contenu_bloc)
                ecrire(p, contenu_bloc, taille, gras=(typ == "h3"))
        for lignes_table in tableaux[:1]:
            affiche = _tableau_affiche(lignes_table)
            haut = 4.3 if lignes else 1.8
            forme = diapo.shapes.add_table(len(affiche), len(affiche[0]), Inches(0.8), Inches(haut),
                                           Inches(11.7), Inches(0.4 * len(affiche)))
            for r, ligne in enumerate(affiche):
                for c, (valeur, _) in enumerate(ligne):
                    cellule = forme.table.cell(r, c)
                    cellule.text = ""
                    ecrire(cellule.text_frame.paragraphs[0], valeur, 14,
                           couleur=RGBColor(0xFF, 0xFF, 0xFF) if r == 0 else encre, gras=(r == 0))
        if page["notes"]:
            diapo.notes_slide.notes_text_frame.text = "\n".join(page["notes"])
        ecrire(texte(diapo, 12.2, 6.9, 0.9, 0.4).paragraphs[0], str(numero), 12, couleur=gris)
    prs.save(str(sortie))
    return str(sortie)


def _dossier_documents() -> Path:
    """Dossier des documents produits par Neo : <racine>/workspace/documents."""
    from backend.tools.common import _default_root

    return Path(_default_root()) / "workspace" / "documents"


def _chemin_document(save_path: str, titre: str, type_doc: str) -> str:
    """Chemin d'ecriture d'un document.

    Le modele invente volontiers un chemin (« /home/user/lettre.pdf », un
    chemin relatif, le « Documents » du compte systeme) : l'outil le refusait,
    le modele recommencait six fois puis annoncait un fichier qui n'existait pas
    (essai du 11/11/2026). Un chemin absent ou inutilisable ne bloque plus : le
    document va dans workspace\\documents, sous le nom propose ou tire du titre.
    Seul un chemin absolu que le bac a sable accepte est respecte."""
    import time as _t

    ext = "." + type_doc
    propose = (save_path or "").strip().strip('"')
    if propose and not propose.lower().endswith(ext):
        propose += ext
    utilisable = bool(
        propose
        and re.match(r"^[A-Za-z]:[\\/]", propose)              # absolu, avec lecteur
        and "systemprofile" not in propose.lower()
        and not _verifier_acces(propose, ecriture=True)
    )
    if utilisable:
        return propose
    nom = Path(propose.replace("\\", "/")).name if propose else ""
    # Nom tire du titre : sans l'extension que le modele y glisse parfois
    # (« Compte_Rendu.pdf » donnait « compte_rendu_pdf.pdf ») ni les champs a
    # completer (« CV_[TonNom] »).
    du_titre = re.sub(r"\[[^\]]*\]", " ", _titre_propre(titre)).lower()
    base = re.sub(r"_+", "_", re.sub(r"[^\w-]+", "_", Path(nom).stem if nom else du_titre)).strip("_")[:60]
    dossier = _dossier_documents()
    try:
        dossier.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    chemin = dossier / f"{base or 'document'}{ext}"
    if chemin.exists():
        chemin = dossier / f"{base or 'document'}_{int(_t.time())}{ext}"
    return str(chemin)


def tool_document_create(titre: str, contenu_md: str, type_doc: str = "docx",
                         save_path: str = "") -> str:
    """Cree un document professionnel (docx/pdf/xlsx) a partir de Markdown.

    type_doc : "docx" (Word), "pdf" ou "xlsx" (tableur ; le Markdown table
    | a | b | devient un vrai tableau).
    """
    type_doc = (type_doc or "docx").lower().lstrip(".")
    type_doc = {"powerpoint": "pptx", "ppt": "pptx", "word": "docx", "excel": "xlsx"}.get(type_doc, type_doc)
    if type_doc not in ("docx", "pdf", "xlsx", "pptx"):
        return "Erreur: type_doc doit etre docx, pdf, xlsx ou pptx"
    save_path = _chemin_document(save_path, titre, type_doc)
    refus_ext = _extension_bloquee(save_path)
    if refus_ext:
        return refus_ext
    refus = _verifier_acces(save_path, ecriture=True)
    if refus:
        return refus
    sortie = Path(save_path).expanduser().resolve()
    sortie.parent.mkdir(parents=True, exist_ok=True)
    try:
        if type_doc == "docx":
            _vers_docx(titre, contenu_md, sortie)
        elif type_doc == "pdf":
            _vers_pdf(titre, contenu_md, sortie)
        elif type_doc == "pptx":
            _vers_pptx(titre, contenu_md, sortie)
        else:
            _vers_xlsx(titre, contenu_md, sortie)
    except Exception as exc:
        return f"Erreur creation document {type_doc}: {exc}"
    ko = sortie.stat().st_size // 1024
    return f"Document cree: {sortie} ({ko} Ko, format {type_doc.upper()})"
