# -*- coding: utf-8 -*-
"""Pipeline 3D v2 : demande -> spec -> OpenSCAD -> STL -> CONTROLES -> QC vision
-> BOUCLE DE CORRECTION REELLE (max 3) -> 3MF + apercu + rapport.

Differences v1 -> v2 (audit-generation-3d.md) :
- Le verdict de chaque controle ALIMENTE la regeneration : erreur OpenSCAD,
  ecarts de cotes (measure_model), alertes d'imprimabilite et ecarts vision
  sont reinjectes dans le prompt de la tentative suivante.
- Export 3MF par defaut (pieces nommees, mm, Z=0, relecture validee) ;
  STL conserve en secondaire.
- Controles : mesh_info + measure_model (+/- 0,5 mm) + check_printability
  (plateau, surplombs, parois) + validate_mesh (4 vues, vision).
- Apercu PNG cote a cote (_apercu.png) affichable dans le chat.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

SERVEUR_OLLAMA = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
# Modele de code : choisi dynamiquement parmi les modeles REELLEMENT installes
# (l'ancien nom code en dur « oracle-prog-v9.4 » n'existait plus -> 404 et
# pipeline 3D mort au premier appel).
MODELE_CODE = os.environ.get("LOCALAI_3D_MODEL", "")
MAX_ITERATIONS = 3


def _modeles_installes() -> list[str]:
    """Liste des tags Ollama disponibles (vide si Ollama injoignable)."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{SERVEUR_OLLAMA}/api/tags", timeout=10) as rep:
            data = json.loads(rep.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def _choisir_modele_code() -> str:
    """Meilleur modele de code installe, par ordre de preference."""
    global MODELE_CODE
    if MODELE_CODE:
        return MODELE_CODE
    installes = _modeles_installes()
    for pref in (
        # Gemma d'abord : c'est le modele de conversation, deja en memoire —
        # pas d'echange de modeles sur la carte (10 a 20 s dans chaque sens).
        "gemma4:12b",
        "localai-prog-v10-q5km:latest",
        "localai-prog-v10:latest",
        "localai-prog-v9.4:latest",
        "localai-prog-latest:latest",
        "deepseek-coder-v2:16b",
        "qwen3:14b",
        "qwen3:8b",
    ):
        if pref in installes:
            MODELE_CODE = pref
            return MODELE_CODE
    # Repli : premier modele contenant « prog » ou « code ».
    for nom in installes:
        if "prog" in nom.lower() or "coder" in nom.lower():
            MODELE_CODE = nom
            return MODELE_CODE
    MODELE_CODE = installes[0] if installes else "qwen3:8b"
    return MODELE_CODE


def _log(msg):
    print(f"[pipeline_3d] {msg}", flush=True)


def _ollama_chat(model, prompt, temperature=0.2, num_predict=1500, think=False):
    """Appel Ollama. think=False par defaut : le mode raisonnement des modeles
    Qwen3 noie le code dans de longues reflexions tronquees ; on veut une
    reponse DIRECTE (code/spec), pas un raisonnement."""
    import urllib.request

    options = {"temperature": temperature, "num_predict": num_predict}
    try:
        # Meme contexte que la conversation : sinon Ollama recharge le modele.
        from backend.core.config import contexte_pour

        options["num_ctx"] = contexte_pour(model, 8192)
    except Exception:
        pass
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": bool(think),
        "options": options,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVEUR_OLLAMA}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as rep:
            data = json.loads(rep.read().decode("utf-8"))
    except Exception as exc:  # modele absent, Ollama occupe, timeout...
        _log(f"Ollama indisponible ({model}) : {exc}")
        return ""
    msg = data.get("message", {})
    content = msg.get("content", "") or ""
    thinking = msg.get("thinking", "") or ""
    if not content.strip() and thinking.strip():
        content = thinking
    return content + "\n" + thinking if thinking.strip() else content


def _extraire_bloc_code(texte, langage="scad"):
    """Extrait le premier bloc ```langage ... ``` (ou fallback primitives)."""
    if not texte or not texte.strip():
        return ""
    debut = texte.find(f"```{langage}")
    if debut != -1:
        debut = texte.find("\n", debut) + 1
        fin = texte.find("```", debut)
        if fin != -1:
            return texte[debut:fin].strip()
    debut = texte.find("```")
    if debut != -1:
        debut = texte.find("\n", debut) + 1
        fin = texte.find("```", debut)
        if fin != -1:
            return texte[debut:fin].strip()
    # Fallback STRICT : uniquement des lignes qui ressemblent a du vrai code
    # OpenSCAD (appel avec parentheses + point-virgule/accolade, ou mot-cle
    # structural en fin de ligne). Evite de confondre du texte de raisonnement
    # ("$fn=48 for the resolution...") avec du code.
    import re as _re

    lignes = []
    for l in texte.splitlines():
        ls = l.strip()
        if _re.search(r"\b(cube|cylinder|sphere|translate|rotate|scale|color|hull|"
                      r"linear_extrude|rotate_extrude|text|offset|polygon|circle|square)\s*\(", ls) \
                and (ls.endswith(";") or ls.endswith("{") or ls.endswith("(")) \
                and not ls.endswith((".", "?", "!")):
            lignes.append(ls)
        elif _re.match(r"^(module|function|difference|union|intersection|hull|"
                       r"minkowski|import|include)\b.*\{?\s*$", ls) \
                and not ls.endswith((".", "?", "!")):
            lignes.append(ls)
        elif ls.startswith("$fn") or ls.startswith("$fa") or ls.startswith("$fs"):
            lignes.append(ls)
    if len(lignes) >= 3:  # un vrai script, pas une coïncidence
        return "\n".join(lignes).strip()
    return ""


def _ecrire_spec(dossier, request_text, spec):
    spec_path = Path(dossier) / "spec.json"
    spec_path.write_text(
        json.dumps({"request": request_text, "spec": spec, "date": time.strftime("%Y-%m-%d %H:%M")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(spec_path)


# Cles connues d'une spec : sert a reparer le JSON « style Python » que les
# modeles produisent parfois ({"nom","diametre","valeur_mm",40} au lieu de
# {"nom":"diametre","valeur_mm":40}) — cause du repli silencieux « 80 mm ».
_CLES_SPEC = ("nom_piece", "dimensions", "contraintes", "notes_fabrication",
              "valeur_mm", "tolerance_mm", "unite", "couleur", "type", "nom")


def _json_souple(texte: str):
    """JSON de spec tolerant : fences, JSON invalide « cle,valeur », suites
    de plusieurs valeurs JSON (constate : le modele colle
    [{dims}], ["inclinaison 60 degres"], "mm", "#FF0000" ...). On decode la
    PREMIERE valeur JSON complete a partir de chaque '[' ou '{'."""
    if not texte:
        return None
    import re as _re

    brut = texte.strip()
    if brut.startswith("```"):
        brut = _re.sub(r"^```[a-zA-Z]*\s*", "", brut)
        brut = _re.sub(r"\s*```$", "", brut)
    decodeur = json.JSONDecoder()
    donnees = []
    for base in (brut, _reparer_json_spec(brut)):
        try:
            donnees.append(json.loads(base))
            continue
        except Exception:
            pass
        for m in _re.finditer(r"[\[{]", base):
            try:
                data, _ = decodeur.raw_decode(base[m.start():])
            except Exception:
                continue
            donnees.append(data)
            break
    for data in donnees:
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            # Cas 1 : [spec] ; cas 2 : liste de dimensions brute.
            for x in data:
                if isinstance(x, dict) and isinstance(x.get("dimensions"), list):
                    return x
            dims = [x for x in data
                    if isinstance(x, dict) and "valeur_mm" in x]
            if dims:
                return {"dimensions": dims}
    return None


def _reparer_json_spec(texte: str) -> str:
    """Insere les ':' manquants apres les cles connues ("cle", -> "cle": )."""
    import re as _re

    sortie = texte
    for cle in _CLES_SPEC:
        sortie = _re.sub(rf'"{cle}"\s*,\s*', f'"{cle}": ', sortie)
    return sortie


_MOTS_VIDES = ("de", "du", "des", "la", "le", "les", "un", "une", "avec",
               "et", "en", "au", "aux", "sur", "par", "pour", "mais", "ou")


def _extraire_cotes_texte(texte: str) -> list[dict]:
    """Cotes explicites « N mm » de la demande, avec le nom qui les precede.

    Filet de securite mecanique : meme si le LLM rend un JSON inutilisable,
    les cotes ECRITES par l'utilisateur ne sont jamais perdues.
    """
    import re as _re

    cotes: list[dict] = []
    for m in _re.finditer(r"(\d+(?:[.,]\d+)?)\s*mm\b", texte or "", _re.I):
        valeur = float(m.group(1).replace(",", "."))
        # Nom : d'abord ce qui SUIT la cote (« 40 mm de diametre » ->
        # diametre, « 4 mm d'epaisseur » -> epaisseur), sinon ce qui la
        # precede (« trou central de 5 mm » -> trou central).
        apres = (texte or "")[m.end():m.end() + 40]
        m_nom = _re.match(r"\s*(?:de\s+|d['’]\s*)?([A-Za-zÀ-ÿ]{3,})", apres, _re.I)
        mot_apres = (m_nom.group(1).lower() if m_nom else "")
        if mot_apres and mot_apres not in ("de", "des", "du", "la", "le", "les",
                                           "un", "une", "par", "sur", "avec"):
            nom = mot_apres
        else:
            avant = (texte or "")[:m.start()].strip(" ,;:.")
            mots = [x for x in _re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'-]{1,}", avant)
                    if x.lower() not in _MOTS_VIDES
                    and not x.lower().startswith(("d'", "l'"))]
            nom = " ".join(mots[-2:]) if mots else "cote"
        cotes.append({"nom": nom, "valeur_mm": valeur, "tolerance_mm": 0.5})
    # Dedoublonnage par valeur (garde le premier nom rencontre).
    vus: set[float] = set()
    uniques = []
    for c in cotes:
        cle = round(c["valeur_mm"], 3)
        if cle not in vus:
            vus.add(cle)
            uniques.append(c)
    return uniques


def _extraire_angles_texte(texte: str) -> list[str]:
    """Angles explicites (« incline a 60 degres », « 45° ») : les angles ne
    vont PAS dans 'dimensions' (mm) mais dans 'contraintes'."""
    import re as _re

    trouves = []
    for m in _re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:degres|degrés|°|degrees)\b",
                          texte or "", _re.I):
        angle = m.group(1).replace(",", ".")
        trouves.append(f"angle/inclinaison {angle} degres")
    return trouves


def _generer_spec_llm(request_text):
    """Spec JSON parametrique. Tolerant au JSON invalide du modele et aux
    cotes en texte libre : les « N mm » ecrits par l'utilisateur sont
    toujours conserves (audit §8 : jamais de cote imposee en silence)."""
    prompt = (
        "Tu es un ingenieur mecanique. Convertis cette demande en spec JSON pour "
        "conception 3D parametrique (OpenSCAD). Reponds UNIQUEMENT le JSON brut, "
        "sans texte autour.\n"
        "Champs : nom_piece, type (mecanique | organique | hybride), "
        "dimensions (liste de {nom, valeur_mm, tolerance_mm}), "
        "contraintes (liste de texte), unite (toujours mm), "
        "couleur (hex optionnel, ex: #FF0000), "
        "notes_fabrication (texte).\n"
        "REGLES :\n"
        "1. REPRENDS LES COTES ET ANGLES EXACTEMENT COMME DEMANDES par "
        "l'utilisateur (60 degres = 60, jamais une autre valeur).\n"
        "1bis. CHAQUE cote « N mm » de la demande DOIT apparaitre dans "
        "'dimensions' (ex: diametre du disque, epaisseur, diametre du trou), "
        "avec le nom de la fonction correspondante.\n"
        "2. Les ANGLES ne vont PAS dans 'dimensions' (qui ne contient que des "
        "longueurs en mm) : mets-les dans 'contraintes' (ex: 'inclinaison 60 degres').\n"
        "3. Si une cote n'est pas precisee, utilise une valeur REALISTE "
        "typique de l'objet (ex: telephone ~78 mm de long) et mentionne-la "
        "dans notes_fabrication.\n"
        "4. JSON STRICT : guillemets et deux-points ({\"nom\": \"diametre\", "
        "\"valeur_mm\": 40}), jamais {\"nom\",\"diametre\",\"valeur_mm\",40}.\n"
        f"Demande : {request_text}\n\nJSON :"
    )
    rep = _ollama_chat(_choisir_modele_code(), prompt, temperature=0.2, num_predict=1200)
    spec = _json_souple(rep)
    cotes_texte = _extraire_cotes_texte(request_text)
    if isinstance(spec, dict) and isinstance(spec.get("dimensions"), list) \
            and spec.get("dimensions"):
        dims = [d for d in spec["dimensions"] if isinstance(d, dict)]
        vues = {round(float(d.get("valeur_mm", 0) or 0), 2) for d in dims}
        for c in cotes_texte:
            if round(c["valeur_mm"], 2) not in vues:
                dims.append(c)
                vues.add(round(c["valeur_mm"], 2))
        spec["dimensions"] = dims
        # Le modele ne renvoie parfois que les dimensions : completer les
        # champs attendus par le rapport et le prompt du script.
        spec.setdefault("nom_piece", "piece")
        spec.setdefault("type", "mecanique")
        contraintes = spec.get("contraintes")
        if not isinstance(contraintes, list):
            contraintes = [f"Conforme a la demande : {request_text[:100]}"]
        for angle in _extraire_angles_texte(request_text):
            if not any(angle.split()[-2:] == c.split()[-2:] for c in contraintes
                       if isinstance(c, str)):
                contraintes.append(angle)
        spec["contraintes"] = contraintes
        spec.setdefault("unite", "mm")
        spec.setdefault("couleur", "")
        spec.setdefault("notes_fabrication", "Impression FDM, pose a plat.")
        return spec
    # Repli mecanique : uniquement les cotes ECRITES par l'utilisateur.
    # Sans cote explicite, on n'impose rien (le script LLM choisira des
    # proportions typiques, annoncees dans les notes).
    return {
        "nom_piece": "piece",
        "type": "mecanique",
        "dimensions": cotes_texte,
        "contraintes": ([f"Conforme a la demande : {request_text[:100]}"]
                        + _extraire_angles_texte(request_text)),
        "unite": "mm",
        "couleur": "",
        "notes_fabrication": ("Impression FDM, pose a plat."
                              if cotes_texte else
                              "Cotes non precisees : proportions typiques choisies par le modele."),
    }


def _generer_script_3d(request_text, spec, dossier, tentative, retours=""):
    """Script OpenSCAD via LLM. Les ECHECS PRECEDENTS sont reinjectes dans le
    prompt : c'est le coeur de la boucle de correction (v2)."""
    # Prompt valide par experimentation : les regles ABSOLUES + un exemple
    # de style evitent au modele d'ecrire du pseudo-SCAD fonctionnel
    # (x = cube(...), union avec virgules) qui ne compile jamais.
    prompt = (
        "Tu es un expert OpenSCAD. Ecris un script .scad qui produit une piece 3D "
        "imprimable (watertight, posee sur Z=0, centree en XY, dimensions en mm).\n"
        f"DEMANDE : {request_text}\n"
        f"SPEC : {json.dumps(spec, ensure_ascii=False)}\n\n"
        "SYNTAXE OPENSCAD - REGLES ABSOLUES :\n"
        "1. OpenSCAD n'est PAS un langage d'expressions : on N'ASSIGNE JAMAIS "
        "une forme a une variable (interdit : x = cube(...) ou x = translate(...) * y).\n"
        "2. Les primitives s'utilisent DIRECTEMENT comme blocs enfants :\n"
        "   difference() { cube([80,50,30]); translate([35,-1,12]) cube([10,52,8]); }\n"
        "3. Les enfants de union()/difference()/translate() sont separes par des "
        "espaces ou des retours a la ligne, JAMAIS par des virgules.\n"
        "4. Les operations se combinent par IMBRICATION, pas par operateurs (*, +).\n"
        "5. Les variables ne contiennent que des NOMBRES (dimensions, angles).\n"
        "6. $fn = 48 pour les courbes.\n"
        "7. La piece est POSEE sur Z=0 (face inferieure au sol), parois >= 1,2 mm.\n"
        "8. REPRENDS LES COTES EXACTES du SPEC.\n\n"
        "EXEMPLE DE STYLE VALIDE (boitier avec fente) :\n"
        "```scad\n"
        "$fn = 48;\n"
        "// Boitier 80x50x30 avec fente 10 mm de large\n"
        "difference() {\n"
        "    // Corps plein\n"
        "    cube([80, 50, 30]);\n"
        "    // Fente traversante de 10 mm\n"
        "    translate([35, -1, 12])\n"
        "        cube([10, 52, 8]);\n"
        "}```\n"
    )
    if retours:
        prompt += (
            f"\nTENTATIVE {tentative}/{MAX_ITERATIONS}. LES CONTROLES PRECEDENTS "
            f"ONT ECHOUE. Corrige PRECISEMENT ces problemes :\n{retours}\n"
        )
    else:
        prompt += f"\nTentative {tentative}/{MAX_ITERATIONS}.\n"
    prompt += (
        "\nREPONDS UNIQUEMENT avec un bloc ```scad ... ``` contenant le code "
        "complet. Aucun texte avant ni apres le bloc."
    )
    rep = _ollama_chat(MODELE_CODE, prompt, temperature=0.2, num_predict=2000)
    return _extraire_bloc_code(rep, "scad")


def pipeline_3d(request_text: str, save_dir: str = "", printer: str = "",
                couleur: str = "") -> str:
    """Pipeline 3D v2 complet : spec -> OpenSCAD -> STL -> controles ->
    QC vision -> BOUCLE DE CORRECTION (max 3) -> 3MF + apercu + rapport."""
    from backend.tools.registry import execute_tool, PermissionManager

    perms = PermissionManager("POWER")
    if not save_dir:
        save_dir = str(
            Path(os.environ.get("LOCALAI_ROOT", r"E:\ia")) /
            "workspace" / "pipeline_3d" / time.strftime("%Y%m%d_%H%M%S")
        )
    dossier = Path(save_dir)
    dossier.mkdir(parents=True, exist_ok=True)

    lignes = [f"=== PIPELINE 3D v2 : {request_text} ===",
              f"Dossier : {dossier}", ""]

    # ---- 0. Gabarit : un objet courant a sa geometrie toute prete ----
    gabarit = None
    if os.environ.get("NEO_3D_GABARITS", "1").strip().lower() not in ("0", "off", "non", "false"):
        from backend.tools import gabarits_3d

        _log("Gabarit : la demande correspond-elle a un objet connu ?")
        gabarit = gabarits_3d.choisir(
            request_text,
            lambda consigne: _ollama_chat(_choisir_modele_code(), consigne, temperature=0.1, num_predict=300))

    # ---- 1. Spec ----
    if gabarit:
        spec = {"nom_piece": gabarit["nom"], "type": "mecanique", "dimensions": [],
                "gabarit": gabarit["nom"], "parametres": gabarit["parametres"],
                "contraintes": [f"Conforme a la demande : {request_text[:100]}"],
                "unite": "mm", "couleur": "", "notes_fabrication": "Impression FDM, pose a plat."}
    else:
        _log("1/7 Spec parametrique (LLM)...")
        spec = _generer_spec_llm(request_text)
    spec_path = _ecrire_spec(dossier, request_text, spec)
    if not couleur and spec.get("couleur"):
        couleur = str(spec.get("couleur", ""))
    lignes.append(f"[1/7] Spec : {spec_path}")
    lignes.append(f"      Piece : {spec.get('nom_piece','?')} | Type : {spec.get('type','?')}")
    if gabarit:
        lignes.append("      Gabarit Neo « " + gabarit["nom"] + " » : "
                      + ", ".join(f"{c} = {v:g}" if isinstance(v, float) else f"{c} = {v}"
                                  for c, v in gabarit["parametres"].items()))
    for d in spec.get("dimensions", [])[:6]:
        lignes.append(f"      - {d.get('nom','?')} : {d.get('valeur_mm','?')} mm (±{d.get('tolerance_mm',0.5)} mm)")
    notes = spec.get("notes_fabrication", "")
    if notes:
        lignes.append(f"      Notes : {notes[:140]}")
    lignes.append("")

    stl_final = ""
    verdict_final = "NON"
    rapport_controles = []
    dernier_retour = ""
    # Meilleur essai si aucun n'est conforme : score = verdict vision OUI,
    # + mesure conforme, + note de forme. Evite d'exporter le DERNIER essai
    # quand il est moins bon que les precedents (constate : t3 pire que t1).
    meilleur_score = -1
    stl_meilleur = ""
    iter_meilleure = 0
    rapport_meilleur: list[str] = []

    for tentative in range(1, MAX_ITERATIONS + 1):
        _log(f"Iteration {tentative}/{MAX_ITERATIONS} : script + construction...")
        lignes.append(f"--- ITERATION {tentative}/{MAX_ITERATIONS} ---")

        # 2. Script : celui du gabarit au premier essai, sinon ecrit par le
        # modele (avec les retours des echecs precedents).
        par_gabarit = bool(gabarit) and tentative == 1
        if par_gabarit:
            code = gabarit["scad"]
        else:
            code = _generer_script_3d(request_text, spec, dossier, tentative, dernier_retour)
        if not code:
            dernier_retour = "Le script extrait etait VIDE : reponds uniquement par le bloc ```scad```."
            lignes.append("  [script] ECHEC : reponse sans code exploitable.")
            continue
        scad_path = dossier / f"piece_t{tentative}.scad"
        scad_path.write_text(code, encoding="utf-8")
        lignes.append(f"  [script] {scad_path}")

        # 3. Rendu OpenSCAD -> STL
        stl_path = str(dossier / f"piece_t{tentative}.stl")
        res_render = execute_tool("render_scad", {"scad_path": str(scad_path), "stl_path": stl_path}, perms)
        # Verite terrain : le rendu a reussi si et seulement si le STL existe.
        if not Path(stl_path).exists():
            dernier_retour = (
                "Le rendu OpenSCAD a ECHOUE avec ce script. Resultat exact : "
                f"{res_render[:600]}"
            )
            lignes.append(f"  [rendu] ECHEC : {res_render[:200]}")
            continue
        lignes.append(f"  [rendu] {res_render}")
        # Repose systematique sur le plateau AVANT les controles : le 3MF
        # final est de toute facon repose (export_3mf), et un Z<0 ferait
        # echouer la pose et le QC pour rien (constate : support incline
        # genere a Z=-69 mm puis valide 3 fois en boucle).
        try:
            from backend.tools.common import _load_trimesh, _poser_z0

            _m = _load_trimesh(stl_path)
            if abs(float(_m.bounds[0][2])) > 0.01:
                _poser_z0(_m)
                _m.export(stl_path)
                lignes.append("  [pose] piece reposee sur Z=0 (centre XY)")
        except Exception as _exc:
            lignes.append(f"  [pose] non appliquee : {_exc}")
        stl_final = stl_path

        # 4. Controles : etancheite + mesure + imprimabilite
        retours_echec = []
        info = execute_tool("mesh_info", {"mesh_path": stl_path}, perms)
        if "Watertight: False" in info:
            rep = execute_tool("repair_mesh", {"mesh_path": stl_path}, perms)
            lignes.append(f"  [reparation] {rep[:160]}")
            info = execute_tool("mesh_info", {"mesh_path": stl_path}, perms)
            if "Watertight: False" in info:
                retours_echec.append("Le maillage reste NON ETANCHE apres reparation.")

        if par_gabarit and not retours_echec:
            # Geometrie juste par construction, cotes posees telles quelles :
            # ni controle visuel ni boucle de correction (10 s au lieu de 95).
            res_print = execute_tool("check_printability",
                                     {"mesh_path": stl_path, "printer": printer or "ad5x"}, perms)
            lignes.append(f"  [impression] {res_print.splitlines()[0] if res_print else ''}")
            for a in [l for l in res_print.splitlines() if "[!!]" in l]:
                lignes.append(f"      {a.strip()[:150]}")
            lignes.append("  [gabarit] geometrie verifiee par construction")
            rapport_controles = [info, res_print]
            verdict_final = "OUI"
            break

        res_mesure = execute_tool("measure_model",
                                  {"mesh_path": stl_path, "spec_json": json.dumps(spec, ensure_ascii=False)},
                                  perms)
        lignes.append(f"  [mesure] {res_mesure.splitlines()[0] if res_mesure else '?'}")
        for l in res_mesure.splitlines()[1:6]:
            lignes.append(f"      {l.strip()}")
        if "ECART" in res_mesure.splitlines()[0] if res_mesure else False:
            retours_echec.append("Cotes hors tolerance :\n" + "\n".join(res_mesure.splitlines()[1:6]))

        res_print = execute_tool("check_printability",
                                 {"mesh_path": stl_path, "printer": printer or "ad5x"}, perms)
        etat_print = res_print.splitlines()[0] if res_print else ""
        lignes.append(f"  [impression] {etat_print}")
        alertes_print = [l for l in res_print.splitlines() if "[!!]" in l]
        for a in alertes_print:
            lignes.append(f"      {a.strip()[:150]}")

        rapport_controles = [info, res_mesure, res_print]

        # 5. QC vision (4 vues)
        _log(f"Iteration {tentative} : QC vision multi-vues...")
        res_vision = execute_tool("validate_mesh",
                                  {"request_text": request_text, "stl_path": stl_path},
                                  perms)
        # Verdict : lire d'abord la ligne DECISION FINALE (fiable), puis
        # retomber sur un motif VERDICT: OUI/NON. « INDISPONIBLE » (erreur
        # Ollama, contexte depasse...) n'est PAS un refus de la piece.
        import re as _re

        vision_indispo = "INDISPONIBLE" in res_vision.upper()
        verdict_iter = ""
        for l in res_vision.splitlines():
            if l.strip().upper().startswith("DECISION FINALE"):
                if "OUI" in l.upper():
                    verdict_iter = "OUI"
                elif "NON" in l.upper():
                    verdict_iter = "NON"
                else:
                    verdict_iter = "INCERTAIN"
        if not verdict_iter:
            m_v = _re.search(r"VERDICT\s*:?\s*(OUI|NON)", res_vision.upper())
            verdict_iter = m_v.group(1) if m_v else "INCERTAIN"
        score = ""
        for l in res_vision.splitlines():
            if "SCORE" in l.upper() and "/10" in l:
                score = l.strip()
                break
        lignes.append(f"  [vision] VERDICT {verdict_iter} {score}")
        ecarts = [l for l in res_vision.splitlines() if l.strip().startswith("-") or "ECARTS" in l.upper()][:6]
        for e in ecarts:
            lignes.append(f"      {e.strip()[:140]}")
        # Retour integral (trunque) pour la boucle : c'est le DETAIL des ecarts
        # qui permet a la tentative suivante de corriger reellement.
        retour_vision = "\n".join(res_vision.splitlines()[:14])

        # Score de l'essai (pour exporter le meilleur si aucun n'est conforme).
        m_note = _re.search(r"SCORE\s+(?:FORME|RESSEMBLANCE)\s*:?\s*(\d+)\s*/\s*10",
                            res_vision.upper())
        note_vision = int(m_note.group(1)) if m_note else 0
        score_iter = ((100 if verdict_iter == "OUI" else 0)
                      + (10 if "MESURE CONFORME" in res_mesure else 0)
                      + note_vision)
        if score_iter > meilleur_score:
            meilleur_score = score_iter
            stl_meilleur = stl_path
            iter_meilleure = tentative
            rapport_meilleur = list(rapport_controles)
            lignes.append(f"  [essai] meilleur score jusqu'ici : {score_iter}")

        # 6. Decision : succes ou retours pour la prochaine iteration
        if verdict_iter == "OUI" and not retours_echec:
            verdict_final = "OUI"
            dernier_retour = ""
            break
        if vision_indispo and not retours_echec:
            # Le controleur n'a pas pu rendre de verdict (erreur outil) :
            # ne pas condamner la piece ni la regenerer en boucle pour rien.
            verdict_final = "INCERTAIN"
            break
        morceaux = []
        if retours_echec:
            morceaux.append("\n".join(retours_echec))
        if verdict_iter == "NON":
            morceaux.append(
                "Le controle VISUEL a refuse la piece. Rapport du controleur :\n"
                + retour_vision[:700]
            )
        if alertes_print:
            morceaux.append("Alertes d'imprimabilite a corriger si possible :\n"
                            + "\n".join(a.strip() for a in alertes_print[:3]))
        dernier_retour = "\n\n".join(morceaux) or "Ameliore globalement la piece."

    if not stl_final:
        lignes.append("")
        lignes.append("ECHEC : aucune piece construite apres les iterations.")
        return "\n".join(lignes)

    # Aucun essai conforme : on exporte le MEILLEUR (score), pas le dernier.
    if verdict_final != "OUI" and stl_meilleur and stl_meilleur != stl_final:
        lignes.append("")
        lignes.append(f"[choix] aucun essai conforme : export du meilleur "
                      f"(iteration {iter_meilleure}, score {meilleur_score}) "
                      f"au lieu de l'iteration {MAX_ITERATIONS}.")
        stl_final = stl_meilleur

    # ---- 7. Export final : 3MF (defaut) + STL + apercu ----
    _log("Export 3MF + apercu...")
    # Nom fourni par le LLM : jamais de separateur ni de « .. » dans le chemin.
    base_nom = re.sub(r"[^\w-]", "_", str(spec.get("nom_piece") or "piece"))[:40].strip("_") or "piece"
    stl_definitif = str(dossier / f"{base_nom}.stl")
    if Path(stl_final) != Path(stl_definitif):
        shutil.copyfile(stl_final, stl_definitif)
    res_3mf = execute_tool("export_3mf",
                           {"stl_paths": stl_definitif,
                            "colors": couleur if couleur.startswith("#") else "",
                            "out_path": str(dossier / f"{base_nom}.3mf")},
                           perms)
    chemin_3mf = ""
    for l in res_3mf.splitlines():
        if ".3mf" in l:
            chemin_3mf = l.split(":")[-1].strip()
            break
    lignes.append("")
    lignes.append(f"[3MF] {res_3mf.splitlines()[0] if res_3mf else 'ECHEC'}")

    # Apercu : la vue iso du dernier STL devient <nom>_apercu.png
    prefix = str(Path(stl_definitif).with_suffix(""))
    apercu = f"{prefix}_apercu.png"
    iso = f"{prefix}_iso.png"
    if Path(iso).exists():
        shutil.copyfile(iso, apercu)
        lignes.append(f"[apercu] {apercu}")
    else:
        execute_tool("render_stl_views", {"stl_path": stl_definitif}, perms)
        if Path(iso).exists():
            shutil.copyfile(iso, apercu)
            lignes.append(f"[apercu] {apercu}")

    # ---- Rapport final ----
    lignes.append("")
    lignes.append("=" * 62)
    lignes.append(f"VERDICT FINAL : {verdict_final}")
    lignes.append(f"3MF (format par defaut) : {dossier / (base_nom + '.3mf')}")
    lignes.append(f"STL (secondaire) : {stl_definitif}")
    if chemin_3mf:
        lignes.append("Ouvre le 3MF dans ton slicer : bouton 'Ouvrir dans le slicer'")
        lignes.append("ou demande-moi 'imprime-le'.")
    # Controles affiches : ceux de l'essai EXPORTE (le meilleur si aucun
    # essai n'est conforme), pas ceux de la derniere iteration.
    etq = "Controles :"
    controles_affichees = rapport_controles
    if verdict_final != "OUI" and rapport_meilleur:
        controles_affichees = rapport_meilleur
        etq = f"Controles (essai exporte : iteration {iter_meilleure}) :"
    for r in controles_affichees[:1]:
        lignes.append("")
        lignes.append(etq)
        for l in r.splitlines():
            lignes.append(f"  {l}")
    lignes.append("=" * 62)
    _log("Pipeline v2 termine.")
    return "\n".join(lignes)
