use std::sync::Arc;

use axum::{
    extract::{Multipart, Path, State},
    routing::{delete, get, post},
    Json, Router,
};
use localai_core::db::now_iso;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/documents", get(list))
        .route("/api/documents/upload", post(upload))
        .route("/api/documents/search", post(search))
        .route("/api/documents/fiches", get(fiches))
        .route("/api/documents/classify", post(classify))
        .route("/api/documents/describe", post(describe))
        .route("/api/documents/apprentissage", post(apprentissage))
        .route("/api/documents/apprentissage/status", get(apprentissage_status))
        .route("/api/documents/reindexer", post(reindexer))
        .route("/api/documents/:id", delete(remove))
        .route("/api/documents/:id/content", get(content))
        .route("/api/documents/neo-files", get(neo_files))
        .route("/api/documents/neo-delete", post(neo_delete))
    // Les fiches de connaissances (/api/fiches) sont servies par le service
    // Python (port 8321, backend/api/routes_neo.py), qui les redige aussi.
}

const MAX_FILE_SIZE: usize = 30 * 1024 * 1024;
/// Modele d'origine de l'index (avant le reglage `modele_plongement`).
const EMBED_MODEL: &str = "nomic-embed-text:latest";
/// Modele vise par la reindexation (banc du 09/11/2026, docs/CERVEAU.md) :
/// bon passage dans les 4 retenus 63 % -> 73 % seul, 83 % -> 97 % avec le tri.
pub(crate) const EMBED_MODEL_CIBLE: &str = "qwen3-embedding:4b";

/// Modele qui a construit l'index : requetes et nouveaux documents DOIVENT
/// utiliser le meme, sinon les vecteurs ne sont pas comparables.
pub(crate) fn modele_index(db: &localai_core::db::Db) -> String {
    let m = db.get_setting("modele_plongement", EMBED_MODEL);
    if m.trim().is_empty() { EMBED_MODEL.to_string() } else { m.trim().to_string() }
}

/// Texte de requete selon le modele : Qwen3-Embedding attend une consigne
/// devant la question (pas devant les documents).
pub(crate) fn texte_requete(modele: &str, question: &str) -> String {
    if modele.starts_with("qwen3-embedding") {
        format!(
            "Instruct: Given a question in French, retrieve the passage of documentation that answers it\nQuery: {question}"
        )
    } else {
        question.to_string()
    }
}

const CATEGORIES: &[&str] = &[
    "programmation", "ia", "impression3d", "bricolage",
    "cuisine", "sante", "finances", "loisirs", "science", "autre",
];

static CATEGORY_OK: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn ensure_category(st: &Arc<crate::AppState>) {
    if CATEGORY_OK.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    let _ = st.db.execute("ALTER TABLE documents ADD COLUMN category TEXT DEFAULT ''", &[]);
    let _ = st.db.execute("ALTER TABLE documents ADD COLUMN description TEXT DEFAULT ''", &[]);
    // Colonne system : 1 = document d'entrainement / fichier du logiciel ->
    // invisible dans l'UI (consigne : jamais supprime, juste masque).
    let _ = st.db.execute("ALTER TABLE documents ADD COLUMN system INTEGER DEFAULT 0", &[]);
    // Index FTS5 pour recherche hybride (BM25) cote embedding.
    let _ = st.db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts \
         USING fts5(content, content=document_chunks, content_rowid=id)",
        &[],
    );
    // Index a contenu externe : des triggers le tiennent a jour pour TOUS
    // les ecrivains (Rust et Python) ; un `rebuild` au demarrage rattrape
    // l'existant. L'ancien rattrapage `NOT IN (SELECT rowid FROM fts)` lisait
    // la table de contenu elle-meme et n'inserait donc jamais rien.
    for sql in [
        "CREATE TRIGGER IF NOT EXISTS document_chunks_fts_ai AFTER INSERT ON document_chunks BEGIN \
         INSERT INTO document_chunks_fts(rowid, content) VALUES (new.id, new.content); END",
        "CREATE TRIGGER IF NOT EXISTS document_chunks_fts_ad AFTER DELETE ON document_chunks BEGIN \
         INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content) VALUES ('delete', old.id, old.content); END",
        "CREATE TRIGGER IF NOT EXISTS document_chunks_fts_au AFTER UPDATE OF content ON document_chunks BEGIN \
         INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content) VALUES ('delete', old.id, old.content); \
         INSERT INTO document_chunks_fts(rowid, content) VALUES (new.id, new.content); END",
        "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')",
    ] {
        if let Err(e) = st.db.execute(sql, &[]) {
            tracing::warn!("index FTS documents : {e}");
        }
    }
    marquer_docs_systeme(st);
    CATEGORY_OK.store(true, std::sync::atomic::Ordering::Relaxed);
}

/// Marque comme systeme les documents d'entrainement de Nco et les
/// fichiers sources du logiciel (restent en base/disque pour le RAG et
/// l'apprentissage, mais n'apparaissent plus dans l'interface).
/// Les lecons apprises (apprentissage_*) et les notes personnelles de
/// l'utilisateur RESTENT visibles.
fn marquer_docs_systeme(st: &Arc<crate::AppState>) {
    let fichiers = [
        // Corpus / fiches d'entrainement
        "sft.txt", "sft_rlvr.txt", "dpo_pairs.txt", "agent_traces.txt",
        "_rlvr_full.py", "_corpus_prog.py", "train_lora.py", "lora_prep.py",
        "superviseur_train.py", "pipeline.py", "gen_corpus_v7.py", "gen_corpus_v92.py",
        "autobench.py", "score_corpus.py", "score_corpus2.py", "score_corpus3.py",
        "promote.py", "post_train_v9.py", "post_train_v7.py", "post_train_v8.py",
        "verif_v92.py", "smoke_latest.py", "test_teacher.py", "test_teacher2.py",
        "check_corpus.py", "check_dataset_v9.py", "compare_eval_v9.py",
        "eval_battery_v9.py", "gen_amelioration_v9.py", "dump_corpus.py", "dump_smoke.py",
        "collect_stats.py", "api_test_v6.json", "tags.json",
        // Fichiers sources du logiciel
        "main.py", "engine.py", "executor.py", "__init__.py", "ollama.py",
        "llamacpp.py", "base.py", "vector_store.py", "security.py", "sdgen.py",
        "router.py", "rlvr.py", "rag.py", "orchestrator.py", "knowledge.py",
        "interests.py", "hardware.py", "documents.py", "distiller.py",
        "database.py", "config.py", "catalog.py", "autotune.py", "agents.py",
        "schemas.py", "run_dev.py", "launcher.py", "requirements.txt",
        "requirements-dev.txt", "package.json", "package-lock.json",
        "tsconfig.json", "tsconfig.node.json", "tsconfig.app.json",
        ".oxlintrc.json", "security_rules.json", "hardware_profile.json",
        "models.json", "manifest.json", "README.md", "CONTEXT.md",
        "SETUP-PC-PUISSANT.md", "MASTER_PLAN.md", "GUIDE_PUISSANCE.md",
        "PROMPT_OPENCODE.md", "FIX_BENCH.md", "FIX_COMMANDES_OPENCODE.md",
        "mem.bat", "mem.py", "vite.config.ts", "run_dev.ts",
        "docker-compose.yml", "Dockerfile", ".gitignore", "AGENTS.md",
        "INSTALL.bat", "INSTALLER.bat", "INSTALLER.ps1", "launcher_localai.bat",
        "PUBLIER.md", "app.py", "server.py", "client.py",
    ];
    let liste: Vec<String> = fichiers.iter().map(|f| format!("'{f}'")).collect();
    let sql = format!(
        "UPDATE documents SET system=1 WHERE (system IS NULL OR system=0) AND (          filename IN ({}) OR filename LIKE 'routes%' OR substr(filename,1,1)='_'          OR description LIKE '%%entrain%%' OR description LIKE '%%exercice%%'          OR description LIKE '%%logiciel%%' OR description LIKE '%%plateforme d%%'          OR description LIKE '%%corpus%%')",
        liste.join(",")
    );
    // NB : notes_projet.txt, doc_*.txt, codes_postaux*, apprentissage_*
    // restent visibles : ce sont des documents/fiches utilisateur.
    let _ = st.db.execute(&sql, &[]);
}

async fn classify_text(st: &Arc<crate::AppState>, filename: &str, excerpt: &str) -> String {
    let model = st.db.get_setting("model_rapide", "qwen3:8b").to_string();
    let prompt = format!(
        "Classe ce document dans exactement une de ces categories : {}. \
Reponds UNIQUEMENT le nom de la categorie, un seul mot, sans ponctuation, sans phrase.\n\
Nom du fichier : {}\nDebut du contenu :\n{}",
        CATEGORIES.join(", "), filename, excerpt
    );
    let rep = match st
        .ollama
        .chat(
            &model,
            vec![localai_core::ollama::Msg { role: "user".into(), content: prompt }],
            0.0,
            12,
        )
        .await
    {
        Ok(r) => r,
        Err(_) => return "autre".into(),
    };
    let low = rep.to_lowercase();
    CATEGORIES
        .iter()
        .filter(|c| low.contains(**c))
        .max_by_key(|c| c.len())
        .map(|c| (*c).to_string())
        .unwrap_or_else(|| "autre".into())
}

fn illustration_prompt(cat: &str) -> String {
    let sujet = match cat {
        "programmation" => "code brackets and a terminal cursor",
        "ia" => "a brain made of connected glowing nodes",
        "impression3d" => "a 3d printer printing a small object, layer by layer",
        "bricolage" => "a hammer and a screwdriver crossed",
        "cuisine" => "a cooking pot with rising steam",
        "sante" => "a heart with a pulse line",
        "finances" => "stacked coins and a rising chart",
        "loisirs" => "an open book and a game controller",
        "science" => "an atom and a laboratory flask",
        "neo" => "a glowing green wireframe head silhouette with data streams",
        _ => "a folder full of documents",
    };
    format!(
        "minimalist flat vector icon of {sujet}, dark background, subtle green \
accent, clean simple shapes, no text, centered composition"
    )
}

static ILLUSTRATION_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn spawn_illustrations(st: &Arc<crate::AppState>, missing: Vec<String>) {
    if ILLUSTRATION_RUNNING.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return;
    }
    let root = st.settings.root_dir.clone();
    let dir = st.settings.data_dir.join("fiches");
    tokio::spawn(async move {
        for cat in missing {
            let png = dir.join(format!("fiche_{cat}.png"));
            let jpg = dir.join(format!("fiche_{cat}.jpg"));
            if jpg.is_file() {
                continue;
            }
            use crate::pybridge::py_str;
            // resolution="hd" : une vignette 512 px, sans upscale 4K (le
            // defaut "1024_upscale" produisait une image 3840 px pour une icone).
            let code = format!(
                "from backend.core.sdgen import generer; import os, json; \
from PIL import Image; out = {}; jpeg = {}; \
m = generer({}, out, '', 512, 512, 4, -1, resolution='hd'); \
ok = os.path.exists(out); \
im = Image.open(out).convert('RGB') if ok else None; \
im.save(jpeg, 'JPEG', quality=85, optimize=True) if im else None; \
os.remove(out) if ok else None; \
print(json.dumps({{'ok': os.path.exists(jpeg)}}))",
                py_str(&png.to_string_lossy()),
                py_str(&jpg.to_string_lossy()),
                py_str(&illustration_prompt(&cat)),
            );
            let _ = crate::pybridge::py_json_timeout(
                &root,
                &code,
                std::time::Duration::from_secs(900),
            )
            .await;
        }
        ILLUSTRATION_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    });
}

async fn list(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    // Consigne : les documents d'entrainement/logiciel restent en base
    // mais sont invisibles dans l'UI (system=0 requis).
    let rows = st.db.query_all_json(
        "SELECT id, filename, size_bytes, chunk_count, created_at, category, description          FROM documents WHERE COALESCE(system,0)=0 ORDER BY id DESC",
        &[],
    )?;
    Ok(Json(json!({ "documents": rows })))
}

async fn describe_text(st: &Arc<crate::AppState>, filename: &str, excerpt: &str) -> String {
    let model = st.db.get_setting("model_rapide", "qwen3:8b").to_string();
    let prompt = format!(
        "Decris en UNE phrase tres courte (15 mots maximum), en francais SIMPLE \
comprehensible par une personne qui ne fait PAS du tout d informatique, ce que \
represente ce document (exemples de tournures : exercices qui ont servi a \
entrainer l IA, notes personnelles, liste de donnees, programme du logiciel, \
texte de reference). Evite les mots techniques et le jargon. Reponds \
uniquement la phrase, sans introduction.\n\
Nom du fichier : {}\nDebut du contenu :\n{}",
        filename, excerpt
    );
    let rep = match st
        .ollama
        .chat(
            &model,
            vec![localai_core::ollama::Msg { role: "user".into(), content: prompt }],
            0.0,
            60,
        )
        .await
    {
        Ok(r) => r,
        Err(_) => return String::new(),
    };
    let d = rep.trim().trim_matches(|c| c == '"' || c == '«' || c == '»').to_string();
    let d = d.trim().to_string();
    let d: String = d.chars().take(140).collect();
    d.trim_end_matches('.').trim().to_string()
}

fn doc_extrait(st: &Arc<crate::AppState>, id: i64) -> (String, String) {
    let rows = st.db
        .query_all_json("SELECT filename FROM documents WHERE id=?", &[&id])
        .unwrap_or_default();
    let filename = rows
        .first()
        .and_then(|r| r.get("filename"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let chunks = st.db
        .query_all_json(
            "SELECT content FROM document_chunks WHERE document_id=? ORDER BY position LIMIT 1",
            &[&id],
        )
        .unwrap_or_default();
    let extrait: String = chunks
        .first()
        .and_then(|c| c.get("content"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .chars()
        .take(400)
        .collect();
    (filename, extrait)
}

async fn fiches(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    let dir = st.settings.data_dir.join("fiches");
    let _ = std::fs::create_dir_all(&dir);
    // Comptage des fiches : uniquement les documents VISIBLES (les
    // documents systeme masques ne comptent pas dans les fiches).
    let rows = st.db.query_all_json(
        "SELECT category, COUNT(*) AS n FROM documents WHERE COALESCE(system,0)=0 GROUP BY category",
        &[],
    )?;
    let mut counts: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    let mut non_classes: i64 = 0;
    for r in rows {
        let c = r.get("category").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let n = r.get("n").and_then(|v| v.as_i64()).unwrap_or(0);
        if c.is_empty() {
            non_classes = n;
        } else {
            counts.insert(c, n);
        }
    }
    let mut fiches: Vec<Value> = Vec::new();
    let mut missing: Vec<String> = Vec::new();
    for c in CATEGORIES {
        let has = dir.join(format!("fiche_{c}.jpg")).is_file();
        if !has {
            missing.push((*c).to_string());
        }
        fiches.push(json!({
            "category": c,
            "count": counts.get(*c).copied().unwrap_or(0),
            "illustration": has,
        }));
    }
    // Categorie "Neo" : fichiers generes pendant le chat (photos 4K, STL...).
    {
        let has = dir.join("fiche_neo.jpg").is_file();
        if !has {
            missing.push("neo".to_string());
        }
        fiches.push(json!({
            "category": "neo",
            "count": neo_fichiers(&st.settings.root_dir).len() as i64,
            "illustration": has,
        }));
    }
    if !missing.is_empty() {
        spawn_illustrations(&st, missing);
    }
    Ok(Json(json!({ "fiches": fiches, "non_classes": non_classes })))
}

#[derive(Debug, Deserialize)]
pub struct ClassifyBody {
    pub limit: Option<i64>,
}

async fn classify(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ClassifyBody>,
) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    let limit = b.limit.unwrap_or(8).clamp(1, 20);
    let rows = st.db.query_all_json(
        "SELECT id, filename FROM documents WHERE category IS NULL OR category='' ORDER BY id LIMIT ?",
        &[&limit],
    )?;
    let mut done = 0;
    for r in rows {
        let id = r.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let filename = r.get("filename").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let chunks = st.db.query_all_json(
            "SELECT content FROM document_chunks WHERE document_id=? ORDER BY position LIMIT 1",
            &[&id],
        )?;
        let excerpt: String = chunks
            .first()
            .and_then(|c| c.get("content"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .chars()
            .take(400)
            .collect();
        let cat = classify_text(&st, &filename, &excerpt).await;
        let _ = st.db.execute("UPDATE documents SET category=? WHERE id=?", &[&cat.as_str(), &id]);
        done += 1;
    }
    let rem = st.db.query_all_json(
        "SELECT COUNT(*) AS n FROM documents WHERE category IS NULL OR category=''",
        &[],
    )?;
    let remaining = rem
        .first()
        .and_then(|r| r.get("n"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    Ok(Json(json!({ "classified": done, "remaining": remaining })))
}

#[derive(Debug, Deserialize)]
pub struct DescribeBody {
    pub limit: Option<i64>,
}

async fn describe(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<DescribeBody>,
) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    let limit = b.limit.unwrap_or(8).clamp(1, 20);
    let rows = st.db.query_all_json(
        "SELECT id FROM documents WHERE description IS NULL OR description='' ORDER BY id LIMIT ?",
        &[&limit],
    )?;
    let mut done = 0;
    for r in rows {
        let id = r.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let (filename, extrait) = doc_extrait(&st, id);
        let d = describe_text(&st, &filename, &extrait).await;
        if !d.is_empty() {
            let _ = st.db.execute(
                "UPDATE documents SET description=? WHERE id=?",
                &[&d.as_str(), &id],
            );
            done += 1;
        }
    }
    let rem = st.db.query_all_json(
        "SELECT COUNT(*) AS n FROM documents WHERE description IS NULL OR description=''",
        &[],
    )?;
    let remaining = rem
        .first()
        .and_then(|r| r.get("n"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    Ok(Json(json!({ "described": done, "remaining": remaining })))
}

static APPRENTISSAGE_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

#[derive(Debug, Deserialize)]
pub struct ApprentissageBody {
    pub category: String,
}

fn apprentissage_pid(st: &Arc<crate::AppState>) -> u32 {
    std::fs::read_to_string(st.settings.data_dir.join("apprentissage.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|m| m.get("pid").and_then(|v| v.as_u64()))
        .unwrap_or(0) as u32
}

async fn apprentissage(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ApprentissageBody>,
) -> ApiResult<Json<Value>> {
    let cat = b.category.trim().to_lowercase();
    if !CATEGORIES.contains(&cat.as_str()) {
        return Err(ApiError(localai_core::AppError::BadRequest("categorie inconnue".into())));
    }
    if APPRENTISSAGE_RUNNING.load(std::sync::atomic::Ordering::SeqCst) {
        let ancien = apprentissage_pid(&st);
        let vivant = ancien > 0 && crate::routes::finetune::pid_alive(ancien).await;
        if vivant {
            return Ok(Json(json!({ "ok": false, "detail": "un apprentissage est deja en cours" })));
        }
        APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    }
    APPRENTISSAGE_RUNNING.store(true, std::sync::atomic::Ordering::SeqCst);
    let root = st.settings.root_dir.clone();
    let py = crate::pybridge::venv_python(&root);
    if !py.is_file() {
        APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let logs = root.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let log = logs.join(format!("apprentissage_{cat}.out"));
    let pid = match crate::routes::finetune::spawn_detached(&py, &["-u", "apprentissage.py", &cat], &root, &log)
        .await
    {
        Ok(p) => p,
        Err(e) => {
            APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
            return Err(ApiError(localai_core::AppError::Internal(format!("lancement apprentissage: {e}"))));
        }
    };
    let meta = json!({ "pid": pid, "category": cat, "started": crate::routes::finetune::now_unix() });
    let _ = std::fs::write(st.settings.data_dir.join("apprentissage.json"), meta.to_string());
    Ok(Json(json!({ "ok": true, "category": cat, "pid": pid })))
}

async fn apprentissage_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let pid = apprentissage_pid(&st);
    let meta: Value = std::fs::read_to_string(st.settings.data_dir.join("apprentissage.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}));
    let cat = meta.get("category").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if pid == 0 {
        return Ok(Json(json!({ "running": false, "category": "", "etape": "" })));
    }
    let running = crate::routes::finetune::pid_alive(pid).await;
    if !running {
        APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    }
    let log = st.settings.root_dir.join("logs").join(format!("apprentissage_{cat}.out"));
    let etape = std::fs::read_to_string(&log)
        .map(|t| {
            t.lines()
                .rev()
                .find(|l| !l.trim().is_empty())
                .unwrap_or("")
                .trim()
                .trim_start_matches("[apprentissage] ")
                .chars()
                .take(160)
                .collect::<String>()
        })
        .unwrap_or_default();
    Ok(Json(json!({ "running": running, "category": cat, "etape": etape })))
}

/// Liste les fichiers generes par le chat (photos, meshes, rendus) dans
/// workspace/ et data/img_gen : classement automatique en categorie "Neo".
/// Retourne (chemin relatif a la racine, taille octets, modifie unix sec).
fn neo_fichiers(root: &std::path::Path) -> Vec<(String, u64, i64)> {
    let mut out: Vec<(String, u64, i64)> = Vec::new();
    let exts = ["png", "jpg", "jpeg", "webp", "tif", "tiff", "exr",
        "stl", "scad", "gcode", "obj", "3mf"];
    for base in ["workspace", "data/img_gen"] {
        let dir = root.join(base);
        let mut pile: Vec<std::path::PathBuf> = vec![dir];
        let mut garde = 0usize;
        while let Some(d) = pile.pop() {
            garde += 1;
            if garde > 64 {
                break;
            }
            let Ok(entries) = std::fs::read_dir(&d) else { continue };
            for e in entries.flatten() {
                let p = e.path();
                if p.is_dir() {
                    if p.components().count() < 6 {
                        pile.push(p);
                    }
                    continue;
                }
                let Some(ext) = p.extension().and_then(|x| x.to_str()) else { continue };
                if !exts.contains(&ext.to_ascii_lowercase().as_str()) {
                    continue;
                }
                // Aperçus JPEG côté à côté des masters TIFF/EXR : servis
                // à l'affichage mais jamais listés comme fichiers Néo.
                if let Some(nom) = p.file_name().and_then(|n| n.to_str()) {
                    if nom.to_ascii_lowercase().ends_with("_apercu.jpg") {
                        continue;
                    }
                }
                let Ok(meta) = std::fs::metadata(&p) else { continue };
                let taille = meta.len();
                let modifie = meta
                    .modified()
                    .ok()
                    .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs() as i64)
                    .unwrap_or(0);
                let relatif = p
                    .strip_prefix(root)
                    .map(|r| r.to_string_lossy().to_string())
                    .unwrap_or_else(|_| p.to_string_lossy().to_string());
                out.push((relatif, taille, modifie));
            }
        }
    }
    out.sort_by_key(|e| std::cmp::Reverse(e.2));
    out.truncate(300);
    out
}

#[derive(Debug, Deserialize)]
pub struct NeoDeleteBody {
    pub path: String,
}

/// POST /api/documents/neo-delete : supprime un fichier genere (consigne
/// §3). Garde-fou : chemin sous la racine uniquement + extensions sures ;
/// l'apercu JPEG cote a cote est supprime avec son master.
async fn neo_delete(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<NeoDeleteBody>,
) -> ApiResult<Json<Value>> {
    let root = st.settings.root_dir.canonicalize()
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let cand = root.join(b.path.trim_start_matches(['/', '\\'].as_slice()));
    let canon = cand.canonicalize()
        .map_err(|_e| ApiError(localai_core::AppError::BadRequest("fichier introuvable".into())))?;
    if !canon.starts_with(&root) || !canon.is_file() {
        return Ok(Json(json!({ "ok": false, "error": "chemin invalide" })));
    }
    let ext = canon.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
    let exts_sures = ["png", "jpg", "jpeg", "webp", "tif", "tiff", "exr",
        "stl", "scad", "gcode", "obj", "3mf"];
    if !exts_sures.contains(&ext.as_str()) {
        return Ok(Json(json!({ "ok": false, "error": "extension non supprimable" })));
    }
    let mut supprimes = vec![canon.to_string_lossy().to_string()];
    // Apercu JPEG cote a cote : supprime avec le master.
    if let Some(stem) = canon.file_stem().and_then(|s| s.to_str()) {
        if let Some(parent) = canon.parent() {
            let apercu = parent.join(format!("{stem}_apercu.jpg"));
            if apercu.is_file() {
                let _ = std::fs::remove_file(&apercu);
                supprimes.push(apercu.to_string_lossy().to_string());
            }
        }
    }
    match std::fs::remove_file(&canon) {
        Ok(()) => Ok(Json(json!({ "ok": true, "supprimes": supprimes }))),
        Err(e) => Ok(Json(json!({ "ok": false, "error": e.to_string() }))),
    }
}

/// GET /api/documents/neo-files : fichiers de la categorie "Neo".
async fn neo_files(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let fichiers = neo_fichiers(&st.settings.root_dir);
    let arr: Vec<Value> = fichiers
        .into_iter()
        .map(|(chemin, taille, modifie)| {
            let nom = std::path::Path::new(&chemin)
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| chemin.clone());
            json!({ "name": nom, "path": chemin, "size_bytes": taille, "modified": modifie })
        })
        .collect();
    Ok(Json(json!({ "files": arr })))
}

fn unpack_f32le(blob: &[u8]) -> Vec<f32> {
    let mut out = Vec::with_capacity(blob.len() / 4);
    let mut i = 0;
    while i + 4 <= blob.len() {
        out.push(f32::from_le_bytes([blob[i], blob[i + 1], blob[i + 2], blob[i + 3]]));
        i += 4;
    }
    out
}

fn pack_f32le(v: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(v.len() * 4);
    for f in v {
        out.extend_from_slice(&f.to_le_bytes());
    }
    out
}

/// Passages a garder quand le trieur doute : ceux du groupe de tete, si ce
/// groupe se detache nettement des 20 meilleurs (ecart meilleur - mediane au
/// moins 0,12 ; mesure du 09/11/2026 avec qwen3-embedding : questions avec une
/// reponse 0,09 a 0,15, questions hors sujet 0,03 a 0,09). Indices tries.
pub(crate) fn groupe_detache(sims: &[f32], k: usize) -> Vec<usize> {
    let mut ordre: Vec<usize> = (0..sims.len()).collect();
    ordre.sort_by(|a, b| sims[*b].partial_cmp(&sims[*a]).unwrap_or(std::cmp::Ordering::Equal));
    let tete: Vec<usize> = ordre.iter().take(20).cloned().collect();
    if tete.len() < 5 {
        return Vec::new();
    }
    let meilleur = sims[tete[0]];
    let mediane = sims[tete[tete.len() / 2]];
    if meilleur - mediane < 0.12 {
        return Vec::new();
    }
    tete.into_iter().filter(|j| sims[*j] >= meilleur - 0.03).take(k).collect()
}

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    // Vecteurs de deux modeles differents : incomparables (zip tronquait
    // silencieusement au plus court et donnait des scores sans aucun sens).
    if a.len() != b.len() {
        return 0.0;
    }
    let (mut dot, mut na, mut nb) = (0.0f64, 0.0f64, 0.0f64);
    for (x, y) in a.iter().zip(b.iter()) {
        dot += (*x as f64) * (*y as f64);
        na += (*x as f64) * (*x as f64);
        nb += (*y as f64) * (*y as f64);
    }
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    (dot / (na.sqrt() * nb.sqrt())) as f32
}

fn chunk_text(text: &str) -> Vec<String> {
    // Fenetres de 800 caracteres avec recouvrement de 100 (proche du Python).
    const WIN: usize = 800;
    const OVERLAP: usize = 100;
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut start = 0;
    while start < chars.len() {
        let end = (start + WIN).min(chars.len());
        let s: String = chars[start..end].iter().collect();
        let s = s.trim().to_string();
        if !s.is_empty() {
            out.push(s);
        }
        if end == chars.len() {
            break;
        }
        start = end.saturating_sub(OVERLAP);
    }
    out
}

async fn extract_via_python(
    root: &std::path::Path,
    path: &std::path::Path,
    suffix: &str,
) -> Result<String, ApiError> {
    let code = match suffix {
        ".pdf" => "from pypdf import PdfReader; import sys; r=PdfReader(sys.argv[1]); print('\\n'.join((p.extract_text() or '') for p in r.pages))",
        ".docx" => "from docx import Document; import sys; d=Document(sys.argv[1]); print('\\n'.join(p.text for p in d.paragraphs))",
        ".xlsx" | ".xlsm" => "from openpyxl import load_workbook; import sys; wb=load_workbook(sys.argv[1], read_only=True, data_only=True); print('\\n'.join(' '.join(str(c.value) for c in row if c.value) for ws in wb.worksheets for row in ws.rows))",
        _ => return Err(ApiError(localai_core::AppError::BadRequest(format!("Format non supporte: {suffix}")))),
    };
    // Pont commun : UTF-8 (accents intacts), delai borne (un PDF piege ne
    // bloque plus la requete), process tue en cas d'abandon.
    crate::pybridge::py_text_timeout(
        root,
        &["-c", code, &path.to_string_lossy()],
        std::time::Duration::from_secs(180),
    )
    .await
    .map_err(|e| ApiError(localai_core::AppError::Internal(format!("extraction {suffix}: {e}"))))
}

async fn upload(
    State(st): State<Arc<crate::AppState>>,
    mut mp: Multipart,
) -> ApiResult<Json<Value>> {
    let upload_dir = st.settings.data_dir.join("documents");
    let _ = std::fs::create_dir_all(&upload_dir);
    // Tous les fichiers de la requete : la page en envoie plusieurs d'un coup.
    // Avant, seul le premier etait indexe et la page annoncait le compte
    // qu'elle avait envoye (essai du 11/11/2026 : 2 envoyes, 1 indexe).
    let mut indexes: Vec<Value> = Vec::new();
    let mut refuses: Vec<Value> = Vec::new();
    let mut premiere_erreur: Option<ApiError> = None;
    while let Some(field) = mp
        .next_field()
        .await
        .map_err(|e| ApiError(localai_core::AppError::BadRequest(e.to_string())))?
    {
        let filename = field.file_name().unwrap_or("document.txt").to_string();
        let resultat = match field.bytes().await {
            Ok(raw) => indexer_fichier(&st, &upload_dir, &filename, &raw).await,
            Err(e) => Err(ApiError(localai_core::AppError::BadRequest(e.to_string()))),
        };
        match resultat {
            Ok(v) => indexes.push(v),
            Err(e) => {
                refuses.push(json!({ "filename": filename, "raison": e.0.to_string() }));
                premiere_erreur.get_or_insert(e);
            }
        }
    }
    if indexes.is_empty() {
        // Rien d'indexe : l'erreur du (premier) fichier, avec son code HTTP.
        return Err(premiere_erreur
            .unwrap_or_else(|| ApiError(localai_core::AppError::BadRequest("aucun fichier".into()))));
    }
    // Les champs du premier document restent a la racine (anciens clients).
    let mut reponse = indexes[0].clone();
    reponse["count"] = json!(indexes.len());
    reponse["documents"] = json!(indexes);
    reponse["refuses"] = json!(refuses);
    Ok(Json(reponse))
}

/// Indexe un fichier importe : extraction du texte, decoupage, vecteurs,
/// enregistrement, puis categorie et description.
async fn indexer_fichier(
    st: &Arc<crate::AppState>,
    upload_dir: &std::path::Path,
    filename: &str,
    raw: &[u8],
) -> Result<Value, ApiError> {
    {
        let suffix = std::path::Path::new(&filename)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{e}").to_lowercase())
            .unwrap_or_default();
        const ALLOWED: &[&str] = &[".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv", ".json", ".py", ".log"];
        if !ALLOWED.contains(&suffix.as_str()) {
            return Err(ApiError(localai_core::AppError::BadRequest(format!("Format non supporte: {suffix}"))));
        }
        if raw.len() > MAX_FILE_SIZE {
            return Err(ApiError(localai_core::AppError::BadRequest("Fichier trop volumineux (max 30 Mo)".into())));
        }
        let text = match suffix.as_str() {
            ".pdf" | ".docx" | ".xlsx" | ".xlsm" => {
                let tmp = upload_dir.join(format!("tmp_{}.{}", chrono::Local::now().format("%Y%m%d%H%M%S%f"), &suffix[1..]));
                std::fs::write(&tmp, &raw)
                    .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
                let t = extract_via_python(&st.settings.root_dir, &tmp, &suffix).await;
                let _ = std::fs::remove_file(&tmp);
                t?
            }
            _ => String::from_utf8_lossy(&raw).to_string(),
        };
        let chunks = chunk_text(&text);
        if chunks.is_empty() {
            return Err(ApiError(localai_core::AppError::BadRequest("Aucun texte extractible".into())));
        }
        // Embeddings par lots de 16.
        let mut vectors: Vec<Vec<f32>> = Vec::new();
        for batch in chunks.chunks(16) {
            let vs = st
                .ollama
                .embed(&modele_index(&st.db), batch.iter().map(|s| s.to_string()).collect())
                .await
                .map_err(|e| ApiError(localai_core::AppError::Ollama(format!("Embedding impossible: {e}"))))?;
            vectors.extend(vs);
        }
        let now = now_iso();
        let doc_id = st.db.execute(
            "INSERT INTO documents (filename, size_bytes, chunk_count, created_at) VALUES (?,?,?,?)",
            &[&filename, &(raw.len() as i64), &(chunks.len() as i64), &now.as_str()],
        )?;
        for (pos, (content, vec)) in chunks.iter().zip(vectors.iter()).enumerate() {
            let blob = pack_f32le(vec);
            // rusqlite ToSql pour &[u8] via Value Blob.
            let blob_val = rusqlite::types::Value::Blob(blob);
            st.db.execute(
                "INSERT INTO document_chunks (document_id, position, content, embedding) VALUES (?,?,?,?)",
                &[&doc_id, &(pos as i64), &content.as_str(), &blob_val],
            )?;
        }
        let final_path = upload_dir.join(format!("doc_{doc_id}{suffix}"));
        std::fs::write(&final_path, &raw)
            .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
        ensure_category(&st);
        let excerpt: String = text.chars().take(400).collect();
        let cat = classify_text(&st, &filename, &excerpt).await;
        let desc = describe_text(&st, &filename, &excerpt).await;
        let _ = st.db.execute(
            "UPDATE documents SET category=?, description=? WHERE id=?",
            &[&cat.as_str(), &desc.as_str(), &doc_id],
        );
        Ok(json!({ "id": doc_id, "filename": filename, "chunks": chunks.len(), "category": cat, "description": desc }))
    }
}

#[derive(Debug, Deserialize)]
pub struct ReindexBody {
    pub modele: Option<String>,
}

/// POST /api/documents/reindexer {modele?} : recalcule TOUS les vecteurs avec
/// `modele` (defaut EMBED_MODEL_CIBLE). Tout est calcule en memoire avant la
/// moindre ecriture, puis ecrit en une transaction et le reglage
/// `modele_plongement` bascule : un echec en route laisse l'index intact.
async fn reindexer(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ReindexBody>,
) -> ApiResult<Json<Value>> {
    let modele = b.modele.filter(|m| !m.trim().is_empty()).unwrap_or_else(|| EMBED_MODEL_CIBLE.to_string());
    let debut = std::time::Instant::now();
    let lignes = st.db.query_all_json("SELECT id, content FROM document_chunks ORDER BY id", &[])?;
    let ids: Vec<i64> = lignes.iter().filter_map(|l| l.get("id").and_then(|v| v.as_i64())).collect();
    let textes: Vec<String> = lignes
        .iter()
        .map(|l| l.get("content").and_then(|v| v.as_str()).unwrap_or("").to_string())
        .collect();
    let mut vecteurs: Vec<Vec<f32>> = Vec::with_capacity(textes.len());
    for lot in textes.chunks(32) {
        let vs = st.ollama.embed(&modele, lot.to_vec()).await.map_err(|e| {
            ApiError(localai_core::AppError::Ollama(format!("reindexation interrompue, index intact : {e}")))
        })?;
        vecteurs.extend(vs);
    }
    if vecteurs.len() != ids.len() || vecteurs.iter().any(|v| v.len() < 128) {
        return Err(ApiError(localai_core::AppError::Ollama(
            "reindexation : reponse d'embedding incomplete, index intact".into())));
    }
    let dimension = vecteurs.first().map(|v| v.len()).unwrap_or(0);
    let db_path = st.settings.db_path();
    let n = ids.len();
    tokio::task::spawn_blocking(move || -> rusqlite::Result<()> {
        let mut c = rusqlite::Connection::open(db_path)?;
        let tx = c.transaction()?;
        {
            let mut maj = tx.prepare("UPDATE document_chunks SET embedding=? WHERE id=?")?;
            for (id, v) in ids.iter().zip(vecteurs.iter()) {
                maj.execute(rusqlite::params![pack_f32le(v), id])?;
            }
        }
        tx.commit()
    })
    .await
    .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?
    .map_err(|e| ApiError(localai_core::AppError::Internal(format!("ecriture de l'index : {e}"))))?;
    st.db.set_setting("modele_plongement", &modele)?;
    Ok(Json(json!({ "ok": true, "modele": modele, "passages": n, "dimension": dimension,
                     "duree_s": debut.elapsed().as_secs_f32() })))
}

async fn remove(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    st.db.execute("DELETE FROM document_chunks WHERE document_id=?", &[&id])?;
    st.db.execute("DELETE FROM documents WHERE id=?", &[&id])?;
    let upload_dir = st.settings.data_dir.join("documents");
    if let Ok(entries) = std::fs::read_dir(&upload_dir) {
        for e in entries.flatten() {
            if let Some(n) = e.file_name().to_str() {
                if n.starts_with(&format!("doc_{id}.")) {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
    }
    Ok(Json(json!({ "deleted": id })))
}

/// Echappe le texte insere dans la page HTML des sources.
fn echapper_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// GET /api/documents/:id/content
///
/// Page lisible ciblee par les citations du chat (`#p<position>` fait defiler
/// jusqu'a l'extrait). Volontairement en HTML plutot qu'en JSON : le lien
/// s'ouvre dans un onglet, l'utilisateur doit voir le document, pas du JSON.
async fn content(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<axum::response::Html<String>> {
    let doc = st
        .db
        .query_one_json(
            "SELECT filename, created_at FROM documents WHERE id=?",
            &[&id],
        )?
        .ok_or_else(|| ApiError(localai_core::AppError::NotFound("document inconnu".into())))?;
    let filename = doc["filename"].as_str().unwrap_or("document");
    let cree = doc["created_at"].as_str().unwrap_or("");
    let extraits = st.db.query_all_json(
        "SELECT position, content FROM document_chunks WHERE document_id=? ORDER BY position",
        &[&id],
    )?;

    let mut corps = String::new();
    for e in &extraits {
        let pos = e["position"].as_i64().unwrap_or(0);
        let texte = echapper_html(e["content"].as_str().unwrap_or(""));
        corps.push_str(&format!(
            "<section id=\"p{pos}\"><h2>Extrait {pos}</h2><pre>{texte}</pre></section>\n"
        ));
    }
    let html = format!(
        r#"<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre}</title>
<style>
  body{{background:#0b0e14;color:#dfe6f3;font:15px/1.6 system-ui,sans-serif;margin:0;padding:32px}}
  main{{max-width:860px;margin:0 auto}}
  h1{{font-size:22px;margin:0 0 4px}}
  .meta{{color:#8c9ab3;font-size:13px;margin-bottom:28px}}
  h2{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#5eead4;margin:28px 0 6px}}
  pre{{white-space:pre-wrap;word-wrap:break-word;background:#151b28;border:1px solid #232c3d;
       border-radius:10px;padding:16px;margin:0;font:14px/1.6 ui-monospace,monospace}}
  section:target pre{{border-color:#5eead4;box-shadow:0 0 0 1px #5eead4}}
  a{{color:#818cf8}}
</style>
</head>
<body><main>
<p><a href="/">&larr; retour a N&#233;o</a></p>
<h1>{titre}</h1>
<p class="meta">{nb} extrait(s) &middot; ajoute le {cree}</p>
{corps}
</main></body></html>"#,
        titre = echapper_html(filename),
        nb = extraits.len(),
        cree = echapper_html(cree),
        corps = corps,
    );
    Ok(axum::response::Html(html))
}

#[derive(Debug, Deserialize)]
pub struct SearchBody {
    pub query: String,
    pub top_k: Option<i64>,
}

async fn search(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<SearchBody>,
) -> ApiResult<Json<Value>> {
    // S'assurer que l'index FTS5 existe (BM25).
    ensure_category(&st);
    let k = b.top_k.unwrap_or(5).clamp(1, 20) as usize;
    let alpha = 0.6; // poids du vecteur dans le score hybride

    // --- 1) Embedding de la requete (async), puis calculs en tache bloquante :
    // parcours SQLite + cosinus sur tous les extraits ne doivent pas occuper
    // un thread du runtime.
    let vecs = st
        .ollama
        .embed(&modele_index(&st.db), vec![texte_requete(&modele_index(&st.db), &b.query)])
        .await
        .map_err(|e| ApiError(localai_core::AppError::Ollama(e.to_string())))?;
    let q = vecs.into_iter().next().unwrap_or_default();
    let db_path = st.settings.db_path();
    let requete = b.query.clone();
    let results = tokio::task::spawn_blocking(move || recherche_hybride(&db_path, &q, &requete, k, alpha))
        .await
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?
        .map_err(|e| ApiError(localai_core::AppError::from(e)))?;
    Ok(Json(json!({ "results": results })))
}

/// Requete FTS5 sure : chaque mot est cite (`"mot"`) et relie par OR. Une
/// saisie brute (`c++`, `l'IA`, `a:b`, guillemets) provoquait une erreur de
/// syntaxe MATCH, donc zero resultat BM25 en silence.
fn requete_fts(texte: &str) -> String {
    texte
        .split(|c: char| !c.is_alphanumeric())
        .filter(|m| m.chars().count() >= 2)
        .map(|m| format!("\"{m}\""))
        .collect::<Vec<_>>()
        .join(" OR ")
}

/// Fusion cosinus + BM25, indexee par l'id d'EXTRAIT (chunk). Avant : la
/// table BM25 (ids d'extraits) etait consultee avec l'id de document, et le
/// score BM25 etait inverse (le meilleur extrait obtenait 0).
fn recherche_hybride(
    db_path: &std::path::Path,
    q: &[f32],
    requete: &str,
    k: usize,
    alpha: f32,
) -> rusqlite::Result<Vec<Value>> {
    use std::collections::HashMap;
    let conn = rusqlite::Connection::open(db_path)?;
    // chunk_id -> (document_id, filename, position, content)
    let mut infos: HashMap<i64, (i64, String, i64, String)> = HashMap::new();
    let mut cosinus: HashMap<i64, f32> = HashMap::new();
    {
        let mut stmt = conn.prepare(
            "SELECT c.id, c.document_id, d.filename, c.position, c.content, c.embedding \
             FROM document_chunks c JOIN documents d ON d.id = c.document_id",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, Vec<u8>>(5).unwrap_or_default(),
            ))
        })?;
        for (id, doc, fichier, pos, contenu, emb) in rows.flatten() {
            if !emb.is_empty() {
                cosinus.insert(id, cosine(q, &unpack_f32le(&emb)));
            }
            infos.insert(id, (doc, fichier, pos, contenu));
        }
    }

    // bm25() est negatif : plus il est petit, meilleur est l'extrait.
    let mut bm25: HashMap<i64, f32> = HashMap::new();
    let fts = requete_fts(requete);
    if !fts.is_empty() {
        if let Ok(mut stmt) = conn.prepare(
            "SELECT rowid, bm25(document_chunks_fts) FROM document_chunks_fts \
             WHERE document_chunks_fts MATCH ?1 ORDER BY bm25(document_chunks_fts) LIMIT 100",
        ) {
            if let Ok(rows) = stmt.query_map([&fts], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, f64>(1)?))) {
                bm25.extend(rows.flatten().map(|(id, s)| (id, s.abs() as f32)));
            }
        }
    }

    let cos_max = cosinus.values().copied().fold(0.0f32, f32::max);
    let bm_max = bm25.values().copied().fold(0.0f32, f32::max);
    let norm = |v: f32, max: f32| if max > 0.0 { v / max } else { 0.0 };
    let mut hybride: Vec<(f32, i64)> = infos
        .keys()
        .filter(|id| cosinus.contains_key(id) || bm25.contains_key(id))
        .map(|id| {
            let b = norm(bm25.get(id).copied().unwrap_or(0.0), bm_max);
            // Extrait sans embedding : seul le BM25 compte (pondere 0,5).
            let score = match cosinus.get(id) {
                Some(c) => alpha * norm(*c, cos_max) + (1.0 - alpha) * b,
                None => 0.5 * b,
            };
            (score, *id)
        })
        .collect();
    hybride.sort_by(|a, b| b.0.total_cmp(&a.0));
    Ok(hybride
        .into_iter()
        .take(k)
        .filter_map(|(s, id)| {
            let (doc, fichier, pos, contenu) = infos.get(&id)?;
            Some(json!({ "document_id": doc, "filename": fichier, "position": pos,
                         "content": contenu, "score": (s * 10000.0).round() / 10000.0 }))
        })
        .collect())
}

/// Reference d'un extrait utilise comme contexte, renvoyee dans l'evenement
/// `meta` du chat pour que l'interface affiche des liens cliquables.
#[derive(Debug, Clone, serde::Serialize)]
pub(crate) struct Source {
    pub filename: String,
    pub url: String,
    pub document_id: i64,
    pub position: i64,
    pub score: f32,
}

/// Bloc de contexte RAG pour le chat, avec la liste des extraits retenus.
/// Reutilise par chat.rs. Reglages DB : rag_boost (1/0), rerank_url.
#[allow(clippy::too_many_arguments)]
pub(crate) async fn rag_block_detaille(
    db: &localai_core::db::Db,
    db_path: &std::path::Path,
    ollama: &localai_core::ollama::Ollama,
    http: &reqwest::Client,
    ollama_url: &str,
    query: &str,
    k: usize,
) -> (String, Vec<Source>) {
    crate::metrics::rag_requetes();
    let boost = db.get_setting("rag_boost", "1").trim() != "0";
    let rerank_url = db
        .get_setting("rerank_url", "http://127.0.0.1:8322")
        .to_string();

    // 1. Variantes de la requete : originale + reformulations LLM (timeout
    // borne : le RAG ne doit jamais retarder la reponse de plus de 10 s).
    let mut variantes: Vec<String> = vec![query.to_string()];
    if boost {
        let judge = db.get_setting("model_rapide", "qwen3:8b").to_string();
        let expansion = tokio::time::timeout(
            std::time::Duration::from_secs(10),
            crate::rag_boost::expand_queries(http, ollama_url, &judge, query),
        )
        .await;
        if let Ok(v) = expansion {
            for x in v.into_iter().skip(1) {
                variantes.push(x);
            }
        }
    }
    variantes.truncate(4);
    let nv = variantes.len();

    // 2. Embeddings de toutes les variantes en un seul appel.
    let modele = modele_index(db);
    let requetes: Vec<String> = variantes.iter().map(|v| texte_requete(&modele, v)).collect();
    let vecs = match ollama.embed(&modele, requetes).await {
        Ok(v) => v,
        Err(e) => {
            crate::metrics::embed_echecs();
            crate::metrics::rag_echecs();
            tracing::warn!("rag_block: embedding indisponible ({e})");
            return (String::new(), Vec::new());
        }
    };
    if vecs.is_empty() {
        crate::metrics::rag_vides();
        return (String::new(), Vec::new());
    }

    // 3. Chunks + scores de similarite par variante. Scope dedie : les
    // objets SQLite ne doivent pas traverser un await (future non-Send).
    // (document_id, filename, position, contenu, scores par variante)
    let chunks: Vec<(i64, String, i64, String, Vec<f32>)> = {
        let conn = match rusqlite::Connection::open(db_path) {
            Ok(c) => c,
            Err(e) => {
                crate::metrics::rag_echecs();
                tracing::warn!("rag_block: base illisible ({e})");
                return (String::new(), Vec::new());
            }
        };
        let mut stmt = match conn.prepare(
            "SELECT c.document_id, d.filename, c.position, c.content, c.embedding FROM document_chunks c JOIN documents d ON d.id = c.document_id") {
            Ok(s) => s,
            Err(e) => {
                crate::metrics::rag_echecs();
                tracing::warn!("rag_block: requete invalide ({e})");
                return (String::new(), Vec::new());
            }
        };
        let mut out: Vec<(i64, String, i64, String, Vec<f32>)> = Vec::new();
        if let Ok(rows) = stmt.query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, Vec<u8>>(4).unwrap_or_default(),
            ))
        }) {
            for r in rows.flatten() {
                if r.4.is_empty() {
                    continue;
                }
                let v = unpack_f32le(&r.4);
                let scores: Vec<f32> = vecs.iter().map(|q| cosine(q, &v)).collect();
                if scores.iter().any(|s| *s >= 0.05) {
                    out.push((r.0, r.1, r.2, r.3, scores));
                }
            }
        }
        out
    };
    if chunks.is_empty() {
        crate::metrics::rag_vides();
        return (String::new(), Vec::new());
    }

    // 4. Une liste classee par variante -> fusion RRF (avec une seule
    // variante, la RRF redonne l'ordre cosinus : code unique).
    let lists: Vec<Vec<(String, String, f32)>> = (0..nv)
        .map(|i| {
            let mut l: Vec<(usize, f32)> = chunks
                .iter()
                .enumerate()
                .map(|(j, c)| (j, c.4[i]))
                .collect();
            l.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            l.into_iter()
                .take(20)
                .map(|(j, s)| (j.to_string(), chunks[j].3.clone(), s))
                .collect()
        })
        .collect();
    let hits: Vec<(String, String, f32)> = crate::rag_boost::rrf(&lists, 60.0, 24);

    // 5. Rerank cross-encoder (repli : garde l'ordre RRF si le service est
    // absent). Le service declare les fichiers via l'index de chunks.
    let rerank_actif = boost;
    let (top, rerank_reel) = crate::rag_boost::rerank(http, &rerank_url, query, hits, k).await;
    // Seuillage "je ne sais pas" : si le meilleur score de rerank est trop faible,
    // aucun chunk n'est pertinent -> retourne vide, le chat saura qu'il n'a pas
    // de contexte documentaire fiable. Uniquement quand le rerank a REELLEMENT
    // tourne : en repli RRF, les scores valent ~1/61 et feraient tout rejeter.
    let mut top = top;
    if rerank_reel {
        if let Some((_, _, best_score)) = top.first() {
            if *best_score < 0.20 {
                // Le trieur est sceptique (question large : « quelles recettes
                // ai-je enregistrees ? » notait 0,008 des notes de cuisine
                // classees 1 a 5 par la recherche). On garde quand meme un
                // groupe de passages NETTEMENT detache du lot par la similarite ;
                // sinon, contexte vide comme avant.
                // Similarite a la question D'ORIGINE seulement : les
                // reformulations (variables d'un appel a l'autre) relevent les
                // passages hors sujet et ecrasaient l'ecart une fois sur deux.
                let sims: Vec<f32> = chunks
                    .iter()
                    .map(|c| c.4.first().cloned().unwrap_or(0.0))
                    .collect();
                let garde = groupe_detache(&sims, k);
                if garde.is_empty() {
                    crate::metrics::rag_vides();
                    return (String::new(), Vec::new());
                }
                top = garde
                    .into_iter()
                    .map(|j| (j.to_string(), chunks[j].3.clone(), sims[j]))
                    .collect();
            }
        }
    }
    let mut blocs: Vec<String> = Vec::new();
    let mut sources: Vec<Source> = Vec::new();
    for (id, contenu, score) in top {
        let Some(j) = id.parse::<usize>().ok() else {
            continue;
        };
        let Some((doc_id, filename, position, _, _)) = chunks.get(j) else {
            continue;
        };
        blocs.push(format!(
            "[Source: {filename} (extrait {position}, score {score:.2})]\n{contenu}"
        ));
        sources.push(Source {
            filename: filename.clone(),
            url: format!("/api/documents/{doc_id}/content#p{position}"),
            document_id: *doc_id,
            position: *position,
            score: (score * 100.0).round() / 100.0,
        });
    }
    if blocs.is_empty() {
        crate::metrics::rag_vides();
        return (String::new(), Vec::new());
    }
    tracing::info!(
        "rag_block: boost={boost} variantes={nv} chunks={} rerank={rerank_actif} retenus={}",
        chunks.len(),
        blocs.len()
    );
    (
        format!(
            "Contexte documentaire extrait des documents de l'utilisateur:\n\n{}",
            blocs.join("\n\n---\n\n")
        ),
        sources,
    )
}

/// Variante historique : ne renvoie que le bloc de texte.
#[allow(clippy::too_many_arguments, dead_code)]
pub(crate) async fn rag_block(
    db: &localai_core::db::Db,
    db_path: &std::path::Path,
    ollama: &localai_core::ollama::Ollama,
    http: &reqwest::Client,
    ollama_url: &str,
    query: &str,
    k: usize,
) -> String {
    rag_block_detaille(db, db_path, ollama, http, ollama_url, query, k)
        .await
        .0
}

// ---------------------------------------------------------------- tests

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requete_fts_neutralise_la_syntaxe() {
        assert_eq!(requete_fts("c++ l'IA a:b"), "\"IA\"");
        assert_eq!(requete_fts("réseau \"neuronal\""), "\"réseau\" OR \"neuronal\"");
        assert_eq!(requete_fts("?!"), "");
    }

    /// Base temporaire : l'extrait le plus pertinent pour BM25 doit sortir
    /// premier, avec l'id de DOCUMENT (et non l'id d'extrait).
    #[test]
    fn recherche_hybride_classe_et_identifie_correctement() {
        let chemin = std::env::temp_dir().join(format!("neo_rag_test_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&chemin);
        let conn = rusqlite::Connection::open(&chemin).unwrap();
        conn.execute_batch(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT);
             CREATE TABLE document_chunks (id INTEGER PRIMARY KEY, document_id INTEGER, position INTEGER, content TEXT, embedding BLOB);
             CREATE VIRTUAL TABLE document_chunks_fts USING fts5(content, content=document_chunks, content_rowid=id);
             INSERT INTO documents VALUES (7, 'chats.txt'), (9, 'divers.txt');
             INSERT INTO document_chunks VALUES (100, 9, 0, 'la meteo de demain', NULL);
             INSERT INTO document_chunks VALUES (101, 7, 0, 'chat chat chat : le chat dort', NULL);
             INSERT INTO document_chunks VALUES (102, 9, 1, 'un chat passe', NULL);
             INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild');",
        )
        .unwrap();
        drop(conn);
        let res = recherche_hybride(&chemin, &[], "chat", 5, 0.6).unwrap();
        let _ = std::fs::remove_file(&chemin);
        assert_eq!(res.len(), 2, "{res:?}");
        assert_eq!(res[0]["document_id"], 7);
        assert_eq!(res[0]["filename"], "chats.txt");
        assert!(res[0]["score"].as_f64().unwrap() > res[1]["score"].as_f64().unwrap());
    }

    /// Tests unitaires : aucun acces reseau, toujours executes.
    #[test]
    fn groupe_detache_seulement_si_net() {
        // Cuisine : 0,641 0,633 nettement au-dessus d'un lot vers 0,49.
        let mut sims = vec![0.641, 0.633, 0.58, 0.55, 0.532];
        sims.extend(std::iter::repeat(0.49).take(20));
        assert_eq!(groupe_detache(&sims, 4), vec![0, 1]);
        // Hors sujet : tout est plat.
        let plat: Vec<f32> = (0..25).map(|i| 0.50 - i as f32 * 0.003).collect();
        assert!(groupe_detache(&plat, 4).is_empty());
    }

    #[test]
    fn cosine_dimensions_differentes_nulle() {
        assert_eq!(cosine(&[1.0, 0.0, 0.0], &[1.0, 0.0]), 0.0);
    }

    #[test]
    fn requete_qwen3_avec_consigne_les_autres_telles_quelles() {
        assert!(texte_requete("qwen3-embedding:4b", "Où est la gare ?").ends_with("Query: Où est la gare ?"));
        assert_eq!(texte_requete("nomic-embed-text:latest", "Où est la gare ?"), "Où est la gare ?");
    }

    #[test]
    fn cosine_identiques_et_orthogonaux() {
        let a = vec![1.0f32, 0.0, 0.0];
        let b = vec![1.0f32, 0.0, 0.0];
        let c = vec![0.0f32, 1.0, 0.0];
        assert!((cosine(&a, &b) - 1.0).abs() < 1e-6);
        assert!(cosine(&a, &c).abs() < 1e-6);
        // Vecteur nul : pas de division par zero, score neutre.
        assert!(cosine(&a, &[0.0f32; 3]).is_finite());
    }

    #[test]
    fn echapper_html_neutralise_les_balises() {
        let brut = r#"<script>alert("x")</script> & suite"#;
        let sur = echapper_html(brut);
        assert!(!sur.contains('<'));
        assert!(!sur.contains('>'));
        assert!(!sur.contains('"'));
        assert!(sur.contains("&amp; suite"));
    }

    #[test]
    fn pack_unpack_aller_retour() {
        let v = vec![0.5f32, -1.25, 3.0, f32::MIN / 2.0];
        let relu = unpack_f32le(&pack_f32le(&v));
        assert_eq!(relu, v);
        // Blob tronque : on ne panique pas, on ignore les octets orphelins.
        let tronque = &pack_f32le(&v)[..7];
        assert_eq!(unpack_f32le(tronque).len(), 1);
    }

    #[test]
    fn chunk_text_vide_et_court() {
        assert!(chunk_text("").is_empty());
        assert!(chunk_text("   ").is_empty());
        assert_eq!(chunk_text("bonjour"), vec!["bonjour".to_string()]);
    }

    #[test]
    fn chunk_text_recouvre_les_fenetres() {
        // 2000 caracteres -> 3 fenetres de 800 avec recouvrement de 100.
        let texte: String = "a".repeat(2000);
        let chunks = chunk_text(&texte);
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0].chars().count(), 800);
        // Recouvrement : la fin du chunk 1 reapparait au debut du chunk 2.
        let fin1: String = chunks[0].chars().skip(700).collect();
        assert!(chunks[1].starts_with(&fin1));
    }

    /// Tests d'integration : exigent Ollama joignable. Actives par
    /// `NEO_LIVE_TESTS=1` pour que la CI et les machines sans GPU restent
    /// vertes (voir docs/TECHNIQUE.md).
    fn live() -> bool {
        std::env::var("NEO_LIVE_TESTS").map(|v| v == "1").unwrap_or(false)
    }

    fn ollama_url() -> String {
        std::env::var("LOCALAI_OLLAMA_HOST")
            .unwrap_or_else(|_| "http://127.0.0.1:11434".to_string())
    }

    #[tokio::test]
    async fn embed_retourne_un_vecteur_utilisable() {
        if !live() {
            eprintln!("ignore : NEO_LIVE_TESTS != 1");
            return;
        }
        let o = localai_core::ollama::Ollama::new(ollama_url()).expect("client ollama");
        let v = o
            .embed(EMBED_MODEL, vec!["le chat dort sur le clavier".to_string()])
            .await
            .expect("embed: Ollama doit repondre sur /api/embed");
        assert_eq!(v.len(), 1, "un vecteur par entree");
        assert!(v[0].len() >= 128, "dimension inattendue: {}", v[0].len());
        assert!(
            v[0].iter().any(|x| *x != 0.0),
            "vecteur entierement nul : le modele d'embedding est casse"
        );
    }

    /// Bout en bout de `rag_block` : document reel en base temporaire,
    /// embeddings reels, rerank volontairement injoignable pour verifier
    /// le repli silencieux (la panne qui avait echoue en production).
    #[tokio::test]
    async fn rag_block_retrouve_le_document_malgre_le_rerank_absent() {
        if !live() {
            eprintln!("ignore : NEO_LIVE_TESTS != 1");
            return;
        }
        let dir = std::env::temp_dir().join(format!("neo_test_rag_{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dossier temporaire");
        let db_path = dir.join("rag_test.db");
        let _ = std::fs::remove_file(&db_path);

        let db = localai_core::db::Db::open(&db_path).expect("base temporaire");
        // Boost desactive (pas d'expansion LLM) + rerank sur un port mort.
        db.set_setting("rag_boost", "0").expect("setting boost");
        db.set_setting("rerank_url", "http://127.0.0.1:1")
            .expect("setting rerank");

        let host = ollama_url();
        let o = localai_core::ollama::Ollama::new(&host).expect("client ollama");
        let http = reqwest::Client::new();

        const MARQUEUR: &str = "NEO-CAPSULE-7734";
        let texte = "Le code de deverrouillage de la capsule est NEO-CAPSULE-7734 \
                     et il est range dans le tiroir de gauche.";
        let vec = o
            .embed(EMBED_MODEL, vec![texte.to_string()])
            .await
            .expect("embed du document de test");
        let blob = rusqlite::types::Value::Blob(pack_f32le(&vec[0]));
        let now = now_iso();
        let doc_id = db
            .execute(
                "INSERT INTO documents (filename, size_bytes, chunk_count, created_at) VALUES (?,?,?,?)",
                &[&"capsule.md", &(texte.len() as i64), &1i64, &now.as_str()],
            )
            .expect("insert document");
        db.execute(
            "INSERT INTO document_chunks (document_id, position, content, embedding) VALUES (?,?,?,?)",
            &[&doc_id, &0i64, &texte, &blob],
        )
        .expect("insert chunk");

        let block = rag_block(&db, &db_path, &o, &http, &host, "quel est le code de la capsule ?", 3).await;

        assert!(
            block.contains(MARQUEUR),
            "rag_block n'a pas retrouve le document; bloc recu: {block:?}"
        );
        assert!(
            block.contains("capsule.md"),
            "la source doit etre citee; bloc recu: {block:?}"
        );
        // Le repli rerank doit avoir conserve le classement RRF, sans erreur.
        assert!(block.contains("Contexte documentaire"), "entete manquante");

        let _ = std::fs::remove_dir_all(&dir);
    }
}