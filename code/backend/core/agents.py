"""Agents de Neo : qui fait quoi, et avec quels outils.

Source UNIQUE de verite : le serveur Rust relit ce module a chaque message
(`localai-server/src/tools.rs` : `agent_system`, `agent_tools`, `agent_rag`).

Parcours d'un message :
  1. `backend.core.router.route_agent(message)` choisit l'agent — regles, puis
     classifieur d'intention. C'est le SEUL endroit ou cela se decide ;
  2. l'agent apporte sa CONSIGNE (`consignes/<id>.md`, un fichier par agent) et
     sa liste d'OUTILS (declares dans `backend.tools.registry`) ;
  3. le serveur conduit la boucle d'outils ; chaque outil est execute en Python
     par `backend.tools.registry.execute_tool`.

« general » n'est pas un agent a outils : c'est Neo lui-meme. Sa consigne est le
persona `config/neo_persona.md`, que le serveur lit directement.

Regle de coherence (verifiee par `incoherences()` et les tests) : un agent ne
cite dans sa consigne que des outils qu'il possede, et ne possede que des outils
qui existent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_CONSIGNES = Path(__file__).resolve().parent / "consignes"
_PERSONA = Path(__file__).resolve().parents[2] / "config" / "neo_persona.md"


def _lire(chemin: Path, defaut: str = "") -> str:
    try:
        return chemin.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
    except OSError:
        return defaut


def _consigne(agent_id: str) -> str:
    """Consigne d'un agent : `consignes/<id>.md`."""
    return _lire(_CONSIGNES / f"{agent_id}.md")


@dataclass
class Agent:
    id: str
    name: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    rag: bool = False
    model: str | None = None
    role: str | None = None


BUILTIN_AGENTS: list[Agent] = [
    Agent(
        id="general",
        name="Assistant Général",
        description="Conversation polyvalente sans outils",
        system_prompt=_lire(_PERSONA, "Tu es Néo, un assistant francophone qui tourne entièrement en local."),
    ),
    Agent(
        id="dev",
        name="Développeur",
        description="Code, fichiers et terminal avec permissions",
        system_prompt=_consigne("dev"),
        tools=["calculator", "get_datetime", "list_dir", "read_file", "write_file", "run_command",
               "check_code", "deep_research", "knowledge_search", "object_dimensions", "unit_convert",
               "memory_search", "memory_forget"],
    ),
    Agent(
        id="docs",
        name="Analyste Documents",
        description="Répond à partir de vos documents (RAG)",
        system_prompt=_consigne("docs"),
        tools=["calculator"],
        rag=True,
    ),
    Agent(
        id="image",
        name="Création d'images",
        description="Génération d'images 2D par IA (FLUX local, photoréaliste 4K)",
        system_prompt=_consigne("image"),
        tools=["generate_image", "edit_image", "analyze_image", "find_reference_image",
               "video_storyboard", "video_clip", "video_render", "image_search", "fetch_image",
               "web_search", "web_fetch", "calculator", "get_datetime", "envoyer_telegram"],
        role="agent_brain",
    ),
    Agent(
        id="web",
        name="Recherche web",
        description="Actualité et faits récents : cherche sur internet, lit les pages, répond avec ses sources",
        system_prompt=_consigne("web"),
        tools=["web_search", "web_fetch", "deep_research", "fiche_creer", "get_datetime", "calculator",
               "taux_change"],
        role="agent_brain",
    ),
    Agent(
        id="bureau",
        name="Bureau & Documents",
        description="Rédaction de documents professionnels : rapports, lettres, CV, notes, tableurs (docx/pdf/xlsx)",
        system_prompt=_consigne("bureau"),
        tools=["document_create", "write_file", "run_command", "check_code", "deep_research",
               "web_search", "calculator", "get_datetime"],
        role="agent_brain",
    ),
    Agent(
        id="systeme",
        name="Système & Maison",
        description=("Contrôle du PC et infos pratiques : météo, change, adresses, statistiques, volume, "
                     "applications, vision d'écran"),
        system_prompt=_consigne("systeme"),
        tools=["meteo", "taux_change", "adresse", "system_stats", "volume", "open_app", "capture_ecran",
               "web_search", "calculator", "get_datetime"],
        role="agent_brain",
    ),
    Agent(
        id="cad3d",
        name="Conception 3D",
        description="Objets 3D autonomes : formes organiques par IA (TripoSR) + paramétrique OpenSCAD → STL → G-code",
        system_prompt=_consigne("cad3d"),
        tools=["calculator", "get_datetime", "list_dir", "read_file", "write_file", "run_command",
               "render_scad", "slice_model", "pipeline_3d", "measure_model", "check_printability",
               "export_3mf", "slicer_open", "slicers_list", "units_scale", "unit_convert",
               "object_dimensions", "knowledge_search", "mesh_info", "repair_mesh", "image_search",
               "fetch_image", "analyze_image", "generate_image", "render_preview", "validate_design",
               "find_reference_image", "generate_mesh_from_text", "generate_mesh_from_image",
               "mesh_boolean", "fuse_meshes",
               "render_stl_views", "validate_mesh", "blender_run", "blender_render"],
        role="agent_brain",
    ),
    Agent(
        id="depot",
        name="Dépôt & Git",
        description="Gestion du dépôt de code : lecture/écriture, index, diff, rollback, tests",
        system_prompt=_consigne("depot"),
        tools=["file_search", "git_diff", "git_rollback", "repo_index", "run_tests", "read_file",
               "write_file", "code_edit", "check_code"],
        role="agent_brain",
    ),
]


def get_agent(agent_id: str | None) -> Agent | None:
    if not agent_id:
        return None
    return next((a for a in BUILTIN_AGENTS if a.id == agent_id), None)


def list_agents() -> list[dict]:
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "tools": a.tools,
            "rag": a.rag,
        }
        for a in BUILTIN_AGENTS
    ]


# Outils cites dans une consigne sans que l'agent les possede, a dessein :
# mises en garde (« ne touche jamais aux outils 3D ») ou mots courants qui sont
# aussi des noms d'outils (« notes », « livre »).
_CITATIONS_ADMISES = {
    "image": {"generate_mesh_from_image"},
    "bureau": {"notes"},
    "cad3d": {"livre"},
}


def incoherences() -> list[str]:
    """Defauts de la table des agents ; liste vide si tout est coherent."""
    from backend.tools.registry import TOOL_DEFINITIONS

    connus = set(TOOL_DEFINITIONS)
    defauts: list[str] = []
    vus: set[str] = set()
    for a in BUILTIN_AGENTS:
        if a.id in vus:
            defauts.append(f"{a.id} : identifiant en double")
        vus.add(a.id)
        if not a.system_prompt.strip():
            defauts.append(f"{a.id} : consigne vide (consignes/{a.id}.md manquant ?)")
        for outil in a.tools:
            if outil not in connus:
                defauts.append(f"{a.id} : outil inconnu « {outil} »")
        if len(set(a.tools)) != len(a.tools):
            defauts.append(f"{a.id} : outil liste deux fois")
        if a.id == "general":
            continue
        cites = {n for n in connus if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", a.system_prompt)}
        for outil in sorted(cites - set(a.tools) - _CITATIONS_ADMISES.get(a.id, set())):
            defauts.append(f"{a.id} : la consigne cite « {outil} », que l'agent n'a pas")
    return defauts
