use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde_json::Value;

use crate::error::ApiResult;
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/version", get(version))
        .route("/api/tokens/usage", get(tokens_usage))
        .route("/api/system/info", get(info))
        .route("/api/system/services", get(services))
        .route("/api/system/gpu", get(gpu))
        .route("/api/system/bridges", get(bridges))
        .route("/api/system/hardware/pulls", get(hardware_pulls))
}

/// Commit grave a la compilation par `build.rs` (`inconnu` hors depot git).
pub const COMMIT: &str = match option_env!("NEO_GIT_COMMIT") {
    Some(v) => v,
    None => "inconnu",
};

/// Date de compilation (UTC, RFC 3339), gravee par `build.rs`.
pub const DATE_BUILD: &str = match option_env!("NEO_BUILD_DATE") {
    Some(v) => v,
    None => "inconnue",
};

/// GET /api/version : identite exacte du binaire qui repond.
///
/// Utile pour verifier qu'un deploiement a bien pris (le cache navigateur et
/// le watchdog peuvent laisser croire qu'une ancienne version tourne encore).
async fn version() -> Json<Value> {
    Json(serde_json::json!({
        "app": localai_core::APP_NAME,
        "version": localai_core::APP_VERSION,
        "commit": COMMIT,
        "build_date": DATE_BUILD,
    }))
}


async fn info(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    // Contrat exact : on appelle le handler Python lui-meme.
    let v = pybridge::py_json_chaud(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_system import system_info; print(json.dumps(asyncio.run(system_info()), default=str))").await?;
    Ok(Json(v))
}

/// Le superviseur ecrit son etat toutes les 15 s (jusqu'a ~25 s si un service
/// ne repond pas) : au-dela de ce delai, il est considere comme arrete.
const SUPERVISEUR_MUET_S: i64 = 90;

/// GET /api/system/services : etat des services tel que l'ecrit le superviseur
/// (localai-watch) dans `logs/services_etat.json`.
async fn services(State(st): State<Arc<crate::AppState>>) -> Json<Value> {
    let chemin = st.settings.root_dir.join("logs").join("services_etat.json");
    let lu = tokio::fs::read_to_string(&chemin)
        .await
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok());
    Json(etat_services(lu, chrono::Local::now()))
}

fn etat_services(lu: Option<Value>, maintenant: chrono::DateTime<chrono::Local>) -> Value {
    let Some(mut v) = lu else {
        return serde_json::json!({ "superviseur_actif": false, "maj": null, "services": [] });
    };
    let age = v
        .get("maj")
        .and_then(|m| m.as_str())
        .and_then(|m| chrono::DateTime::parse_from_rfc3339(m).ok())
        .map(|m| (maintenant.fixed_offset() - m).num_seconds());
    v["superviseur_actif"] = Value::Bool(age.is_some_and(|a| a <= SUPERVISEUR_MUET_S));
    v
}

async fn gpu() -> ApiResult<Json<Value>> {
    let out = localai_core::processus::commande("nvidia-smi")
        .args(["--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
               "--format=csv,noheader"])
        .output()
        .await
        .map_err(|e| crate::error::ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok(Json(serde_json::json!({ "nvidia_smi": text })))
}

/// Sante des ponts Python (venv + modules critiques). 503 si le venv est casse.
async fn bridges(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let py = crate::pybridge::venv_python(&st.settings.root_dir);
    let mut out = serde_json::json!({
        "venv_python": py.is_file(),
        "torch_cuda": false,
        "modules": {},
    });
    if !py.is_file() {
        return Ok(Json(out));
    }
    // Un seul appel Python pour tout (rapide, timeout court).
    // NOTE : pas de `import json` ici (le pont le prepend) et indentation
    // Python stricte (pas d'espaces parasites) — une seule ligne Rust.
    let code = "r = {'torch_cuda': False, 'modules': {}}\ntry:\n import torch\n r['torch_cuda'] = bool(torch.cuda.is_available())\nexcept Exception as e:\n r['torch_error'] = str(e)[:120]\nfor m in ['backend.tts.engine', 'backend.core.stt', 'backend.core.sdgen', 'backend.tools.executor', 'pypdf', 'docx', 'openpyxl', 'pyttsx3']:\n try:\n  __import__(m)\n  r['modules'][m] = True\n except Exception as e:\n  r['modules'][m] = str(e)[:120]\nprint(json.dumps(r))";
    match crate::pybridge::py_json_timeout(
        &st.settings.root_dir,
        code,
        std::time::Duration::from_secs(120),
    )
    .await
    {
        Ok(v) => {
            if let Some(o) = v.as_object() {
                let mut map = out.as_object().cloned().unwrap_or_default();
                for (k, val) in o {
                    map.insert(k.clone(), val.clone());
                }
                out = Value::Object(map);
            }
        }
        Err(e) => {
            out["pont_error"] = serde_json::Value::from(e.0.message());
        }
    }
    Ok(Json(out))
}

/// GET /api/tokens/usage (consigne §6) : compteur global depuis
/// l'installation - totaux, repartition par modele/agent, conversation vs
/// systeme, plus date de premiere utilisation.
async fn tokens_usage(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let total = st.db.query_one_json(
        "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS tin, \
         COALESCE(SUM(completion_tokens),0) AS tout, \
         MIN(created_at) AS premiere \
         FROM messages WHERE role='assistant'", &[])?;
    let (n, tin, tout, premiere) = match &total {
        Some(v) => (
            v.get("n").and_then(|x| x.as_i64()).unwrap_or(0),
            v.get("tin").and_then(|x| x.as_i64()).unwrap_or(0),
            v.get("tout").and_then(|x| x.as_i64()).unwrap_or(0),
            v.get("premiere").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        ),
        None => (0, 0, 0, String::new()),
    };
    let par_modele = st.db.query_all_json(
        "SELECT model, COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS tin, \
         COALESCE(SUM(completion_tokens),0) AS tout FROM messages \
         WHERE role='assistant' AND model IS NOT NULL AND model != '' \
         GROUP BY model ORDER BY tout DESC LIMIT 12", &[])?;
    Ok(Json(serde_json::json!({
        "total_messages": n,
        "tokens_in": tin,
        "tokens_out": tout,
        "premiere_utilisation": premiere,
        "par_modele": par_modele,
    })))
}

/// GET /api/system/hardware/pulls : modeles optionnels recommandes pour la
/// machine (GLM, DeepSeek-R1, Phi...) selon le palier detecte. Reutilise le
/// pont Python (build_profile + modeles_optionnels) pour ne pas dupliquer.
async fn hardware_pulls(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let code = r#"
from backend.core.hardware import build_profile, modeles_optionnels
import httpx, json
try:
    tags = [m.get('name', '') for m in
            httpx.get('http://127.0.0.1:11434/api/tags', timeout=6).json().get('models', [])]
except Exception:
    tags = []
prof = build_profile(tags)
tier = prof.get('tier', 'T1')
print(json.dumps({'optionnels': modeles_optionnels(tier), 'tier': tier}, default=str))
"#;
    let v = crate::pybridge::py_json(&st.settings.root_dir, code).await?;
    Ok(Json(v))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn version_expose_app_version_commit_et_date() {
        let Json(v) = version().await;
        assert_eq!(v["app"], localai_core::APP_NAME);
        assert_eq!(v["version"], localai_core::APP_VERSION);
        assert_eq!(v["commit"], COMMIT);
        assert_eq!(v["build_date"], DATE_BUILD);
        assert!(
            v["build_date"].as_str().unwrap().contains('T'),
            "la date de build doit etre au format RFC 3339 : {v}"
        );
    }

    #[test]
    fn services_superviseur_actif_selon_fraicheur() {
        let maintenant = chrono::Local::now();
        let etat = |age: i64| {
            serde_json::json!({
                "maj": (maintenant - chrono::Duration::seconds(age)).to_rfc3339(),
                "services": [{ "nom": "serveur", "etat": "ok" }],
            })
        };
        let frais = etat_services(Some(etat(20)), maintenant);
        assert_eq!(frais["superviseur_actif"], true);
        assert_eq!(frais["services"][0]["etat"], "ok");
        assert_eq!(etat_services(Some(etat(600)), maintenant)["superviseur_actif"], false);
        let absent = etat_services(None, maintenant);
        assert_eq!(absent["superviseur_actif"], false);
        assert_eq!(absent["services"].as_array().unwrap().len(), 0);
    }
}
