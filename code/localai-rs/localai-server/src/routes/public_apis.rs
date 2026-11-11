//! Catalogue public-apis (annuaire d'APIs publiques) + gestion des cles API
//! par service. Le catalogue est telecharge/mis en cache par le module Python
//! backend.core.public_apis (rafraichissement auto toutes les 24 h).
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    routing::{get, post, put},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::ApiResult;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/public-apis", get(catalog))
        .route("/api/public-apis/refresh", post(refresh))
        .route("/api/public-apis/keys", get(keys_list))
        .route("/api/public-apis/keys/:slug", put(keys_set).delete(keys_delete))
}

async fn catalog(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let code = "from backend.core.public_apis import get_catalog\nimport json\nprint(json.dumps(get_catalog(), default=str))";
    let v = crate::pybridge::py_json(&st.settings.root_dir, code).await?;
    Ok(Json(v))
}

async fn refresh(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let code = "from backend.core.public_apis import get_catalog\nimport json\nprint(json.dumps(get_catalog(force=True), default=str))";
    let v = crate::pybridge::py_json(&st.settings.root_dir, code).await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct KeyBody {
    pub value: String,
}

async fn keys_list(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let rows = st.db.query_all_json("SELECT key FROM app_settings WHERE key LIKE 'pubapi_key.%'", &[])?;
    let mut out = serde_json::Map::new();
    for r in rows {
        let Some(k) = r.get("key").and_then(|x| x.as_str()) else {
            continue;
        };
        if let Some(slug) = k.strip_prefix("pubapi_key.") {
            // Valeur DECHIFFREE (le brut en base est « enc:v1:... ») et
            // masquage par caracteres (le decoupage par octets paniquait sur
            // une cle non ASCII).
            let v = st.db.get_setting(k, "");
            let configured = !v.trim().is_empty();
            let masked = if configured {
                let c: Vec<char> = v.chars().collect();
                if c.len() > 4 {
                    format!("{}{}…{}{}", c[0], c[1], c[c.len() - 2], c[c.len() - 1])
                } else {
                    "••••".to_string()
                }
            } else {
                String::new()
            };
            out.insert(
                slug.to_string(),
                json!({ "configured": configured, "masked": masked }),
            );
        }
    }
    Ok(Json(json!({ "keys": Value::Object(out) })))
}

async fn keys_set(
    State(st): State<Arc<crate::AppState>>,
    Path(slug): Path<String>,
    Json(b): Json<KeyBody>,
) -> ApiResult<Json<Value>> {
    if slug.is_empty() || slug.len() > 80 {
        return Ok(Json(json!({ "ok": false, "error": "slug invalide" })));
    }
    st.db.set_setting(&format!("pubapi_key.{}", slug), &b.value)?;
    Ok(Json(json!({ "ok": true })))
}

async fn keys_delete(
    State(st): State<Arc<crate::AppState>>,
    Path(slug): Path<String>,
) -> ApiResult<Json<Value>> {
    st.db.set_setting(&format!("pubapi_key.{}", slug), "")?;
    Ok(Json(json!({ "ok": true })))
}