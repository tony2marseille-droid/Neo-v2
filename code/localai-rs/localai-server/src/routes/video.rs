//! Routes API video de Neo : storyboard, clips, montage, liste, etat.
//! Pont vers `backend/tools/t_video.py` ; toutes les chaines passent par
//! `py_str` (aucune entree n'est interpretee comme du code Python).

use std::sync::Arc;
use std::time::Duration;

use axum::{extract::State, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::Value;

use crate::error::ApiResult;
use crate::pybridge::{py_json_timeout, py_str};
use crate::AppState;

/// Storyboard = un appel LLM ; clip/montage = generation d'images + ffmpeg.
const DELAI_STORYBOARD: Duration = Duration::from_secs(300);
const DELAI_RENDU: Duration = Duration::from_secs(1800);

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/api/video/storyboard", post(storyboard))
        .route("/api/video/clip", post(clip))
        .route("/api/video/render", post(render))
        .route("/api/video/liste", get(liste))
        .route("/api/video/status", get(status))
}

#[derive(Deserialize)]
struct StoryboardReq {
    histoire: String,
    #[serde(default)]
    modele: String,
    /// Duree totale visee (0 = libre). Transmise au storyboarder.
    #[serde(default)]
    duree_totale: i64,
}

#[derive(Deserialize)]
struct ClipReq {
    storyboard_path: String,
    #[serde(default = "default_shot_id")]
    shot_id: i64,
}

fn default_shot_id() -> i64 { 1 }

#[derive(Deserialize)]
struct RenderReq {
    project_name: String,
    #[serde(default)]
    audio_texte: String,
    /// Duree totale visee (0 = libre).
    #[serde(default)]
    duree_totale: i64,
    /// Timestamp epoch (s) du storyboard : ne monter que les clips crees
    /// apres cette date, pour ne pas melanger plusieurs projets.
    #[serde(default)]
    since: f64,
}

/// Les `tool_video_*` signalent un echec par une chaine « Erreur... » et non
/// par une exception : `ok` le reflete (avant : toujours `true`).
async fn appel_outil(st: &AppState, appel: String, delai: Duration) -> ApiResult<Json<Value>> {
    let code = format!(
        "from backend.tools.t_video import *\nresultat = {appel}\n\
         print(json.dumps({{'ok': not str(resultat).startswith('Erreur'), 'resultat': resultat}}, ensure_ascii=False))"
    );
    Ok(Json(py_json_timeout(&st.settings.root_dir, &code, delai).await?))
}

async fn storyboard(State(st): State<Arc<AppState>>, Json(req): Json<StoryboardReq>) -> ApiResult<Json<Value>> {
    let appel = format!(
        "tool_video_storyboard({}, {}, {})",
        py_str(&req.histoire), py_str(&req.modele), req.duree_totale
    );
    appel_outil(&st, appel, DELAI_STORYBOARD).await
}

async fn clip(State(st): State<Arc<AppState>>, Json(req): Json<ClipReq>) -> ApiResult<Json<Value>> {
    let appel = format!("tool_video_clip({}, {})", py_str(&req.storyboard_path), req.shot_id);
    appel_outil(&st, appel, DELAI_RENDU).await
}

async fn render(State(st): State<Arc<AppState>>, Json(req): Json<RenderReq>) -> ApiResult<Json<Value>> {
    let since = if req.since.is_finite() { req.since } else { 0.0 };
    let appel = format!(
        "tool_video_render({}, {}, {}, since={since})",
        py_str(&req.project_name), py_str(&req.audio_texte), req.duree_totale
    );
    appel_outil(&st, appel, DELAI_RENDU).await
}

/// Liste des videos : VIDEO_DIR plus les dossiers video des Documents de Neo
/// (utilisateur courant et profil SYSTEM quand le service tourne en SYSTEM).
async fn liste(State(st): State<Arc<AppState>>) -> ApiResult<Json<Value>> {
    let code = r"import os
from pathlib import Path
from backend.tools.t_video import VIDEO_DIR
sous = Path('Documents') / 'Neo' / 'Images_generees' / 'video'
dirs = [VIDEO_DIR, Path.home() / sous,
        Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'config' / 'systemprofile' / sous]
vus, fichiers = set(), []
for d in dirs:
    if not d.is_dir():
        continue
    for f in sorted(d.glob('*')):
        cle = os.path.normcase(str(f))
        if f.is_file() and cle not in vus:
            vus.add(cle)
            fichiers.append({'nom': f.name, 'chemin': str(f), 'taille': f.stat().st_size})
print(json.dumps({'ok': True, 'dossier': str(VIDEO_DIR), 'fichiers': fichiers}, ensure_ascii=False))
";
    Ok(Json(py_json_timeout(&st.settings.root_dir, code, Duration::from_secs(60)).await?))
}

async fn status(State(st): State<Arc<AppState>>) -> ApiResult<Json<Value>> {
    let code = format!(
        r"import subprocess, httpx
from backend.tools.t_video import VIDEO_DIR
statut = {{'dossier_sortie': str(VIDEO_DIR), 'comfyui': False, 'ollama': False, 'ffmpeg': False, 'modeles_ollama': []}}
try:
    r = httpx.get({ollama} + '/api/tags', timeout=5)
    statut['ollama'] = r.status_code == 200
    if statut['ollama']:
        statut['modeles_ollama'] = [m['name'] for m in r.json().get('models', [])]
except Exception:
    pass
try:
    statut['comfyui'] = httpx.get('http://127.0.0.1:8188/system_stats', timeout=5).status_code == 200
except Exception:
    pass
for exe in ('ffmpeg', r'C:\ffmpeg\bin\ffmpeg.exe', r'C:\ffmpeg\ffmpeg.exe'):
    try:
        if subprocess.run([exe, '-version'], capture_output=True, timeout=5).returncode == 0:
            statut['ffmpeg'], statut['ffmpeg_exe'] = True, exe
            break
    except Exception:
        continue
print(json.dumps({{'ok': True, 'statut': statut}}, ensure_ascii=False))
",
        ollama = py_str(st.settings.ollama_host.trim_end_matches('/')),
    );
    Ok(Json(py_json_timeout(&st.settings.root_dir, &code, Duration::from_secs(30)).await?))
}
