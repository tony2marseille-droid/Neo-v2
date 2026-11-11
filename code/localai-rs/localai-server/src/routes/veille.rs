//! Veille technologique : repos GitHub + modeles Hugging Face a la mode,
//! analyses par le cerveau (resume + gain potentiel pour le projet).

use std::sync::Arc;

use axum::{
    extract::State,
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};

use crate::error::ApiResult;
use crate::routes::finetune::{lancer_script_unique, pid_alive};

static VEILLE_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn dernier_fichier(st: &Arc<crate::AppState>) -> Option<std::path::PathBuf> {
    let dir = st.settings.data_dir.clone();
    let mut best: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
    for e in std::fs::read_dir(dir).ok()?.flatten() {
        let p = e.path();
        let Some(n) = p.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if !n.starts_with("veille_") || !n.ends_with(".jsonl") {
            continue;
        }
        let Ok(m) = e.metadata().and_then(|m| m.modified()) else {
            continue;
        };
        let remplacer = match &best {
            Some((t, _)) => m > *t,
            None => true,
        };
        if remplacer {
            best = Some((m, p));
        }
    }
    best.map(|(_, p)| p)
}

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/veille/lancer", post(lancer))
        .route("/api/veille/status", get(etat))
        .route("/api/veille", get(liste))
}

async fn lancer(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    lancer_script_unique(&st, &VEILLE_RUNNING, "veille.py", "veille").await
}

async fn etat(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let meta: Value = std::fs::read_to_string(st.settings.data_dir.join("veille.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}));
    let pid = meta.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let running = pid > 0 && pid_alive(pid).await;
    if !running {
        VEILLE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    }
    let nb = dernier_fichier(&st)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .map(|t| t.lines().filter(|l| !l.trim().is_empty()).count())
        .unwrap_or(0);
    let progres: Value = std::fs::read_to_string(st.settings.data_dir.join("veille_progress.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}));
    Ok(Json(json!({
        "running": running,
        "analyses": nb,
        "progres": progres,
    })))
}

async fn liste(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let Some(p) = dernier_fichier(&st) else {
        return Ok(Json(json!({ "veille": [] })));
    };
    let items: Vec<Value> = std::fs::read_to_string(&p)
        .map(|t| {
            t.lines()
                .filter(|l| !l.trim().is_empty())
                .filter_map(|l| serde_json::from_str(l).ok())
                .collect()
        })
        .unwrap_or_default();
    Ok(Json(json!({ "veille": items })))
}

