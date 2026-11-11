//! Reglages applicatifs.
//!
//! Les valeurs sensibles (jetons, mot de passe, cles d'API) ne quittent
//! jamais le serveur : la lecture les remplace par un masque et l'ecriture
//! ignore ce masque. Sans cette seconde precaution, l'UI qui renvoie le
//! formulaire tel qu'elle l'a lu ecraserait le secret par `********`.
//!
//! La detection est volontairement doublee : liste explicite pour les cles
//! connues, motifs pour les cles ajoutees plus tard (un `*_token` oublie ne
//! doit pas se retrouver en clair dans `/api/settings`).

use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::ApiResult;

/// Valeur renvoyee a la place d'un secret configure.
pub const MASQUE: &str = "********";

/// Cles sensibles connues (la detection par motif couvre le reste).
pub const CLES_SECRETES: &[&str] = &[
    "api_token",
    "webui_pass",
    "telegram_token",
    "livekit_api_key",
    "livekit_api_secret",
    "connecteur_meshy_token",
    "connecteur_telegram_token",
    "connecteur_discord_token",
];

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/settings", get(get_all).put(set_one))
        .route("/api/settings/:key", get(get_one).delete(supprimer))
}

/// Supprime une cle d'API des reglages (ElevenLabs, DeepL, Unsplash, LiveKit…).
/// Reserve aux secrets de la liste blanche : un reglage ordinaire se remet a
/// sa valeur par defaut par l'ecriture, et `api_token` / `webui_pass`
/// (l'acces a Neo lui-meme) ne se suppriment pas d'ici.
async fn supprimer(
    State(st): State<Arc<crate::AppState>>,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> ApiResult<Json<Value>> {
    if !ALLOWED.contains(&key.as_str()) || !est_secrete(&key) {
        return Ok(Json(json!({ "ok": false, "error": format!("Cle non supprimable: {key}") })));
    }
    let supprime = st.db.delete_setting(&key)?;
    if key == "telegram_token" {
        st.db.delete_setting("connecteur_telegram_token")?;
        st.db.set_setting("telegram_enabled", "0")?;
    }
    Ok(Json(json!({ "ok": true, "supprime": supprime })))
}

const ALLOWED: &[&str] = &[
    "tool_level", "tts_voice", "tts_rate", "default_model", "inference_backend",
    "modele_conversation", "agents_modele_conversation", "model_rapide",
    "auto_learn", "tts_autoplay", "tts_followup", "theme", "reflection_mode",
    "voice_model", "stt_model", "telegram_token", "telegram_enabled",
    "telegram_model", "livekit_url", "livekit_api_key", "livekit_api_secret",
    "style_auto", "telegram_dangerous_tools",
    // Produit Néo : langue, premiere installation, nom de l'assistant,
    // centres d'interet (apprentissage par categorie), identite affichee.
    "language", "onboarded", "assistant_name", "interests",
    "web_user", "display_name", "listen_lan", "image_format",
    // RAG booste : expansion + RRF + rerank cross-encoder.
    "rag_boost", "rerank_url",
    // Cles des services externes optionnels (chiffrees au repos).
    "api_key_elevenlabs", "api_key_deepl", "api_key_unsplash",
];

/// Vrai si la cle designe un secret qui ne doit jamais etre renvoye.
pub fn est_secrete(cle: &str) -> bool {
    if CLES_SECRETES.contains(&cle) {
        return true;
    }
    // Cles generees par prefixe (API publiques, fournisseurs cloud).
    if cle.starts_with("pubapi_key.") {
        return true;
    }
    if cle.starts_with("provider_") && cle.ends_with("_key") {
        return true;
    }
    let minuscule = cle.to_ascii_lowercase();
    ["token", "secret", "password", "passwd", "_pass", "apikey", "api_key", "cle_api"]
        .iter()
        .any(|motif| minuscule.contains(motif))
}

/// Valeur telle qu'elle peut sortir du serveur : masquee si secrete.
pub fn valeur_exposee(cle: &str, valeur: &Value) -> Value {
    if !est_secrete(cle) {
        return valeur.clone();
    }
    match valeur.as_str() {
        Some("") => json!(""),
        Some(_) => json!(MASQUE),
        // Un secret stocke en JSON/entier reste un secret.
        None if valeur.is_null() => json!(null),
        None => json!(MASQUE),
    }
}

async fn get_all(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let rows = st.db.query_all_json("SELECT key, value FROM app_settings", &[])?;
    let mut map = serde_json::Map::new();
    let mut definis: Vec<String> = Vec::new();
    for r in rows {
        if let (Some(cle), Some(valeur)) = (
            r.get("key").and_then(|x| x.as_str()),
            r.get("value"),
        ) {
            if est_secrete(cle) && valeur.as_str().map(|v| !v.is_empty()).unwrap_or(false) {
                definis.push(cle.to_string());
            }
            map.insert(cle.to_string(), valeur_exposee(cle, valeur));
        }
    }
    definis.sort();
    Ok(Json(json!({
        "settings": Value::Object(map),
        // Permet a l'UI d'afficher « cle definie » sans jamais recevoir la valeur.
        "secrets_definis": definis,
        "masquage": true,
    })))
}

#[derive(Debug, Deserialize)]
pub struct SetBody {
    pub key: String,
    pub value: String,
}

async fn set_one(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<SetBody>,
) -> ApiResult<Json<Value>> {
    if !ALLOWED.contains(&b.key.as_str()) {
        return Ok(Json(json!({ "ok": false, "error": format!("Cle non autorisee: {}", b.key) })));
    }
    // L'UI relit un secret masque puis le renvoie tel quel : ne rien ecraser.
    if est_secrete(&b.key) && b.value == MASQUE {
        return Ok(Json(json!({ "ok": true, "conserve": true })));
    }
    st.db.set_setting(&b.key, &b.value)?;
    Ok(Json(json!({ "ok": true })))
}

async fn get_one(
    State(st): State<Arc<crate::AppState>>,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> ApiResult<Json<Value>> {
    if est_secrete(&key) {
        let defini = !st.db.get_setting(&key, "").is_empty();
        return Ok(Json(json!({
            "key": key,
            "value": if defini { MASQUE } else { "" },
            "secret": true,
            "defini": defini,
        })));
    }
    let v = st.db.get_setting(&key, "");
    Ok(Json(json!({ "key": key, "value": v })))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support;

    #[test]
    fn secrets_connus_et_motifs() {
        for cle in [
            "api_token",
            "webui_pass",
            "telegram_token",
            "livekit_api_secret",
            "connecteur_meshy_token",
            "provider_openai_key",
            "pubapi_key.cats",
            "un_nouveau_token",
            "mot_de_passe_oublie",
        ] {
            assert!(est_secrete(cle), "{cle} devrait etre secrete");
        }
    }

    #[test]
    fn reglages_ordinaires_non_masques() {
        for cle in [
            "tool_level",
            "theme",
            "default_model",
            "language",
            "webui_user",
            "interests",
            "rate_limit_par_minute",
            "image_format",
        ] {
            assert!(!est_secrete(cle), "{cle} ne devrait pas etre secrete");
        }
    }

    #[test]
    fn valeur_masquee_seulement_si_configuree() {
        assert_eq!(valeur_exposee("api_token", &json!("abc")), json!(MASQUE));
        assert_eq!(valeur_exposee("api_token", &json!("")), json!(""));
        assert_eq!(valeur_exposee("theme", &json!("sombre")), json!("sombre"));
        assert_eq!(valeur_exposee("api_token", &json!(null)), json!(null));
    }

    #[tokio::test]
    async fn supprimer_une_cle_d_api() {
        let st = test_support::etat("settings_suppr");
        st.db.set_setting("api_key_deepl", "cle-de-test").unwrap();
        st.db.set_setting("theme", "sombre").unwrap();
        st.db.set_setting("api_token", "jeton").unwrap();
        let chemin = |c: &str| axum::extract::Path(c.to_string());

        let Json(v) = supprimer(State(st.clone()), chemin("api_key_deepl")).await.expect("handler");
        assert_eq!(v["supprime"], json!(true));
        assert_eq!(st.db.get_setting("api_key_deepl", ""), "");
        // Un reglage ordinaire et l'acces a Neo lui-meme ne se suppriment pas ici.
        for cle in ["theme", "api_token", "cle_inconnue"] {
            let Json(v) = supprimer(State(st.clone()), chemin(cle)).await.expect("handler");
            assert_eq!(v["ok"], json!(false), "{cle}");
        }
        assert_eq!(st.db.get_setting("theme", ""), "sombre");
        assert_eq!(st.db.get_setting("api_token", ""), "jeton");
    }

    #[tokio::test]
    async fn get_all_ne_laisse_fuir_aucun_secret() {
        let st = test_support::etat("settings_all");
        st.db.set_setting("api_token", "jeton-secret").unwrap();
        st.db.set_setting("telegram_token", "123:ABC").unwrap();
        st.db.set_setting("theme", "sombre").unwrap();

        let Json(v) = get_all(State(st)).await.expect("handler");
        let settings = v["settings"].as_object().expect("settings");
        assert_eq!(settings["api_token"], json!(MASQUE));
        assert_eq!(settings["telegram_token"], json!(MASQUE));
        assert_eq!(settings["theme"], json!("sombre"));
        assert_eq!(v["masquage"], json!(true));

        let definis = v["secrets_definis"].as_array().expect("liste");
        assert!(definis.iter().any(|x| x == "api_token"));
        assert!(definis.iter().any(|x| x == "telegram_token"));
        // Le contenu reel ne doit apparaitre nulle part dans la reponse.
        let brut = serde_json::to_string(&v).unwrap();
        assert!(!brut.contains("jeton-secret"));
        assert!(!brut.contains("123:ABC"));
    }

    #[tokio::test]
    async fn get_one_refuse_un_secret() {
        let st = test_support::etat("settings_one");
        st.db.set_setting("api_token", "jeton-secret").unwrap();

        let Json(v) = get_one(State(st.clone()), axum::extract::Path("api_token".into()))
            .await
            .expect("handler");
        assert_eq!(v["secret"], json!(true));
        assert_eq!(v["defini"], json!(true));
        assert_eq!(v["value"], json!(MASQUE));
        assert!(!serde_json::to_string(&v).unwrap().contains("jeton-secret"));

        let Json(vide) = get_one(State(st), axum::extract::Path("livekit_api_key".into()))
            .await
            .expect("handler");
        assert_eq!(vide["defini"], json!(false));
        assert_eq!(vide["value"], json!(""));
    }

    #[tokio::test]
    async fn get_one_rend_une_valeur_ordinaire() {
        let st = test_support::etat("settings_one_ordinaire");
        st.db.set_setting("theme", "clair").unwrap();
        let Json(v) = get_one(State(st), axum::extract::Path("theme".into()))
            .await
            .expect("handler");
        assert_eq!(v["value"], json!("clair"));
        assert!(v.get("secret").is_none());
    }

    #[tokio::test]
    async fn set_one_ignore_le_masque_renvoye_par_l_ui() {
        let st = test_support::etat("settings_masque");
        st.db.set_setting("telegram_token", "vrai-jeton").unwrap();

        let Json(v) = set_one(
            State(st.clone()),
            Json(SetBody { key: "telegram_token".into(), value: MASQUE.into() }),
        )
        .await
        .expect("handler");
        assert_eq!(v["ok"], json!(true));
        assert_eq!(v["conserve"], json!(true));
        assert_eq!(st.db.get_setting("telegram_token", ""), "vrai-jeton");
    }

    #[tokio::test]
    async fn set_one_ecrit_une_cle_autorisee() {
        let st = test_support::etat("settings_ecrit");
        let Json(v) = set_one(
            State(st.clone()),
            Json(SetBody { key: "theme".into(), value: "sombre".into() }),
        )
        .await
        .expect("handler");
        assert_eq!(v["ok"], json!(true));
        assert_eq!(st.db.get_setting("theme", ""), "sombre");
    }

    #[tokio::test]
    async fn set_one_refuse_une_cle_hors_liste() {
        let st = test_support::etat("settings_refus");
        for cle in ["api_token", "webui_pass", "cle_inventee"] {
            let Json(v) = set_one(
                State(st.clone()),
                Json(SetBody { key: cle.into(), value: "x".into() }),
            )
            .await
            .expect("handler");
            assert_eq!(v["ok"], json!(false), "{cle} ne doit pas etre modifiable");
        }
        assert_eq!(st.db.get_setting("api_token", ""), "");
    }
}
