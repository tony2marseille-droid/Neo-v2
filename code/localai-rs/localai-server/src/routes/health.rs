use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use localai_core::{APP_NAME, APP_VERSION};
use serde_json::{json, Value};

use crate::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/api/health", get(health))
}

/// Sonde de sante.
///
/// `status` reste `"ok"` tant que le processus repond : le watchdog
/// (`oracle-watch`) ne teste que le code HTTP et redemarrerait le serveur
/// pour une dependance degradee, ce qui n'arrangerait rien. La degradation
/// est exposee a part, dans `degraded`, avec le detail dans `metrics`.
async fn health(State(st): State<Arc<AppState>>) -> Json<Value> {
    let metrics = crate::metrics::snapshot();
    let degrade = crate::metrics::degrade();
    let limite_debit = st.limiter.max();

    Json(json!({
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "degraded": degrade,
        "limite_debit_par_minute": limite_debit,
        "metrics": metrics,
    }))
}
