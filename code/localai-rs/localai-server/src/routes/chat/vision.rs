//! Correctif vision : sanitisation base64, injection des images dans le payload Ollama,
//! forçage du modèle vision, limite de corps HTTP.
//! Contient aussi la detection de visages (route /api/vision/faces).
use std::sync::Arc;
use std::time::Duration;

use axum::extract::{DefaultBodyLimit, State};
use axum::Json;
use serde_json::{json, Value};

use crate::error::ApiResult;

/// Modeles de vision par ordre de preference (le premier installe voit).
/// Comparatif du 09/11/2026 (docs/CERVEAU.md) : gemma4:12b 29/29 elements et
/// 14/14 en lecture/comptage (avec reflexion) contre 28/29 et 12/14 pour
/// qwen2.5vl:7b ; c'est aussi le modele de conversation : plus d'echange de
/// modele en VRAM quand une image arrive.
pub const VISION_MODELES: &[&str] = &["gemma4:12b", "qwen2.5vl:7b"];

pub const VISION_SYSTEM: &str =
    "Des images sont jointes au dernier message de l'utilisateur. Tu les vois : analyse-les réellement \
     (objets, texte, couleurs, contexte) et réponds en français à la question posée.";

/// Enregistre les images jointes (base64 brut) dans `<racine>/input/` et rend
/// leurs chemins absolus. Le format vient des octets magiques (png, jpg, webp,
/// gif) ; un contenu illisible est ignore.
pub fn enregistrer_jointes(racine: &std::path::Path, images_b64: &[String]) -> Vec<String> {
    use base64::Engine;
    let dossier = racine.join("input");
    if std::fs::create_dir_all(&dossier).is_err() {
        return Vec::new();
    }
    let horodatage = chrono::Local::now().format("%Y%m%d_%H%M%S");
    let mut chemins = Vec::new();
    for (i, b64) in images_b64.iter().enumerate() {
        let Ok(octets) = base64::engine::general_purpose::STANDARD.decode(b64.as_bytes()) else {
            continue;
        };
        let ext = extension_image(&octets);
        if ext.is_empty() {
            continue;
        }
        let chemin = dossier.join(format!("joint_{horodatage}_{i}.{ext}"));
        if std::fs::write(&chemin, &octets).is_ok() {
            chemins.push(chemin.to_string_lossy().to_string());
        }
    }
    chemins
}

/// Extension d'apres les octets magiques ; vide si ce n'est pas une image connue.
pub fn extension_image(o: &[u8]) -> &'static str {
    if o.starts_with(&[0x89, b'P', b'N', b'G']) {
        "png"
    } else if o.starts_with(&[0xFF, 0xD8, 0xFF]) {
        "jpg"
    } else if o.len() > 12 && &o[0..4] == b"RIFF" && &o[8..12] == b"WEBP" {
        "webp"
    } else if o.starts_with(b"GIF8") {
        "gif"
    } else {
        ""
    }
}

#[cfg(test)]
mod tests_jointes {
    use super::*;

    #[test]
    fn images_jointes_enregistrees_avec_la_bonne_extension() {
        use base64::Engine;
        let dir = std::env::temp_dir().join(format!("neo_jointes_{}", std::process::id()));
        let png = base64::engine::general_purpose::STANDARD.encode([0x89, b'P', b'N', b'G', 0, 1, 2]);
        let jpg = base64::engine::general_purpose::STANDARD.encode([0xFF, 0xD8, 0xFF, 0xE0, 9]);
        let rien = base64::engine::general_purpose::STANDARD.encode(b"pas une image");
        let chemins = enregistrer_jointes(&dir, &[png, jpg, rien, "%%%".into()]);
        assert_eq!(chemins.len(), 2);
        assert!(chemins[0].ends_with(".png") && chemins[1].ends_with(".jpg"));
        assert!(std::path::Path::new(&chemins[0]).is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }
}

/// Axum limite le JSON à 2 Mo par défaut : une photo en base64 dépasse => rejet silencieux (413).
/// À ajouter au routeur : `.layer(vision::body_limit())`
pub fn body_limit() -> DefaultBodyLimit {
    DefaultBodyLimit::max(64 * 1024 * 1024)
}

/// Ollama attend du base64 BRUT : retire "data:image/...;base64," et les espaces/retours ligne.
pub fn clean_b64(s: &str) -> Option<String> {
    let raw = s.split_once("base64,").map(|(_, b)| b).unwrap_or(s);
    let raw: String = raw.chars().filter(|c| !c.is_whitespace()).collect();
    (raw.len() >= 16).then_some(raw)
}


#[derive(Debug, serde::Deserialize)]
pub struct FacesBody {
    pub path: String,
    pub save_path: Option<String>,
}

/// POST /api/vision/faces { path, save_path? } -> { ok, count, faces, annotated_path }
/// Detection locale (YuNet/OpenCV DNN), aucun envoi reseau.
pub async fn faces(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<FacesBody>,
) -> ApiResult<Json<Value>> {
    let chemin = b.path.trim();
    if chemin.is_empty() {
        return Ok(Json(json!({"ok": false, "error": "chemin vide"})));
    }
    let bas = chemin.to_lowercase();
    if ![".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
        .iter()
        .any(|e| bas.ends_with(e))
    {
        return Ok(Json(json!({"ok": false, "error": "format d'image non supporte"})));
    }
    let code = format!(
        "from backend.tools.t_visage import tool_detect_faces\nimport json\nr = tool_detect_faces({}, {})\ntry:\n    json.loads(r)\n    print(r)\nexcept Exception:\n    print(json.dumps({{'ok': False, 'error': r}}, ensure_ascii=False))",
        crate::pybridge::py_str(chemin),
        crate::pybridge::py_str(b.save_path.as_deref().unwrap_or(""))
    );
    let v = crate::pybridge::py_json_timeout(
        &st.settings.root_dir,
        &code,
        Duration::from_secs(180),
    )
    .await?;
    Ok(Json(v))
}
