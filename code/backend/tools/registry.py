# -*- coding: utf-8 -*-
ALLOWED_LEVELS = ["SAFE", "USER", "POWER", "ADMIN"]
_LEVEL_ORDER = {name: i for i, name in enumerate(ALLOWED_LEVELS)}
MAX_TOOL_ROUNDS = 14

import logging
import os
import subprocess
import sys

from backend.tools.common import (_default_root, _resolve_sandbox, _sandbox_cfg,
    chemin_lecture)
from backend.tools.t_donnees import tool_adresse, tool_taux_change
from backend.tools.t_util import tool_calculator, tool_get_datetime
from backend.tools.t_files import (tool_check_code, tool_list_dir,
    tool_read_file, tool_run_command, tool_write_file)
from backend.tools.t_code import (tool_code_edit, tool_code_review, tool_git_status)
from backend.tools.t_web import (tool_deep_research, tool_web_browse, tool_web_fetch,
    tool_web_search)
from backend.tools.t_video import (tool_video_storyboard, tool_video_clip,
    tool_video_render)
from backend.tools.t_tts import tool_tts_generer
from backend.tools.t_translate import tool_traduire
from backend.tools.t_illustrate import tool_illustrer
from backend.tools.t_image import (tool_analyze_image, tool_edit_image,
    tool_fetch_image, tool_find_reference_image, tool_generate_image,
    tool_image_search)
from backend.tools.t_cad import (tool_render_preview, tool_render_scad,
    tool_slice_model, tool_validate_design)
from backend.tools.t_mesh import (tool_fuse_meshes,
    tool_generate_mesh_from_image, tool_mesh_boolean, tool_mesh_info,
    tool_repair_mesh, tool_render_stl_views, tool_validate_mesh)
from backend.tools.t_blender import tool_blender_render, tool_blender_run
from backend.tools.t_3d_v2 import (
    tool_check_printability, tool_export_3mf, tool_measure_model,
    tool_slicer_open, tool_slicers_list, tool_unit_convert, tool_units_scale,
)
from backend.tools.t_3d_v2 import TOOL_SPECS_3D_V2
from backend.tools.t_culture import (
    tool_knowledge_search, tool_object_dimensions, TOOL_SPECS_CULTURE,
)
from backend.tools.t_wiki import tool_wikipedia
from backend.tools.t_books import tool_livre
from backend.tools.t_github import tool_github

# Fonctions des outils culture generale.
_FONCTIONS_CULTURE = {
    "object_dimensions": lambda a: tool_object_dimensions(a["objet"]),
    "knowledge_search": lambda a: tool_knowledge_search(a["query"]),
}

# Fonctions des outils 3D v2 (registre) - definies AVANT TOOL_DEFINITIONS.
_FONCTIONS_3D_V2 = {
    "unit_convert": lambda a: tool_unit_convert(
        a["valeur"], a.get("de", "mm"), a.get("vers", "mm")),
    "units_scale": lambda a: tool_units_scale(
        a["mesh_path"], a.get("from_unit", "cm"), a.get("to_unit", "mm")),
    "measure_model": lambda a: tool_measure_model(
        a["mesh_path"], a.get("spec_json", "")),
    "check_printability": lambda a: tool_check_printability(
        a["mesh_path"], a.get("printer", "")),
    "export_3mf": lambda a: tool_export_3mf(
        a["stl_paths"], a.get("colors", ""), a.get("out_path", "")),
    "slicers_list": lambda a: tool_slicers_list(),
    "slicer_open": lambda a: tool_slicer_open(
        a["file_path"], a.get("slicer", "")),
}
from backend.tools.t_docs import tool_document_create
from backend.tools.t_extra import (tool_convertir_image, tool_memoire_ajouter,
    tool_notes, tool_presse_papiers, tool_rechercher_fichiers,
    tool_telecharger_fichier, tool_envoyer_telegram,
    tool_memory_search, tool_memory_forget)
from backend.tools.t_systeme import (tool_capture_ecran, tool_meteo,
    tool_open_app, tool_system_stats, tool_volume)


class PermissionDenied(Exception):
    pass


def level_rank(level: str) -> int:
    return _LEVEL_ORDER.get(level.upper(), 0)


class PermissionManager:
    def __init__(self, current_level: str):
        self.current = (current_level or "USER").upper()
        if self.current not in _LEVEL_ORDER:
            self.current = "USER"

    def check(self, required: str) -> None:
        if level_rank(self.current) < level_rank(required):
            raise PermissionDenied(
                f"Outil refuse : niveau '{required}' requis, niveau actuel '{self.current}'"
            )



TOOL_DEFINITIONS: dict[str, dict] = {
    "memory_search": {
        "level": "SAFE",
        "description": (
            "Recherche dans les souvenirs persistants de Neo (faits retenus "
            "entre conversations). A utiliser quand l'utilisateur demande "
            "'tu te souviens de...' ou pour enrichir une reponse."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "sujet a rechercher dans la memoire"},
                "k": {"type": "number", "description": "nombre max de resultats (defaut 5)"},
            },
            "required": ["query"],
        },
        "function": lambda a: tool_memory_search(a["query"], int(a.get("k", 5))),
    },
    "memory_forget": {
        "level": "USER",
        "description": (
            "Supprime les souvenirs qui contiennent le texte donne "
            "(quand l'utilisateur demande d'oublier une information)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contenu": {"type": "string", "description": "texte du souvenir a supprimer"},
            },
            "required": ["contenu"],
        },
        "function": lambda a: tool_memory_forget(a["contenu"]),
    },
    "memoire_ajouter": {
        "level": "USER",
        "description": (
            "Ajoute une information DURABLE a ta memoire (preferences, faits, "
            "projets de l'utilisateur). Seules les memoires de type utilisateur "
            "(tag user/fait/preference/projet) sont injectees dans les futures "
            "conversations ; le tag 'outil' est reserve aux notes techniques."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contenu": {"type": "string", "description": "l'information a retenir (une phrase concise)"},
                "tag": {
                    "type": "string",
                    "enum": ["user", "fait", "preference", "projet", "outil"],
                    "description": (
                        "Nature de la note : 'user' par defaut (ce que dit "
                        "l'utilisateur), 'outil' pour un constat technique "
                        "(non injecte dans le chat)."
                    ),
                },
            },
            "required": ["contenu"],
        },
        "function": lambda args: tool_memoire_ajouter(
            args["contenu"], args.get("tag", "user")),
    },
    "notes": {
        "level": "USER",
        "description": (
            "Notes personnelles persistantes (workspace/notes.md). action: "
            "'ecrire' (ajoute, avec titre optionnel), 'lire' (dernieres notes), 'effacer'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "ecrire | lire | effacer"},
                "texte": {"type": "string", "description": "contenu de la note (pour ecrire)"},
                "titre": {"type": "string", "description": "titre optionnel de la note"},
            },
            "required": ["action"],
        },
        "function": lambda args: tool_notes(
            args["action"], str(args.get("texte", "")), str(args.get("titre", ""))
        ),
    },
    "rechercher_fichiers": {
        "level": "USER",
        "description": (
            "Cherche des fichiers par nom (jokers autorises) dans le workspace "
            "et les documents. Retourne chemin + taille + date."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motif": {"type": "string", "description": "nom ou motif (*, ?) du fichier"},
                "dossier": {"type": "string", "description": "dossier optionnel (workspace ou documents)"},
            },
            "required": ["motif"],
        },
        "function": lambda args: tool_rechercher_fichiers(
            args["motif"], str(args.get("dossier", ""))
        ),
    },
    "presse_papiers": {
        "level": "USER",
        "description": (
            "Presse-papiers Windows : action 'lire' (retourne le contenu) ou "
            "'ecrire' (y place le texte). Pratique pour copier un resultat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "lire | ecrire"},
                "texte": {"type": "string", "description": "texte a placer (pour ecrire)"},
            },
            "required": ["action"],
        },
        "function": lambda args: tool_presse_papiers(
            args["action"], str(args.get("texte", ""))
        ),
    },
    "convertir_image": {
        "level": "USER",
        "description": (
            "Redimensionne ou convertit une image locale (PIL) : largeur et/ou "
            "hauteur cible, format de sortie png/jpg/webp. Utile pour adapter "
            "une image generee (miniature, banniere, conversion)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "chemin de l'image source"},
                "largeur": {"type": "integer", "description": "largeur cible (0 = proportionnel/inchange)"},
                "hauteur": {"type": "integer", "description": "hauteur cible (0 = proportionnel/inchange)"},
                "format_sortie": {"type": "string", "description": "png | jpg | webp (defaut = source)"},
                "destination": {"type": "string", "description": "chemin de sortie optionnel"},
            },
            "required": ["source"],
        },
        "function": lambda args: tool_convertir_image(
            args["source"], int(args.get("largeur", 0)), int(args.get("hauteur", 0)),
            str(args.get("format_sortie", "")), str(args.get("destination", "")),
        ),
    },
    "telecharger_fichier": {
        "level": "USER",
        "description": (
            "Telecharge un fichier depuis une URL (PDF, archive, code, "
            "image...) vers le dossier de travail sandbox. Retourne le "
            "chemin local. Pour les documents recus par lien, avant "
            "read_file ou analyse."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL du fichier"},
                "destination": {"type": "string", "description": "chemin de sortie optionnel (dossier sandbox)"},
            },
            "required": ["url"],
        },
        "function": lambda args: tool_telecharger_fichier(
            args["url"], str(args.get("destination", ""))
        ),
    },
    "envoyer_telegram": {
        "level": "USER",
        "description": (
            "Envoie un message et/ou une image sur le(s) chat(s) Telegram connectes "
            "au bot configure (token dans settings). Si aucun chat actif, retour erreur.\n\n"
            "texte : message a envoyer (max 4000 car). image_path : chemin absolu d'une "
            "image locale a joindre (optionnel). Au moins l'un des deux requis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texte": {"type": "string", "description": "message a envoyer"},
                "image_path": {"type": "string", "description": "chemin absolu image a joindre"}
            }
        },
        "function": lambda args: tool_envoyer_telegram(
            args.get("texte", ""), args.get("image_path", "")
        ),
    },
    "calculator": {
        "level": "SAFE",
        "description": ("Calcule une expression mathematique : + - * / // % **, parentheses, pi, e, "
                        "sqrt, log, sin, cos, factorial, round, et statistiques sur une liste : "
                        "mean, median, stdev (ecart-type), variance, min, max, sum. "
                        "Exemples : 17.5/100*2340 ; stdev([3, 7, 9, 12]) ; sqrt(2025)"),
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        "function": lambda args: tool_calculator(args["expression"]),
    },
    "get_datetime": {
        "level": "SAFE",
        "description": "Donne la date et l'heure actuelles",
        "parameters": {"type": "object", "properties": {}},
        "function": lambda args: tool_get_datetime(),
    },
    "generate_image": {
        "level": "POWER",
        "description": (
            "Studio image de Neo (FLUX local, 4K). A utiliser en PRIORITE pour "
            "toute demande d'image, photo, dessin, illustration, logo ou affiche. "
            "Passe la DEMANDE DE L'UTILISATEUR TELLE QUELLE en francais dans "
            "'prompt' (avec ses mots, ses fautes, ses references) : le directeur "
            "artistique la comprend, corrige, choisit le cadrage et ecrit le "
            "prompt du generateur ; ne la traduis pas et ne la resume pas. "
            "Retourne COMPRIS (ce qui a ete compris), IMAGE (chemin) et "
            "VERIFICATION (elements visibles). Seulement pour un SUJET REEL NOMME "
            "(avion SR-71, Tour Eiffel, Peugeot 205 GTI...), appelle d'abord "
            "find_reference_image puis passe son chemin en 'reference' pour une "
            "fidelite maximale au vrai sujet (FLUX Kontext). Jamais pour une "
            "personne, un animal ou un decor decrits en general (un vieux pecheur, "
            "une femme rousse, un port au matin) : appelle generate_image directement. "
            "Retourne le chemin du PNG cree."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "la demande de l'utilisateur, telle quelle, en francais (+ precisions utiles de la conversation)"},
                "precedent": {"type": "string", "description": "pour une SUITE (variation, plus sombre, autre angle...) : chemin EXACT de la derniere image produite"},
                "cadrage": {"type": "string", "enum": ["", "paysage", "portrait", "carre"], "description": "seulement si l'utilisateur l'impose (vide = choix du directeur artistique)"},
                "format": {"type": "string", "description": "tiff (defaut), jpeg, png, webp ou exr, seulement si demande"},
                "save_path": {"type": "string"},
                "negatif": {"type": "string", "description": "choses a exclure (vide = defaut)"},
                "reference": {"type": "string", "description": "chemin d'une image locale de reference du sujet (find_reference_image) - fidelite maximale"},
                "reference_desc": {"type": "string", "description": "description visuelle du sujet de reference (fournie par find_reference_image)"},
                "variantes": {"type": "integer", "description": "nombre d'images a produire (1 a 3), pour « plusieurs versions / variations ». Chacune est verifiee par la vision, la meilleure vient en tete"},
            },
            "required": ["prompt"],
        },
        # largeur/hauteur/steps sont volontairement ABSENTS du schema (le modele
        # les fixait mal) : ils restent lus pour les appels internes/API.
        "function": lambda args: tool_generate_image(
            args["prompt"],
            args.get("save_path", ""),
            int(args.get("largeur", 0)),
            int(args.get("hauteur", 0)),
            str(args.get("negatif", "")),
            int(args.get("steps", 0)),
            str(args.get("reference", "")),
            str(args.get("reference_desc", "")),
            int(args.get("variantes", 1)),
            str(args.get("format", "")),
            None,
            str(args.get("precedent", "")),
            str(args.get("cadrage", "")),
        ),
    },
    "edit_image": {
        "level": "POWER",
        "description": (
            "Modifie une image EXISTANTE (retouche FLUX.2-klein, ~15 s ; repli Kontext) : "
            "recoloration, changement de ciel/decor, transformation de style, ajout/suppression "
            "d'elements, amelioration. Le reste de l'image (visage, pose, cadrage) est garde. "
            "L'image d'entree est conservee, une nouvelle image modifiee est creee. "
            "Passe la demande de l'utilisateur telle quelle (le francais est compris). "
            "A utiliser quand l'utilisateur fournit "
            "une image et demande de la modifier/retoucher/transformer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "chemin de l'image a modifier"},
                "prompt": {"type": "string", "description": "modifications demandees (FR ou EN)"},
                "save_path": {"type": "string"},
            },
            "required": ["image_path", "prompt"],
        },
        "function": lambda args: tool_edit_image(
            args["image_path"], args["prompt"], args.get("save_path", "")
        ),
    },
    "document_create": {
        "level": "POWER",
        "description": (
            "Cree un document professionnel (Word .docx, PDF, tableur .xlsx ou presentation PowerPoint .pptx) "
            "a partir d'un contenu en Markdown (titres #, listes -, tableaux "
            "| a | b |). Le fichier est range dans le dossier des documents de "
            "Neo : ne passe PAS save_path, sauf si l'utilisateur impose un "
            "emplacement. Retourne le chemin REEL du fichier cree, a citer tel quel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "titre": {"type": "string", "description": "titre du document"},
                "contenu_md": {"type": "string", "description": "contenu complet en Markdown"},
                "type_doc": {"type": "string", "description": "docx | pdf | xlsx | pptx (defaut docx ; pptx = presentation PowerPoint, une diapositive par titre ##)"},
                "save_path": {"type": "string", "description": "a laisser vide ; seulement un chemin Windows absolu impose par l'utilisateur"},
            },
            "required": ["titre", "contenu_md"],
        },
        "function": lambda args: tool_document_create(
            args["titre"], args["contenu_md"],
            args.get("type_doc", "docx"), args.get("save_path", "")
        ),
    },
    "meteo": {
        "level": "USER",
        "description": "Meteo actuelle + aujourd'hui/demain pour une ville (API Open-Meteo gratuite, sans cle)",
        "parameters": {
            "type": "object",
            "properties": {"ville": {"type": "string", "description": "nom de la ville"}},
            "required": ["ville"],
        },
        "function": lambda args: tool_meteo(args["ville"]),
    },
    "taux_change": {
        "level": "SAFE",
        "description": ("Convertit un montant d'une devise a une autre au taux de reference du jour de la "
                        "Banque centrale europeenne (gratuit, sans cle). Devises : EUR, USD, GBP, CHF, JPY, "
                        "CAD, CNY... ou leur nom en francais."),
        "parameters": {
            "type": "object",
            "properties": {
                "montant": {"type": "number", "description": "montant a convertir (defaut 1)"},
                "de": {"type": "string", "description": "devise de depart, ex. EUR ou euros"},
                "vers": {"type": "string", "description": "devise d'arrivee, ex. USD ou dollars"},
            },
            "required": ["de", "vers"],
        },
        "function": lambda args: tool_taux_change(args.get("montant", 1), args["de"], args["vers"]),
    },
    "adresse": {
        "level": "SAFE",
        "description": ("Trouve une adresse ou un lieu en France (mairie, gare, rue) avec code postal, "
                        "commune et coordonnees GPS, via le service officiel de l'IGN (gratuit, sans cle)."),
        "parameters": {
            "type": "object",
            "properties": {"recherche": {"type": "string", "description": "adresse ou lieu, ex. 'mairie d'Annecy'"}},
            "required": ["recherche"],
        },
        "function": lambda args: tool_adresse(args["recherche"]),
    },
    "system_stats": {
        "level": "USER",
        "description": "Statistiques du PC : CPU, RAM, disques, GPU (temperature, memoire, utilisation)",
        "parameters": {"type": "object", "properties": {}},
        "function": lambda args: tool_system_stats(),
    },
    "volume": {
        "level": "POWER",
        "description": "Controle le volume systeme : 'up', 'down', 'muet', un nombre (ex '35'), ou vide pour l'etat",
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string", "description": "up | down | muet | nombre | vide"}},
            "required": ["action"],
        },
        "function": lambda args: tool_volume(args.get("action", "")),
    },
    "open_app": {
        "level": "POWER",
        "description": "Ouvre une application connue (notepad, calculatrice, chrome, firefox, edge, vlc, explorateur, paint, parametres, powershell...)",
        "parameters": {
            "type": "object",
            "properties": {"nom": {"type": "string", "description": "nom de l'application"}},
            "required": ["nom"],
        },
        "function": lambda args: tool_open_app(args["nom"]),
    },
    "capture_ecran": {
        "level": "POWER",
        "description": "Capture l'ecran et repond a une question dessus avec le modele vision ('c'est quoi cette erreur ?', 'lis ce texte'...)",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "question sur le contenu de l'ecran"}},
            "required": ["question"],
        },
        "function": lambda args: tool_capture_ecran(args.get("question", "")),
    },
    "list_dir": {
        "level": "USER",
        "description": "Liste le contenu d'un dossier",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "function": lambda args: tool_list_dir(args["path"]),
    },
    "read_file": {
        "level": "USER",
        "description": "Lit le contenu d'un fichier texte",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "function": lambda args: tool_read_file(args["path"]),
    },
    "write_file": {
        "level": "POWER",
        "description": "Ecrit du contenu dans un fichier",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        "function": lambda args: tool_write_file(args["path"], args["content"]),
    },
    "run_command": {
        "level": "POWER",
        "description": "Execute une commande shell et retourne sa sortie",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        "function": lambda args: tool_run_command(args["command"]),
    },
    "web_search": {
        "level": "USER",
        "description": "Recherche sur internet (DuckDuckGo) : titre, URL et extrait des resultats. A utiliser pour toute question d'actualite ou de fait recent.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "function": lambda args: tool_web_search(args["query"]),
    },
    "deep_research": {
        "level": "USER",
        "description": "Recherche approfondie multi-etapes : genere plusieurs requetes, lit les pages web et produit une synthese avec sources. A utiliser pour les sujets complexes qui meritent une vraie investigation (pas les questions rapides).",
        "parameters": {
            "type": "object",
            "properties": {
                "sujet": {"type": "string"},
                "profondeur": {"type": "integer", "description": "1 a 3 (defaut 2)"},
            },
            "required": ["sujet"],
        },
        "function": lambda args: tool_deep_research(
            args["sujet"], args.get("profondeur", 2)
        ),
    },
    "check_code": {
        "level": "POWER",
        "description": "Agent de controle : valide la syntaxe d'un code (python, javascript, json) et peut l'executer en sandbox 15s pour verifier qu'il fonctionne. A utiliser APRES avoir ecrit du code important, avant de le livrer.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "langage": {"type": "string", "description": "python | javascript | json"},
                "executer": {"type": "boolean", "description": "executer en sandbox (python uniquement)"},
            },
            "required": ["code"],
        },
        "function": lambda args: tool_check_code(
            args["code"], args.get("langage", "python"), bool(args.get("executer"))
        ),
    },
    "web_fetch": {
        "level": "USER",
        "description": "Recupere le contenu texte d'une page web a partir de son URL",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "function": lambda args: tool_web_fetch(args["url"]),
    },
    "web_browse": {
        "level": "USER",
        "description": "Navigation legere d'une page web : extrait le texte propre et la liste des liens cliquables, pour naviguer de page en page sans navigateur lourd. A utiliser pour explorer un site ou suivre des liens.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "function": lambda args: tool_web_browse(args["url"]),
    },
    "code_edit": {
        "level": "POWER",
        "description": "Edite un fichier de code selon une tache decrite en langage naturel. Lit le fichier, genere le nouveau contenu via le LLM, ecrit le fichier, renvoie le diff git. Option commit pour valider. A utiliser pour modifier du code existant.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "description de la modification a faire"},
                "file_path": {"type": "string", "description": "chemin absolu du fichier a modifier"},
                "repo_dir": {"type": "string", "description": "repertoire git (optionnel, defaut = dossier du fichier)"},
                "commit": {"type": "boolean", "description": "creer un commit git apres edition (defaut false)"},
            },
            "required": ["task", "file_path"],
        },
        "function": lambda args: tool_code_edit(
            args["task"], args["file_path"], args.get("repo_dir", ""), bool(args.get("commit", False))
        ),
    },
    "code_review": {
        "level": "USER",
        "description": "Revues un fichier de code (bugs, securite, bonnes pratiques) via le LLM. Option focus pour cibler.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "focus": {"type": "string", "description": "point a privilegier (optionnel)"},
            },
            "required": ["file_path"],
        },
        "function": lambda args: tool_code_review(args["file_path"], args.get("focus", "")),
    },
    "git_status": {
        "level": "USER",
        "description": "Affiche le statut git et les 5 derniers commits d'un depot.",
        "parameters": {
            "type": "object",
            "properties": {"repo_dir": {"type": "string"}},
            "required": ["repo_dir"],
        },
        "function": lambda args: tool_git_status(args["repo_dir"]),
    },
    "render_scad": {
        "level": "POWER",
        "description": "Genere un fichier STL a partir d'un script OpenSCAD (.scad). Workflow 3D : ecrire le .scad avec write_file puis appeler render_scad.",
        "parameters": {
            "type": "object",
            "properties": {
                "scad_path": {"type": "string"},
                "stl_path": {"type": "string"},
            },
            "required": ["scad_path"],
        },
        "function": lambda args: tool_render_scad(args["scad_path"], args.get("stl_path", "")),
    },
    "slice_model": {
        "level": "POWER",
        "description": "Tranche un fichier STL en G-code avec un slicer installe (prusa, orca, cura, bambu)",
        "parameters": {
            "type": "object",
            "properties": {
                "stl_path": {"type": "string"},
                "slicer": {"type": "string"},
                "out_gcode": {"type": "string"},
            },
            "required": ["stl_path"],
        },
        "function": lambda args: tool_slice_model(
            args["stl_path"], args.get("slicer", "prusa"), args.get("out_gcode", "")
        ),
    },
    "image_search": {
        "level": "USER",
        "description": "Recherche d'images de reference sur DuckDuckGo : titres + URL d'images. A utiliser pour voir a quoi ressemble un objet avant de le concevoir en 3D.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "function": lambda args: tool_image_search(args["query"]),
    },
    "fetch_image": {
        "level": "USER",
        "description": "Telecharge une image depuis son URL et retourne le chemin local (pour analyse par le modele vision)",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "save_path": {"type": "string"},
            },
            "required": ["url"],
        },
        "function": lambda args: tool_fetch_image(args["url"], args.get("save_path", "")),
    },
    "analyze_image": {
        "level": "USER",
        "description": "Analyse une image locale avec un modele vision : description, reconnaissance d'objet, comparaison avec une demande",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["image_path", "question"],
        },
        "function": lambda args: tool_analyze_image(args["image_path"], args["question"]),
    },
    "render_preview": {
        "level": "POWER",
        "description": "Genere un apercu PNG d'un script OpenSCAD pour inspection visuelle",
        "parameters": {
            "type": "object",
            "properties": {
                "scad_path": {"type": "string"},
                "png_path": {"type": "string"},
            },
            "required": ["scad_path"],
        },
        "function": lambda args: tool_render_preview(args["scad_path"], args.get("png_path", "")),
    },
    "validate_design": {
        "level": "POWER",
        "description": "CONTROLE QUALITE 3D : rend un apercu PNG du script OpenSCAD puis fait verifier par le modele vision que l'objet correspond a la demande. Retourne VERDICT OUI/NON + ecarts. Obligatoire avant le tranchage.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_text": {"type": "string"},
                "scad_path": {"type": "string"},
            },
            "required": ["request_text", "scad_path"],
        },
        "function": lambda args: tool_validate_design(args["request_text"], args["scad_path"]),
    },
    "find_reference_image": {
        "level": "USER",
        "description": "RECHERCHE VISUELLE INTELLIGENTE : cherche des photos de l'objet demande, telecharge les candidates et fait valider chaque image par le modele vision. Retourne la meilleure reference locale + description geometrique. A utiliser AVANT toute conception 3D.",
        "parameters": {
            "type": "object",
            "properties": {"object_query": {"type": "string"}},
            "required": ["object_query"],
        },
        "function": lambda args: tool_find_reference_image(args["object_query"]),
    },
    "fiche_creer": {
        "level": "USER",
        "description": (
            "FICHE DE CONNAISSANCE : cherche sur le web, redige et ENREGISTRE la fiche d'un sujet (titre, "
            "resume, points cles, sources), visible ensuite dans Documents > Fiches. A utiliser quand "
            "l'utilisateur demande une fiche, ou de retenir / garder un sujet. Un seul appel fait tout ; "
            "retourne le contenu de la fiche."
        ),
        "parameters": {
            "type": "object",
            "properties": {"sujet": {"type": "string", "description": "le sujet, en quelques mots (ex. « le pont du Gard »)"}},
            "required": ["sujet"],
        },
        "function": lambda args: __import__("backend.core.fiches", fromlist=["tool_fiche_creer"]).tool_fiche_creer(
            args["sujet"]),
    },
    "generate_mesh_from_text": {
        "level": "POWER",
        "description": (
            "MODELE 3D ORGANIQUE DEPUIS UNE DESCRIPTION : pour un sujet imagine ou generique (tete de "
            "lion, dragon, buste, figurine, objet decoratif). Neo dessine lui-meme l'image de reference "
            "puis genere le mesh imprimable (.stl), a l'echelle height_mm. Un seul appel, 1 a 3 minutes. "
            "Retourne le chemin du STL. Pour un sujet REEL precis dont on a une photo, utiliser "
            "generate_mesh_from_image."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sujet": {"type": "string", "description": "le sujet a modeliser, decrit simplement (ex. « tete de lion rugissant »)"},
                "height_mm": {"type": "number", "description": "plus grande dimension souhaitee, en mm (defaut 80)"},
                "texture": {"type": "boolean", "description": "true = version texturee (OBJ + PNG) en plus"},
                "moteur": {"type": "string", "enum": ["", "meshy", "hunyuan", "triposr"],
                           "description": "vide = moteur local (defaut). \"meshy\" SEULEMENT si l'utilisateur demande Meshy"},
            },
            "required": ["sujet"],
        },
        "function": lambda args: __import__("backend.tools.t_mesh", fromlist=["tool_generate_mesh_from_text"]).tool_generate_mesh_from_text(
            args["sujet"], float(args.get("height_mm") or 80), str(args.get("moteur") or ""),
            bool(args.get("texture", False))),
    },
    "generate_mesh_from_image": {
        "level": "POWER",
        "description": "GENERATION ORGANIQUE PAR IA (TripoSR local) : transforme une photo de reference en mesh 3D imprimable (.stl), echelle a height_mm. Pour les formes organiques impossibles en OpenSCAD : tetes, personnages, sculptures. Compter 1 a 2 minutes. Mettre texture=true pour obtenir en plus la version TEXTUREE (OBJ + PNG, style Meshy) au lieu du mesh uni.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "output_stl": {"type": "string", "description": "a ne PAS passer : l'outil range le STL lui-meme et renvoie son chemin (seulement si l'utilisateur impose un emplacement)"},
                "height_mm": {"type": "number"},
                "texture": {"type": "boolean", "description": "true = bake la texture (OBJ+PNG texturés)"},
                "moteur": {"type": "string", "enum": ["", "meshy", "hunyuan", "triposr"],
                           "description": "vide = moteur local (defaut). \"meshy\" SEULEMENT si l'utilisateur demande Meshy (service en ligne, cle dans Reglages > Connexions, consomme des credits)"},
            },
            "required": ["image_path"],
        },
        "function": lambda args: tool_generate_mesh_from_image(
            args["image_path"], args.get("output_stl", ""), float(args.get("height_mm", 80)),
            0, bool(args.get("texture", False)), str(args.get("moteur", "")),
        ),
    },
    "mesh_info": {
        "level": "SAFE",
        "description": "Statistiques d'un fichier 3D (STL/OBJ) : triangles, watertight, dimensions mm, volume cm3, position Z",
        "parameters": {
            "type": "object",
            "properties": {"mesh_path": {"type": "string"}},
            "required": ["mesh_path"],
        },
        "function": lambda args: tool_mesh_info(args["mesh_path"]),
    },
    "repair_mesh": {
        "level": "POWER",
        "description": "Repare un mesh 3D : normales inverses, trous, orientation. A faire sur tout mesh genere par IA avant impression ou fusion",
        "parameters": {
            "type": "object",
            "properties": {"mesh_path": {"type": "string"}},
            "required": ["mesh_path"],
        },
        "function": lambda args: tool_repair_mesh(args["mesh_path"]),
    },
    "mesh_boolean": {
        "level": "POWER",
        "description": "Operation booleenne EXACTE entre 2 meshes (union/subtract/intersect). Exige des meshes watertight. Pour assembler simplement 2 pieces, prefere fuse_meshes qui tolere les maillages imparfaits",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string"},
                "mesh_a": {"type": "string"},
                "mesh_b": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["op", "mesh_a", "mesh_b"],
        },
        "function": lambda args: tool_mesh_boolean(
            args["op"], args["mesh_a"], args["mesh_b"], args.get("output_path", "")
        ),
    },
    "fuse_meshes": {
        "level": "POWER",
        "description": "FUSION ROBUSTE de 2 fichiers STL en un seul : repare automatiquement les maillages defectueux (voxelisation) puis union booleenne. A utiliser pour assembler la forme organique generee par IA avec la piece fonctionnelle OpenSCAD. Les pieces doivent se chevaucher d'au moins 2 mm.",
        "parameters": {
            "type": "object",
            "properties": {
                "mesh_a": {"type": "string"},
                "mesh_b": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["mesh_a", "mesh_b"],
        },
        "function": lambda args: tool_fuse_meshes(
            args["mesh_a"], args["mesh_b"], args.get("output_path", "")
        ),
    },
    "render_stl_views": {
        "level": "POWER",
        "description": "Rend 4 vues PNG d'un fichier STL (face/profil/dos/iso) pour inspection visuelle par le modele vision",
        "parameters": {
            "type": "object",
            "properties": {"stl_path": {"type": "string"}},
            "required": ["stl_path"],
        },
        "function": lambda args: tool_render_stl_views(args["stl_path"]),
    },
    "validate_mesh": {
        "level": "POWER",
        "description": "CONTROLE QUALITE MULTI-VUES D'UN STL : rend 4 vues puis verification croisee par le modele vision contre la demande (+ reference optionnelle). VERDICT OUI/NON + ecarts + score. Obligatoire avant tranchage pour tout mesh.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_text": {"type": "string"},
                "stl_path": {"type": "string"},
                "reference_image": {"type": "string"},
            },
            "required": ["request_text", "stl_path"],
        },
        "function": lambda args: tool_validate_mesh(
            args["request_text"], args["stl_path"], args.get("reference_image", "")
        ),
    },
    "blender_run": {
        "level": "POWER",
        "description": (
            "Execute un script Python Blender (bpy) en headless, sans interface. "
            "Workflow : ecrire le script avec write_file (import bpy, creer la geometrie "
            "avec les cotes demandes), puis blender_run. Le script DOIT lire "
            "BLENDER_JOB_DIR (variable d'environnement = dossier du job) et y ecrire "
            "piece.stl. Pour un apercu photo, utiliser ensuite blender_render."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script_path": {"type": "string"},
                "timeout_s": {"type": "integer"},
            },
            "required": ["script_path"],
        },
        "function": lambda args: tool_blender_run(
            args["script_path"], int(args.get("timeout_s", 600))
        ),
    },
    "blender_render": {
        "level": "POWER",
        "description": (
            "Rendu photo PNG d'un fichier STL/OBJ via Blender (eclairage + camera "
            "automatiques, sans GPU). Moteur 'workbench' (rapide, ~30s) ou 'cycles' "
            "(hero, ~5-10 min CPU, fond transparent). Si le STL/OBJ a une texture "
            "(bake TripoSR), elle est appliquee automatiquement (ou via 'texture')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stl_path": {"type": "string"},
                "png_path": {"type": "string"},
                "largeur": {"type": "integer"},
                "hauteur": {"type": "integer"},
                "moteur": {"type": "string", "description": "workbench | cycles"},
                "echantillons": {"type": "integer", "description": "samples Cycles (defaut 48)"},
                "texture": {"type": "string", "description": "PNG de texture (auto-detectee sinon)"},
            },
            "required": ["stl_path"],
        },
        "function": lambda args: tool_blender_render(
            args["stl_path"], args.get("png_path", ""),
            int(args.get("largeur", 600)), int(args.get("hauteur", 450)),
            str(args.get("moteur", "workbench")), int(args.get("echantillons", 48)),
            str(args.get("texture", "")),
        ),
    },
    "pipeline_3d": {
        "level": "POWER",
        "description": (
            "PIPELINE 3D v2 COMPLET (3MF par defaut + boucle de correction, STL secondaire) : transforme une demande en langage naturel en pièce "
            "3D imprimable (STL). Étapes : spec JSON paramétrique → script OpenSCAD "
            "généré par LLM → rendu headless → contrôles auto (watertight, dimensions) "
            "→ rendu 4 vues → contrôle qualité par modèle vision. Retourne le chemin du "
            "STL final et le rapport de contrôle. Utiliser pour toute demande de création "
            "d'objet 3D mécanique ou fonctionnel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request_text": {
                    "type": "string",
                    "description": "Demande de conception 3D en langage naturel "
                                   "(ex: 'un support de téléphone avec fente 8mm').",
                },
                "save_dir": {
                    "type": "string",
                    "description": "Dossier de sortie optionnel (défaut : workspace/pipeline_3d/<date>).",
                },
                "printer": {
                    "type": "string",
                    "description": "Imprimante cible pour les controles (ad5x par defaut).",
                },
                "couleur": {
                    "type": "string",
                    "description": "Couleur optionnelle au format hex (ex: #FF0000).",
                },
            },
            "required": ["request_text"],
        },
        "function": lambda args: __import__("backend.tools.t_pipeline_3d", fromlist=["pipeline_3d"]).pipeline_3d(
            args["request_text"], args.get("save_dir", ""),
            args.get("printer", ""), args.get("couleur", "")
        ),
    },

    # ---- Outils 3D v2 (audit-generation-3d.md) ----
    **{
        _nom: {
            **dict(TOOL_SPECS_3D_V2[_nom]),
            "function": _fn,
        }
        for _nom, _fn in _FONCTIONS_3D_V2.items()
    },

    # ---- Culture generale ----
    **{
        _nom: {
            **dict(TOOL_SPECS_CULTURE[_nom]),
            "function": _fn,
        }
        for _nom, _fn in _FONCTIONS_CULTURE.items()
    },

    # ---- API publiques gratuites (sans cle) ----
    "wikipedia": {
        "level": "SAFE",
        "description": (
            "Recherche un article Wikipedia et en retourne le resume. "
            "Gratuit, sans cle API. Utile pour verifier un fait, "
            "definir un concept ou enrichir une reponse."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Sujet a rechercher sur Wikipedia"},
                "lang": {"type": "string", "description": "Code langue (defaut 'fr')"},
            },
            "required": ["query"],
        },
        "function": lambda args: tool_wikipedia(
            args["query"], args.get("lang", "fr")
        ),
    },
    "livre": {
        "level": "SAFE",
        "description": (
            "Recherche un livre sur Open Library (titre, auteur, annee, "
            "couverture). Gratuit, sans cle API."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Titre, auteur ou mot-cle de livre"},
            },
            "required": ["query"],
        },
        "function": lambda args: tool_livre(args["query"]),
    },
    "github": {
        "level": "SAFE",
        "description": (
            "Recherche des depots GitHub pertinents (nom, description, "
            "langage, etoiles). Gratuit sans cle, rate-limite ~10 req/min."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Sujet, langage ou mot-cle a rechercher sur GitHub"},
            },
            "required": ["query"],
        },
        "function": lambda args: tool_github(args["query"]),
    },

    # ---- API publiques avec cle gratuite (Niveau 2) ----
    "tts_generer": {
        "level": "SAFE",
        "description": (
            "Genere un fichier audio MP3 via ElevenLabs (voix ultra-realistes). "
            "Gratuit sans cle : 10 000 caracteres/mois. Repli local si cle manquante."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texte": {"type": "string", "description": "Texte a convertir en audio"},
                "voix": {"type": "string", "description": "Nom de la voix ElevenLabs (defaut Rachel)"},
            },
            "required": ["texte"],
        },
        "function": lambda args: tool_tts_generer(
            args["texte"], args.get("voix", "Rachel")
        ),
    },
    "traduire": {
        "level": "SAFE",
        "description": (
            "Traduit un texte via DeepL API gratuite (500 000 caracteres/mois). "
            "Repli : texte original si cle manquante."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texte": {"type": "string", "description": "Texte a traduire"},
                "cible": {"type": "string", "description": "Langue cible (defaut EN)"},
                "source": {"type": "string", "description": "Langue source (defaut FR, ou auto)"},
            },
            "required": ["texte"],
        },
        "function": lambda args: tool_traduire(
            args["texte"], args.get("cible", "EN"), args.get("source", "FR")
        ),
    },
    "illustrer": {
        "level": "SAFE",
        "description": (
            "Recherche des images sur Unsplash pour illustrer un sujet. "
            "Gratuit avec cle d application Unsplash."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sujet": {"type": "string", "description": "Sujet de recherche image"},
                "nombre": {"type": "number", "description": "Nombre d images (defaut 3, max 10)"},
            },
            "required": ["sujet"],
        },
        "function": lambda args: tool_illustrer(
            args["sujet"], args.get("nombre", 3)
        ),
    },

    # ---- Vidéo (Open-Drama local) ----
    "video_storyboard": {
        "level": "SAFE",
        "description": (
            "Découpe une histoire en storyboard JSON (meilleur modèle texte local) : "
            "plans de 3 à 5 s, prompts d'image détaillés, personnage et style figés, "
            "mouvement décrit pour chaque plan. 100 % local."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "histoire": {"type": "string", "description": "Histoire à adapter en vidéo"},
                "modele": {"type": "string", "description": "Modèle Ollama (défaut: auto)"},
            },
            "required": ["histoire"],
        },
        "function": lambda args: tool_video_storyboard(
            args["histoire"], args.get("modele", "")
        ),
    },
    "video_clip": {
        "level": "SAFE",
        "description": (
            "Génère le clip d'un plan : image 4K (FLUX) puis VRAI mouvement par "
            "Wan 2.2 (image vers vidéo, 24 i/s, jusqu'à 5 s) si le modèle est "
            "installé ; sinon mouvement de caméra fluide (Ken Burns) d'après le "
            "videoPrompt. Plusieurs minutes par plan avec Wan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "storyboard_path": {"type": "string", "description": "Chemin du JSON storyboard"},
                "shot_id": {"type": "number", "description": "Identifiant du plan (défaut 1)"},
            },
            "required": ["storyboard_path"],
        },
        "function": lambda args: tool_video_clip(
            args["storyboard_path"], args.get("shot_id", 1)
        ),
    },
    "video_render": {
        # USER : le montage supprime les clips et images intermediaires.
        "level": "USER",
        "description": (
            "Montage final : fondus enchaînés, ouverture et fermeture au noir, "
            "narration (voix locale ou ElevenLabs) normalisée, encodage GPU."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Nom du projet"},
                "audio_texte": {"type": "string", "description": "Texte à convertir en audio (optionnel)"},
            },
            "required": ["project_name"],
        },
        "function": lambda args: tool_video_render(
            args["project_name"], args.get("audio_texte", "")
        ),
    },

    # ---- Depot (git, index, tests) ----
    **{
        "file_search": {
            "level": "USER",
            "description": "Recherche un texte dans les fichiers d'un repertoire.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Texte a chercher"},
                    "path": {"type": "string", "description": "Repertoire (defaut: .)"},
                },
                "required": ["query"],
            },
            "function": lambda a: _tool_file_search(a["query"], a.get("path", ".")),
        },
        "git_diff": {
            "level": "USER",
            "description": "Affiche le diff git d'un repertoire.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_dir": {"type": "string", "description": "Repertoire git (defaut: .)"},
                },
                "required": [],
            },
            "function": lambda a: _tool_git_diff(a.get("repo_dir", ".")),
        },
        "git_rollback": {
            "level": "POWER",
            "description": "Annule les modifications non commites dans un repertoire git.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_dir": {"type": "string", "description": "Repertoire git (defaut: .)"},
                },
                "required": [],
            },
            "function": lambda a: _tool_git_rollback(a.get("repo_dir", ".")),
        },
        "repo_index": {
            "level": "USER",
            "description": "Liste les fichiers d'un repertoire.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repertoire (defaut: .)"},
                },
                "required": [],
            },
            "function": lambda a: _tool_repo_index(a.get("path", ".")),
        },
        "run_tests": {
            "level": "POWER",
            "description": "Execute les tests (pytest) d'un repertoire.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Chemin vers les tests (defaut: .)"},
                },
                "required": [],
            },
            "function": lambda a: _tool_run_tests(a.get("path", ".")),
        },
    },
}


# ---- Outils depot (git, index, tests) : confines aux racines sandbox ----

_DOSSIERS_LOURDS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                    "models", "$RECYCLE.BIN", "System Volume Information"}


def _racines_recherche(path: str, outil: str) -> tuple:
    """Racines a parcourir : `path` verifie, ou toutes les racines de lecture
    si path vaut '' ou '.'."""
    if (path or ".").strip() in ("", "."):
        return [r for r in _sandbox_cfg()["read_roots"] if r.is_dir()], None
    p, refus = chemin_lecture(path, outil)
    return ([p] if p else []), refus


def _tool_file_search(query: str, path: str = ".") -> str:
    import time

    query = (query or "").strip().lower()
    if not query:
        return "Erreur: texte a chercher vide"
    racines, refus = _racines_recherche(path, "file_search")
    if refus:
        return refus
    resultats = []
    debut = time.time()
    TIMEOUT = 10
    for racine in racines:
        for root_dir, dirs, files in os.walk(racine):
            dirs[:] = [d for d in dirs if d not in _DOSSIERS_LOURDS
                       and not os.path.islink(os.path.join(root_dir, d))]
            for f in files:
                if time.time() - debut > TIMEOUT:
                    return f"Timeout ({TIMEOUT}s) : {len(resultats)} resultats\n" + "\n".join(resultats)
                chemin = os.path.join(root_dir, f)
                try:
                    if os.path.getsize(chemin) > 2_000_000:
                        continue
                    with open(chemin, "r", encoding="utf-8", errors="ignore") as fh:
                        for i, ligne in enumerate(fh, 1):
                            if query in ligne.lower():
                                resultats.append(f"{chemin}:{i}: {ligne.strip()[:120]}")
                                if len(resultats) >= 50:
                                    return "\n".join(resultats)
                except OSError:
                    continue
    return "\n".join(resultats) if resultats else "Aucun resultat"


def _git_sur(repo_dir: str, args: list[str], outil: str, ecriture: bool) -> str:
    """git -C <depot verifie> ... (lecture : racines de lecture ; ecriture :
    workspace seul)."""
    if ecriture:
        p, refus = _resolve_sandbox(repo_dir or ".", outil, ecriture=True)
    else:
        p, refus = chemin_lecture(repo_dir or ".", outil)
    if refus:
        return refus
    try:
        r = subprocess.run(["git", "-C", str(p), *args], capture_output=True,
                           text=True, timeout=30, encoding="utf-8", errors="replace")
        return r.stdout or r.stderr or ""
    except Exception as e:
        return f"Erreur: {e}"


def _tool_git_diff(repo_dir: str = ".") -> str:
    return _git_sur(repo_dir, ["diff"], "git_diff", ecriture=False) or "Aucune modification"


def _tool_git_rollback(repo_dir: str = ".") -> str:
    return _git_sur(repo_dir, ["checkout", "--", "."], "git_rollback",
                    ecriture=True) or "Rollback effectue"


def _tool_repo_index(path: str = ".") -> str:
    racines, refus = _racines_recherche(path, "repo_index")
    if refus:
        return refus
    fichiers = []
    for racine in racines:
        for root, dirs, files in os.walk(racine):
            dirs[:] = [d for d in dirs if d not in _DOSSIERS_LOURDS]
            fichiers.extend(os.path.join(root, f) for f in files[:20])
            if len(fichiers) >= 100:
                return "\n".join(fichiers[:100])
    return "\n".join(fichiers) if fichiers else "Vide"


def _tool_run_tests(path: str = ".") -> str:
    # pytest importe les conftest.py du dossier : on n'execute que du code
    # situe dans le workspace (niveau POWER, comme check_code).
    p, refus = _resolve_sandbox(path or ".", "run_tests", ecriture=True)
    if refus:
        return refus
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", str(p), "-q", "--tb=short"],
                           cwd=str(p if p.is_dir() else p.parent), capture_output=True,
                           text=True, timeout=120, encoding="utf-8", errors="replace")
        return r.stdout or r.stderr or "Tests termines sans sortie"
    except subprocess.TimeoutExpired:
        return "Erreur: tests TIMEOUT (>120s)"
    except Exception as e:
        return f"Erreur: {e}"


def ollama_tool_specs(names: list[str]) -> list[dict]:
    specs = []
    for name in names:
        tool = TOOL_DEFINITIONS.get(name)
        if tool is None:
            logging.getLogger(__name__).warning("ollama_tool_specs: outil inconnu ignore: %s", name)
            continue
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
        )
    return specs


def _resolve_workspace_paths(args: dict) -> dict:
    """Remplace {WORKSPACE_IMAGES} par le chemin reel du dossier images."""
    images = str(_default_root() / "workspace" / "images")
    return {k: v.replace("{WORKSPACE_IMAGES}", images)
            if isinstance(v, str) and "{WORKSPACE_IMAGES}" in v else v
            for k, v in args.items()}


def execute_tool(name: str, arguments: dict, permissions: PermissionManager) -> str:
    tool = TOOL_DEFINITIONS.get(name)
    if tool is None:
        return f"Erreur: outil inconnu '{name}'"
    try:
        permissions.check(tool["level"])
    except PermissionDenied as exc:
        return f"PERMISSION REFUSEE: {exc}"
    try:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return f"Erreur d'execution de {name}: arguments attendus sous forme d'objet JSON"
        # Resout les placeholders de chemins workspace
        arguments = _resolve_workspace_paths(arguments)
        resultat = str(tool["function"](arguments))[:6000]
    except Exception as exc:
        return f"Erreur d'execution de {name}: {exc}"
    # Controle automatique du Python livre par write_file : syntaxe seulement.
    # Plus d'execution automatique (elle lancait n'importe quel code ecrit par
    # le modele) ; pour executer, l'agent appelle explicitement check_code.
    if name == "write_file" and str(arguments.get("path", "")).lower().endswith(".py") \
            and resultat.startswith("Ecrit:"):
        verdict = tool_check_code(str(arguments.get("content", "")), "python", executer=False)
        if "ECHEC SYNTAXE" in verdict:
            resultat += (
                "\n\n[CONTROLE AUTOMATIQUE] ECHEC: le fichier Python ecrit ne compile pas!\n"
                + verdict[:600]
            )
        else:
            resultat += "\n\n[CONTROLE AUTOMATIQUE] Syntaxe OK."
    return resultat
