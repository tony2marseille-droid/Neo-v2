//! Outils : specs via pont Python (cachees), execution via pont.
//! Le Rust pilote la boucle tool-calling, le Python execute (fidelite 100 %).
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use serde_json::Value;

use crate::error::ApiResult;
use crate::pybridge::py_str;

static SPEC_CACHE: OnceLock<Mutex<HashMap<String, Value>>> = OnceLock::new();

fn cache() -> &'static Mutex<HashMap<String, Value>> {
    SPEC_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn cache_key(names: &[String]) -> String {
    let mut sorted = names.to_vec();
    sorted.sort();
    sorted.join(",")
}

/// Specs Ollama pour une liste d'outils (via pont, cache en memoire).
pub async fn specs(root: &std::path::Path, names: &[String]) -> ApiResult<Vec<Value>> {
    let key = cache_key(names);
    if let Some(v) = cache().lock().unwrap().get(&key) {
        if let Some(arr) = v.as_array() {
            return Ok(arr.clone());
        }
    }
    let names_py: Vec<String> = names.iter().map(|n| py_str(n)).collect();
    let code = format!(
        "from backend.tools.executor import ollama_tool_specs; print(json.dumps(ollama_tool_specs([{}])))",
        names_py.join(","));
    let v = crate::pybridge::py_json_chaud(root, &code).await?;
    let arr = v.as_array().cloned().unwrap_or_default();
    cache().lock().unwrap().insert(key, Value::Array(arr.clone()));
    Ok(arr)
}

/// Specs de TOUS les outils declares (pour le serveur MCP).
pub async fn specs_tous(root: &std::path::Path) -> ApiResult<Vec<Value>> {
    let code = "from backend.tools.executor import TOOL_DEFINITIONS; \
                print(json.dumps(sorted(TOOL_DEFINITIONS.keys())))";
    let v = crate::pybridge::py_json(root, code).await?;
    let names: Vec<String> = v
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    specs(root, &names).await
}

/// Routeur d'intention Python : (id d'agent ou "" si indecis, raison
/// affichee en badge dans le chat « routé : ... »).
pub async fn route_agent_full(root: &std::path::Path, message: &str) -> (String, String) {
    let q: String = message.chars().take(500).collect();
    let code = format!(
        "from backend.core.router import route_agent; r = route_agent({}); print(json.dumps(list(r) if isinstance(r, (list, tuple)) else [r, '']))",
        py_str(&q));
    match crate::pybridge::py_json_chaud(root, &code).await {
        Ok(v) => {
            let arr = v.as_array().cloned().unwrap_or_default();
            let agent = arr.first()
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            let raison = arr.get(1)
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            (agent, raison)
        }
        Err(_) => (String::new(), String::new()),
    }
}

/// System prompt d'un agent (bridge). Vide si inconnu.
pub async fn agent_system(root: &std::path::Path, agent_id: &str) -> String {
    let code = format!(
        "from backend.core.agents import get_agent; a = get_agent({}); print(json.dumps(a.system_prompt if a and getattr(a, 'system_prompt', None) else ''))",
        py_str(agent_id));
    crate::pybridge::py_json_chaud(root, &code)
        .await
        .ok()
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_default()
}

/// Flag RAG d'un agent (docs -> true). Faux si inconnu.
pub async fn agent_rag(root: &std::path::Path, agent_id: &str) -> bool {
    let code = format!(
        "from backend.core.agents import get_agent; a = get_agent({}); print(json.dumps(bool(a and getattr(a, 'rag', False))))",
        py_str(agent_id));
    crate::pybridge::py_json_chaud(root, &code)
        .await
        .ok()
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

/// Outils d'un agent (via pont). Retourne [] si inconnu.
pub async fn agent_tools(root: &std::path::Path, agent_id: &str) -> Vec<String> {
    let code = format!(
        "from backend.core.agents import get_agent; a = get_agent({}); print(json.dumps(a.tools if a and getattr(a, 'tools', None) else []))",
        py_str(agent_id));
    crate::pybridge::py_json_chaud(root, &code)
        .await
        .ok()
        .and_then(|v| v.as_array().map(|a| {
            a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()
        }))
        .unwrap_or_default()
}

/// Resultat d'execution d'un outil, avec les metadonnees de fiabilite
/// necessaires aux traces (duree, tentatives, depassement de delai).
pub struct ToolOutcome {
    pub text: String,
    pub duration_ms: i64,
    pub attempts: u32,
    pub timed_out: bool,
}

/// Delai maximal par outil. Les generations (image, video, 3D, tests,
/// fine-tuning) sont longues ; les lectures doivent echouer vite pour ne pas
/// bloquer la boucle de chat.
fn delai_outil(nom: &str) -> std::time::Duration {
    let n = nom.to_lowercase();
    // « mesh » : `generate_mesh_from_text` (image de reference + Hunyuan3D,
    // ~3 min) n'avait aucun de ces mots et tombait dans le delai de 120 s ; le
    // pont le tuait, le modele recommencait trois fois (442 s perdues, essai
    // en production du 11/11/2026).
    let lourd = [
        "image", "video", "3d", "music", "audio", "train", "finetune", "render",
        "research", "test", "upscale", "studio", "pipeline", "mesh",
    ];
    let rapide = [
        "list", "read", "search", "diff", "status", "log", "datetime", "calculator",
        "stat", "info", "memory", "index",
    ];
    if lourd.iter().any(|m| n.contains(m)) {
        std::time::Duration::from_secs(600)
    } else if rapide.iter().any(|m| n.contains(m)) {
        std::time::Duration::from_secs(30)
    } else {
        std::time::Duration::from_secs(120)
    }
}

/// Outils dont le rejeu est sans effet de bord (lecture seule). On ne rejoue
/// jamais une ecriture, une suppression ou une generation (doublons) : un nom
/// qui contient un verbe d'ecriture (`save_memory`, `index_documents`...)
/// n'est jamais rejouable, meme s'il contient aussi un mot de lecture.
fn rejouable(nom: &str) -> bool {
    let n = nom.to_lowercase();
    let ecriture = [
        "save", "write", "delete", "forget", "create", "edit", "index", "generate",
        "send", "envoy", "rollback", "install", "remember", "apply", "set_",
    ];
    if ecriture.iter().any(|m| n.contains(m)) {
        return false;
    }
    [
        "read", "list", "search", "diff", "status", "log", "datetime", "calculator",
        "stat", "info",
    ]
    .iter()
    .any(|m| n.contains(m))
}

/// Erreurs pour lesquelles une seconde tentative a du sens.
fn transitoire(msg: &str) -> bool {
    let m = msg.to_lowercase();
    [
        "timeout", "timed out", "delai", "connection refused", "connection reset",
        "temporarily unavailable", "try again", "502", "503", "504",
    ]
    .iter()
    .any(|x| m.contains(x))
}

/// Issue d'un appel au worker resident.
enum Worker {
    Ok(String),
    /// Aucun worker : rien n'a ete execute, le pont classique est sur.
    Absent,
    /// Requete envoyee puis echec (timeout, crash) : l'outil a PU s'executer.
    Echec,
}

/// Execution d'un outil dans le worker resident (un seul process chaud).
/// La permission reste evaluee cote Python.
async fn worker_tool(name: &str, args: &Value, level: &str) -> Worker {
    let Some(w) = crate::worker::get() else { return Worker::Absent };
    let req = serde_json::json!({
        "op": "tool",
        "name": name,
        "args": args,
        "level": level,
    });
    match w.call(req, 30).await {
        Ok(v) => Worker::Ok(v.as_str().map(|s| s.to_string()).unwrap_or_else(|| v.to_string())),
        Err(e) => {
            tracing::debug!("worker outil en echec: {e}");
            Worker::Echec
        }
    }
}

/// Execute un outil via le pont (permission evaluee cote Python).
/// Le texte est tronque a 6000 caracteres comme le Python.
pub async fn execute(
    root: &std::path::Path,
    name: &str,
    args: &Value,
    level: &str,
) -> ToolOutcome {
    let debut = std::time::Instant::now();
    if let Some(refus) = precheck(root, name, args) {
        return ToolOutcome {
            text: refus,
            duration_ms: debut.elapsed().as_millis() as i64,
            attempts: 1,
            timed_out: false,
        };
    }
    let nm = name;
    let args_json = serde_json::to_string(args).unwrap_or_else(|_| "{}".into());
    // Arguments via fichier (evite les problemes de quoting shell). Le nom
    // inclut pid + nanos + compteur : deux appels simultanes (deux
    // conversations, ou deux outils d'un meme tour) ne se marchent plus
    // dessus.
    static ARGS_SEQ: std::sync::atomic::AtomicU64 =
        std::sync::atomic::AtomicU64::new(0);
    let seq = ARGS_SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let tmp = std::env::temp_dir().join(format!(
        "tool_args_{}_{}_{}.json",
        std::process::id(),
        nanos,
        seq
    ));
    if std::fs::write(&tmp, &args_json).is_err() {
        return ToolOutcome {
            text: format!("Erreur: impossible d'ecrire les arguments de {nm}"),
            duration_ms: debut.elapsed().as_millis() as i64,
            attempts: 1,
            timed_out: false,
        };
    }
    let code = format!(
        "import json; from backend.tools.executor import execute_tool, PermissionManager; \
         args = json.load(open({}, encoding='utf-8')); \
         print(execute_tool({}, args, PermissionManager({})), end='')",
        py_str(&tmp.to_string_lossy()), py_str(name), py_str(level));
    let delai = delai_outil(name);
    let max_tentatives: u32 = if rejouable(name) { 2 } else { 1 };
    let mut tentatives: u32 = 0;
    loop {
        tentatives += 1;
        let t0 = std::time::Instant::now();
        // Outils courts (lecture d'etat) : execution dans le worker resident,
        // imports deja chauds, pas de python.exe a relancer ni de fichier
        // d'arguments temporaire. Les taches longues (image, 3D, tests,
        // fine-tuning) restent sur le pont classique pour ne pas monopoliser
        // le worker, qui traite une requete a la fois.
        let out = if delai <= std::time::Duration::from_secs(30) {
            match worker_tool(name, args, level).await {
                Worker::Ok(texte) => {
                    let _ = std::fs::remove_file(&tmp);
                    return ToolOutcome {
                        text: texte.chars().take(6000).collect(),
                        duration_ms: t0.elapsed().as_millis() as i64,
                        attempts: tentatives,
                        timed_out: false,
                    };
                }
                // Un outil a effet de bord deja envoye au worker ne doit pas
                // etre relance par le pont classique (double execution).
                Worker::Echec if !rejouable(name) => {
                    let _ = std::fs::remove_file(&tmp);
                    return ToolOutcome {
                        text: format!("Erreur: worker indisponible pendant {nm} (non relance pour eviter une double execution)"),
                        duration_ms: t0.elapsed().as_millis() as i64,
                        attempts: tentatives,
                        timed_out: true,
                    };
                }
                Worker::Echec | Worker::Absent => {
                    crate::pybridge::py_text_timeout(root, &["-c", &code], delai).await
                }
            }
        } else {
            crate::pybridge::py_text_timeout(root, &["-c", &code], delai).await
        };
        let duration_ms = t0.elapsed().as_millis() as i64;
        match out {
            Ok(s) => {
                let _ = std::fs::remove_file(&tmp);
                return ToolOutcome {
                    text: s.chars().take(6000).collect(),
                    duration_ms,
                    attempts: tentatives,
                    timed_out: false,
                };
            }
            Err(e) => {
                let msg = e.to_string();
                let bas = msg.to_lowercase();
                let timed_out = bas.contains("timeout")
                    || bas.contains("timed out")
                    || bas.contains("delai");
                if tentatives < max_tentatives && transitoire(&msg) {
                    tokio::time::sleep(std::time::Duration::from_millis(
                        300 * tentatives as u64,
                    ))
                    .await;
                    continue;
                }
                let _ = std::fs::remove_file(&tmp);
                tracing::warn!(
                    outil = %nm,
                    tentatives,
                    duration_ms,
                    timed_out,
                    "echec outil: {msg}"
                );
                return ToolOutcome {
                    text: format!("Erreur d'execution de {nm}: {msg}"),
                    duration_ms,
                    attempts: tentatives,
                    timed_out,
                };
            }
        }
    }
}

/// Niveau de permission depuis les settings (defaut USER comme le Python).
pub fn tool_level(db: &localai_core::db::Db) -> String {
    db.get_setting("tool_level", "USER").to_uppercase()
}

pub const MAX_TOOL_ROUNDS: usize = 14;

pub fn default_tools() -> Vec<String> {
    ["calculator", "get_datetime", "list_dir", "read_file", "check_code", "deep_research"]
        .iter().map(|s| s.to_string()).collect()
}

// ---- Sandbox chantier 2 : double controle Rust avant le pont ----

pub const SANDBOX_PATH_TOOLS: [&str; 3] = ["read_file", "list_dir", "write_file"];

fn lower(s: &str) -> String {
    s.to_lowercase().replace(['/', '\\'], "\\")
}

fn rules(root: &std::path::Path) -> serde_json::Value {
    let p = root.join("data").join("security_rules.json");
    std::fs::read_to_string(&p)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or(Value::Null)
}

fn jstrs(v: &Value, key: &str, fallback: &[&str]) -> Vec<String> {
    if let Some(items) = v.get(key).and_then(|x| x.as_array()) {
        let out: Vec<String> = items
            .iter()
            .filter_map(|x| x.as_str().map(|s| s.to_lowercase()))
            .collect();
        if !out.is_empty() { return out; }
    }
    fallback.iter().map(|s| s.to_string()).collect()
}

fn sous_racine(p: &str, racine: &str) -> bool {
    let ps = lower(p);
    let rs = lower(racine);
    if !rs.is_empty() && ps.as_str() != rs.as_str() && ps.starts_with(rs.as_str()) {
        // doit se terminer au joncteur de dossier exact
        let rs_len = rs.len();
        let suite = &ps[rs_len..];
        suite.starts_with('\\') || rs.ends_with('\\')
    } else {
        ps == rs
    }
}

/// Pre-controle avant pont. Retourne Some(refus) si l'appel doit etre rejete.
pub fn precheck(root: &std::path::Path, name: &str, args: &Value) -> Option<String> {
    let r = rules(root);
    let root_w = r
        .get("sandbox_root")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "E:\\ia\\workspace".to_string());
    let read_roots = {
        let mut v: Vec<String> = r
            .get("sandbox_read_roots")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(|s| s.to_string())).collect())
            .unwrap_or_default();
        if v.is_empty() {
            v.push(root_w.clone());
            v.push("E:\\ia\\data\\documents".to_string());
        }
        v
    };
    let deny = jstrs(&r, "cmd_deny", &["del", "rmdir", "format", "reg", "schtasks", "net", "sc", "powershell", "pwsh", "cmd", "curl", "wget", "ssh", "scp", "taskkill"]);
    // python/node retires du defaut : `python -c` executerait n'importe quoi.
    let allow = jstrs(&r, "cmd_allow", &["git", "openscad", "dir", "type", "findstr"]);
    if name == "run_command" {
        let cmd = args
            .get("command")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if cmd.is_empty() {
            return Some("refusé: commande vide".into());
        }
        for ch in ['|', '&', ';', '>', '<', '`', '\n', '\r'] {
            if cmd.contains(ch) {
                return Some(format!("refusé: caractere interdit en sandbox: {ch}"));
            }
        }
        if cmd.contains("$(") {
            return Some("refusé: substitution de commande interdite ($())".into());
        }
        let head = cmd
            .split_whitespace()
            .next()
            .unwrap_or("")
            .trim_matches('"')
            .to_lowercase();
        let base = head.rsplit(['\\', '/']).next().unwrap_or(&head).to_string();
        if deny.contains(&head) || deny.contains(&base) {
            return Some(format!("refusé: executable interdit en sandbox: {head}"));
        }
        if !allow.contains(&head) && !allow.contains(&base) {
            return Some(format!("refusé: executable hors liste blanche: {head}"));
        }
        return None;
    }
    if SANDBOX_PATH_TOOLS.contains(&name) {
        let raw = args
            .get("path")
            .and_then(|x| x.as_str())
            .unwrap_or("");
        // `..` refuse comme COMPOSANT (et pas seulement suivi d'un separateur :
        // `E:\ia\workspace\..` passait auparavant).
        if raw.split(['\\', '/']).any(|c| c.trim() == "..") {
            return Some("refusé: traversée de dossier interdite (..) ".into());
        }
        if raw.starts_with("\\\\") || raw.starts_with("//") {
            return Some("refusé: chemin reseau (UNC) interdit".into());
        }
        // Chemin absolu (`C:\...`), relatif au lecteur (`C:x`) ou a la racine
        // du lecteur (`\Users\...`) : il doit etre sous une racine autorisee
        // (ecriture = workspace, lecture = racines de lecture). Un chemin
        // relatif simple est resolu par Python dans le workspace.
        let ancre = raw.contains(':') || raw.starts_with('\\') || raw.starts_with('/');
        if ancre {
            let racines: Vec<String> = if name == "write_file" {
                vec![root_w.clone()]
            } else {
                read_roots.clone()
            };
            if !racines.iter().any(|rac| sous_racine(raw, rac)) {
                return Some(format!("refusé: chemin hors racines autorisées {racines:?}"));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn les_generations_longues_ont_dix_minutes() {
        let secondes = |nom: &str| delai_outil(nom).as_secs();
        for lourd in ["generate_mesh_from_text", "generate_mesh_from_image", "generate_image", "pipeline_3d",
                      "video_clip", "deep_research"] {
            assert_eq!(secondes(lourd), 600, "{lourd}");
        }
        assert_eq!(secondes("get_datetime"), 30);
        assert_eq!(secondes("document_create"), 120);
        assert_eq!(secondes("fiche_creer"), 120);
    }

    /// Racine sans `data/security_rules.json` : regles par defaut
    /// (ecriture `E:\ia\workspace`, lecture workspace + documents).
    fn refuse(name: &str, args: Value) -> bool {
        let root = std::env::temp_dir().join("neo_precheck_sans_regles");
        precheck(&root, name, &args).is_some()
    }

    #[test]
    fn precheck_refuse_les_evasions_de_chemin() {
        for p in [
            r"E:\ia\workspace\..",
            r"E:\ia\workspace\..\backend\x.py",
            r"E:\ia\workspace_old\x.txt",
            r"..\secret.txt",
            r"\Users\Camille\.ssh\id_rsa",
            r"\serveur\partage\x",
            r"C:\Windows\win.ini",
        ] {
            assert!(refuse("read_file", json!({ "path": p })), "devait refuser {p}");
        }
        assert!(refuse("write_file", json!({ "path": r"E:\ia\data\documents\x.txt" })));
    }

    #[test]
    fn precheck_accepte_le_workspace() {
        assert!(!refuse("read_file", json!({ "path": r"E:\ia\workspace\notes.txt" })));
        assert!(!refuse("write_file", json!({ "path": "sous/dossier/notes.txt" })));
        assert!(!refuse("list_dir", json!({ "path": r"E:\ia\data\documents" })));
    }

    #[test]
    fn precheck_commandes() {
        assert!(refuse("run_command", json!({ "command": "python -c \"print(1)\"" })));
        assert!(refuse("run_command", json!({ "command": "dir\nwhoami" })));
        assert!(refuse("run_command", json!({ "command": "dir & whoami" })));
        assert!(!refuse("run_command", json!({ "command": "dir" })));
    }

    #[test]
    fn rejouable_exclut_les_ecritures() {
        assert!(rejouable("read_file"));
        assert!(rejouable("list_dir"));
        assert!(!rejouable("save_memory"));
        assert!(!rejouable("index_documents"));
        assert!(!rejouable("write_file"));
    }
}
