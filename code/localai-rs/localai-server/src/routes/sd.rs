use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Query, State},
    http::{header, HeaderMap, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::Value;

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/sd/status", get(status))
        .route("/api/sd/install", post(install))
        .route("/api/sd/generate", post(generate))
        .route("/api/sd/image-to-3d", post(image_to_3d))
        .route("/api/sd/file", get(file))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json_chaud(&st.settings.root_dir,
        "from backend.core.sdgen import status; print(json.dumps(status()))").await?;
    Ok(Json(v))
}

async fn install(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.sdgen import install; print(json.dumps(install()))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize, Default)]
pub struct GenBody {
    pub prompt: String,
    pub negatif: Option<String>,
    pub largeur: Option<i32>,
    pub hauteur: Option<i32>,
    pub steps: Option<i32>,
    pub graine: Option<i64>,
    pub resolution: Option<String>,
    pub traduire: Option<bool>,
    pub reference: Option<String>,
    pub reference_desc: Option<String>,
    pub mode: Option<String>,
    /// Chemin de sortie explicite (video : images intermediaires nommees).
    pub save_path: Option<String>,
    /// "standard" ou "max" (Q8 + base 1536x864 + 6 steps).
    pub qualite: Option<String>,
    /// Format de sortie choisi par l'utilisateur : png, jpeg, webp, tiff.
    pub format: Option<String>,
}

async fn generate(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<GenBody>,
) -> ApiResult<Json<Value>> {
    if b.prompt.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("prompt vide".into())));
    }
    use pybridge::py_str;
    // 0 = auto : sdgen choisit selon le modele (FLUX schnell : 4 steps,
    // 1024 px). Forcer 512/20 degradait l'image et multipliait le temps.
    let steps = b.steps.unwrap_or(0).max(0);
    let larg = b.largeur.unwrap_or(0).max(0);
    let haut = b.hauteur.unwrap_or(0).max(0);
    let graine = b.graine.unwrap_or(-1);
    let traduire_py = if b.traduire.unwrap_or(true) { "True" } else { "False" };
    // Toutes les chaines passent par py_str : aucune entree n'est interpretee
    // comme du code Python (les apostrophes du prompt sont conservees).
    // generer_auto renvoie {ok, message, path, url, duree_s}.
    let code = format!(
        "from backend.core.sdgen import generer_auto; r = generer_auto({}, {}, {larg}, {haut}, {steps}, {graine}, {}, {traduire_py}, {}, {}, {}, None, {}, {}, {}); print(json.dumps(r, ensure_ascii=False))",
        py_str(&b.prompt),
        py_str(b.negatif.as_deref().unwrap_or("")),
        py_str(b.resolution.as_deref().unwrap_or("1024_upscale")),
        py_str(b.reference.as_deref().unwrap_or("")),
        py_str(b.reference_desc.as_deref().unwrap_or("")),
        py_str(b.mode.as_deref().unwrap_or("creation")),
        py_str(b.save_path.as_deref().unwrap_or("")),
        py_str(b.qualite.as_deref().unwrap_or("standard")),
        py_str(&b.format.as_deref().unwrap_or("png").to_lowercase()),
    );
    let v = pybridge::py_json_timeout(
        &st.settings.root_dir, &code, std::time::Duration::from_secs(900)).await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize, Default)]
pub struct Img3DBody {
    pub image_path: String,
    pub output_stl: Option<String>,
    pub height_mm: Option<f64>,
    pub max_faces: Option<i64>,
}

async fn image_to_3d(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<Img3DBody>,
) -> ApiResult<Json<Value>> {
    // Validation : un chemin vide doit produire un 400 explicite, pas un 500.
    if b.image_path.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest(
            "image_path vide : indiquez le chemin d'une image existante".into(),
        )));
    }
    // TripoSR local via l'executor Python (peut durer 10+ min).
    let h = b.height_mm.filter(|v| v.is_finite()).unwrap_or(80.0);
    let mf = b.max_faces.unwrap_or(0);
    let stl = pybridge::py_str(b.output_stl.as_deref().unwrap_or(""));
    let code = format!(
        "from backend.tools.executor import tool_generate_mesh_from_image; import os; s = {stl}; m = tool_generate_mesh_from_image({}, s, {h}, {mf}); ok = bool(s) and os.path.exists(s); print(json.dumps({{'ok': ok, 'message': m, 'path': s}}))",
        pybridge::py_str(&b.image_path));
    let v = pybridge::py_json_timeout(
        &st.settings.root_dir, &code, std::time::Duration::from_secs(1800)).await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct FileQuery {
    pub path: String,
    #[serde(default)]
    pub download: Option<String>,
    /// `ecran` : version d'affichage (`<nom>_ecran.jpg`, 1920 px) si elle existe.
    #[serde(default)]
    pub taille: Option<String>,
}

/// Version d'affichage d'une image generee : `<nom>_ecran.jpg` a cote du fichier.
fn version_ecran(canon: &std::path::Path) -> Option<std::path::PathBuf> {
    let stem = canon.file_stem()?.to_str()?;
    if stem.ends_with("_ecran") {
        return None;
    }
    let p = canon.with_file_name(format!("{stem}_ecran.jpg"));
    p.is_file().then_some(p)
}

/// Etiquette de cache : taille + date de modification (change si le fichier est regenere).
fn etag(meta: &std::fs::Metadata) -> String {
    let mtime = meta
        .modified()
        .ok()
        .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!("\"{:x}-{:x}\"", meta.len(), mtime)
}

async fn file(
    State(st): State<Arc<crate::AppState>>,
    Query(q): Query<FileQuery>,
    entetes: HeaderMap,
) -> ApiResult<Response> {
    // Garde-fou : uniquement sous une racine autorisee (racine projet,
    // et eventuellement dossiers accordes via reglages `allowed_roots`),
    // anti path-traversal.
    let root = st.settings.root_dir.canonicalize()
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    // Racines supplementaires : dossiers accordes + Documents de Neo (les
    // images generees y sont ecrites, hors de E:\ia).
    let mut extra: Vec<std::path::PathBuf> = Vec::new();
    for s in serde_json::from_str::<Vec<String>>(&st.db.get_setting("allowed_roots", "[]"))
        .unwrap_or_default()
    {
        if let Ok(p) = std::path::PathBuf::from(s).canonicalize() {
            extra.push(p);
        }
    }
    for s in [
        std::env::var("LOCALAI_NEO_DOCS").unwrap_or_default(),
        std::env::var("USERPROFILE")
            .map(|u| format!("{u}\\Documents\\Neo"))
            .unwrap_or_default(),
        r"C:\Windows\System32\config\systemprofile\Documents\Neo".to_string(),
    ] {
        if !s.is_empty() {
            if let Ok(p) = std::path::PathBuf::from(s).canonicalize() {
                extra.push(p);
            }
        }
    }
    let roots: Vec<std::path::PathBuf> = std::iter::once(root.clone())
        .chain(extra)
        .collect();

    let chemin = q.path.trim_start_matches(['/', '\\'].as_slice());
    let candidat = if std::path::Path::new(chemin).is_absolute() {
        std::path::PathBuf::from(chemin)
    } else {
        root.join(chemin)
    };
    let canon = candidat.canonicalize()
        .map_err(|_| ApiError(localai_core::AppError::NotFound("fichier introuvable".into())))?;
    // Seuls les fichiers PRODUITS par Neo sont servis : la racine autorisee
    // contient aussi la base (localai.db), la cle de chiffrement des secrets
    // (.neo_keystore) et le code, qui ne doivent jamais sortir par ici.
    let ext = canon.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
    let Some(mime) = mime_servi(&ext) else {
        return Err(ApiError(localai_core::AppError::NotFound("fichier introuvable".into())));
    };
    if !roots.iter().any(|r| canon.starts_with(r)) || !canon.is_file() {
        return Err(ApiError(localai_core::AppError::NotFound("fichier introuvable".into())));
    }
    // Version d'affichage : meme dossier (donc meme racine autorisee), JPEG.
    let (canon, mime) = match (q.taille.as_deref(), q.download.as_deref()) {
        (Some("ecran"), None) => match version_ecran(&canon) {
            Some(p) => (p, "image/jpeg"),
            None => (canon, mime),
        },
        _ => (canon, mime),
    };
    let meta = tokio::fs::metadata(&canon)
        .await
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let etiquette = etag(&meta);
    // Le fil de discussion re-rend souvent ses images : 304 sans relire 2 Mo.
    if entetes
        .get(header::IF_NONE_MATCH)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v == etiquette)
    {
        return Response::builder()
            .status(StatusCode::NOT_MODIFIED)
            .header(header::ETAG, etiquette)
            .body(Body::empty())
            .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())));
    }
    let bytes = tokio::fs::read(&canon)
        .await
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let mut builder = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, mime)
        .header(header::ETAG, etiquette)
        .header(header::CACHE_CONTROL, "private, no-cache");
    // Si download=1 present, forcer le telechargement avec nom de fichier
    if q.download.as_deref() == Some("1") {
        let filename = canon.file_name().and_then(|s| s.to_str()).unwrap_or("fichier");
        let safe_filename = filename.replace('"', "");
        builder = builder.header(
            header::CONTENT_DISPOSITION,
            format!(r#"attachment; filename="{}""#, safe_filename)
        );
    }
    builder
        .body(Body::from(bytes))
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))
}

/// Types servis par /api/sd/file (images, video, audio, 3D, documents
/// generes). Toute autre extension (db, json, py, cles...) est refusee.
fn mime_servi(ext: &str) -> Option<&'static str> {
    Some(match ext {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "bmp" => "image/bmp",
        "tif" | "tiff" => "image/tiff",
        "exr" => "image/x-exr",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "glb" => "model/gltf-binary",
        "obj" | "gcode" | "scad" | "txt" | "md" | "csv" => "text/plain; charset=utf-8",
        "stl" => "model/stl",
        "3mf" => "model/3mf",
        "pdf" => "application/pdf",
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx" => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::{mime_servi, version_ecran};

    #[test]
    fn version_ecran_seulement_si_presente() {
        let dir = std::env::temp_dir().join(format!("neo_ecran_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let image = dir.join("chat.jpg");
        std::fs::write(&image, b"x").unwrap();
        assert!(version_ecran(&image).is_none());
        std::fs::write(dir.join("chat_ecran.jpg"), b"y").unwrap();
        assert_eq!(version_ecran(&image).unwrap(), dir.join("chat_ecran.jpg"));
        // La version ecran ne pointe jamais vers une autre version ecran.
        assert!(version_ecran(&dir.join("chat_ecran.jpg")).is_none());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn fichiers_sensibles_jamais_servis() {
        for ext in ["db", "db-wal", "json", "py", "neo_keystore", "env", "exe", "ps1", ""] {
            assert!(mime_servi(ext).is_none(), "{ext}");
        }
        for ext in ["png", "jpg", "tiff", "mp4", "stl", "pdf"] {
            assert!(mime_servi(ext).is_some(), "{ext}");
        }
    }
}
