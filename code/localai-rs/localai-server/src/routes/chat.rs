use std::sync::Arc;

use axum::{
    extract::State,
    response::sse::{Event, KeepAlive, Sse},
    routing::{get, post},
    Json, Router,
};
use futures_util::{Stream, StreamExt};
use localai_core::db::now_iso;
use localai_core::ollama::Msg;
use crate::error::ApiError;
use localai_core::AppError;
use serde::Deserialize;
use serde_json::json;
use std::convert::Infallible;
use tokio::sync::mpsc;

use crate::tools;
use crate::AppState;
use crate::reasoning;

mod images;
mod persona;
mod prompt;
mod stream;
mod vision;

use images::{chemin_existe, chemins_images, corrige_chemins_image, vivants_depuis_rows};
use prompt::{est_tokens, messages_json, NUM_CTX, TOOL_OUT_LIMIT};
use stream::{sse_data, spawn_reponse_cloud, spawn_reponse_locale};

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/api/chat", post(chat))
        .route("/api/user-active", get(user_active))
        .route("/api/vision/faces", post(vision::faces))
        .layer(vision::body_limit())
}

/// Marqueur d'activite utilisateur : les jobs de fond (boucle agents,
/// veille, conseil...) passent background=true et ne le mettent PAS a jour.
/// Ils consultent GET /api/user-active pour ceder la file d'attente GPU
/// a l'utilisateur interactif (priorite Neo).
async fn user_active(State(st): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let last: i64 = st.db.get_setting("user_active_at", "").parse().unwrap_or(0);
    let now = crate::routes::finetune::now_unix() as i64;
    Json(json!({ "seconds_since_user": if last > 0 { now - last } else { -1i64 } }))
}

#[derive(Debug, Deserialize, Clone, Default)]
pub struct ChatBody {
    pub conversation_id: Option<i64>,
    pub model: Option<String>,
    pub message: String,
    pub temperature: Option<f32>,
    pub regenerate: Option<bool>,
    pub agent_id: Option<String>,
    pub tools_enabled: Option<bool>,
    pub rag_enabled: Option<bool>,
    pub images: Option<Vec<String>>,
    pub web_enabled: Option<bool>,
    /// true = requete de fond (boucle agents, scripts auto) : ne met pas
    /// a jour le marqueur d'activite utilisateur (priorite file d'attente).
    pub background: Option<bool>,
}

pub async fn chat(
    State(st): State<Arc<AppState>>,
    Json(b): Json<ChatBody>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ApiError> {
    if b.message.trim().is_empty() {
        return Err(ApiError(AppError::BadRequest("message vide".into())));
    }
    // Priorite file d'attente : trace l'activite utilisateur interactif.
    // Les requetes de fond (background=true) ne touchent pas ce marqueur :
    // elles doivent lui ceder la place (voir GET /api/user-active).
    if !b.background.unwrap_or(false) {
        let _ = st.db.set_setting(
            "user_active_at",
            &crate::routes::finetune::now_unix().to_string(),
        );
    }
    let mut model = if b.model.as_deref().unwrap_or("auto") == "auto" {
        // Le reglage utilisateur (DB) prime sur la variable d'env du boot.
        st.db.get_setting("default_model", &st.settings.default_model)
    } else {
        b.model.clone().unwrap()
    };
    let temperature = b.temperature.unwrap_or(0.2).clamp(0.0, 2.0);

    // Conversation : reuse ou creation (identique v1).
    let now = now_iso();
    let cid: i64 = match b.conversation_id {
        Some(id) => {
            let exists = st
                .db
                .query_one_json("SELECT id FROM conversations WHERE id=?", &[&id])?;
            if exists.is_none() {
                return Err(ApiError(AppError::NotFound("Conversation introuvable".into())));
            }
            if b.regenerate.unwrap_or(false) {
                // Seule une reponse POSTERIEURE a la derniere question est
                // remplacee : si le tour precedent a echoue (aucune reponse
                // enregistree), la bonne reponse d'avant reste intacte.
                if let Some(last) = st.db.query_one_json(
                    "SELECT id FROM messages WHERE conversation_id=?1 AND role='assistant' \
                     AND id > COALESCE((SELECT MAX(id) FROM messages WHERE conversation_id=?1 AND role='user'), 0) \
                     ORDER BY id DESC LIMIT 1",
                    &[&id],
                )? {
                    if let Some(mid) = last.get("id").and_then(|v| v.as_i64()) {
                        st.db.execute("DELETE FROM messages WHERE id=?", &[&mid])?;
                    }
                }
            } else {
                st.db.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                    &[&id, &"user", &b.message.as_str(), &now.as_str()],
                )?;
            }
            st.db.execute(
                "UPDATE conversations SET updated_at=?, model=? WHERE id=?",
                &[&now.as_str(), &model.as_str(), &id],
            )?;
            id
        }
        None => {
            let title: String = b.message.trim().chars().take(60).collect();
            let title = if title.is_empty() { "Nouvelle conversation".to_string() } else { title };
            let id = st.db.execute(
                "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
                &[&title.as_str(), &model.as_str(), &now.as_str(), &now.as_str()],
            )?;
            st.db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                &[&id, &"user", &b.message.as_str(), &now.as_str()],
            )?;
            id
        }
    };

    let rows = st.db.query_all_json(
        "SELECT role, content FROM messages WHERE conversation_id=? AND role IN ('user','assistant') ORDER BY id DESC LIMIT 40",
        &[&cid],
    )?;
    let mut history: Vec<Msg> = rows
        .into_iter()
        .rev()
        .filter_map(|r| {
            Some(Msg {
                role: r.get("role")?.as_str()?.to_string(),
                content: r.get("content")?.as_str()?.to_string(),
            })
        })
        .collect();

    // Outils : agent explicite, detection d'intention, ou activation explicite (+ web).
    // 1) agent explicite (non-auto) -> ses outils.
    // 2) sinon : routeur Python enrichi EN PRIORITE (il distingue
    //    voir_image / generer_image / 3d / dev / docs). L'ancienne
    //    detection Rust court-circuitait sur "photo"/"image" et envoyait
    //    "montre-moi une photo de X" en cad3d (mesh !) ; elle ne sert plus
    //    que de repli, avec des mots-cles 3D STRICTS uniquement.
    let mut tool_names: Vec<String> = Vec::new();
    let mut resolved_agent: Option<String> = None;
    let mut route_reason: String = String::new();
    if let Some(aid) = b.agent_id.as_deref() {
        if !aid.is_empty() && aid != "auto" {
            tool_names = tools::agent_tools(&st.settings.root_dir, aid).await;
            if !tool_names.is_empty() {
                resolved_agent = Some(aid.to_string());
                route_reason = format!("agent explicite: {aid}");
            }
        }
    }
    if tool_names.is_empty() {
        let (mut routed, mut raison) =
            tools::route_agent_full(&st.settings.root_dir, &b.message).await;
        if (routed.is_empty() || routed == "general") && !b.regenerate.unwrap_or(false) {
            // Suite d'une image (« plus de variations », « refais-la plus
            // sombre ») : la reponse precedente de Neo montrait une image.
            let precedente = history
                .iter()
                .rev()
                .find(|m| m.role == "assistant")
                .map(|m| m.content.as_str())
                .unwrap_or("");
            if persona::suite_d_image(&b.message, precedente) {
                routed = "image".to_string();
                raison = "suite d'une image (reponse precedente avec image)".to_string();
            }
        }
        // Le choix de l'agent appartient au routeur Python (`router.py`), seul.
        // L'ancien repli d'ici envoyait en conception 3D tout message contenant
        // « 3d », « imprim » ou « tranche » (« mon imprimante ne marche plus »,
        // « un film en 3D »), contre l'avis du routeur.
        // Ecrire du code SANS toucher aux fichiers ni rien executer : Neo repond
        // lui-meme (modele conversationnel + reflexion, code complet dans la
        // reponse). Banc du 09/11/2026 : 5/5 contre 3/5 via l'agent dev, dont la
        // reponse passe par le JSON contraint du toolshim et perd le code.
        if routed == "dev" && persona::code_a_ecrire_seulement(&b.message) {
            routed = "general".to_string();
            raison = "code a ecrire seulement -> Neo (reflexion)".to_string();
            route_reason = raison.clone();
        }
        if !routed.is_empty() && routed != "general" {
            tool_names = tools::agent_tools(&st.settings.root_dir, &routed).await;
            if routed == "docs" {
                // Documents : la reponse vient du contexte injecte, aucun outil
                // n'est utile. Sans boucle d'outils, c'est le modele de
                // conversation (gemma4:12b, reflexion) qui repond au lieu de
                // localai-prog via le toolshim (58 s mesurees le 09/11/2026).
                tool_names.clear();
                resolved_agent = Some(routed.clone());
                route_reason = raison;
            } else if !tool_names.is_empty() {
                resolved_agent = Some(routed.clone());
                route_reason = raison;
            }
        }
    }
    if tool_names.is_empty() && b.tools_enabled.unwrap_or(false) {
        tool_names = tools::default_tools();
    }
    if b.web_enabled.unwrap_or(false) {
        for w in ["web_search", "web_fetch"] {
            if !tool_names.iter().any(|t| t == w) {
                tool_names.push(w.to_string());
            }
        }
    }
    // System prompt combine : agent + memoirs + RAG (miroir _build_system_prompt).
    // Quand des images sont jointes : sanitisation base64 (Ollama exige du brut
    // sans prefixe data:), forçage du modele vision (gemma4:12b, sinon
    // qwen2.5vl:7b) et desactivation des outils natifs Ollama.
    let images_clean: Vec<String> = b
        .images
        .as_ref()
        .map(|v| v.iter().filter_map(|s| vision::clean_b64(s)).collect())
        .unwrap_or_default();
    let has_images = !images_clean.is_empty();
    if has_images {
        let tags = st.ollama.tags().await.ok();
        model = persona::choisir_installe(vision::VISION_MODELES, tags.as_ref());
    }
    // Conversation pure (aucun agent outille, aucune image, modele « auto ») :
    // le personnage de Neo parle avec le meilleur generaliste installe, capable
    // de reflexion. Les agents outilles gardent leur modele (`default_model`).
    let conversation_pure = (resolved_agent.is_none() || resolved_agent.as_deref() == Some("docs"))
        && tool_names.is_empty()
        && !has_images;
    if conversation_pure && b.model.as_deref().unwrap_or("auto") == "auto" {
        let impose = st.db.get_setting("modele_conversation", "");
        model = if impose.trim().is_empty() {
            let tags = st.ollama.tags().await.ok();
            persona::choisir_installe(persona::MODELES_CONVERSATION, tags.as_ref())
        } else {
            impose
        };
    }
    // Agents outilles (sauf dev) : le modele de conversation, deja en VRAM.
    // Avec le dialogue d'outils complet, gemma4:12b fait 10/10 en 1,4 s sur le
    // banc des outils (localai-prog : 10/10 en 2,0 s) et l'echange de modeles
    // coutait 25 a 35 s a chaque passage conversation <-> agent. L'agent dev
    // (code, fichiers, execution) garde le modele entraine pour lui.
    // Reglage `agents_modele_conversation` = 0 pour revenir a `default_model`.
    if !conversation_pure
        && !has_images
        && resolved_agent.as_deref().is_some_and(|a| a != "dev")
        && b.model.as_deref().unwrap_or("auto") == "auto"
        && st.db.get_setting("agents_modele_conversation", "1").trim() != "0"
    {
        let tags = st.ollama.tags().await.ok();
        let m = persona::choisir_installe(persona::MODELES_CONVERSATION, tags.as_ref());
        if m.starts_with("gemma4") {
            model = m;
        }
    }
    // Une question sur une image (« combien… », « que dit ce ticket… ») merite
    // aussi la reflexion : comptage et lecture 13/14 -> 14/14 avec gemma4:12b.
    let image_seule = has_images && resolved_agent.is_none() && tool_names.is_empty();
    let reflexion = (conversation_pure || image_seule)
        && persona::sait_reflechir(&model)
        && persona::doit_reflechir(&b.message);
    // Extraits documentaires reellement injectes : renvoyes dans l'evenement
    // `meta` pour que l'interface affiche des citations cliquables.
    let mut rag_sources: Vec<crate::routes::documents::Source> = Vec::new();
    {
        // Identite de Neo en tete, pour TOUS les agents : un seul personnage.
        let identite = if resolved_agent.is_some() {
            persona::identite_agent(&st.db)
        } else {
            persona::fiche(&st.settings.root_dir, &st.db)
        };
        let mut parts: Vec<String> = vec![identite];
        if has_images {
            parts.push(vision::VISION_SYSTEM.to_string());
        }
        // Ce que Neo a deja mis en fiche sur ce sujet (Documents > Fiches).
        if let Some(bloc) = persona::fiches_connues(&st.db, &b.message) {
            parts.push(bloc);
        }
        if let Some(agent) = resolved_agent.as_deref() {
            let sys = tools::agent_system(&st.settings.root_dir, agent).await;
            if !sys.trim().is_empty() {
                parts.push(sys);
            }
            // Le routeur a deja reconnu l'intention : l'agent n'a pas a la redeviner.
            if let Some(intention) = persona::consigne_intention(&route_reason) {
                parts.push(intention);
            }
            // RAG si l'agent l'exige (docs) ou demande explicite.
            let want_rag = b.rag_enabled.unwrap_or(false)
                || tools::agent_rag(&st.settings.root_dir, agent).await;
            if want_rag {
                let (block, sources) = crate::routes::documents::rag_block_detaille(
                    &st.db, &st.settings.db_path(), &st.ollama, &st.http,
                    &st.settings.ollama_host, &b.message, 4).await;
                if !block.is_empty() {
                    parts.push(block);
                }
                rag_sources = sources;
            }
        } else if b.rag_enabled.unwrap_or(false) {
            let (block, sources) = crate::routes::documents::rag_block_detaille(
                &st.db, &st.settings.db_path(), &st.ollama, &st.http,
                &st.settings.ollama_host, &b.message, 4).await;
            if !block.is_empty() {
                parts.push(block);
            }
            rag_sources = sources;
        }
        // Memoires utilisateur uniquement. Les notes techniques ecrites par
        // les agents (tag "outil"/"agent") ne doivent JAMAIS entrer dans le
        // prompt de conversation : elles decrivent des echecs d'implementation
        // passes et faisaient croire a l'assistant que c'etait des faits sur
        // l'utilisateur (regression constatee apres le lot "conseil").
        if let Ok(mems) = st.db.query_all_json(
            "SELECT content FROM memories \
             WHERE tag NOT IN ('outil','agent','debug','trace') \
             ORDER BY id DESC LIMIT 20", &[]) {
            let lines: Vec<String> = mems.iter().filter_map(|m| {
                m.get("content")?.as_str().map(|c| format!("- {c}"))
            }).collect();
            if !lines.is_empty() {
                parts.push(format!("Informations memorisees a propos de l'utilisateur:\n{}", lines.join("\n")));
            }
        }
        // Image jointe a une demande outillee (retouche, analyse...) : les
        // outils travaillent sur des FICHIERS. Sans chemin reel, le modele en
        // inventait un (« input_file_0.png », 10/11/2026) et la retouche echouait.
        if has_images && !tool_names.is_empty() {
            let chemins = vision::enregistrer_jointes(&st.settings.root_dir, &images_clean);
            if !chemins.is_empty() {
                parts.push(format!(
                    "Images jointes par l'utilisateur, enregistrees sur le disque. Pour un outil \
                     (image_path), utilise ce chemin EXACT et n'invente jamais de nom de fichier :\n{}",
                    chemins.iter().map(|c| format!("- {c}")).collect::<Vec<_>>().join("\n")
                ));
            }
        }
        if !parts.is_empty() {
            history.insert(0, Msg { role: "system".into(), content: parts.join("\n\n") });
        }
    }

    // Modeles cloud (cloud:<fournisseur>) : reserves au chat pur. Si des
    // outils doivent tourner (image, 3D, documents...), on reste en LOCAL
    // (les outils tournent via Ollama ; consigne bascule : modele
    // "adapte a la demande").
    if model.starts_with("cloud:") && !tool_names.is_empty() {
        if route_reason.is_empty() {
            route_reason = "outils requis -> modele local (cloud ignore)".to_string();
        } else {
            route_reason.push_str(" | outils requis -> modele local (cloud ignore)");
        }
        model = st.db.get_setting("default_model", &st.settings.default_model);
    }

    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let tx_meta = tx.clone();
    let _ = tx_meta.send(sse_data(json!({
        "type": "meta",
        "conversation_id": cid,
        "model": model,
        "agent": resolved_agent.clone(),
        "reflexion": reflexion,
        "route_reason": route_reason,
        "rag_sources": rag_sources,
    })));

    // Consigne neo-boost : best-of-N pour le raisonnement pur, quand
    // should_boost detecte une question de raisonnement et que l'agent est
    // general/aucun. Le spawn ecrit dans le canal ORIGINAL ; la generation
    // normale est alors sautee. Exclus : outils demandes (web, agent a
    // outils), images jointes (le boost ne transmet pas les images) et
    // modeles cloud (le boost interroge Ollama).
    let boost_eligible = (resolved_agent.is_none()
        || resolved_agent.as_deref() == Some("general"))
        && tool_names.is_empty()
        && !has_images
        && !model.starts_with("cloud:");
    // Best-of-N : desormais optionnel (reglage boost_mode = "best_of_n"). Par
    // defaut, la reflexion native du modele conversationnel le remplace : une
    // seule generation qui raisonne, au lieu de 4 tirages et d'un petit juge.
    let boost_actif = boost_eligible
        && st.db.get_setting("boost_mode", "reflexion") == "best_of_n"
        && reasoning::should_boost(&b.message);
    if boost_actif {
        let db2 = st.db.clone();
        let ollama_url = st.settings.ollama_host.clone();
        let model2 = model.clone();
        let judge = st.db.get_setting("model_rapide", "qwen3:8b").to_string();
        let boost_n = st.db.get_setting("boost_n", "4")
            .parse::<usize>().unwrap_or(4);
        let boost_par = st.db.get_setting("boost_parallel", "2")
            .parse::<usize>().unwrap_or(2);
        let temp = temperature;
        let msg_user = b.message.clone();
        // Le boost doit recevoir TOUT le contexte (systeme + RAG + memoires +
        // tours precedents), sinon il repond hors-sol. On repart de l'historique
        // deja construit, le message utilisateur y figure deja.
        let msgs_boost: Vec<serde_json::Value> = history
            .iter()
            .map(|m| json!({ "role": m.role, "content": m.content }))
            .collect();
        let tx_boost = tx.clone();
        let root_extract = st.settings.root_dir.clone();
        tokio::spawn(async move {
            let _ = tx_boost.send(sse_data(json!({
                "type": "boost_start",
                "n": boost_n,
                "model": model2,
            })));
            let http = reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(300))
                .build()
                .unwrap_or_default();
            let msgs = if msgs_boost.is_empty() {
                vec![json!({"role": "user", "content": msg_user})]
            } else {
                msgs_boost
            };
            match reasoning::best_of_n(
                &http, &ollama_url, &model2, &judge, &msgs,
                boost_n, boost_par, temp, 16384,
            ).await {
                Ok(outcome) => {
                    let reponse = outcome.answer.clone();
                    let method = outcome.method;
                    let votes = outcome.votes;
                    let generated = outcome.generated;
                    let chars: Vec<char> = reponse.chars().collect();
                    let step = (chars.len() / 20).max(8);
                    let mut i = 0;
                    while i < chars.len() {
                        let end = (i + step).min(chars.len());
                        let chunk: String = chars[i..end].iter().collect();
                        let _ = tx_boost.send(sse_data(json!({
                            "type": "token", "content": chunk
                        })));
                        tokio::time::sleep(std::time::Duration::from_millis(15)).await;
                        i = end;
                    }
                    let now2 = now_iso();
                    let _ = db2.execute(
                        "INSERT INTO messages (conversation_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
                        &[&cid, &"assistant", &reponse.as_str(), &model2.as_str(), &now2.as_str()],
                    );
                    let _ = db2.execute(
                        "UPDATE conversations SET updated_at=? WHERE id=?",
                        &[&now2.as_str(), &cid],
                    );
                    let _ = tx_boost.send(sse_data(json!({
                        "type": "boost_done",
                        "method": method,
                        "votes": votes,
                        "generated": generated,
                        "final_answer": outcome.final_answer,
                    })));
                    let _ = tx_boost.send(sse_data(json!({
                        "type": "done",
                        "conversation_id": cid,
                        "model": model2,
                    })));
                    // Extraction automatique de faits depuis la conversation terminee
                    crate::pybridge::extraire_faits_en_fond(&root_extract, cid);
                }
                Err(e) => {
                    crate::metrics::ollama_echecs();
                    let _ = tx_boost.send(sse_data(json!({
                        "type": "error", "error": format!("boost: {e}")
                    })));
                }
            }
        });
    }

    if !boost_actif && tool_names.is_empty() {
        // Chemin rapide : streaming local compte OU fournisseur cloud.
        let db2 = st.db.clone();
        let ollama = st.ollama.clone();
        let model2 = model.clone();
        let root2 = st.settings.root_dir.clone();
        // Reflexion : plus de jetons (pensee + reponse) et un contexte plus large.
        let body = serde_json::json!({
            "messages": messages_json(&history, &Some(images_clean.clone())),
            "think": reflexion,
            "options": {
                "temperature": if reflexion { temperature.min(0.6) } else { temperature },
                // Image : budget de reflexion plus court (un comptage peut
                // tourner en rond ; le secours sans reflexion prend le relais).
                "num_predict": match (reflexion, has_images) {
                    (true, false) => 8192,
                    (true, true) => 4096,
                    _ => 2048,
                },
                "num_ctx": localai_core::ollama::contexte_pour(
                    &model, if reflexion { NUM_CTX + NUM_CTX / 2 } else { NUM_CTX }),
            },
        });
        if let Some(pid) = model.strip_prefix("cloud:").map(|s| s.to_string()) {
            spawn_reponse_cloud(
                tx,
                db2,
                ollama,
                root2,
                pid,
                history,
                cid,
                temperature,
                st.settings.default_model.clone(),
            );
        } else {
            spawn_reponse_locale(tx, db2, ollama, root2, model2, body, cid);
        }
    } else if !boost_actif {
        // Chemin outils : boucle multi-rounds avec specs + execution.
        let db2 = st.db.clone();
        let ollama = st.ollama.clone();
        let root = st.settings.root_dir.clone();
        let mut model2 = model.clone();
        let defaut2 = st.settings.default_model.clone();
        let level = tools::tool_level(&st.db);
        let images2 = images_clean.clone();
        let msg_origine = b.message.clone();
        let agent_outils = resolved_agent.clone().unwrap_or_default();
        tokio::spawn(async move {
            let started = std::time::Instant::now();
            let debut_iso = now_iso();
            let mut relance_faite = false;
            // Depuis l'injection de toolshim (proxy d'emulation du tool-calling),
            // le modele vision peut aussi appeler des outils : toolshim transforme
            // les tools en consigne JSON et ne les passe jamais en natif a Ollama
            // (plus d'erreur "does not support tools" pour qwen2.5vl). On garde
            // donc les specs meme quand des images sont jointes.
            let specs = tools::specs(&root, &tool_names).await.unwrap_or_default();
            let mut messages = history;
            let mut full = String::new();
            let mut tool_pos: Vec<(usize, String, usize)> = Vec::new();
            // Champs propres au dialogue d'outils, fusionnes dans le JSON envoye :
            // `tool_calls` sur le message de l'assistant, `tool_name` sur chaque
            // resultat. Sans eux, un modele a appel natif (gemma4) voyait des
            // resultats qu'il ne s'attribuait pas et rappelait l'outil en boucle
            // (banc des outils du 09/11/2026 : 13 appels, aucune reponse) ; le
            // toolshim les convertit pour les modeles emules.
            let mut champs_outils: Vec<(usize, serde_json::Value)> = Vec::new();
            let seuil = NUM_CTX * 8 / 10;
            for round in 0..tools::MAX_TOOL_ROUNDS {
                let mut payload = messages_json(&messages, &Some(images2.clone()));
                fusionner_champs(&mut payload, &champs_outils);
                let (text, calls) = match ollama
                    .chat_tools(&model2, payload, &specs, temperature, 2048)
                    .await
                {
                    Ok(r) => r,
                    Err(e) => {
                        let msg = e.to_string();
                        // Modele sans support d'outils natifs (ex gemma3:
                        // 400 Bad Request) -> bascule AUTOMATIQUE vers le
                        // modele par defaut (adapte a la demande : les
                        // outils sont requis), puis on relance CE tour.
                        // Le message commence toujours par « chat_tools: » :
                        // tester « tools » seul basculait sur TOUTE erreur
                        // (Ollama arrete, memoire insuffisante...).
                        let bascule_possible = model2 != defaut2
                            && (msg.contains(" 400 ")
                                || msg.contains("does not support"));
                        if bascule_possible {
                            let _ = tx.send(sse_data(json!({
                                "type": "bascule",
                                "from": model2,
                                "to": defaut2,
                                "raison": "modele sans support d'outils -> bascule vers le modele par defaut",
                            })));
                            model2 = defaut2.clone();
                            let payload2 = messages_json(&messages, &Some(images2.clone()));
                            match ollama
                                .chat_tools(&model2, payload2, &specs, temperature, 2048)
                                .await
                            {
                                Ok(r) => r,
                                Err(e2) => {
                                    crate::metrics::ollama_echecs();
                                    let _ = tx.send(sse_data(json!({
                                        "type": "error", "error": e2.to_string()
                                    })));
                                    return;
                                }
                            }
                        } else {
                            crate::metrics::ollama_echecs();
                            let _ = tx.send(sse_data(json!({
                                "type": "error", "error": msg
                            })));
                            return;
                        }
                    }
                };
                // Marqueurs internes de Gemma (« thought », « <channel|> ») : jamais
                // dans la reponse.
                let text = persona::sans_marqueurs(&text);
                // Un agent qui PRODUIT un fichier (document, piece 3D) a repondu
                // par du texte sans appeler d'outil : on le lui dit une fois et il
                // rejoue. Essai du 11/11/2026 : « fais-moi une facture en PDF »
                // donnait un chemin invente et aucun fichier.
                if calls.is_empty() && !relance_faite {
                    if let Some(rappel) = persona::relance_sans_outil(&agent_outils, tool_pos.len()) {
                        relance_faite = true;
                        messages.push(Msg { role: "assistant".into(), content: text.clone() });
                        messages.push(Msg { role: "user".into(), content: rappel.to_string() });
                        continue;
                    }
                }
                let dernier_tour = calls.is_empty() || round + 1 >= tools::MAX_TOOL_ROUNDS;
                // Un texte qui ACCOMPAGNE un appel d'outil (« je relance… ») n'est
                // pas la reponse : accumule, il se retrouvait colle devant la
                // reponse finale (« Désolé, erreur technique… J'ai créé l'image »,
                // essai du 11/11/2026). L'interface montre deja l'outil en cours.
                if !text.is_empty() && dernier_tour {
                    full.push_str(&text);
                    let _ = tx.send(sse_data(json!({ "type": "token", "content": text })));
                }
                if dernier_tour {
                    break;
                }
                // Message assistant avec les appels (pour le contexte du round suivant).
                messages.push(Msg { role: "assistant".into(), content: text.clone() });
                champs_outils.push((messages.len() - 1, json!({
                    "tool_calls": calls.iter().map(|(n, a)| json!({
                        "function": { "name": n, "arguments": a }
                    })).collect::<Vec<_>>()
                })));
                for (seq, (name, args)) in calls.iter().enumerate() {
                    // Annonce AVANT execution : l'UI affiche l'etape en cours
                    // et Telegram previent l'utilisateur des etapes longues.
                    let _ = tx.send(sse_data(json!({ "type": "tool_start", "name": name })));
                    let issue = tools::execute(&root, name, args, &level).await;
                    let result = issue.text.clone();
                    let denied = result.starts_with("PERMISSION REFUSEE")
                        || result.starts_with("refusé")
                        || result.starts_with("ACCES REFUSE")
                        || result.starts_with("COMMANDE BLOQUEE")
                        || result.starts_with("Erreur: timeout sandbox");
                    let reason: String = result
                        .lines()
                        .next()
                        .unwrap_or("")
                        .chars()
                        .take(200)
                        .collect();
                    let preview: String = result.chars().take(280).collect();
                    let _ = tx.send(sse_data(json!({
                        "type": "tool_exec", "name": name, "args": args,
                        "denied": denied, "preview": preview,
                        "duration_ms": issue.duration_ms,
                        "attempts": issue.attempts,
                        "timed_out": issue.timed_out,
                    })));
                    let now3 = now_iso();
                    let args_s = serde_json::to_string(args).unwrap_or_default();
                    let status_s = if denied { "denied" } else { "ok" };
                    let _ = db2.execute(
                        "INSERT INTO tool_traces (conversation_id, seq, tool_name, arguments, result_preview, created_at, status, reason, duration_ms, attempts, timed_out) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        &[&cid, &(round as i64), &name.as_str(), &args_s.as_str(), &preview.as_str(), &now3.as_str(), &status_s, &reason.as_str(), &issue.duration_ms, &(issue.attempts as i64), &(if issue.timed_out { 1i64 } else { 0i64 })],
                    );
                    let _ = seq;
                    // Chantier 4 : sortie tronquee a 4000 car. avant reinjection
                    // (le complet reste en base via tool_traces + messages).
                    let result_short: String = result.chars().take(TOOL_OUT_LIMIT).collect();
                    tool_pos.push((messages.len(), name.clone(), round));
                    messages.push(Msg {
                        role: "tool".into(),
                        content: format!("[{name}] {result_short}"),
                    });
                    champs_outils.push((messages.len() - 1, json!({ "tool_name": name })));
                }
                // Chantier 4 : compactage de contexte — au-dela de 80 % de
                // num_ctx, les sorties d'outils anciennes (hors 2 derniers
                // rounds) deviennent un resume d'une ligne, journalise.
                if est_tokens(&messages) > seuil {
                    let now4 = now_iso();
                    for (idx, nom, tround) in tool_pos.iter().cloned() {
                        if tround + 2 <= round {
                            let orig = messages[idx].content.clone();
                            let orig_len = orig.chars().count();
                            if orig_len <= 600 {
                                continue;
                            }
                            let prem_ligne: String = orig
                                .lines()
                                .next()
                                .unwrap_or("")
                                .chars()
                                .take(120)
                                .collect();
                            messages[idx].content =
                                format!("[{nom} — résultat résumé] {prem_ligne}");
                            let note = format!("compacte: {} car. -> resume", orig_len);
                            let reason4 = "compactage_contexte_80pct_numctx";
                            let args4 = "";
                            let _ = db2.execute(
                                "INSERT INTO tool_traces (conversation_id, seq, tool_name, arguments, result_preview, created_at, status, reason) VALUES (?,?,?,?,?,?,?,?)",
                                &[&cid, &(tround as i64), &nom.as_str(), &args4, &note.as_str(), &now4.as_str(), &"compact", &reason4],
                            );
                        }
                    }
                }
            }
            let ms = started.elapsed().as_millis() as i64;
            // Garde-fou anti-hallucination : les chemins d'images cites
            // doivent exister. Un chemin mort est remplace par un vrai
            // fichier produit par les outils de CE tour (traces), sinon
            // retire avec avertissement. Event "replace" -> le frontend
            // remplace le texte deja streamé.
            let mut final_content = full.clone();
            {
                let rootp = std::path::Path::new(&root);
                let morts: Vec<String> = chemins_images(&full)
                    .into_iter()
                    .filter(|c| !chemin_existe(rootp, c))
                    .collect();
                if !morts.is_empty() {
                    let vivants = db2
                        .query_all_json(
                            "SELECT result_preview FROM tool_traces WHERE conversation_id=? \
                             AND created_at>=? AND status='ok' \
                             AND tool_name IN ('generate_image','edit_image','fetch_image') \
                             ORDER BY id DESC LIMIT 12",
                            &[&cid, &debut_iso.as_str()],
                        )
                        .map(|rows| vivants_depuis_rows(&rows, rootp))
                        .unwrap_or_default();
                    tracing::warn!(
                        "garde-fou images: morts={:?} vivants={:?}",
                        morts, vivants
                    );
                    let corrige = corrige_chemins_image(&full, rootp, &vivants);
                    if corrige != full {
                        let _ = tx.send(sse_data(json!({
                            "type": "replace", "content": corrige,
                        })));
                    }
                    final_content = corrige;
                }
            }
            // Consigne §4 response_checker : la reponse PRETEND une image
            // (voici l'image... / j'ai genere...) sans citer aucun chemin et
            // sans qu'aucun outil d'image n'ait tourne -> avertissement
            // explicite plutot qu'une reponse texte passive.
            {
                let bas = final_content.to_lowercase();
                let annonce = bas.contains("voici l'image")
                    || bas.contains("voici l’image")
                    || (bas.contains("j'ai g") && bas.contains("image"))
                    || bas.contains("image generee");
                let chemins = chemins_images(&final_content);
                let image_executee = tool_pos
                    .iter()
                    .any(|(_, n, _)| n == "generate_image" || n == "fetch_image");
                if annonce && chemins.is_empty() && !image_executee {
                    final_content.push_str(
                        "\n\n(Rappel : aucune image n'a été réellement générée ni téléchargée. \
Reformulez en demandant explicitement la génération, par exemple : \
« génère une image de ... »)",
                    );
                }
            }
            // Garantie d'affichage image : si un outil image a tourne
            // durant ce tour, les fichiers REELS produits (traces) qui ne
            // sont pas deja cites dans la reponse sont ajoutes en fin de
            // message. Corrige le cas ou le modele cite un nom de fichier
            // nu (non resolu par le frontend) ou oublie une image quand
            // plusieurs generations s'enchainent.
            {
                let outils_image = tool_pos.iter().any(|(_, n, _)| {
                    n == "generate_image"
                        || n == "fetch_image"
                        || n == "edit_image"
                        || n == "find_reference_image"
                });
                if outils_image {
                    let rootp2 = std::path::Path::new(&root);
                    let rows = db2
                        .query_all_json(
                            "SELECT result_preview FROM tool_traces WHERE conversation_id=? \
                             AND created_at>=? AND status='ok' \
                             AND tool_name IN ('generate_image','edit_image','fetch_image') \
                             ORDER BY id DESC LIMIT 8",
                            &[&cid, &debut_iso.as_str()],
                        )
                        .unwrap_or_default();
                    let vivants = vivants_depuis_rows(&rows, rootp2);
                    let deja: Vec<String> = chemins_images(&final_content);
                    let manquants: Vec<String> = vivants
                        .into_iter()
                        .filter(|p| !deja.iter().any(|d| d == p))
                        .collect();
                    if !manquants.is_empty() {
                        let mut ajout = String::new();
                        for p in &manquants {
                            ajout.push_str(&format!("\n\nImage : {}", p));
                        }
                        final_content.push_str(&ajout);
                        tracing::info!(
                            "garantie images: {} chemin(s) ajoute(s)",
                            manquants.len()
                        );
                        let _ = tx.send(sse_data(json!({
                            "type": "replace", "content": final_content,
                        })));
                    }
                }
            }
            // Consigne §2 : auto-fiche - si la reponse s'appuie sur une
            // recherche web sur un sujet nomme, une fiche de connaissance
            // est creee/mise a jour (illustration comprise) et signalee.
            {
                let recherche = tool_pos
                    .iter()
                    .any(|(_, n, _)| n == "web_search" || n == "deep_research");
                let substantielle = final_content.chars().count() > 200;
                if recherche && substantielle {
                    // En tache de fond : la reponse n'attend plus la fiche
                    // (jusqu'a 150 s auparavant, avant meme d'etre enregistree).
                    // `backend.core.fiches` juge si le sujet en merite une et
                    // l'ecrit ; elle apparait dans Documents > Fiches.
                    let (root_f, question, reponse) = (root.clone(), msg_origine.clone(), final_content.clone());
                    tokio::spawn(async move {
                        // Nom unique par conversation et instant : deux reponses
                        // simultanees ne s'ecrasent pas leur fichier d'echange.
                        let tmp = std::env::temp_dir().join(format!(
                            "fiche_auto_{cid}_{}.json",
                            chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default()
                        ));
                        let corps = serde_json::json!({ "m": question, "r": reponse }).to_string();
                        if std::fs::write(&tmp, &corps).is_ok() {
                            let code = format!(
                                "import json; from backend.core.fiches import depuis_reponse; \
                                 a = json.load(open({}, encoding='utf-8')); \
                                 print(depuis_reponse(a['m'], a['r']), end='')",
                                crate::pybridge::py_str(&tmp.to_string_lossy())
                            );
                            let res = crate::pybridge::py_text_timeout(
                                &root_f,
                                &["-c", &code],
                                std::time::Duration::from_secs(150),
                            )
                            .await;
                            let _ = std::fs::remove_file(&tmp);
                            if let Ok(res) = res {
                                if res.starts_with("FICHE") {
                                    tracing::info!("{}", res.chars().take(160).collect::<String>());
                                }
                            }
                        }
                    });
                }
            }
            let now2 = now_iso();
            let _ = db2.execute(
                "INSERT INTO messages (conversation_id, role, content, model, duration_ms, created_at) VALUES (?,?,?,?,?,?)",
                &[&cid, &"assistant", &final_content.as_str(), &model2.as_str(), &ms, &now2.as_str()],
            );
            let _ = db2.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                &[&now2.as_str(), &cid],
            );
            // Stats estimees (multi-rounds : entree = contexte cumule,
            // sortie = reponse finale) pour l'encart tokens.
            let est_in = est_tokens(&messages) as i64;
            let est_out = (full.chars().count() / 3) as i64;
            let tps = if ms > 0 {
                ((est_out as f64) * 1000.0 / (ms as f64) * 10.0).round() / 10.0
            } else {
                0.0
            };
            let _ = tx.send(sse_data(json!({
                "type": "done",
                "conversation_id": cid,
                "duration_ms": ms,
                "model": model2,
                "prompt_tokens": est_in,
                "completion_tokens": est_out,
                "tokens_per_second": tps,
                "estime": true,
            })));
        });
    }

    let stream = tokio_stream::wrappers::UnboundedReceiverStream::new(rx);
    let raw = stream.map(|s| {
        let payload = s.trim_start_matches("data: ").trim().to_string();
        Ok::<_, Infallible>(Event::default().data(payload))
    });
    Ok(Sse::new(raw).keep_alive(KeepAlive::default()))
}

/// Fusionne des champs supplementaires dans les messages JSON (par indice).
fn fusionner_champs(payload: &mut serde_json::Value, champs: &[(usize, serde_json::Value)]) {
    if let Some(arr) = payload.as_array_mut() {
        for (i, extra) in champs {
            if let (Some(m), Some(e)) = (arr.get_mut(*i).and_then(|m| m.as_object_mut()), extra.as_object()) {
                for (k, v) in e {
                    m.insert(k.clone(), v.clone());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests_dialogue_outils {
    use super::*;

    #[test]
    fn appels_et_resultats_nommes() {
        let mut p = json!([{"role": "user", "content": "q"}, {"role": "assistant", "content": ""},
                           {"role": "tool", "content": "[meteo] 18 °C"}]);
        let champs = vec![
            (1, json!({"tool_calls": [{"function": {"name": "meteo", "arguments": {"ville": "Lyon"}}}]})),
            (2, json!({"tool_name": "meteo"})),
            (9, json!({"tool_name": "hors limites"})),
        ];
        fusionner_champs(&mut p, &champs);
        assert_eq!(p[1]["tool_calls"][0]["function"]["name"], "meteo");
        assert_eq!(p[2]["tool_name"], "meteo");
        assert_eq!(p[2]["content"], "[meteo] 18 °C");
    }
}
