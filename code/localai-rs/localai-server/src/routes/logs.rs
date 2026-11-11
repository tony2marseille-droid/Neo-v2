//! Journal general de Neo : lecture du JSONL ecrit par le backend Python
//! (`backend/core/logs.py`). Aucune ecriture ici : une seule source de verite.

use std::io::{Read, Seek, SeekFrom};
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    routing::get,
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/logs", get(lire))
        .route("/api/logs/stats", get(stats))
}

#[derive(Deserialize)]
struct Params {
    #[serde(default = "def_n")]
    n: usize,
    #[serde(default)]
    niveau: String,
    #[serde(default)]
    source: String,
}

fn def_n() -> usize {
    200
}

fn fichier(st: &Arc<crate::AppState>) -> std::path::PathBuf {
    st.settings.root_dir.join("logs").join("neo.log")
}

/// Lit la fin du journal (2 Mo max) sans charger tout le fichier.
fn queue(st: &Arc<crate::AppState>) -> String {
    let p = fichier(st);
    let Ok(mut f) = std::fs::File::open(&p) else {
        return String::new();
    };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let debut = len.saturating_sub(2 * 1024 * 1024);
    if f.seek(SeekFrom::Start(debut)).is_err() {
        return String::new();
    }
    // Octets puis conversion tolerante : le seek tombe souvent au milieu
    // d'un caractere accentue, et read_to_string echouait alors en entier
    // (page des journaux vide).
    let mut octets = Vec::new();
    let _ = f.take(2 * 1024 * 1024).read_to_end(&mut octets);
    let mut buf = String::from_utf8_lossy(&octets).into_owned();
    if debut > 0 {
        if let Some(i) = buf.find('\n') {
            buf.drain(..=i);
        }
    }
    buf
}

fn filtre(v: &Value, niveau: &str, source: &str) -> bool {
    if !niveau.is_empty() && v.get("niveau").and_then(|x| x.as_str()) != Some(niveau) {
        return false;
    }
    if !source.is_empty() {
        let s = v.get("source").and_then(|x| x.as_str()).unwrap_or("");
        if !s.to_lowercase().contains(&source.to_lowercase()) {
            return false;
        }
    }
    true
}

async fn lire(
    State(st): State<Arc<crate::AppState>>,
    Query(p): Query<Params>,
) -> Json<Value> {
    let n = p.n.clamp(1, 5000);
    let st2 = st.clone();
    let brut = tokio::task::spawn_blocking(move || queue(&st2))
        .await
        .unwrap_or_default();
    let mut entrees: Vec<Value> = brut
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(|v| filtre(v, &p.niveau, &p.source))
        .collect();
    if entrees.len() > n {
        entrees = entrees.split_off(entrees.len() - n);
    }
    Json(json!({
        "ok": true,
        "fichier": fichier(&st).to_string_lossy(),
        "total": entrees.len(),
        "entrees": entrees,
    }))
}

async fn stats(State(st): State<Arc<crate::AppState>>) -> Json<Value> {
    let st2 = st.clone();
    let brut = tokio::task::spawn_blocking(move || queue(&st2))
        .await
        .unwrap_or_default();
    let mut par_niveau: std::collections::BTreeMap<String, u64> = Default::default();
    let mut par_source: std::collections::HashMap<String, u64> = Default::default();
    let mut total = 0u64;
    for l in brut.lines() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        total += 1;
        let niv = v
            .get("niveau")
            .and_then(|x| x.as_str())
            .unwrap_or("?")
            .to_string();
        *par_niveau.entry(niv).or_insert(0) += 1;
        let src = v
            .get("source")
            .and_then(|x| x.as_str())
            .unwrap_or("?")
            .to_string();
        *par_source.entry(src).or_insert(0) += 1;
    }
    let mut sources: Vec<(String, u64)> = par_source.into_iter().collect();
    sources.sort_by_key(|s| std::cmp::Reverse(s.1));
    sources.truncate(15);
    let taille = std::fs::metadata(fichier(&st)).map(|m| m.len()).unwrap_or(0);
    Json(json!({
        "ok": true,
        "fichier": fichier(&st).to_string_lossy(),
        "taille_octets": taille,
        "entrees_recentes": total,
        "par_niveau": par_niveau,
        "par_source": sources,
    }))
}
