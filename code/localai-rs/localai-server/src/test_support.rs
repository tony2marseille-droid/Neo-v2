//! Utilitaires partages par les tests du binaire.
//!
//! `oracle-server` est un binaire (pas de cible `lib`) : les tests vivent
//! donc dans les modules. Ce module n'est compile qu'avec `cfg(test)` et
//! fournit un etat complet sur un dossier temporaire, sans toucher a la base
//! de production ni au port 8320.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use localai_core::Settings;

static COMPTEUR: AtomicU64 = AtomicU64::new(0);

/// Dossier temporaire unique (les tests tournent en parallele).
pub fn dossier_temp(nom: &str) -> std::path::PathBuf {
    let n = COMPTEUR.fetch_add(1, Ordering::Relaxed);
    let chemin = std::env::temp_dir().join(format!(
        "neo89_{nom}_{}_{n}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&chemin);
    std::fs::create_dir_all(&chemin).expect("creation du dossier de test");
    chemin
}

/// Etat applicatif complet sur une base SQLite neuve.
pub fn etat(nom: &str) -> Arc<crate::AppState> {
    let racine = dossier_temp(nom);
    let settings = Settings {
        host: "127.0.0.1".into(),
        port: 0,
        ollama_host: "http://127.0.0.1:1".into(),
        root_dir: racine.clone(),
        data_dir: racine.clone(),
        default_model: "modele-de-test".into(),
    };
    let db = localai_core::db::Db::open(&racine.join("test.sqlite"))
        .expect("ouverture de la base de test");
    let ollama = localai_core::ollama::Ollama::new(settings.ollama_host.clone())
        .expect("client ollama de test");
    Arc::new(crate::AppState {
        settings,
        db,
        ollama,
        jobs: crate::routes::outil::JobStore::new(),
        // 0 = pas de limitation : les tests ne doivent pas se bloquer eux-memes.
        limiter: Arc::new(crate::rate_limit::RateLimiter::new(0)),
        http: reqwest::Client::new(),
    })
}

/// Requete HTTP de test avec une adresse distante explicite.
///
/// Les middlewares d'authentification et de debit lisent `ConnectInfo` :
/// sans cette extension, l'extracteur echouerait avant le handler.
pub fn requete(
    methode: &str,
    uri: &str,
    ip: [u8; 4],
    jeton: Option<&str>,
) -> axum::http::Request<axum::body::Body> {
    let mut constructeur = axum::http::Request::builder().method(methode).uri(uri);
    if let Some(t) = jeton {
        constructeur = constructeur.header("authorization", format!("Bearer {t}"));
    }
    let mut req = constructeur
        .body(axum::body::Body::empty())
        .expect("requete de test");
    req.extensions_mut()
        .insert(axum::extract::ConnectInfo(std::net::SocketAddr::from((
            ip,
            40000,
        ))));
    req
}

/// Corps complet d'une reponse (sans limite de taille).
pub async fn corps(rep: axum::response::Response) -> String {
    use http_body_util::BodyExt;
    let octets = rep
        .into_body()
        .collect()
        .await
        .expect("lecture du corps")
        .to_bytes();
    String::from_utf8_lossy(&octets).to_string()
}
