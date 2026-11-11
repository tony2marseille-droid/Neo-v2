//! TTS : pont vers le moteur Python (pyttsx3/SAPI) en attendant un moteur natif.
//! Le Rust possede la couche HTTP, le Python ne fait que synthetiser.
use std::sync::Arc;
use std::time::Duration;

use axum::{
    body::Body,
    extract::State,
    http::{header, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::pybridge::{self, py_str};

/// Une synthese SAPI depasse rarement quelques secondes ; au-dela le moteur
/// est bloque et le process est tue (kill_on_drop du pont).
const TTS_TIMEOUT: Duration = Duration::from_secs(120);

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/tts/voices", get(voices))
        .route("/api/tts", post(synthesize))
}

#[derive(Debug, Deserialize)]
pub struct TtsBody {
    pub text: String,
    pub voice: Option<String>,
    pub rate: Option<i32>,
}

fn interne(e: impl std::fmt::Display) -> ApiError {
    ApiError(localai_core::AppError::Internal(e.to_string()))
}

async fn voices(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json_timeout(
        &st.settings.root_dir,
        "from backend.tts.engine import list_voices; print(json.dumps(list_voices()))",
        TTS_TIMEOUT,
    )
    .await?;
    Ok(Json(json!({ "voices": v })))
}

async fn synthesize(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<TtsBody>,
) -> ApiResult<Response> {
    if b.text.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("texte vide".into())));
    }
    // Voix et debit choisis dans les Reglages : appliques a TOUTE synthese
    // (chat, lecture a voix haute, Telegram) quand l'appelant n'en impose pas.
    let rate = b
        .rate
        .or_else(|| st.db.get_setting("tts_rate", "").parse().ok())
        .unwrap_or(185)
        .clamp(50, 400);
    let voix = b
        .voice
        .clone()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| st.db.get_setting("tts_voice", ""));
    let cache = st.settings.data_dir.join("tts");
    tokio::fs::create_dir_all(&cache).await.map_err(interne)?;
    let fname = format!("tts_{}.wav", chrono::Local::now().format("%Y%m%d_%H%M%S_%f"));
    let out_path = cache.join(&fname);
    let code = format!(
        "from backend.tts.engine import synthesize_to_wav; from pathlib import Path; \
         synthesize_to_wav({}, Path({}), voice_id={}, rate={rate}); print(json.dumps(True))",
        py_str(&b.text),
        py_str(&out_path.to_string_lossy()),
        py_str(&voix),
    );
    let res = pybridge::py_json_timeout(&st.settings.root_dir, &code, TTS_TIMEOUT).await;
    let bytes = match res {
        Ok(_) => tokio::fs::read(&out_path).await.map_err(|e| interne(format!("tts synthese: {e}"))),
        Err(e) => Err(e),
    };
    let _ = tokio::fs::remove_file(&out_path).await;
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "audio/wav")
        .body(Body::from(bytes?))
        .map_err(interne)
}
