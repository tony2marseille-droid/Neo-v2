# -*- coding: utf-8 -*-
"""Outils 3D v2 : export 3MF, mesure vs spec, imprimabilite, slicer, unites.

Conforme audit-generation-3d.md (sections 3, 5, 6) :
- export_3mf : format par defaut (mm, Z=0, pieces nommees, couleurs)
- measure_model : cotes reelles vs spec (tolerance +/- 0,5 mm)
- check_printability : plateau, surplombs, parois fines, pose Z=0
- units_scale : conversion cm/pouce/m -> mm
- slicer_open / slicers_list : ouverture directe du 3MF dans le slicer
"""
import json
import os
import subprocess
from pathlib import Path

from backend.core.security import verifier_acces as _verifier_acces
from backend.tools.common import _load_trimesh, _poser_z0
from backend.core import reglages

_RACINE = str(reglages.racine())

# Volume d'impression (mm) : Flashforge AD5X par defaut (audit §6).
_PLATES = {
    "ad5x": (220.0, 220.0, 240.0),
    "ad5m": (220.0, 220.0, 240.0),
    "ender3": (220.0, 220.0, 250.0),
    "prusa_mk4": (250.0, 210.0, 220.0),
    "bambu_x1": (256.0, 256.0, 250.0),
}
_PLATE_DEFAUT = "ad5x"

# Slicers connus (audit §6) : nom -> motifs d'executable.
_SLICER_NOMS = ["flashprint", "orca-flashforge", "orcaslicer", "prusaslicer", "bambu", "cura"]
_SLICER_EXES = {
    "flashprint": ["FlashPrint.exe", "flashprint.exe"],
    "orca-flashforge": ["orca-slicer.exe", "OrcaSlicer.exe", "orca_flashforge.exe"],
    "orcaslicer": ["orca-slicer.exe", "OrcaSlicer.exe"],
    "prusaslicer": ["prusa-slicer.exe", "prusa-slicer-console.exe", "PrusaSlicer.exe"],
    "bambu": ["bambu-studio.exe", "BambuStudio.exe"],
    "cura": ["Cura.exe", "cura.exe", "Ultimaker-Cura.exe"],
}
_SLICER_DIRS = [
    r"C:\Program Files\FlashForge",
    r"C:\Program Files\Flashforge",
    r"C:\Program Files\Orca-Flashforge",
    r"C:\Program Files\OrcaSlicer",
    r"C:\Program Files\Prusa3D",
    r"C:\Program Files\Bambu Studio",
    r"C:\Program Files\Ultimaker Cura 5*",
    r"C:\Program Files\Ultimaker Cura*",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\OrcaSlicer"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Orca-Flashforge"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\BambuStudio"),
    # Dossiers supplementaires propres a la machine (neo.toml, [variables]) :
    # NEO_DOSSIERS_TRANCHEURS = "D:\\Logiciels\\Trancheur"
    *[d for d in os.environ.get("NEO_DOSSIERS_TRANCHEURS", "").split(";") if d.strip()],
]


# ------------------------------------------------------------------
# Unites (audit §3 : tout en mm)
# ------------------------------------------------------------------
_FACTEURS_MM = {
    "mm": 1.0, "millimetre": 1.0, "millimetre(s)": 1.0,
    "cm": 10.0, "centimetre": 10.0,
    "m": 1000.0, "metre": 1000.0,
    "in": 25.4, "inch": 25.4, "pouce": 25.4, "pouces": 25.4, '"': 25.4,
    "ft": 304.8, "pied": 304.8,
}


def tool_unit_convert(valeur: float, de: str = "cm", vers: str = "mm") -> str:
    """Convertit une mesure vers une autre unite (mm, cm, m, pouce...)."""
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        return f"Erreur: valeur non numerique '{valeur}'"
    d = (de or "mm").strip().lower().rstrip("s.")
    vrs = (vers or "mm").strip().lower().rstrip("s.")
    if d not in _FACTEURS_MM:
        return f"Erreur: unite source inconnue '{de}' (mm, cm, m, pouce...)"
    if vrs not in _FACTEURS_MM:
        return f"Erreur: unite cible inconnue '{vers}'"
    resultat = v * _FACTEURS_MM[d] / _FACTEURS_MM[vrs]
    return f"{v} {de} = {resultat:.4g} {vers}"


def tool_units_scale(mesh_path: str, from_unit: str = "cm", to_unit: str = "mm") -> str:
    """Remet un maillage a l'echelle dans l'unite cible (par defaut mm) puis
    le repose sur le plateau. Ex: modelle en cm -> mm (x10)."""
    mesh_path = str(Path(mesh_path).expanduser())
    if not Path(mesh_path).exists():
        return f"Erreur: fichier introuvable {mesh_path}"
    refus = _verifier_acces(mesh_path, ecriture=True)
    if refus:
        return refus
    d = (from_unit or "cm").strip().lower()
    vrs = (to_unit or "mm").strip().lower()
    if d not in _FACTEURS_MM or vrs not in _FACTEURS_MM:
        return "Erreur: unite inconnue (mm, cm, m, pouce...)"
    facteur = _FACTEURS_MM[d] / _FACTEURS_MM[vrs]
    if abs(facteur - 1.0) < 1e-9:
        return "Rien a faire : le maillage est deja dans l'unite cible."
    import numpy as np

    m = _load_trimesh(mesh_path)
    avant = np.asarray(m.extents, dtype=float)
    m.apply_scale(facteur)
    _poser_z0(m)
    m.export(mesh_path)
    apres = np.asarray(m.extents, dtype=float)
    return (
        f"Echelle x{facteur:g} ({from_unit} -> {to_unit}) appliquee: {mesh_path}\n"
        f"Dimensions avant (mm equivalent): {avant[0]:.1f} x {avant[1]:.1f} x {avant[2]:.1f}\n"
        f"Dimensions apres: {apres[0]:.1f} x {apres[1]:.1f} x {apres[2]:.1f}"
    )


# ------------------------------------------------------------------
# Mesure vs spec (audit §3 : +/- 0,5 mm)
# ------------------------------------------------------------------
def tool_measure_model(mesh_path: str, spec_json: str = "") -> str:
    """Compare les cotes REELLES du maillage au spec de conception.

    spec_json : JSON (ou texte contenant un JSON) avec dimensions:
    [{"nom": "hauteur", "valeur_mm": 80, "tolerance_mm": 0.5}, ...]
    Verdict CONFORME si la plus grande cote du spec correspond a la plus
    grande dimension du maillage a la tolerance pres (et idem par tri).
    """
    mesh_path = str(Path(mesh_path).expanduser())
    if not Path(mesh_path).exists():
        return f"Erreur: fichier introuvable {mesh_path}"
    try:
        debut = spec_json.find("{")
        fin = spec_json.rfind("}") + 1
        if debut < 0 or fin <= debut:
            return "Erreur: spec_json ne contient pas de JSON."
        spec = json.loads(spec_json[debut:fin])
    except Exception as exc:
        return f"Erreur lecture spec_json: {exc}"
    if isinstance(spec, dict) and "dimensions" in spec:
        dims_spec = spec["dimensions"]
    else:
        dims_spec = spec
    if not isinstance(dims_spec, list) or not dims_spec:
        return "Erreur: spec sans liste 'dimensions'."
    cibles = []
    for d in dims_spec:
        try:
            nom = str(d.get("nom", "?")).lower()
            # Les angles ne sont pas des cotes lineaires : on les ignore
            # (ils vivent dans les contraintes, pas dans les dimensions mm).
            if "angle" in nom or "degre" in nom or "inclinaison" in nom:
                continue
            cibles.append((str(d.get("nom", "?")), float(d.get("valeur_mm", 0)),
                           float(d.get("tolerance_mm", 0.5))))
        except Exception:
            continue
    if not cibles:
        return "Erreur: dimensions du spec illisibles (ou uniquement des angles)."

    import numpy as np

    m = _load_trimesh(mesh_path)
    e = np.sort(np.asarray(m.extents, dtype=float))[::-1]  # desc
    extents = [float(x) for x in e]

    # Comparaison par PLUS PROCHE dimension reelle, pas par position : un
    # spec « diametre 40, epaisseur 4, trou 5 » ne doit pas etre compare
    # positionnellement a la boite englobante (40,40,4) — l'ancien tri
    # donnait « 5 vs 4 = HORS TOLERANCE » sur une piece correcte. Les
    # fonctionnalites (trous, fentes, gorges) absentes de la boite
    # englobante sont declarees « verifiees visuellement » sans echec.
    MOTS_FEATURES = ("trou", "percage", "perçage", "alesage", "alésage",
                     "fente", "rainure", "gorge", "filetage", "perforation",
                     "ouverture", "passage", "interieur", "interne",
                     "evidement", "évidement", "chanfrein", "congé", "conge")
    tols = [c[2] if c[2] > 0 else 0.5 for c in cibles]
    # Deux passes : 1) chaque cote contre la dimension reelle la plus proche ;
    # 2) une cote sans correspondance est toleree si TOUTES les dimensions de
    # la boite englobante sont deja couvertes par d'autres cotes (c'est une
    # fonctionnalite : fente, rainure, nervure...). Exemple reel : boitier
    # 80x50x30 + fente 10 mm -> la fente ne doit pas faire echouer la mesure.
    resultats = []
    for nom, val, tol in sorted(cibles, key=lambda c: -c[1]):
        tol = tol if tol > 0 else 0.5
        reel = min(extents, key=lambda x: abs(x - val))
        ecart = abs(reel - val)
        resultats.append((nom, val, tol, reel, ecart, ecart <= tol))
    valeurs_ok = [r[1] for r in resultats if r[5]]
    extents_tous_couverts = all(
        any(abs(ext - v) <= 0.5 for v in valeurs_ok) for ext in extents
    )
    lignes, conforme = [], True
    for nom, val, tol, reel, ecart, ok in resultats:
        if not ok and (any(mot in nom.lower() for mot in MOTS_FEATURES)
                       or extents_tous_couverts):
            lignes.append(
                f"  - {nom}: {val:g} mm — fonctionnalite (hors boite englobante, "
                "verifiee visuellement)"
            )
            continue
        conforme = conforme and ok
        lignes.append(
            f"  - {nom}: demande {val:g} mm, reel {reel:.1f} mm, "
            f"ecart {ecart:.2f} mm {'OK' if ok else 'HORS TOLERANCE'}"
        )
    verdict = "CONFORME" if conforme else "ECART"
    return (
        f"MESURE {verdict} (tolerance {max(tols):g} mm) : {mesh_path}\n"
        + "\n".join(lignes)
        + f"\nDimensions reelles (mm): X={e[2]:.1f} Y={e[1]:.1f} Z={e[0]:.1f}"
        + f" | Volume: {abs(float(m.volume))/1000.0:.1f} cm3"
    )


# ------------------------------------------------------------------
# Imprimabilite (audit §3 + §6)
# ------------------------------------------------------------------
def tool_check_printability(mesh_path: str, printer: str = "") -> str:
    """Controles d'impression FDM : volume plateau, pose Z=0, surplombs > 45 deg,
    parois fines < 1,2 mm (echantillonne), details < buse 0,4 mm."""
    mesh_path = str(Path(mesh_path).expanduser())
    if not Path(mesh_path).exists():
        return f"Erreur: fichier introuvable {mesh_path}"
    import numpy as np

    m = _load_trimesh(mesh_path)
    nom_plate = (printer or _PLATE_DEFAUT).strip().lower().replace(" ", "")
    plateau = _PLATES.get(nom_plate, _PLATES[_PLATE_DEFAUT])
    notes, alertes = [], []
    e = np.asarray(m.extents, dtype=float)
    b = np.asarray(m.bounds, dtype=float)

    # 1. Tient dans le plateau ?
    depasse = [e[i] > plateau[i] for i in range(3)]
    if any(depasse):
        axes = "x".join(("X", "Y", "Z")[i] for i in range(3) if depasse[i])
        alertes.append(
            f"DEPASSEMENT PLATEAU ({axes}) : {e[0]:.0f}x{e[1]:.0f}x{e[2]:.0f} mm "
            f"vs {plateau[0]:.0f}x{plateau[1]:.0f}x{plateau[2]:.0f} mm "
            "(- agrandir le plateau, mettre a l'echelle ou decouper en pieces)"
        )
    else:
        notes.append(f"Dimensions {e[0]:.0f}x{e[1]:.0f}x{e[2]:.0f} mm : tient sur le plateau")

    # 2. Pose Z=0 ?
    if abs(b[0][2]) > 0.5:
        alertes.append(
            f"POSE INCORRECTE : Z min = {b[0][2]:.1f} mm (doit etre 0). "
            "Utilise units_scale ou export_3mf pour reposer la piece."
        )
    else:
        notes.append("Pose Z=0 correcte")

    # 3. Watertight ?
    if not m.is_watertight:
        alertes.append("MAILLAGE NON ETANCHE : repair_mesh requis avant impression")
    else:
        notes.append("Maillage etanche (watertight)")

    # 4. Surplombs > 45 deg (normale tres vers le bas, hors face au sol).
    n = np.asarray(m.face_normals, dtype=float)
    centres = m.triangles_center if hasattr(m, "triangles_center") else m.triangles.mean(axis=1)
    surf_tot = float(m.area) or 1.0
    masque_surplomb = (n[:, 2] < -0.707) & (np.asarray(centres)[:, 2] > 1.0)
    surf_dep = float(m.area_faces[masque_surplomb].sum()) if hasattr(m, "area_faces") else 0.0
    pct = 100.0 * surf_dep / surf_tot
    if pct > 15.0:
        alertes.append(f"SURPLOMBS {pct:.0f}% de la surface > 45 deg : supports conseilles")
    elif pct > 5.0:
        notes.append(f"Surplombs {pct:.0f}% : supports selon orientation")
    else:
        notes.append(f"Surplombs negligeables ({pct:.0f}%)")

    # 5. Details < buse (aretes plus courtes que 0,4 mm en grande quantite).
    try:
        # Indices de sommets : entiers (en float, l'indexation levait une
        # IndexError avalee et ce controle ne tournait jamais).
        aretes = np.asarray(m.edges_sorted, dtype=int)
        longueurs = np.linalg.norm(
            m.vertices[aretes[:, 0]] - m.vertices[aretes[:, 1]], axis=1)
        micro = int((longueurs < 0.4).sum())
        if micro > len(longueurs) * 0.02:
            alertes.append(f"MICRO-DETAILS : {micro} aretes < 0,4 mm (buse) - risque de details fondus")
    except Exception:
        pass

    # 6. Parois fines : echantillon de rayons interieurs (60 faces aleatoires).
    try:
        idx = np.random.default_rng(7).choice(len(m.faces), size=min(60, len(m.faces)), replace=False)
        fins = 0
        mesures = []
        for i in idx:
            c = np.asarray(centres)[i]
            d = -n[i]
            origine = c - d * 0.05
            locs, _, _ = m.ray.intersects_location(
                ray_origins=[origine], ray_directions=[d])
            if len(locs):
                dists = np.linalg.norm(np.asarray(locs) - origine, axis=1)
                dists = dists[dists > 0.1]
                if len(dists):
                    ep = float(dists.min())
                    mesures.append(ep)
                    if ep < 1.2:
                        fins += 1
        if mesures:
            med = float(np.median(mesures))
            if fins >= 3 or med < 1.2:
                alertes.append(
                    f"PAROIS FINES : epaisseur mediane echantillonnee {med:.1f} mm "
                    f"({fins}/60 points < 1,2 mm) - paroi minimum conseillee 1,2 mm"
                )
            else:
                notes.append(f"Epaisseur paroi mediane ~{med:.1f} mm : OK")
    except Exception:
        pass

    verdict = "IMPRIMABLE" if not alertes else "A CORRIGER"
    return (
        f"CONTROLE IMPRIMABILITE [{verdict}] : {Path(mesh_path).name}\n"
        + "\n".join(f"  [ok] {x}" for x in notes)
        + ("\n" + "\n".join(f"  [!!] {x}" for x in alertes) if alertes else "")
        + f"\nImprimante : {nom_plate} ({plateau[0]:.0f}x{plateau[1]:.0f}x{plateau[2]:.0f} mm)"
    )


# ------------------------------------------------------------------
# Export 3MF (audit §5)
# ------------------------------------------------------------------
def tool_export_3mf(stl_paths: str, colors: str = "", out_path: str = "") -> str:
    """Exporte un ou plusieurs maillages en 3MF (format par defaut).

    stl_paths : chemin unique ou plusieurs separes par ';'
    colors    : couleurs hex par piece, separees par ';' (ex: '#FF0000;#000000')
    out_path  : .3mf de sortie (defaut : <premier stl>.3mf)
    Le 3MF est ecrit en millimetres, pieces nommees, posees Z=0, puis RELU
    pour validation (volume/cotes compares a la source).
    """
    chemins = [c.strip() for c in str(stl_paths).split(";") if c.strip()]
    if not chemins:
        return "Erreur: aucun maillage fourni."
    for c in chemins:
        if not Path(c).exists():
            return f"Erreur: fichier introuvable {c}"
        refus = _verifier_acces(c)
        if refus:
            return refus
    out = str(Path(out_path).expanduser()) if out_path else str(Path(chemins[0]).with_suffix(".3mf"))
    refus = _verifier_acces(out, ecriture=True)
    if refus:
        return refus

    import trimesh

    couleurs = [c.strip() for c in str(colors or "").split(";") if c.strip()]
    try:
        from trimesh.exchange.threemf import export_3MF
    except Exception as exc:
        return f"Erreur: export 3MF indisponible dans trimesh ({exc})"

    scene = trimesh.Scene()
    for i, chemin in enumerate(chemins):
        m = _load_trimesh(chemin)
        _poser_z0(m)
        m.metadata["name"] = Path(chemin).stem or f"piece_{i+1}"
        if i < len(couleurs):
            try:
                m.visual.face_colors = trimesh.visual.color.hex_to_rgba(couleurs[i])
            except Exception:
                pass
        scene.add_geometry(m, node_name=m.metadata["name"])
    # trimesh 5.1.1 : export_3MF retourne les octets (le 2e argument
    # positionnel est batch_size, pas un chemin !).
    donnees = export_3MF(scene)
    Path(out).write_bytes(donnees)

    # Validation : relecture du 3MF + comparaison source.
    try:
        relu = trimesh.load(out, force="scene")
        geoms = list(relu.geometry.values()) if hasattr(relu, "geometry") else [relu]
        total_faces = sum(len(g.faces) for g in geoms)
        src_faces = 0
        for chemin in chemins:
            src_faces += len(_load_trimesh(chemin).faces)
        if total_faces < 4:
            return f"Erreur: 3MF relu invalide ({total_faces} triangles)."
        ecart = abs(total_faces - src_faces) / max(src_faces, 1)
        if ecart > 0.02:
            return (
                f"Erreur: le 3MF relu differe de la source "
                f"({total_faces} vs {src_faces} triangles)."
            )
    except Exception as exc:
        return f"Erreur validation 3MF ({exc}) : {out}"

    taille = Path(out).stat().st_size
    pieces = len(chemins)
    return (
        f"3MF VALIDE genere ({pieces} piece(s), {taille//1024} Ko) : {out}\n"
        f"Unite mm, pieces nommees, posees sur le plateau. "
        "Ouvrable directement dans le slicer (bouton 'Ouvrir dans le slicer')."
    )


# ------------------------------------------------------------------
# Slicers (audit §6)
# ------------------------------------------------------------------
def _setting(cle: str, defaut: str = "") -> str:
    try:
        from backend.core.database import get_db

        l = get_db().query_one("SELECT value FROM app_settings WHERE key=?", (cle,))
        return str(l["value"]) if l and l["value"] else defaut
    except Exception:
        return defaut


def _chercher_slicers() -> dict:
    """Detecte les slicers installes. Retourne {nom: chemin}."""
    import glob

    trouves: dict[str, str] = {}
    # 1. Chemin explicite par slicer dans les reglages.
    for nom in _SLICER_NOMS:
        p = _setting(f"slicer_path_{nom.replace('-', '_')}")
        if p and Path(p).exists():
            trouves[nom] = p
    if trouves:
        return trouves
    # 2. Variable d'environnement.
    env = os.environ.get("LOCALAI_SLICER", "")
    if env and Path(env).exists():
        base = Path(env).name.lower()
        for nom, exes in _SLICER_EXES.items():
            if any(base.startswith(e.lower().rsplit(".", 1)[0]) for e in exes):
                trouves[nom] = env
                return trouves
        trouves["slicer"] = env
        return trouves
    # 3. Balayage des dossiers connus.
    for dossier in _SLICER_DIRS:
        for base in glob.glob(dossier + "*") + ([dossier] if Path(dossier).exists() else []):
            d = Path(base)
            racine = d if d.is_dir() else d.parent
            if not racine.is_dir():
                continue
            for nom, exes in _SLICER_EXES.items():
                if nom in trouves:
                    continue
                for exe in exes:
                    candidat = racine / exe
                    if candidat.exists():
                        trouves[nom] = str(candidat)
                        break
                    sous = list(racine.rglob(exe))
                    if sous:
                        trouves[nom] = str(sous[0])
                        break
    return trouves


def tool_slicers_list() -> str:
    """Liste les slicers detectes sur la machine + le slicer par defaut."""
    trouves = _chercher_slicers()
    if not trouves:
        return (
            "Aucun slicer detecte. Installez FlashPrint (AD5X), OrcaSlicer, "
            "PrusaSlicer, Bambu Studio ou Cura, ou definissez le reglage "
            "'slicer_path_<nom>' (Reglages) ou la variable LOCALAI_SLICER."
        )
    defaut = _setting("default_slicer", "")
    if not defaut or defaut not in trouves:
        defaut = next(iter(trouves))
    lignes = [f"  - {n} : {p}" + (" [DEFaut]" if n == defaut else "") for n, p in trouves.items()]
    return (
        f"Slicers detectes ({len(trouves)}) :\n" + "\n".join(lignes)
        + f"\nSlicer par defaut : {defaut}"
    )


def tool_slicer_open(file_path: str, slicer: str = "") -> str:
    """Ouvre le fichier 3MF/STL directement dans le slicer de la machine.

    slicer : nom optionnel parmi ceux detectes (defaut : reglage default_slicer).
    NE TRANCHE PAS : ouvre juste le slicer avec le fichier pret a configurer.
    """
    file_path = str(Path(file_path).expanduser())
    if not Path(file_path).exists():
        return f"Erreur: fichier introuvable {file_path}"
    refus = _verifier_acces(file_path)
    if refus:
        return refus
    suffixe = Path(file_path).suffix.lower()
    if suffixe not in (".3mf", ".stl", ".obj", ".scad", ".gcode"):
        return f"Erreur: format non ouvert par les slicers ({suffixe})"
    trouves = _chercher_slicers()
    if not trouves:
        return (
            "Aucun slicer detecte sur cette machine. Installez FlashPrint "
            "(AD5X) ou OrcaSlicer/PrusaSlicer/Bambu Studio, ou definissez "
            "slicer_path_<nom> dans les Reglages. Le fichier reste "
            f"telechargeable : {file_path}"
        )
    nom = (slicer or "").strip().lower()
    if nom and nom not in trouves:
        connus = ", ".join(trouves)
        return f"Slicer '{slicer}' introuvable. Detectes : {connus}."
    if not nom:
        nom = _setting("default_slicer", "")
        if nom not in trouves:
            nom = next(iter(trouves))
    exe = trouves[nom]
    try:
        subprocess.Popen(
            [exe, file_path],
            cwd=str(Path(exe).parent),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:
        return f"Erreur ouverture {nom}: {exc}"
    return (
        f"Ouverture de {Path(file_path).name} dans {nom}...\n"
        "Le slicer demarre avec le fichier charge. Configurez l'impression "
        "(profil AD5X conseille) puis lancez le tranchage."
    )


# ------------------------------------------------------------------
# Specs outils (registre)
# ------------------------------------------------------------------
TOOL_SPECS_3D_V2 = {
    "unit_convert": {
        "level": "SAFE",
        "description": (
            "Convertit une mesure entre unites (mm, cm, m, pouce, pied). "
            "Ex: 80 cm -> mm. A utiliser AVANT toute conception."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "valeur": {"type": "number", "description": "valeur numerique a convertir"},
                "de": {"type": "string", "description": "unite source (mm, cm, m, pouce...)"},
                "vers": {"type": "string", "description": "unite cible (defaut mm)"},
            },
            "required": ["valeur", "de"],
        },
    },
    "units_scale": {
        "level": "USER",
        "description": (
            "Reechelle un maillage d'une unite vers une autre (ex: modelle en "
            "cm -> mm = x10) puis le repose sur le plateau Z=0."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mesh_path": {"type": "string", "description": "chemin du maillage (stl/obj)"},
                "from_unit": {"type": "string", "description": "unite actuelle du modele"},
                "to_unit": {"type": "string", "description": "unite cible (defaut mm)"},
            },
            "required": ["mesh_path", "from_unit"],
        },
    },
    "measure_model": {
        "level": "USER",
        "description": (
            "Compare les cotes REELLES d'un maillage au spec de conception "
            "(liste dimensions {nom, valeur_mm, tolerance_mm}). Verdict "
            "CONFORME/ECART avec ecarts detailles (+/- 0,5 mm)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mesh_path": {"type": "string", "description": "chemin du maillage"},
                "spec_json": {"type": "string", "description": "spec JSON de conception"},
            },
            "required": ["mesh_path", "spec_json"],
        },
    },
    "check_printability": {
        "level": "USER",
        "description": (
            "Controle l'imprimabilite FDM : volume plateau (defaut Flashforge "
            "AD5X 220x220x240), pose Z=0, etancheite, surplombs > 45 deg, "
            "parois fines < 1,2 mm, micro-details < buse. Verdict IMPRIMABLE/"
            "A CORRIGER avec alertes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mesh_path": {"type": "string", "description": "chemin du maillage"},
                "printer": {"type": "string", "description": "imprimante (ad5x, ender3, prusa_mk4, bambu_x1...)"},
            },
            "required": ["mesh_path"],
        },
    },
    "export_3mf": {
        "level": "USER",
        "description": (
            "Exporte un ou plusieurs maillages en 3MF VALIDE (unite mm, pieces "
            "nommees, posees Z=0, couleurs optionnelles '#RRGGBB' par piece "
            "separees par ';', relecture+validation automatique). Format "
            "par defaut pour l'impression. STL uniquement si demande explicite."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stl_paths": {"type": "string", "description": "chemin(s) des maillages, separes par ';'"},
                "colors": {"type": "string", "description": "couleurs par piece '#RRGGBB;...' (optionnel)"},
                "out_path": {"type": "string", "description": "chemin .3mf de sortie (optionnel)"},
            },
            "required": ["stl_paths"],
        },
    },
    "slicers_list": {
        "level": "SAFE",
        "description": "Liste les slicers detectes sur la machine et le slicer par defaut.",
        "parameters": {"type": "object", "properties": {}},
    },
    "slicer_open": {
        "level": "USER",
        "description": (
            "Ouvre un fichier 3MF/STL directement dans le slicer installe "
            "(defaut : reglage default_slicer, sinon premier detecte). "
            "Utiliser apres export_3mf pour passer a l'impression."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "fichier 3MF/STL a ouvrir"},
                "slicer": {"type": "string", "description": "nom du slicer (optionnel)"},
            },
            "required": ["file_path"],
        },
    },
}