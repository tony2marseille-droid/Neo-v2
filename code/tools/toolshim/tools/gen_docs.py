"""Génère docs/index.html : aperçu rédigé + référence de toutes les fonctions extraite du code (ast)."""
import ast
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = [
    ("toolshim/server.py", "Entrée HTTP : intercepte les chats, relaie le reste."),
    ("toolshim/engine.py", "Moteur d'émulation et boucle de nouvelles tentatives."),
    ("toolshim/prompting.py", "Prompt, schéma JSON contraint, conversion de l'historique."),
    ("toolshim/parsing.py", "Extraction du JSON, normalisation, validation des appels."),
    ("toolshim/selector.py", "Sélection des outils pertinents parmi 63."),
    ("toolshim/capabilities.py", "Détection des modèles natifs (relayés tels quels)."),
    ("toolshim/formats.py", "Réponses au format OpenAI / Ollama, bloc ou flux."),
    ("toolshim/upstream.py", "Client HTTP vers Ollama."),
    ("toolshim/config.py", "Configuration."),
    ("toolshim/__main__.py", "Lancement en ligne de commande."),
    ("demo_client.py", "Client d'exemple avec trois outils « dépôt »."),
]
FIELD_DOCS = {
    "upstream_url": "Adresse du serveur Ollama réel.",
    "host": "Interface d'écoute du proxy.",
    "port": "Port d'écoute du proxy (8330 par défaut).",
    "native_models": "Motifs de modèles relayés sans émulation (ex. qwen3*).",
    "emulated_models": "Motifs de modèles toujours émulés ; prioritaire sur native_models.",
    "auto_detect": "Interroge /api/show pour savoir si le modèle gère « tools ».",
    "max_tools_in_prompt": "Nombre maximal d'outils injectés dans le prompt (sélection par pertinence).",
    "max_retries": "Nouvelles tentatives guidées quand le JSON est invalide.",
    "max_calls_per_turn": "Nombre maximal d'appels d'outils par réponse.",
    "request_timeout": "Délai maximal d'une requête amont, en secondes.",
    "constrained_output": "Impose le schéma JSON au décodage (paramètre format d'Ollama).",
    "temperature": "Température par défaut si le client n'en fournit pas.",
    "disable_thinking": "Envoie think=false pour les modèles à raisonnement.",
    "log_level": "Niveau de journalisation (DEBUG, INFO, WARNING).",
}
E = html.escape


def signature(fn) -> str:
    """Signature lisible d'une fonction : nom(args) -> retour."""
    ret = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
    prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    return f"{prefix}{fn.name}({ast.unparse(fn.args)}){ret}"


def doc_of(node) -> str:
    """Docstring d'un nœud, ou un avertissement visible si elle manque."""
    return ast.get_docstring(node) or "⚠ docstring manquante"


def render_fn(fn, indent=False, route=None) -> str:
    """Bloc HTML d'une fonction ou méthode."""
    tag = f'<span class="route">{E(route)}</span>' if route else ""
    cls = "fn sub" if indent else "fn"
    return f'<div class="{cls}"><code class="sig">{E(signature(fn))}</code>{tag}<p>{E(doc_of(fn))}</p></div>'


def route_of(fn):
    """Retourne « METHODE /chemin » si la fonction est décorée par @app.<verbe>(...)."""
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args:
            if isinstance(d.args[0], ast.Constant):
                return f"{d.func.attr.upper().replace('API_ROUTE', 'ANY')} {d.args[0].value}"
    return None


def render_module(rel: str, summary: str) -> str:
    """Section HTML d'un fichier : docstring, classes, méthodes, fonctions (y compris imbriquées)."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    out = [f'<section id="{E(rel)}"><h3>{E(rel)}</h3><p class="lead">{E(summary)}</p>']
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.append(f'<div class="cls"><code class="sig">class {E(node.name)}</code><p>{E(doc_of(node))}</p>')
            out += [render_fn(m, True) for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out.append("</div>")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(render_fn(node))
            for sub in ast.walk(node):
                if sub is not node and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(render_fn(sub, True, route_of(sub)))
    out.append("</section>")
    return "\n".join(out)


def config_rows() -> str:
    """Lignes du tableau de configuration, lues dans la dataclass Config ; échoue si un champ n'est pas documenté."""
    tree = ast.parse((ROOT / "toolshim/config.py").read_text(encoding="utf-8"))
    cfg = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Config")
    rows = []
    for n in cfg.body:
        if isinstance(n, ast.AnnAssign):
            name = n.target.id
            if name not in FIELD_DOCS:
                sys.exit(f"champ de config non documenté : {name}")
            default = "[]" if "default_factory" in ast.unparse(n.value) else ast.unparse(n.value)
            rows.append(f"<tr><td><code>{name}</code></td><td><code>{E(default)}</code></td><td>{E(FIELD_DOCS[name])}</td></tr>")
    return "\n".join(rows)


CSS = """
:root{--bg:#f2f5f4;--ink:#12262b;--mute:#53686d;--line:#cfdad8;--card:#fff;--accent:#0b6b6f;--accent-ink:#fff;--code:#e6eeec;--warn:#9a3412}
@media (prefers-color-scheme:dark){:root{--bg:#0e1a1d;--ink:#e3eeec;--mute:#93aaa8;--line:#25393c;--card:#142427;--accent:#4fc3c0;--accent-ink:#06201f;--code:#1b3033;--warn:#fdba74}}
*{box-sizing:border-box}html{scroll-padding-top:1rem}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 "Segoe UI",system-ui,sans-serif;padding-top:env(safe-area-inset-top,0)}
main{max-width:62rem;margin:0 auto;padding:2rem 1.25rem 4rem}
h1,h2,h3{font-family:"Iowan Old Style","Palatino Linotype",Georgia,serif;line-height:1.2}
h1{font-size:2.4rem;margin:.2rem 0}h2{font-size:1.6rem;margin:3rem 0 .8rem;border-top:2px solid var(--ink);padding-top:1rem}h3{font-size:1.2rem;margin:2rem 0 .3rem}
.lead{color:var(--mute);max-width:46rem}
code{font-family:Consolas,"Cascadia Mono",monospace;font-size:.88em;background:var(--code);padding:.1em .35em;border-radius:3px}
pre{background:var(--code);padding:1rem;overflow-x:auto;border-radius:4px;line-height:1.5}pre code{background:none;padding:0}
.flow{display:flex;flex-wrap:wrap;gap:.5rem;align-items:stretch;margin:1rem 0;padding:0;list-style:none;counter-reset:s}
.flow li{flex:1 1 11rem;background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);padding:.6rem .8rem;border-radius:3px;font-size:.92rem;counter-increment:s}
.flow li::before{content:counter(s) ". ";color:var(--accent);font-weight:700}
.flow li b{display:block}
.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card)}
th,td{border:1px solid var(--line);padding:.45rem .7rem;text-align:left;vertical-align:top}th{background:var(--code)}
.fn,.cls{background:var(--card);border:1px solid var(--line);padding:.7rem 1rem;margin:.6rem 0;border-radius:3px}
.fn.sub{margin-left:1.5rem;background:transparent}.cls>.fn{margin-left:1rem}
.sig{display:inline-block;font-weight:600;word-break:break-word}.fn p,.cls>p{margin:.35rem 0 0;white-space:pre-line}
.route{margin-left:.6rem;background:var(--accent);color:var(--accent-ink);font-size:.78rem;padding:.1rem .5rem;border-radius:3px;font-family:Consolas,monospace}
nav ul{columns:2;padding-left:1.1rem}nav a{color:var(--accent)}
.note{border-left:4px solid var(--warn);padding:.2rem 1rem;background:var(--card)}
@media(max-width:640px){h1{font-size:1.8rem}nav ul{columns:1}.fn.sub{margin-left:.5rem}}
"""

OVERVIEW = """
<h1>toolshim</h1>
<p class="lead">Une couche entre ton application et Ollama. Quand un modèle ne sait pas appeler d'outils, toolshim lui apprend le format par le prompt, force une sortie JSON valide, vérifie les arguments, puis rend au client de vrais <code>tool_calls</code>, comme si le modèle les gérait nativement.</p>

<h2 id="demarrage">Démarrage</h2>
<pre><code>pip install -r requirements.txt
python -m toolshim --port 8330            # proxy sur http://127.0.0.1:8330
python -m unittest discover -s tests      # 15 tests
python demo_client.py --model qwen3:8b "ajoute 50 VIS-M6 au dépôt"</code></pre>
<p>Dans l'agent <code>depot</code>, remplace l'adresse Ollama (<code>http://127.0.0.1:11434</code>) par celle du proxy (<code>http://127.0.0.1:8330</code>). Rien d'autre ne change : <code>/v1/chat/completions</code> (OpenAI) et <code>/api/chat</code> (Ollama) sont tous deux interceptés ; toutes les autres routes sont relayées telles quelles.</p>

<h2 id="flux">Ce qui se passe pour une requête</h2>
<ol class="flow">
<li><b>Réception</b>Requête chat avec <code>tools</code> (<code>server.handle_chat</code>).</li>
<li><b>Aiguillage</b>Modèle natif ou sans outils : relais direct. Sinon émulation (<code>capabilities</code>).</li>
<li><b>Sélection</b>Les 8 outils les plus pertinents sur 63 (<code>selector</code>).</li>
<li><b>Prompt</b>Catalogue d'outils + format JSON, historique converti en texte (<code>prompting</code>).</li>
<li><b>Génération</b>Appel Ollama avec schéma JSON imposé (<code>engine</code>).</li>
<li><b>Validation</b>Nom, arguments obligatoires, types ; si erreur, nouvelle tentative guidée (<code>parsing</code>).</li>
<li><b>Réponse</b><code>tool_calls</code> au dialecte du client, en bloc ou en flux (<code>formats</code>).</li>
</ol>

<h2 id="config">Configuration</h2>
<p>Fichier <code>config.json</code>, surchargeable par variables d'environnement <code>TOOLSHIM_&lt;CHAMP&gt;</code> (ex. <code>TOOLSHIM_PORT=9000</code>, listes séparées par des virgules).</p>
<div class="table-wrap"><table><tr><th>Champ</th><th>Défaut</th><th>Rôle</th></tr>
{config_rows}
</table></div>

<h2 id="endpoints">Routes</h2>
<div class="table-wrap"><table><tr><th>Route</th><th>Comportement</th></tr>
<tr><td><code>POST /v1/chat/completions</code></td><td>Interceptée. Réponse OpenAI : <code>tool_calls</code> avec <code>arguments</code> en chaîne JSON.</td></tr>
<tr><td><code>POST /api/chat</code></td><td>Interceptée. Réponse Ollama : <code>arguments</code> en objet.</td></tr>
<tr><td><code>GET /toolshim/health</code></td><td>État du proxy et cache de détection des capacités.</td></tr>
<tr><td>toute autre route</td><td>Relayée vers Ollama sans modification (tags, embeddings, pull…).</td></tr>
</table></div>

<h2 id="comportements">Comportements à connaître</h2>
<ul>
<li><b>Choix des modèles.</b> Ordre de priorité : <code>emulated_models</code>, <code>native_models</code>, détection automatique (<code>capabilities</code> ou template d'Ollama), émulation par défaut.</li>
<li><b>tool_choice.</b> <code>"none"</code> retire les outils ; <code>"required"</code> impose au moins un appel ; <code>{"function":{"name":…}}</code> impose cet outil.</li>
<li><b>Formats tolérés en sortie du modèle.</b> <code>calls</code>, <code>tool_calls</code>, <code>action/parameters</code> (format du plan d'action), JSON dans un bloc <code>```</code> ou précédé de texte, blocs <code>&lt;think&gt;</code>.</li>
<li><b>Corrections automatiques.</b> Nom d'outil approchant corrigé, arguments convertis au type déclaré (<code>"50"</code> devient <code>50</code>), arguments inconnus ignorés.</li>
<li><b>Échec après les tentatives.</b> Les appels valides sont gardés ; sinon le texte brut du modèle est renvoyé comme réponse.</li>
</ul>
<p class="note">Limites : en émulation, le streaming est rejoué après génération complète (pas de jetons en direct) ; la fiabilité dépend du modèle, d'où l'intérêt de la sélection top-k et des tests sur tes vrais outils.</p>

<h2 id="reference">Référence des fonctions</h2>
<nav><ul>{nav}</ul></nav>
"""


def main() -> None:
    """Assemble la page et l'écrit dans docs/index.html."""
    nav = "".join(f'<li><a href="#{E(m)}">{E(m)}</a></li>' for m, _ in MODULES)
    body = OVERVIEW.replace("{config_rows}", config_rows()).replace("{nav}", nav)
    sections = "\n".join(render_module(m, s) for m, s in MODULES)
    page = (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'<title>toolshim — documentation</title><style>{CSS}</style></head>'
            f'<body><main>{body}{sections}</main></body></html>')
    out = ROOT / "docs" / "index.html"
    out.write_text(page, encoding="utf-8")
    missing = page.count("docstring manquante")
    print(f"{out} écrit ; docstrings manquantes : {missing}")


if __name__ == "__main__":
    main()
