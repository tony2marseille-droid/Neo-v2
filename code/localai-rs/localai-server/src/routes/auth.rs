//! Acces Web UI : pseudo + mot de passe configures a l'installation
//! (onboarding) ou dans les reglages. Le login delivre le token API que
//! le navigateur presente ensuite en "Authorization: Bearer" (middleware
//! main.rs). SSH : voir la note en fin de module.

use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use hmac::{Hmac, Mac};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/auth/status", get(status))
        .route("/api/auth/login", post(login))
        .route("/api/auth/setup", post(setup))
}

/// Hache un mot de passe : sha256(salt + mot de passe), hex.
fn hacher(salt: &str, pass: &str) -> String {
    let mut h = Sha256::new();
    h.update(salt.as_bytes());
    h.update(pass.as_bytes());
    format!("{:x}", h.finalize())
}

/// PBKDF2-HMAC-SHA256, hex. Iterations e levees pour freiner le brute-force
/// hors-ligne ; maintenu volontairement dans le crate (pas de dep externe)
/// car `hmac` et `sha2` sont deja presents.
const PBKDF2_ITERATIONS: u32 = 100_000;
type HmacSha256 = Hmac<Sha256>;

fn hacher_pbkdf2(sel: &str, pass: &str, iterations: u32) -> String {
    let mut acc = [0u8; 32];
    // U1 = PRF(pass, salt || 0x00000001)
    {
        let mut h = HmacSha256::new_from_slice(pass.as_bytes())
            .expect("HMAC accepte n'importe quelle cle");
        h.update(sel.as_bytes());
        h.update(&[0, 0, 0, 1]);
        acc.copy_from_slice(&h.finalize().into_bytes());
    }
    let mut prev = acc;
    for _ in 1..iterations {
        let mut h = HmacSha256::new_from_slice(pass.as_bytes())
            .expect("HMAC accepte n'importe quelle cle");
        h.update(&prev);
        let u = h.finalize().into_bytes();
        for i in 0..32 {
            acc[i] ^= u[i];
            prev[i] = u[i];
        }
    }
    acc.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Formate neuf pbkdf2.
fn sel_pbkdf2() -> String {
    sel_aleatoire()
}

/// Sel aleatoire (nanos + pid) : suffisant pour un reseau local.
fn sel_aleatoire() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    let mut h = Sha256::new();
    h.update(nanos.to_le_bytes());
    h.update(pid.to_le_bytes());
    format!("{:x}", h.finalize())[..32].to_string()
}

fn pass_ok(stocke: &str, saisi: &str) -> bool {
    if let Some(reste) = stocke.strip_prefix("pbkdf2$") {
        let mut parties = reste.split('$');
        let iters_str = parties.next().unwrap_or("");
        let sel = parties.next().unwrap_or("");
        let hash = parties.next().unwrap_or("");
        if parties.next().is_some() {
            return false;
        }
        let iters: u32 = match iters_str.parse() {
            Ok(v) if v >= 10_000 => v,
            _ => return false,
        };
        if sel.is_empty() || hash.is_empty() {
            return false;
        }
        hacher_pbkdf2(sel, saisi, iters) == hash
    } else if let Some(reste) = stocke.strip_prefix("sha256$") {
        let mut parties = reste.split('$');
        let salt = parties.next().unwrap_or("");
        let hash = parties.next().unwrap_or("");
        if salt.is_empty() {
            return false;
        }
        !salt.is_empty() && hacher(salt, saisi) == hash
    } else {
        false
    }
}

/// GET /api/auth/status : etat de l'acces (route publique, avant login).
async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let token = st.db.get_setting("api_token", "");
    let user = st.db.get_setting("webui_user", "");
    Ok(Json(json!({
        "active": !token.is_empty(),
        "user": user,
    })))
}

#[derive(Debug, Deserialize)]
pub struct CorpsLogin {
    pub user: String,
    pub pass: String,
}

/// POST /api/auth/login : verifie pseudo + mot de passe, delivre le token
/// que le navigateur presentera en Bearer (route publique).
async fn login(State(st): State<Arc<crate::AppState>>, Json(b): Json<CorpsLogin>)
    -> ApiResult<Json<Value>>
{
    let user = st.db.get_setting("webui_user", "");
    let stocke = st.db.get_setting("webui_pass", "");
    let token = st.db.get_setting("api_token", "");
    let pseudo = b.user.trim();
    if user.is_empty() || stocke.is_empty() || token.is_empty() {
        return Ok(Json(json!({ "ok": false, "error": "acces non configure" })));
    }
    // PBKDF2 (100 000 iterations) hors du runtime async.
    let saisi = b.pass.clone();
    let stocke_v = stocke.clone();
    let ok = tokio::task::spawn_blocking(move || pass_ok(&stocke_v, &saisi))
        .await
        .unwrap_or(false);
    if pseudo != user || !ok {
        return Ok(Json(json!({ "ok": false, "error": "pseudo ou mot de passe incorrect" })));
    }
    // Migration transparente : un hash legacy (sha256) etant crackable,
    // on re-hache en PBKDF2 des le login reussi.
    if stocke.starts_with("sha256$") {
        let sel = sel_pbkdf2();
        let nouveau = format!(
            "pbkdf2${PBKDF2_ITERATIONS}${sel}${}",
            hacher_pbkdf2(&sel, &b.pass, PBKDF2_ITERATIONS)
        );
        let _ = st.db.set_setting("webui_pass", &nouveau);
    }
    Ok(Json(json!({ "ok": true, "token": token })))

}

#[derive(Debug, Deserialize)]
pub struct CorpsSetup {
    pub user: String,
    pub pass: String,
    pub old_pass: Option<String>,
}

/// POST /api/auth/setup : definit (ou remplace) le pseudo + mot de passe et
/// genere le token d'acces. Remplacement protege par l'ancien mot de passe.
async fn setup(State(st): State<Arc<crate::AppState>>, Json(b): Json<CorpsSetup>)
    -> ApiResult<Json<Value>>
{
    let pseudo = b.user.trim();
    if pseudo.len() < 2 {
        return Err(ApiError(localai_core::AppError::BadRequest(
            "pseudo trop court (2 caracteres minimum)".into(),
        )));
    }
    if b.pass.len() < 4 {
        return Err(ApiError(localai_core::AppError::BadRequest(
            "mot de passe trop court (4 caracteres minimum)".into(),
        )));
    }
    let stocke = st.db.get_setting("webui_pass", "");
    if !stocke.is_empty() {
        // Deja configure : exiger l'ancien mot de passe pour changer.
        let ancien = b.old_pass.clone().unwrap_or_default();
        if !pass_ok(&stocke, &ancien) {
            return Ok(Json(json!({
                "ok": false,
                "error": "ancien mot de passe requis pour modifier l'acces",
            })));
        }
    }
    let sel = sel_pbkdf2();
    let hash = format!("pbkdf2${PBKDF2_ITERATIONS}${sel}${}", hacher_pbkdf2(&sel, &b.pass, PBKDF2_ITERATIONS));
    st.db.set_setting("webui_user", pseudo)?;
    st.db.set_setting("webui_pass", &hash)?;
    // Token d'acces (Bearer) regenere.
    let token = sel_aleatoire() + &sel_aleatoire();
    st.db.set_setting("api_token", &token)?;
    Ok(Json(json!({ "ok": true, "token": token })))
}

// NOTE SSH (consigne installation) : l'acces SSH se gere au niveau de
// l'OS (serveur OpenSSH Windows + compte utilisateur), pas dans le
// serveur web. L'onboarding affiche la procedure et le port ; la
// creation effective reste une operation systeme volontaire.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support;

    #[test]
    fn hachage_deterministe_et_sensible_au_sel() {
        let a = hacher("sel-a", "motdepasse");
        assert_eq!(a, hacher("sel-a", "motdepasse"));
        assert_ne!(a, hacher("sel-b", "motdepasse"));
        assert_ne!(a, hacher("sel-a", "autre"));
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn sel_aleatoire_longueur_et_unicite() {
        let a = sel_aleatoire();
        let b = sel_aleatoire();
        assert_eq!(a.len(), 32);
        assert_ne!(a, b);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn pass_ok_accepte_le_bon_mot_de_passe_seulement() {
        let sel = "84208f9584ec4d2b47ddd9455c7bc87c";
        let stocke = format!("sha256${sel}${}", hacher(sel, "secret42"));
        assert!(pass_ok(&stocke, "secret42"));
        assert!(!pass_ok(&stocke, "secret43"));
        assert!(!pass_ok(&stocke, ""));
        // Formats invalides : jamais d'acceptation par defaut.
        assert!(!pass_ok("", "secret42"));
        assert!(!pass_ok("sha256$", "secret42"));
        assert!(!pass_ok("bcrypt$sel$hash", "secret42"));
        assert!(!pass_ok("sha256$sel$", "secret42"));
    }

    #[tokio::test]
    async fn setup_puis_login_puis_status() {
        let st = test_support::etat("auth_cycle");
        let Json(v) = setup(
            State(st.clone()),
            Json(CorpsSetup {
                user: "Camille".into(),
                pass: "secret42".into(),
                old_pass: None,
            }),
        )
        .await
        .expect("setup");
        assert_eq!(v["ok"], json!(true));
        let jeton = v["token"].as_str().expect("token").to_string();
        assert_eq!(jeton.len(), 64);
        assert_eq!(st.db.get_setting("api_token", ""), jeton);
        assert!(st.db.get_setting("webui_pass", "").starts_with("pbkdf2$"));

        let Json(v) = login(
            State(st.clone()),
            Json(CorpsLogin {
                user: "Camille".into(),
                pass: "secret42".into(),
            }),
        )
        .await
        .expect("login");
        assert_eq!(v["ok"], json!(true));
        assert_eq!(v["token"].as_str(), Some(jeton.as_str()));

        let Json(v) = login(
            State(st.clone()),
            Json(CorpsLogin {
                user: "Camille".into(),
                pass: "mauvais".into(),
            }),
        )
        .await
        .expect("login refuse");
        assert_eq!(v["ok"], json!(false));

        let Json(v) = status(State(st)).await.expect("status");
        assert_eq!(v["active"], json!(true));
        assert_eq!(v["user"], json!("Camille"));
    }

    #[tokio::test]
    async fn login_et_status_sans_configuration() {
        let st = test_support::etat("auth_vide");
        let Json(v) = login(
            State(st.clone()),
            Json(CorpsLogin {
                user: "Camille".into(),
                pass: "secret42".into(),
            }),
        )
        .await
        .expect("login");
        assert_eq!(v["ok"], json!(false));

        let Json(v) = status(State(st)).await.expect("status");
        assert_eq!(v["active"], json!(false));
        assert_eq!(v["user"], json!(""));
    }

    #[tokio::test]
    async fn setup_refuse_pseudo_et_mot_de_passe_trop_courts() {
        let st = test_support::etat("auth_court");
        let err = setup(
            State(st.clone()),
            Json(CorpsSetup {
                user: "T".into(),
                pass: "secret42".into(),
                old_pass: None,
            }),
        )
        .await
        .expect_err("pseudo trop court");
        assert!(err.to_string().contains("pseudo trop court"));

        let err = setup(
            State(st),
            Json(CorpsSetup {
                user: "Camille".into(),
                pass: "123".into(),
                old_pass: None,
            }),
        )
        .await
        .expect_err("mot de passe trop court");
        assert!(err.to_string().contains("mot de passe trop court"));
    }

    #[tokio::test]
    async fn remplacement_exige_l_ancien_mot_de_passe() {
        let st = test_support::etat("auth_remplacement");
        let Json(premier) = setup(
            State(st.clone()),
            Json(CorpsSetup {
                user: "Camille".into(),
                pass: "secret42".into(),
                old_pass: None,
            }),
        )
        .await
        .expect("premier setup");
        let ancien_jeton = premier["token"].as_str().unwrap().to_string();

        // Sans ancien mot de passe : refuse, rien ne change.
        let Json(v) = setup(
            State(st.clone()),
            Json(CorpsSetup {
                user: "Camille".into(),
                pass: "nouveau42".into(),
                old_pass: None,
            }),
        )
        .await
        .expect("sans ancien");
        assert_eq!(v["ok"], json!(false));
        assert_eq!(st.db.get_setting("api_token", ""), ancien_jeton);

        // Avec un mauvais ancien mot de passe : refuse.
        let Json(v) = setup(
            State(st.clone()),
            Json(CorpsSetup {
                user: "Camille".into(),
                pass: "nouveau42".into(),
                old_pass: Some("faux".into()),
            }),
        )
        .await
        .expect("mauvais ancien");
        assert_eq!(v["ok"], json!(false));

        // Avec le bon : accepte et regenere le jeton.
        let Json(v) = setup(
            State(st.clone()),
            Json(CorpsSetup {
                user: "Camille".into(),
                pass: "nouveau42".into(),
                old_pass: Some("secret42".into()),
            }),
        )
        .await
        .expect("bon ancien");
        assert_eq!(v["ok"], json!(true));
        let nouveau_jeton = v["token"].as_str().unwrap().to_string();
        assert_ne!(nouveau_jeton, ancien_jeton);
        assert_eq!(st.db.get_setting("api_token", ""), nouveau_jeton);

        // L'ancien mot de passe ne fonctionne plus, le nouveau oui.
        let Json(v) = login(
            State(st.clone()),
            Json(CorpsLogin {
                user: "Camille".into(),
                pass: "secret42".into(),
            }),
        )
        .await
        .expect("ancien mot de passe");
        assert_eq!(v["ok"], json!(false));
        let Json(v) = login(
            State(st),
            Json(CorpsLogin {
                user: "Camille".into(),
                pass: "nouveau42".into(),
            }),
        )
        .await
        .expect("nouveau mot de passe");
        assert_eq!(v["ok"], json!(true));
    }
}

