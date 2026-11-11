//! Conseil des 100 idees : generation + validation par le conseil multi-
//! etapes (detache), consultation, et lancement d'une amelioration approuvee
//! par l'utilisateur (le dev agent implemente l'idee dans le workspace).

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::routes::finetune::{lancer_script_unique, pid_alive, spawn_detached};

static CONSEIL_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn data_file(st: &Arc<crate::AppState>) -> std::path::PathBuf {
    st.settings.data_dir.join("conseil_idees.jsonl")
}


/// Liste des IDs d'idees deja implementees (dossier workspace/ameliorations).
fn idees_implementees(st: &Arc<crate::AppState>) -> Vec<i64> {
    let dir = st.settings.root_dir.join("workspace").join("ameliorations");
    let mut ids = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
            if e.path().is_dir() {
                // Un dossier est "implemente" s'il contient au moins un .py ou .md
                let a_du_code = e.path().join("code.py").is_file()
                    || e.path().join("implementation.md").is_file();
                if a_du_code {
                    if let Some(nom) = e.file_name().to_str() {
                        if let Ok(id) = nom.parse::<i64>() {
                            ids.push(id);
                        }
                    }
                }
            }
        }
    }
    ids
}

fn idees(st: &Arc<crate::AppState>) -> Vec<Value> {
    std::fs::read_to_string(data_file(st))
        .map(|t| {
            t.lines()
                .filter(|l| !l.trim().is_empty())
                .filter_map(|l| serde_json::from_str(l).ok())
                .collect()
        })
        .unwrap_or_default()
}

/// IDs des idees installees dans le projet (data/ameliorations.json).
fn idees_appliquees(st: &Arc<crate::AppState>) -> Vec<i64> {
    let p = st.settings.data_dir.join("ameliorations.json");
    let Ok(txt) = std::fs::read_to_string(p) else {
        return Vec::new();
    };
    let Ok(v) = serde_json::from_str::<Value>(&txt) else {
        return Vec::new();
    };
    v.as_object()
        .map(|o| o.keys().filter_map(|k| k.parse::<i64>().ok()).collect())
        .unwrap_or_default()
}

fn sauver(st: &Arc<crate::AppState>, idees: &[Value]) -> bool {
    use std::io::Write;
    let Ok(mut f) = std::fs::File::create(data_file(st)) else {
        return false;
    };
    for e in idees {
        if writeln!(f, "{e}").is_err() {
            return false;
        }
    }
    true
}

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/conseil/lancer", post(lancer))
        .route("/api/conseil/status", get(etat))
        .route("/api/conseil/idees", get(liste))
        .route("/api/conseil/appliquees", get(appliquees))
        .route("/api/conseil/idee/:id/lancer", post(lancer_idee))
        .route("/api/conseil/idee/:id/appliquer", post(appliquer_idee))
        .route("/api/conseil/idee/:id/desinstaller", post(desinstaller_idee))
}

async fn lancer(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    lancer_script_unique(&st, &CONSEIL_RUNNING, "conseil.py", "conseil").await
}

async fn etat(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let meta: Value = std::fs::read_to_string(st.settings.data_dir.join("conseil.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}));
    let pid = meta.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let running = pid > 0 && pid_alive(pid).await;
    if !running {
        CONSEIL_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    }
    let nb = idees(&st).len();
    Ok(Json(json!({ "running": running, "idees": nb })))
}

async fn liste(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let mut liste_idees = idees(&st);
    let faites = idees_implementees(&st);
    let appliquees = idees_appliquees(&st);
    let mut modifie = false;
    for idee in liste_idees.iter_mut() {
        let id = idee.get("id").and_then(|x| x.as_i64()).unwrap_or(0);
        let statut = idee.get("statut").and_then(|x| x.as_str()).unwrap_or("");
        let cible = if appliquees.contains(&id) {
            "appliquee"
        } else if faites.contains(&id) {
            "terminee"
        } else {
            ""
        };
        if !cible.is_empty() && statut != cible {
            idee["statut"] = json!(cible);
            modifie = true;
        }
    }
    if modifie {
        let _ = sauver(&st, &liste_idees);
    }
    Ok(Json(json!({ "idees": liste_idees })))
}


async fn lancer_idee(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    let mut liste_idees = idees(&st);
    let Some(idee) = liste_idees
        .iter_mut()
        .find(|v| v.get("id").and_then(|x| x.as_i64()) == Some(id))
    else {
        return Err(ApiError(localai_core::AppError::NotFound(
            "idee introuvable".into(),
        )));
    };
    let statut = idee
        .get("statut")
        .and_then(|v| v.as_str())
        .unwrap_or("proposee");
    if statut == "en_cours" {
        return Ok(Json(json!({ "ok": false, "detail": "deja en cours" })));
    }
    if statut == "terminee" {
        return Ok(Json(json!({ "ok": false, "detail": "deja implementee (workspace/ameliorations)" })));
    }
    idee["statut"] = json!("approuvee");
    let titre = idee
        .get("titre")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let desc = idee
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let root = st.settings.root_dir.clone();
    let py = crate::pybridge::venv_python(&root);
    let script = root.join("impl_idee.py");
    if !py.is_file() || !script.is_file() {
        return Ok(Json(
            json!({ "ok": true, "statut": "approuvee", "detail": "impl_idee absent, idee approuvee" }),
        ));
    }
    let logs = root.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let log = logs.join(format!("impl_idee_{id}.out"));
    // Statut « en_cours » seulement si le lancement a reussi : sinon l'idee
    // restait bloquee pour toujours (« deja en cours » refuse la relance).
    if let Err(e) = spawn_detached(
        &py,
        &["-u", &script.to_string_lossy(), &id.to_string(), &titre, &desc],
        &root,
        &log,
    )
    .await
    {
        return Err(ApiError(localai_core::AppError::Internal(format!("lancement impl_idee: {e}"))));
    }
    idee["statut"] = json!("en_cours");
    if !sauver(&st, &liste_idees) {
        return Err(ApiError(localai_core::AppError::Internal(
            "ecriture impossible".into(),
        )));
    }
    Ok(Json(json!({ "ok": true, "statut": "en_cours" })))
}

/// Installe l'idee (deja implementee dans le workspace) comme module du
/// projet : compilation, copie dans ameliorations/, manifeste, backup.
async fn appliquer_idee(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    let code = format!(
        "from backend.core.ameliorations import appliquer; print(json.dumps(appliquer({id}), ensure_ascii=False))"
    );
    let v = crate::pybridge::py_json_timeout(
        &st.settings.root_dir,
        &code,
        std::time::Duration::from_secs(120),
    )
    .await?;
    Ok(Json(v))
}

async fn desinstaller_idee(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    let code = format!(
        "from backend.core.ameliorations import desinstaller; print(json.dumps(desinstaller({id}), ensure_ascii=False))"
    );
    let v = crate::pybridge::py_json_timeout(
        &st.settings.root_dir,
        &code,
        std::time::Duration::from_secs(60),
    )
    .await?;
    Ok(Json(v))
}

async fn appliquees(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let code = "from backend.core.ameliorations import lister; print(json.dumps(lister(), ensure_ascii=False))";
    let v = crate::pybridge::py_json_timeout(
        &st.settings.root_dir,
        code,
        std::time::Duration::from_secs(60),
    )
    .await?;
    Ok(Json(v))
}

