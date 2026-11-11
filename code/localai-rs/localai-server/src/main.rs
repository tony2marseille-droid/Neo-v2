mod error;
mod feedback;
mod metrics;
mod pybridge;
mod rag_boost;
mod rate_limit;
mod reasoning;
mod routes;
mod telegram;
mod tools;
mod worker;

#[cfg(test)]
mod test_support;

use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::DefaultBodyLimit;
use axum::Router;
use localai_core::{Settings, APP_NAME, APP_VERSION};
use tower_http::cors::CorsLayer;

use crate::routes::{
    auth, benchmark, chat, comms, connecteurs, conseil, conversations, documents, extras,
    finetune, fournisseurs, health, logs, mcp, memory, models, onboarding, openai, outil, public_apis, sd, security, settings,
    studio, style_collab, system, tts, veille, video, voice,
};

#[derive(Clone)]
pub struct AppState {
    pub settings: Settings,
    pub db: localai_core::db::Db,
    pub ollama: localai_core::ollama::Ollama,
    pub jobs: crate::routes::outil::JobStore,
    /// Limitation de debit par IP (routes de generation).
    pub limiter: Arc<crate::rate_limit::RateLimiter>,
    /// Client HTTP partage (RAG boost, rerank, fournisseurs).
    pub http: reqwest::Client,
}

#[tokio::main]
async fn main() {
    // Lance par une tache planifiee, le processus peut heriter d'une priorite
    // basse (disque et memoire compris) : retour a la normale avant tout.
    localai_core::processus::priorite_normale();
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("localai_server=info".parse().unwrap()),
        )
        .init();

    let settings = Settings::load();
    settings.ensure_dirs();
    let db = localai_core::db::Db::open(&settings.db_path())
        .expect("ouverture SQLite");
    let ollama = localai_core::ollama::Ollama::new(settings.ollama_host.clone())
        .expect("client ollama");
    // Limite de debit : 300 requetes/minute par IP par defaut (0 = desactive).
    let debit = db
        .get_setting("rate_limit_par_minute", "300")
        .trim()
        .parse::<u32>()
        .unwrap_or(300);
    let state = Arc::new(AppState {
        settings: settings.clone(),
        db,
        ollama,
        jobs: crate::routes::outil::JobStore::new(),
        limiter: Arc::new(rate_limit::RateLimiter::new(debit)),
        http: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()
            .unwrap_or_default(),
    });
    tracing::info!("limite de debit : {debit} req/min par IP (0 = desactive)");
    // Worker Python chaud : demarre avec le serveur.
    worker::init(state.settings.root_dir.clone());
    // Nettoyage VRAM : un modele residant 100 % en RAM (charge quand la VRAM
    // etait occupee) est lent ; on le decharge pour qu'il recharge sur GPU.
    {
        let o = state.ollama.clone();
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            if let Ok(v) = o.ps().await {
                if let Some(models) = v.get("models").and_then(|m| m.as_array()) {
                    for m in models {
                        let vram = m.get("size_vram").and_then(|x| x.as_i64()).unwrap_or(0);
                        let nom = m.get("name").and_then(|n| n.as_str()).unwrap_or("");
                        if vram == 0 && !nom.is_empty() {
                            let _ = o.unload(nom).await;
                            tracing::info!("[ollama] modele CPU decharge: {nom}");
                        }
                    }
                }
            }
        });
    }

    let cors = CorsLayer::new()
        .allow_origin([
            "http://localhost:5173".parse().unwrap(),
            "http://127.0.0.1:5173".parse().unwrap(),
        ])
        .allow_methods(tower_http::cors::Any)
        .allow_headers(tower_http::cors::Any);

    // Auth optionnelle : si un token API est defini, les appels /api/*
    // provenant de l'exterieur (non-loopback) doivent le presenter en
    // "Authorization: Bearer <token>". Les requetes locales (loopback,
    // UI auto-hebergee) passent toujours. Route publique : /api/health.
    // Le token est lu dans le middleware (auth_middleware) a chaque requete.
    let mut app: Router = Router::new()
        .merge(health::router())
        .merge(logs::router())
        .merge(auth::router())
        .merge(chat::router())
        .merge(feedback::router())
        .merge(onboarding::router())
        .merge(connecteurs::router())
        .merge(outil::router())
        .merge(conversations::router())
        .merge(models::router())
        .merge(system::router())
        .merge(settings::router())
        .merge(public_apis::router())
        .merge(memory::router())
        .merge(tts::router())
        .merge(documents::router())
        .merge(benchmark::router())
        .merge(voice::router())
        .merge(video::router())
        .merge(sd::router())
        .merge(studio::router())
        .merge(style_collab::router())
        .merge(extras::router())
        .merge(security::router())
        .merge(comms::router())
        .merge(fournisseurs::router())
        .merge(conseil::router())
        .merge(veille::router())
        .merge(finetune::router())
        .merge(openai::router())
        .merge(mcp::router())
        .layer(cors)
        // Images jointes au chat (base64 4K ~10 Mo) : plafond 32 Mo.
        .layer(DefaultBodyLimit::max(32 * 1024 * 1024))
        // Ordre d'execution : authentification, puis debit, puis handler.
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            rate_limit_middleware,
        ))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            auth_middleware,
        ))
        .with_state(state.clone());

    // Frontend statique : dist/ servi, 404 -> index.html (SPA).
    // index.html en no-store : apres chaque deploy, le navigateur doit
    // recharger le HTML (sinon l'ancienne UI reste en cache et les
    // assets haches ont change -> UI fige sur l'ancienne version).
    let dist = state.settings.root_dir.join("frontend").join("dist");
    let index = dist.join("index.html");
    if index.is_file() {
        tracing::info!("frontend: {}", dist.display());
        let svc = tower_http::services::ServeDir::new(&dist)
            .not_found_service(tower_http::services::ServeFile::new(&index));
        app = app.fallback_service(svc);
        let idx = index.clone();
        app = app
            .route("/", axum::routing::get(move || {
                let idx = idx.clone();
                async move { no_store_file(idx).await }
            }))
            .route("/index.html", axum::routing::get(move || {
                let idx2 = index.clone();
                async move { no_store_file(idx2).await }
            }));
    } else {
        tracing::warn!("pas de frontend dist/");
    }

    let addr: SocketAddr = format!("{}:{}", settings.host, settings.port)
        .parse()
        .expect("adresse");
    tracing::info!("{APP_NAME} {APP_VERSION} (rust) sur {addr}");
    telegram::maybe_start(state.clone());
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .expect("serve");
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("arret demande, vidange...");
}

/// Middleware d'authentification API : les requetes EXTERNES (non-loopback)
/// vers /api/* doivent presenter le token si celui-ci est defini.
/// Les requetes locales (UI auto-hebergee) et /api/health passent toujours.
async fn auth_middleware(
    axum::extract::State(state): axum::extract::State<Arc<AppState>>,
    axum::extract::ConnectInfo(addr): axum::extract::ConnectInfo<std::net::SocketAddr>,
    req: axum::http::Request<axum::body::Body>,
    next: axum::middleware::Next,
) -> axum::response::Response {
    let chemin = req.uri().path();
    // Loopback ET hote local : une page web malveillante qui fait pointer son
    // propre domaine sur 127.0.0.1 (DNS rebinding) arrive en loopback avec
    // `Host: domaine-attaquant` ; elle est alors traitee comme un client
    // distant (jeton exige s'il est defini).
    let hote = req
        .headers()
        .get(axum::http::header::HOST)
        .and_then(|v| v.to_str().ok());
    let local = addr.ip().is_loopback() && hote_local(hote);
    let public = chemin == "/api/health"
        || chemin == "/api/auth/login"
        || chemin == "/api/auth/status";
    // `/v1/*` (passerelle OpenAI) et `/mcp` (outils) : meme exigence que
    // `/api/*`, sinon n'importe quel client du LAN pourrait consommer le GPU
    // ou executer des outils sans jeton.
    let protege = chemin.starts_with("/api/")
        || chemin.starts_with("/v1/")
        || chemin == "/mcp";
    if local || public || !protege {
        return next.run(req).await;
    }
    let token = state.db.get_setting("api_token", "");
    if token.is_empty() {
        // Pas de token defini : acces LAN autorise (config actuelle),
        // mais consigne dans le journal pour sensibilite.
        return next.run(req).await;
    }
    // Jeton en en-tete (clients API) OU en query string. Les balises <img>,
    // <video> et les liens de telechargement ne peuvent pas envoyer
    // d'en-tete Authorization : sans cette seconde voie, les images du chat
    // et les fichiers exportes renvoyaient 401 hors loopback.
    let entete_ok = req
        .headers()
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|v| egal_temps_constant(v.trim_start_matches("Bearer ").trim(), &token))
        .unwrap_or(false);
    let query_ok = req
        .uri()
        .query()
        .map(|q| {
            q.split('&').any(|kv| match kv.split_once('=') {
                // Le jeton est hexadecimal : aucun decodage URL necessaire.
                Some(("token", v)) => egal_temps_constant(v, &token),
                _ => false,
            })
        })
        .unwrap_or(false);
    if entete_ok || query_ok {
        return next.run(req).await;
    }
    axum::http::Response::builder()
        .status(axum::http::StatusCode::UNAUTHORIZED)
        .body(axum::body::Body::from(
            "{\"error\":\"token API requis (Authorization: Bearer)\"}",
        ))
        .unwrap()
}

/// Comparaison de jetons sans fuite temporelle (pas d'arret au 1er ecart).
fn egal_temps_constant(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// L'en-tete Host designe-t-il cette machine ? Acceptes : absent (vieux
/// clients HTTP/1.0), adresse IP litterale, `localhost` / `*.localhost`,
/// nom de la machine (COMPUTERNAME) et noms listes dans `NEO_ALLOWED_HOSTS`
/// (separes par des virgules). Tout autre nom vient d'un navigateur qui
/// visite un site tiers.
fn hote_local(hote: Option<&str>) -> bool {
    let Some(h) = hote else { return true };
    let h = h.trim().to_ascii_lowercase();
    // Retirer le port : `[::1]:8320`, `127.0.0.1:8320`, `localhost:8320`.
    let nom = if let Some(reste) = h.strip_prefix('[') {
        reste.split(']').next().unwrap_or("")
    } else {
        h.rsplit_once(':').map(|(n, _)| n).unwrap_or(&h)
    };
    if nom.is_empty() {
        return false;
    }
    if nom.parse::<std::net::IpAddr>().is_ok() || nom == "localhost" || nom.ends_with(".localhost") {
        return true;
    }
    let machine = std::env::var("COMPUTERNAME").unwrap_or_default().to_ascii_lowercase();
    if !machine.is_empty() && nom == machine {
        return true;
    }
    std::env::var("NEO_ALLOWED_HOSTS")
        .unwrap_or_default()
        .split(',')
        .any(|n| n.trim().eq_ignore_ascii_case(nom))
}

/// Middleware de limitation de debit : seules les routes qui coutent du GPU
/// sont comptees. Les lectures (sante, listes, documents) ne le sont pas,
/// sinon l'UI se bloquerait elle-meme en rafraichissant ses panneaux.
async fn rate_limit_middleware(
    axum::extract::State(state): axum::extract::State<Arc<AppState>>,
    axum::extract::ConnectInfo(addr): axum::extract::ConnectInfo<std::net::SocketAddr>,
    req: axum::http::Request<axum::body::Body>,
    next: axum::middleware::Next,
) -> axum::response::Response {
    use axum::response::IntoResponse;

    let chemin = req.uri().path();
    let couteux = matches!(
        chemin,
        // /api/auth/login : PBKDF2 couteux + force brute du mot de passe.
        "/api/chat"
            | "/api/auth/login"
            | "/api/outil/execute"
            | "/v1/chat/completions"
            | "/v1/completions"
            | "/mcp"
    );
    if !couteux || state.limiter.max() == 0 {
        return next.run(req).await;
    }

    let ip = addr.ip();
    if state.limiter.autorise(ip) {
        return next.run(req).await;
    }

    crate::metrics::rate_limited();
    tracing::warn!("limite de debit atteinte pour {ip} sur {chemin}");
    let attente = state.limiter.attente(ip);
    let corps = format!(
        "{{\"error\":\"limite de debit atteinte\",\"limite_par_minute\":{},\"retry_after\":{attente}}}",
        state.limiter.max()
    );
    (
        axum::http::StatusCode::TOO_MANY_REQUESTS,
        [
            (axum::http::header::RETRY_AFTER, attente.to_string()),
            (
                axum::http::header::CONTENT_TYPE,
                "application/json".to_string(),
            ),
        ],
        corps,
    )
        .into_response()
}

/// index.html avec Cache-Control: no-store (anti UI fige apres deploy).
async fn no_store_file(
    path: std::path::PathBuf,
) -> axum::response::Response {
    use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
    use axum::response::IntoResponse;
    match tokio::fs::read(&path).await {
        Ok(bytes) => {
            let mut headers = HeaderMap::new();
            headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
            headers.insert(
                header::CONTENT_TYPE,
                HeaderValue::from_static("text/html; charset=utf-8"),
            );
            (headers, bytes).into_response()
        }
        Err(_) => (StatusCode::NOT_FOUND, "frontend absent").into_response(),
    }
}

#[cfg(test)]
mod tests_auth {
    use super::*;
    use crate::test_support::{corps, etat, requete};
    use tower::ServiceExt;

    /// Routeur minimal derriere le vrai middleware d'authentification.
    async fn appel(st: &Arc<AppState>, ip: [u8; 4], hote: Option<&str>, jeton: Option<&str>) -> u16 {
        let app = Router::new()
            .route("/api/x", axum::routing::get(|| async { "ok" }))
            .layer(axum::middleware::from_fn_with_state(st.clone(), auth_middleware));
        let mut req = requete("GET", "/api/x", ip, jeton);
        if let Some(h) = hote {
            req.headers_mut().insert(axum::http::header::HOST, h.parse().unwrap());
        }
        let rep = app.oneshot(req).await.unwrap();
        let code = rep.status().as_u16();
        let _ = corps(rep).await;
        code
    }

    #[tokio::test]
    async fn middleware_jeton_et_dns_rebinding() {
        let st = etat("auth_middleware");
        st.db.set_setting("api_token", "secret-de-test").unwrap();
        // UI locale : passe sans jeton.
        assert_eq!(appel(&st, [127, 0, 0, 1], Some("localhost:8320"), None).await, 200);
        // DNS rebinding : loopback mais Host etranger -> jeton exige.
        assert_eq!(appel(&st, [127, 0, 0, 1], Some("evil.example.com:8320"), None).await, 401);
        assert_eq!(appel(&st, [127, 0, 0, 1], Some("evil.example.com"), Some("secret-de-test")).await, 200);
        // LAN : jeton exige et verifie.
        assert_eq!(appel(&st, [192, 168, 1, 9], Some("192.168.1.2:8320"), None).await, 401);
        assert_eq!(appel(&st, [192, 168, 1, 9], Some("192.168.1.2:8320"), Some("mauvais")).await, 401);
        assert_eq!(appel(&st, [192, 168, 1, 9], Some("192.168.1.2:8320"), Some("secret-de-test")).await, 200);
    }

    #[test]
    fn hotes_locaux() {
        for h in ["127.0.0.1:8320", "localhost:8320", "[::1]:8320", "192.168.1.20:8320", "neo.localhost"] {
            assert!(hote_local(Some(h)), "{h}");
        }
        assert!(hote_local(None));
        for h in ["evil.example.com", "evil.example.com:8320", "localhost.evil.com", ""] {
            assert!(!hote_local(Some(h)), "{h}");
        }
    }

    #[test]
    fn jeton_compare() {
        assert!(egal_temps_constant("abc123", "abc123"));
        assert!(!egal_temps_constant("abc123", "abc124"));
        assert!(!egal_temps_constant("abc", "abc123"));
    }
}

