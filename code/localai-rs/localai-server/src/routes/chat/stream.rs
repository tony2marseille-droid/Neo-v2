//! Reponses en streaming : chemin local (Ollama) et chemin cloud
//! (fournisseur a cle API, avec bascule automatique vers le local en cas
//! de quota epuise).

use localai_core::db::now_iso;
use localai_core::ollama::Msg;
use serde_json::json;
use tokio::sync::mpsc;

use super::images::{chemin_existe, chemins_images, corrige_chemins_image};
use super::prompt::{messages_json, NUM_CTX};

pub(super) fn sse_data(v: serde_json::Value) -> String {
    format!("data: {}\n\n", v)
}

/// Meme requete, sans reflexion et avec le budget d'une reponse directe.
fn sans_reflexion(body: &serde_json::Value) -> serde_json::Value {
    let mut b = body.clone();
    if let Some(o) = b.as_object_mut() {
        o.insert("think".into(), json!(false));
        if let Some(opts) = o.get_mut("options").and_then(|v| v.as_object_mut()) {
            opts.insert("num_predict".into(), json!(2048));
        }
    }
    b
}

/// Reponse LOCALE en streaming AVEC compteurs de tokens. Utilisee par
/// le chemin rapide et par la bascule de secours cloud -> local.
#[allow(clippy::too_many_arguments)]
pub(super) fn spawn_reponse_locale(
    tx: mpsc::UnboundedSender<String>,
    db2: localai_core::db::Db,
    ollama: localai_core::ollama::Ollama,
    root2: std::path::PathBuf,
    model2: String,
    body: serde_json::Value,
    cid: i64,
) {
    tokio::spawn(async move {
        let started = std::time::Instant::now();
        let txc = tx.clone();
        let txp = tx.clone();
        let reflechit = body.get("think").and_then(|v| v.as_bool()).unwrap_or(false);
        let secours = sans_reflexion(&body);
        let mut res = ollama
            .chat_stream_reflexion(
                &model2,
                body,
                |tok| {
                    let _ = txc.send(sse_data(json!({ "type": "token", "content": tok })));
                },
                // Reflexion de Neo : affichee a part (repliable), jamais enregistree.
                |p| {
                    let _ = txp.send(sse_data(json!({ "type": "pensee", "content": p })));
                },
            )
            .await;
        // Une reflexion qui epuise son budget sans rien conclure (constate le
        // 09/11/2026 : gemma4:12b, 27 000 caracteres de pensee sur un comptage
        // de formes, reponse vide) : on repose la question sans reflexion
        // plutot que de laisser Neo muet.
        if reflechit && matches!(&res, Ok((f, _, _)) if f.trim().is_empty()) {
            tracing::warn!("reflexion sans reponse ({model2}) : nouvel essai sans reflexion");
            let txs = tx.clone();
            res = ollama
                .chat_stream_reflexion(
                    &model2,
                    secours,
                    |tok| {
                        let _ = txs.send(sse_data(json!({ "type": "token", "content": tok })));
                    },
                    |_| {},
                )
                .await;
        }
        match res {
            Ok((mut full, tin, tout)) => {
                let ms = started.elapsed().as_millis() as i64;
                // Garde-fou : chemins d'images cites mais inexistants ->
                // retires (aucun outil n'a tourne, donc aucun fichier
                // reel a substituer).
                let rootp = std::path::Path::new(&root2);
                let morts: Vec<String> = chemins_images(&full)
                    .into_iter()
                    .filter(|c| !chemin_existe(rootp, c))
                    .collect();
                if !morts.is_empty() {
                    let corrige = corrige_chemins_image(&full, rootp, &[]);
                    tracing::warn!(
                        "garde-fou images (chemin rapide): morts={:?}",
                        morts
                    );
                    let _ = tx.send(sse_data(json!({
                        "type": "replace", "content": corrige,
                    })));
                    full = corrige;
                }
                let now2 = now_iso();
                let _ = db2.execute(
                    "INSERT INTO messages (conversation_id, role, content, model, duration_ms, created_at) VALUES (?,?,?,?,?,?)",
                    &[&cid, &"assistant", &full.as_str(), &model2.as_str(), &ms, &now2.as_str()],
                );
                let _ = db2.execute(
                    "UPDATE conversations SET updated_at=? WHERE id=?",
                    &[&now2.as_str(), &cid],
                );
                let tps = if ms > 0 {
                    ((tout as f64) * 1000.0 / (ms as f64) * 10.0).round() / 10.0
                } else {
                    0.0
                };
                let _ = tx.send(sse_data(json!({
                    "type": "done",
                    "conversation_id": cid,
                    "duration_ms": ms,
                    "model": model2,
                    "prompt_tokens": tin,
                    "completion_tokens": tout,
                    "tokens_per_second": tps,
                })));
            }
            Err(e) => {
                crate::metrics::ollama_echecs();
                let _ = tx.send(sse_data(json!({ "type": "error", "error": e.to_string() })));
            }
        }
    });
}

/// Reponse via un fournisseur CLOUD (cle API). Regle impunee de la
/// consigne bascule : en cas de credit/quota epuise, on bascule vers un
/// modele LOCAL adapté ; JAMAIS vers un autre fournisseur a cle API
/// (confirmation utilisateur obligatoire pour cela).
#[allow(clippy::too_many_arguments)]
pub(super) fn spawn_reponse_cloud(
    tx: mpsc::UnboundedSender<String>,
    db2: localai_core::db::Db,
    ollama: localai_core::ollama::Ollama,
    root2: std::path::PathBuf,
    provider_id: String,
    history: Vec<Msg>,
    cid: i64,
    temperature: f32,
    defaut_local: String,
) {
    tokio::spawn(async move {
        let started = std::time::Instant::now();
        let f = match localai_core::providers::trouver(&provider_id) {
            Some(f) => f,
            None => {
                let _ = tx.send(sse_data(json!({
                    "type": "error", "error": format!("fournisseur inconnu: {provider_id}")
                })));
                return;
            }
        };
        let cle = db2.get_setting(&format!("provider_{provider_id}_key"), "");
        if cle.is_empty() {
            let _ = tx.send(sse_data(json!({
                "type": "error",
                "error": format!("cle API absente pour {provider_id} (Reglages > Fournisseurs)"),
            })));
            return;
        }
        let modele_cfg = db2.get_setting(&format!("provider_{provider_id}_model"), "");
        let client = match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(180))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                let _ = tx.send(sse_data(json!({ "type": "error", "error": e.to_string() })));
                return;
            }
        };
        let modele_utilise = format!("cloud:{provider_id}");
        match localai_core::providers::chat_usage(
            &client, f, &cle, &modele_cfg, &history, temperature, 2048,
        )
        .await
        {
            Ok((texte, tin, tout)) => {
                crate::routes::fournisseurs::effacer_quota_err(&db2, &provider_id);
                crate::routes::fournisseurs::enregistrer_usage(&db2, &provider_id, tin, tout);
                let mut full = texte;
                if !full.is_empty() {
                    let _ = tx.send(sse_data(json!({ "type": "token", "content": full.clone() })));
                }
                // Garde-fou images (comme le chemin local rapide).
                let rootp = std::path::Path::new(&root2);
                let morts: Vec<String> = chemins_images(&full)
                    .into_iter()
                    .filter(|c| !chemin_existe(rootp, c))
                    .collect();
                if !morts.is_empty() {
                    let corrige = corrige_chemins_image(&full, rootp, &[]);
                    let _ = tx.send(sse_data(json!({
                        "type": "replace", "content": corrige,
                    })));
                    full = corrige;
                }
                let ms = started.elapsed().as_millis() as i64;
                let now2 = now_iso();
                let _ = db2.execute(
                    "INSERT INTO messages (conversation_id, role, content, model, duration_ms, created_at) VALUES (?,?,?,?,?,?)",
                    &[&cid, &"assistant", &full.as_str(), &modele_utilise.as_str(), &ms, &now2.as_str()],
                );
                let _ = db2.execute(
                    "UPDATE conversations SET updated_at=? WHERE id=?",
                    &[&now2.as_str(), &cid],
                );
                let tps = if ms > 0 {
                    ((tout as f64) * 1000.0 / (ms as f64) * 10.0).round() / 10.0
                } else {
                    0.0
                };
                let _ = tx.send(sse_data(json!({
                    "type": "done",
                    "conversation_id": cid,
                    "duration_ms": ms,
                    "model": modele_utilise,
                    "prompt_tokens": tin,
                    "completion_tokens": tout,
                    "tokens_per_second": tps,
                })));
            }
            Err(e) => {
                if localai_core::providers::est_quota_epuise(&e) {
                    // Credit/quota epuise -> bascule AUTOMATIQUE vers un
                    // modele LOCAL (jamais un autre fournisseur a cle API).
                    crate::routes::fournisseurs::noter_quota_epuise(&db2, &provider_id, &e);
                    let _ = tx.send(sse_data(json!({
                        "type": "bascule",
                        "from": modele_utilise,
                        "to": defaut_local,
                        "raison": "credit/quota du fournisseur epuise -> bascule vers un modele local",
                    })));
                    let body = serde_json::json!({
                        "messages": messages_json(&history, &None),
                        "think": false,
                        "options": {
                            "temperature": temperature,
                            "num_predict": 2048,
                            "num_ctx": localai_core::ollama::contexte_pour(&defaut_local, NUM_CTX),
                        },
                    });
                    spawn_reponse_locale(
                        tx, db2, ollama, root2, defaut_local, body, cid,
                    );
                } else {
                    let _ = tx.send(sse_data(json!({ "type": "error", "error": e })));
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secours_sans_reflexion_garde_le_reste() {
        let b = json!({"messages": [1], "think": true,
                       "options": {"temperature": 0.5, "num_predict": 8192, "num_ctx": 12288}});
        let s = sans_reflexion(&b);
        assert_eq!(s["think"], json!(false));
        assert_eq!(s["options"]["num_predict"], json!(2048));
        assert_eq!(s["options"]["num_ctx"], json!(12288));
        assert_eq!(s["messages"], json!([1]));
    }
}
