# -*- coding: utf-8 -*-
"""Gabarits parametriques d'objets courants a imprimer (OpenSCAD).

Pourquoi : laisses libres, les modeles locaux n'ecrivent pas un objet
fonctionnel correct du premier coup. Essai du 11/11/2026 sur « support de
telephone incline a 60 degres » : une plaque entaillee (modele de code), une
erreur de syntaxe (Gemma), un bloc tourne autour du mauvais axe (Qwen) — et le
controle visuel donnait 10/10 a la plaque. En revanche ils savent tres bien
RECONNAITRE un objet et relever ses cotes. Ici la geometrie est ecrite une fois
pour toutes, juste par construction ; le modele ne choisit que le gabarit et
ses cotes. Une demande qui ne correspond a aucun gabarit garde le chemin libre
(script ecrit par le modele, boucle de correction).

Chaque gabarit : description (pour le choix), parametres {nom: (defaut, mini,
maxi, sens)}, et corps OpenSCAD qui utilise ces noms comme variables. Piece
posee sur Z=0, en millimetres.
"""
from __future__ import annotations

import json
import re

GABARITS: dict[str, dict] = {
    "support_telephone": {
        "description": "support de bureau incline pour telephone ou tablette (dock, chevalet)",
        "parametres": {
            "largeur": (80, 40, 300, "largeur de l'appareil pose dessus"),
            "angle": (60, 30, 85, "inclinaison du dossier par rapport a la table, en degres"),
            "epaisseur_appareil": (12, 6, 40, "epaisseur de l'appareil avec sa coque"),
            "hauteur_dossier": (110, 50, 250, "longueur du dossier qui soutient l'appareil"),
            "epaisseur": (4, 2, 10, "epaisseur des parois"),
        },
        "scad": """
L = largeur + 10;                                   // largeur totale
y0 = epaisseur + epaisseur_appareil;                // pied du dossier
prof = y0 + hauteur_dossier * cos(angle) + 10;      // profondeur de la base
r = hauteur_dossier * 0.7;                          // longueur des renforts
translate([-L / 2, -prof / 2, 0]) union() {
    cube([L, prof, epaisseur]);                     // base
    cube([L, epaisseur, epaisseur + 12]);           // butee avant : retient l'appareil
    translate([0, y0, epaisseur]) rotate([angle - 90, 0, 0])
        cube([L, epaisseur, hauteur_dossier]);      // dossier incline
    for (x = [0, L - epaisseur])                    // renforts lateraux sous le dossier
        translate([x, 0, 0]) rotate([90, 0, 90]) linear_extrude(epaisseur)
            polygon([[y0 + epaisseur, epaisseur], [y0 + r * cos(angle), epaisseur],
                     [y0 + r * cos(angle), r * sin(angle)]]);
}
""",
    },
    "boite": {
        "description": "boite ou bac de rangement rectangulaire, ouvert sur le dessus",
        "parametres": {
            "longueur": (100, 15, 300, "longueur exterieure"),
            "largeur": (60, 15, 300, "largeur exterieure"),
            "hauteur": (40, 8, 250, "hauteur exterieure"),
            "paroi": (2, 1.2, 8, "epaisseur des parois et du fond"),
        },
        "scad": """
translate([-longueur / 2, -largeur / 2, 0]) difference() {
    cube([longueur, largeur, hauteur]);
    translate([paroi, paroi, paroi]) cube([longueur - 2 * paroi, largeur - 2 * paroi, hauteur]);
}
""",
    },
    "pot": {
        "description": "pot cylindrique : pot a crayons, gobelet, cache-pot, vase droit ou evase",
        "parametres": {
            "diametre": (80, 20, 250, "diametre exterieur en haut"),
            "hauteur": (100, 15, 250, "hauteur"),
            "paroi": (2.4, 1.2, 8, "epaisseur de la paroi"),
            "fond": (3, 1.2, 10, "epaisseur du fond"),
            "conicite": (1, 0.5, 1, "diametre du bas / diametre du haut (1 = droit, 0.7 = evase)"),
        },
        "scad": """
difference() {
    cylinder(d1 = diametre * conicite, d2 = diametre, h = hauteur);
    translate([0, 0, fond])
        cylinder(d1 = diametre * conicite - 2 * paroi, d2 = diametre - 2 * paroi, h = hauteur);
}
""",
    },
    "crochet_mural": {
        "description": "crochet mural a visser (patere, porte-manteau, accroche)",
        "parametres": {
            "largeur": (20, 10, 80, "largeur du crochet"),
            "hauteur": (60, 30, 200, "hauteur de la platine fixee au mur"),
            "portee": (30, 10, 100, "avancee du crochet depuis le mur"),
            "epaisseur": (5, 3, 12, "epaisseur de matiere"),
            "trou_vis": (4, 2, 8, "diametre des deux trous de vis"),
        },
        "scad": """
translate([-largeur / 2, 0, 0]) difference() {
    union() {
        cube([largeur, epaisseur, hauteur]);                               // platine murale
        cube([largeur, portee + epaisseur, epaisseur]);                    // bras
        translate([0, portee, 0]) cube([largeur, epaisseur, hauteur * 0.3]);   // retour
    }
    for (z = [hauteur * 0.55, hauteur * 0.85])                             // trous de vis
        translate([largeur / 2, -1, z]) rotate([-90, 0, 0]) cylinder(d = trou_vis, h = epaisseur + 2);
}
""",
    },
    "equerre": {
        "description": "equerre de fixation en L avec un trou de vis par aile",
        "parametres": {
            "cote": (40, 15, 200, "longueur de chaque aile"),
            "largeur": (20, 8, 100, "largeur de l'equerre"),
            "epaisseur": (4, 2, 12, "epaisseur de matiere"),
            "trou": (4, 2, 10, "diametre des trous de vis"),
        },
        "scad": """
translate([-largeur / 2, 0, 0]) difference() {
    union() {
        cube([largeur, cote, epaisseur]);
        cube([largeur, epaisseur, cote]);
    }
    translate([largeur / 2, cote * 0.65, -1]) cylinder(d = trou, h = epaisseur + 2);
    translate([largeur / 2, -1, cote * 0.65]) rotate([-90, 0, 0]) cylinder(d = trou, h = epaisseur + 2);
}
""",
    },
    "entretoise": {
        "description": "entretoise, bague, rondelle ou tube : cylindre perce au centre",
        "parametres": {
            "diametre_exterieur": (20, 4, 250, "diametre exterieur"),
            "diametre_interieur": (8, 1, 240, "diametre du trou central"),
            "hauteur": (10, 0.6, 250, "hauteur (epaisseur pour une rondelle)"),
        },
        "scad": """
difference() {
    cylinder(d = diametre_exterieur, h = hauteur);
    translate([0, 0, -1]) cylinder(d = min(diametre_interieur, diametre_exterieur - 2), h = hauteur + 2);
}
""",
    },
    "plaque": {
        "description": "plaque rectangulaire a coins arrondis, avec ou sans trous aux quatre coins",
        "parametres": {
            "longueur": (100, 10, 300, "longueur"),
            "largeur": (60, 10, 300, "largeur"),
            "epaisseur": (3, 0.8, 30, "epaisseur"),
            "trou": (0, 0, 12, "diametre des trous de coin (0 = sans trou)"),
        },
        "scad": """
rayon = min(3, longueur / 4, largeur / 4);
marge = max(6, trou * 1.5);
difference() {
    hull() for (x = [-1, 1], y = [-1, 1])
        translate([x * (longueur / 2 - rayon), y * (largeur / 2 - rayon), 0]) cylinder(r = rayon, h = epaisseur);
    if (trou > 0) for (x = [-1, 1], y = [-1, 1])
        translate([x * (longueur / 2 - marge), y * (largeur / 2 - marge), -1]) cylinder(d = trou, h = epaisseur + 2);
}
""",
    },
    "dessous_de_verre": {
        "description": "dessous de verre, sous-plat, coupelle ou petit plateau : disque ou polygone, avec ou sans rebord",
        "parametres": {
            "largeur": (90, 30, 250, "diametre, ou largeur entre deux cotes opposes"),
            "epaisseur": (4, 1.2, 20, "epaisseur du fond"),
            "rebord": (2, 0, 30, "hauteur du rebord au-dessus du fond (0 = sans rebord)"),
            "cotes": (0, 0, 12, "nombre de cotes : 0 = rond, 6 = hexagone, 4 = carre"),
        },
        "scad": """
n = round(cotes) < 3 ? 96 : round(cotes);
k = round(cotes) < 3 ? 1 : cos(180 / n);            // largeur mesuree entre deux cotes opposes
difference() {
    cylinder(r = largeur / 2 / k, h = epaisseur + rebord, $fn = n);
    if (rebord > 0) translate([0, 0, epaisseur])
        cylinder(r = (largeur / 2 - 2) / k, h = rebord + 1, $fn = n);
}
""",
    },
    "cale": {
        "description": "cale en biseau : cale-porte, cale de niveau",
        "parametres": {
            "longueur": (120, 20, 300, "longueur de la pente"),
            "largeur": (40, 10, 150, "largeur"),
            "hauteur": (30, 3, 150, "hauteur du cote le plus epais"),
        },
        "scad": """
translate([-largeur / 2, -longueur / 2, 0]) rotate([90, 0, 90]) linear_extrude(largeur)
    polygon([[0, 0], [longueur, 0], [0, hauteur]]);
""",
    },
    "porte_cles": {
        "description": "porte-cles ou etiquette avec un texte en relief et un trou d'anneau",
        "parametres": {
            "hauteur": (20, 12, 60, "hauteur de la plaquette"),
            "epaisseur": (3, 1.6, 8, "epaisseur de la plaquette"),
        },
        "texte": "NEO",
        "scad": """
taille = hauteur * 0.5;
long = max(40, len(texte) * taille * 0.8 + hauteur + 8);
translate([-long / 2, -hauteur / 2, 0]) union() {
    difference() {
        hull() {
            translate([hauteur / 2, hauteur / 2, 0]) cylinder(d = hauteur, h = epaisseur);
            translate([long - hauteur / 2, hauteur / 2, 0]) cylinder(d = hauteur, h = epaisseur);
        }
        translate([hauteur / 2, hauteur / 2, -1]) cylinder(d = 5, h = epaisseur + 2);
    }
    translate([hauteur + 2, hauteur / 2, epaisseur]) linear_extrude(1.2)
        text(texte, size = taille, valign = "center");
}
""",
    },
}


def script(nom: str, parametres: dict | None = None, texte: str = "") -> tuple[str, dict]:
    """(script OpenSCAD complet, valeurs retenues) pour un gabarit.

    Les valeurs sont bornees : une cote aberrante (0, negative, 3 metres) ne
    donne pas une piece impossible. Un parametre inconnu ou illisible est ignore.
    """
    g = GABARITS[nom]
    valeurs: dict = {}
    for cle, (defaut, mini, maxi, _) in g["parametres"].items():
        try:
            v = float((parametres or {}).get(cle, defaut))
        except (TypeError, ValueError):
            v = float(defaut)
        valeurs[cle] = round(min(max(v, mini), maxi), 2)
    lignes = [f"// Gabarit Neo : {nom} ({g['description']})", "$fn = 64;"]
    lignes += [f"{cle} = {v:g};" for cle, v in valeurs.items()]
    if "texte" in g:
        # Texte grave : ni guillemet ni barre oblique inverse dans la chaine OpenSCAD.
        propre = re.sub(r'[^\w \-!?.+&\']', "", str(texte or g["texte"]))[:24].strip() or g["texte"]
        valeurs["texte"] = propre
        lignes.append(f'texte = "{propre}";')
    return "\n".join(lignes) + "\n" + g["scad"].strip() + "\n", valeurs


def consigne_de_choix(demande: str) -> str:
    """Question posee au modele : quel gabarit, quelles cotes ?"""
    catalogue = []
    for nom, g in GABARITS.items():
        params = ", ".join(f"{c} (defaut {d:g} : {sens})" for c, (d, _, _, sens) in g["parametres"].items())
        if "texte" in g:
            params += ", texte (le texte a graver)"
        catalogue.append(f"- {nom} : {g['description']}. Parametres : {params}")
    return (
        "Tu prepares une piece a imprimer en 3D. Voici des gabarits prets a l'emploi :\n"
        + "\n".join(catalogue)
        + "\n\nDEMANDE : " + demande + "\n\n"
        "Si la demande est EXACTEMENT un de ces objets, reponds en JSON : "
        '{"gabarit": "<nom>", "parametres": {"<parametre>": <nombre>, ...}}. '
        "Ne mets dans parametres que les valeurs DONNEES dans la demande (en mm ; les angles en degres ; "
        "convertis les cm). Les autres gardent leur valeur par defaut.\n"
        "Si la demande est un autre objet, une variante que le gabarit ne sait pas faire (couvercle, "
        "compartiments, forme speciale, plusieurs pieces) ou une forme organique, reponds : "
        '{"gabarit": "aucun"}.\n'
        "Reponds UNIQUEMENT le JSON."
    )


def choisir(demande: str, demander) -> dict | None:
    """Gabarit qui correspond a la demande, ou None.

    `demander(consigne) -> texte` interroge le modele. Retour :
    {"nom", "parametres" (valeurs retenues), "scad"}.
    """
    try:
        brut = demander(consigne_de_choix(demande)) or ""
        m = re.search(r"\{.*\}", brut, re.S)
        choix = json.loads(m.group(0)) if m else {}
    except Exception:
        return None
    nom = str(choix.get("gabarit") or "").strip().lower()
    if nom not in GABARITS:
        return None
    parametres = choix.get("parametres") if isinstance(choix.get("parametres"), dict) else {}
    code, valeurs = script(nom, parametres, str(parametres.get("texte", "")))
    return {"nom": nom, "parametres": valeurs, "scad": code}
