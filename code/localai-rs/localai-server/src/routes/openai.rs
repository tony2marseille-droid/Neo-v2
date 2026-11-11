//! Passerelle OpenAI-compatible (`/v1/...`).
//!
//! Permet a Neo d'etre utilise comme backend par les clients qui parlent le
//! protocole OpenAI : Open WebUI, LibreChat, AnythingLLM, LobeHub, Cherry
//! Studio, Continue.dev, Cursor, etc.
//!
//! Choix assumes :
//! - pas de RAG ni d'outils ici : un client OpenAI gere son propre contexte
//!   et attend un chat « pur ». Le pipeline complet reste sur `/api/chat` ;
//! - le format est tolerant (content tableau multimodal accepte) ;
//! - streaming NDJSON Ollama -> SSE OpenAI, avec `[DONE]` final.
use std::convert::Infallible;
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::{get, post},
    Json, Router,
};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use localai_core::AppError;

/// Contexte par defaut aligne sur le serveur (voir reasoning::DEFAULT_NUM_CTX).
const NUM_CTX: u32 = 8192;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/v1/models", get(modeles))
        .route("/v1/models/:id", get(modele))
        .route("/v1/chat/completions", post(completions))
        .route("/v1/completions", post(completion_texte))
}

// ---------------------------------------------------------------- modeles


/// Le client partage coupe a 120 s, corps compris : une generation longue
/// (stream ou non) etait tronquee sans finish_reason. Delai propre aux
/// appels de generation, aligne sur le client Ollama du core.
const DELAI_GENERATION: std::time::Duration = std::time::Duration::from_secs(900);
fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// GET /v1/models : liste des modeles locaux installes.
async fn modeles(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let tags = st
        .ollama
        .tags()
        .await
        .map_err(|e| ApiError(AppError::Ollama(format!("tags: {e}"))))?;
    let created = now_unix();
    let data: Vec<Value> = tags
        .get("models")
        .and_then(|m| m.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| {
                    let id = m
                        .get("name")
                        .or_else(|| m.get("model"))
                        .and_then(|v| v.as_str())?;
                    Some(json!({
                        "id": id,
                        "object": "model",
                        "created": created,
                        "owned_by": "neo",
                    }))
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(Json(json!({ "object": "list", "data": data })))
}

/// GET /v1/models/:id : un modele (404 si absent).
async fn modele(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<String>,
) -> ApiResult<Json<Value>> {
    let tags = st
        .ollama
        .tags()
        .await
        .map_err(|e| ApiError(AppError::Ollama(format!("tags: {e}"))))?;
    let existe = tags
        .get("models")
        .and_then(|m| m.as_array())
        .map(|arr| {
            arr.iter().any(|m| {
                m.get("name").and_then(|v| v.as_str()) == Some(id.as_str())
                    || m.get("model").and_then(|v| v.as_str()) == Some(id.as_str())
            })
        })
        .unwrap_or(false);
    if !existe {
        return Err(ApiError(AppError::NotFound(format!(
            "modele introuvable: {id}"
        ))));
    }
    Ok(Json(json!({
        "id": id,
        "object": "model",
        "created": now_unix(),
        "owned_by": "neo",
    })))
}

// ------------------------------------------------------------- completions

#[derive(Debug, Deserialize)]
pub struct ChatBody {
    pub model: Option<String>,
    #[serde(default)]
    pub messages: Vec<Value>,
    pub temperature: Option<f32>,
    pub top_p: Option<f32>,
    pub max_tokens: Option<i32>,
    #[serde(alias = "max_completion_tokens")]
    pub max_completion_tokens: Option<i32>,
    pub stop: Option<Value>,
    #[serde(default)]
    pub stream: bool,
}

/// Modele effectif : celui demande, sinon le modele par defaut des reglages.
fn modele_effectif(st: &Arc<crate::AppState>, demande: &Option<String>) -> String {
    match demande {
        Some(m) if !m.trim().is_empty() => m.trim().to_string(),
        _ => st
            .db
            .get_setting("default_model", &st.settings.default_model)
            .to_string(),
    }
}

/// Normalise `stop` (string ou tableau) en tableau de chaines pour Ollama.
fn stops(v: &Option<Value>) -> Vec<String> {
    match v {
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|x| x.as_str().map(|s| s.to_string()))
            .collect(),
        _ => Vec::new(),
    }
}

/// Construit le corps Ollama depuis une requete OpenAI.
fn corps_ollama(b: &ChatBody, model: &str, stream: bool) -> Value {
    let num_predict = b
        .max_completion_tokens
        .or(b.max_tokens)
        .unwrap_or(-1)
        .clamp(-1, 32_768);
    let mut options = json!({
        "num_ctx": NUM_CTX,
        "num_predict": num_predict,
    });
    if let Some(t) = b.temperature {
        options["temperature"] = json!(t.clamp(0.0, 2.0));
    }
    if let Some(p) = b.top_p {
        options["top_p"] = json!(p.clamp(0.0, 1.0));
    }
    let stop = stops(&b.stop);
    if !stop.is_empty() {
        options["stop"] = json!(stop);
    }
    json!({
        "model": model,
        "messages": b.messages,
        "stream": stream,
        "think": false,
        "keep_alive": "60m",
        "options": options,
    })
}

fn id_reponse() -> String {
    format!("chatcmpl-neo-{}", now_unix() * 1000 + (std::process::id() as i64 % 1000))
}

async fn completions(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ChatBody>,
) -> Result<Response, ApiError> {
    if b.messages.is_empty() {
        return Err(ApiError(AppError::BadRequest(
            "messages vide : au moins un message est requis".into(),
        )));
    }
    let model = modele_effectif(&st, &b.model);
    let url = format!("{}/api/chat", st.settings.ollama_host);

    if !b.stream {
        let body = corps_ollama(&b, &model, false);
        let r = st
            .http
            .post(&url)
            .timeout(DELAI_GENERATION)
            .json(&body)
            .send()
            .await
            .map_err(|e| ApiError(AppError::Ollama(format!("ollama: {e}"))))?;
        let status = r.status();
        let v: Value = r
            .json()
            .await
            .map_err(|e| ApiError(AppError::Ollama(format!("ollama json: {e}"))))?;
        if !status.is_success() {
            let msg = v
                .get("error")
                .and_then(|x| x.as_str())
                .unwrap_or("erreur ollama");
            return Err(ApiError(AppError::Ollama(msg.to_string())));
        }
        let contenu = v
            .get("message")
            .and_then(|m| m.get("content"))
            .and_then(|c| c.as_str())
            .unwrap_or("")
            .to_string();
        let pin = v.get("prompt_eval_count").and_then(|x| x.as_i64()).unwrap_or(0);
        let pout = v.get("eval_count").and_then(|x| x.as_i64()).unwrap_or(0);
        let raison = v
            .get("done_reason")
            .and_then(|x| x.as_str())
            .unwrap_or("stop")
            .to_string();
        return Ok(Json(json!({
            "id": id_reponse(),
            "object": "chat.completion",
            "created": now_unix(),
            "model": model,
            "choices": [{
                "index": 0,
                "message": { "role": "assistant", "content": contenu },
                "finish_reason": raison,
            }],
            "usage": {
                "prompt_tokens": pin,
                "completion_tokens": pout,
                "total_tokens": pin + pout,
            },
        }))
        .into_response());
    }

    // --- streaming : NDJSON Ollama -> SSE OpenAI ---
    let body = corps_ollama(&b, &model, true);
    let resp = st
        .http
        .post(&url)
        .timeout(DELAI_GENERATION)
        .json(&body)
        .send()
        .await
        .map_err(|e| ApiError(AppError::Ollama(format!("ollama: {e}"))))?;
    if !resp.status().is_success() {
        let txt = resp.text().await.unwrap_or_default();
        return Err(ApiError(AppError::Ollama(format!(
            "ollama stream: {}",
            txt.chars().take(300).collect::<String>()
        ))));
    }

    let (tx, rx) = tokio::sync::mpsc::unbounded_channel::<Result<Event, Infallible>>();
    let id = id_reponse();
    let modele_nom = model.clone();
    tokio::spawn(async move {
        let mut stream = resp.bytes_stream();
        let mut buf: Vec<u8> = Vec::new();
        let mut premier = true;

        while let Some(chunk) = stream.next().await {
            let Ok(bytes) = chunk else { break };
            buf.extend_from_slice(&bytes);
            while let Some(pos) = buf.iter().position(|&x| x == b'\n') {
                let ligne: Vec<u8> = buf.drain(..=pos).collect();
                let s = String::from_utf8_lossy(&ligne);
                let s = s.trim();
                if s.is_empty() {
                    continue;
                }
                let Ok(v) = serde_json::from_str::<Value>(s) else {
                    continue;
                };
                if let Some(e) = v.get("error").and_then(|x| x.as_str()) {
                    let payload = json!({
                        "id": id, "object": "chat.completion.chunk",
                        "created": now_unix(), "model": modele_nom,
                        "choices": [{ "index": 0, "delta": { "content": format!("\n[erreur] {e}") }, "finish_reason": null }],
                    });
                    let _ = tx.send(Ok(Event::default().data(payload.to_string())));
                    let _ = tx.send(Ok(Event::default().data("[DONE]")));
                    return;
                }
                let done = v.get("done").and_then(|d| d.as_bool()).unwrap_or(false);
                if let Some(t) = v
                    .get("message")
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_str())
                {
                    if !t.is_empty() {
                        let delta = if premier {
                            premier = false;
                            json!({ "role": "assistant", "content": t })
                        } else {
                            json!({ "content": t })
                        };
                        let payload = json!({
                            "id": id,
                            "object": "chat.completion.chunk",
                            "created": now_unix(),
                            "model": modele_nom,
                            "choices": [{ "index": 0, "delta": delta, "finish_reason": null }],
                        });
                        let _ = tx.send(Ok(Event::default().data(payload.to_string())));
                    }
                }
                if done {
                    let pin = v.get("prompt_eval_count").and_then(|x| x.as_i64()).unwrap_or(0);
                    let pout = v.get("eval_count").and_then(|x| x.as_i64()).unwrap_or(0);
                    let raison = v
                        .get("done_reason")
                        .and_then(|x| x.as_str())
                        .unwrap_or("stop")
                        .to_string();
                    let fin = json!({
                        "id": id,
                        "object": "chat.completion.chunk",
                        "created": now_unix(),
                        "model": modele_nom,
                        "choices": [{ "index": 0, "delta": {}, "finish_reason": raison }],
                        "usage": {
                            "prompt_tokens": pin,
                            "completion_tokens": pout,
                            "total_tokens": pin + pout,
                        },
                    });
                    let _ = tx.send(Ok(Event::default().data(fin.to_string())));
                    let _ = tx.send(Ok(Event::default().data("[DONE]")));
                    return;
                }
            }
        }
        let _ = tx.send(Ok(Event::default().data("[DONE]")));
    });

    let flux = tokio_stream::wrappers::UnboundedReceiverStream::new(rx);
    Ok(Sse::new(flux).keep_alive(KeepAlive::default()).into_response())
}

// ------------------------------------------------------- /v1/completions

#[derive(Debug, Deserialize)]
pub struct TexteBody {
    pub model: Option<String>,
    pub prompt: Option<Value>,
    pub temperature: Option<f32>,
    pub max_tokens: Option<i32>,
    pub stop: Option<Value>,
    /// Accepte pour compatibilite : `/v1/completions` repond toujours en une
    /// seule fois (les clients recents passent par `/v1/chat/completions`).
    #[serde(default)]
    #[allow(dead_code)]
    pub stream: bool,
}

/// `/v1/completions` : API « legacy ». Traduite en chat a un seul message.
async fn completion_texte(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<TexteBody>,
) -> Result<Response, ApiError> {
    let prompt = match &b.prompt {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(a)) => a
            .first()
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
        _ => String::new(),
    };
    if prompt.trim().is_empty() {
        return Err(ApiError(AppError::BadRequest("prompt vide".into())));
    }
    let chat = ChatBody {
        model: b.model,
        messages: vec![json!({ "role": "user", "content": prompt })],
        temperature: b.temperature,
        top_p: None,
        max_tokens: b.max_tokens,
        max_completion_tokens: None,
        stop: b.stop,
        stream: false,
    };
    let model = modele_effectif(&st, &chat.model);
    let body = corps_ollama(&chat, &model, false);
    let url = format!("{}/api/chat", st.settings.ollama_host);
    let r = st
        .http
        .post(&url)
        .timeout(DELAI_GENERATION)
        .json(&body)
        .send()
        .await
        .map_err(|e| ApiError(AppError::Ollama(format!("ollama: {e}"))))?;
    let status = r.status();
    let v: Value = r
        .json()
        .await
        .map_err(|e| ApiError(AppError::Ollama(format!("ollama json: {e}"))))?;
    if !status.is_success() {
        let msg = v
            .get("error")
            .and_then(|x| x.as_str())
            .unwrap_or("erreur ollama");
        return Err(ApiError(AppError::Ollama(msg.to_string())));
    }
    let texte = v
        .get("message")
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .to_string();
    let pin = v.get("prompt_eval_count").and_then(|x| x.as_i64()).unwrap_or(0);
    let pout = v.get("eval_count").and_then(|x| x.as_i64()).unwrap_or(0);
    Ok(Json(json!({
        "id": id_reponse(),
        "object": "text_completion",
        "created": now_unix(),
        "model": model,
        "choices": [{ "index": 0, "text": texte, "finish_reason": "stop" }],
        "usage": {
            "prompt_tokens": pin,
            "completion_tokens": pout,
            "total_tokens": pin + pout,
        },
    }))
    .into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support;

    #[test]
    fn stops_normalise_string_et_tableau() {
        assert_eq!(stops(&Some(Value::String("STOP".into()))), vec!["STOP".to_string()]);
        assert_eq!(
            stops(&Some(Value::Array(vec![json!("a"), json!(42), json!("b")]))),
            vec!["a".to_string(), "b".to_string()]
        );
        assert_eq!(stops(&None), Vec::<String>::new());
        assert_eq!(stops(&Some(Value::Number(4.into()))), Vec::<String>::new());
    }

    #[test]
    fn corps_ollama_constitue_les_options_attendues() {
        let corps = ChatBody {
            model: Some("m".into()),
            messages: vec![json!({"role":"user","content":"salut"})],
            temperature: Some(1.2),
            top_p: Some(0.5),
            max_tokens: Some(100),
            max_completion_tokens: None,
            stop: Some(Value::String("halte".into())),
            stream: false,
        };
        let res = corps_ollama(&corps, "m", true);
        assert_eq!(res["model"], json!("m"));
        assert_eq!(res["stream"], json!(true));
        assert_eq!(res["options"]["num_ctx"], json!(8192));
        assert_eq!(res["options"]["num_predict"], json!(100));
        assert_eq!(res["options"]["temperature"], json!(1.2_f32));
        assert_eq!(res["options"]["top_p"], json!(0.5));
        assert_eq!(res["options"]["stop"], json!(vec!["halte"]));
        assert_eq!(res["keep_alive"], json!("60m"));
        assert_eq!(res["think"], json!(false));
    }

    #[test]
    fn corps_ollama_max_completion_tokens_a_priorite_et_plafonne() {
        let corps = ChatBody {
            model: None,
            messages: vec![],
            temperature: None,
            top_p: None,
            max_tokens: Some(50),
            max_completion_tokens: Some(10_000_000),
            stop: None,
            stream: false,
        };
        let res = corps_ollama(&corps, "m", false);
        assert_eq!(res["options"]["num_predict"], json!(32768));
        assert!(res["options"].get("top_p").is_none());
    }

    #[test]
    fn corps_ollama_retombe_sur_moins_un_si_rien() {
        let corps = ChatBody {
            model: None,
            messages: vec![],
            temperature: None,
            top_p: None,
            max_tokens: None,
            max_completion_tokens: None,
            stop: None,
            stream: false,
        };
        let res = corps_ollama(&corps, "m", false);
        assert_eq!(res["options"]["num_predict"], json!(-1));
    }

    #[tokio::test]
    async fn modele_effectif_deduit_le_defaut() {
        let st = test_support::etat("openai_mod");
        st.db.set_setting("default_model", "neo-default").unwrap();
        assert_eq!(modele_effectif(&st, &Some(" demande ".into())), "demande");
        assert_eq!(modele_effectif(&st, &Some("   ".into())), "neo-default");
        assert_eq!(modele_effectif(&st, &None), "neo-default");
    }

    #[test]
    fn id_reponse_commence_par_le_prefixe() {
        let id = id_reponse();
        assert!(id.starts_with("chatcmpl-neo-"), "{id}");
        assert!(id.len() > 12, "{id}");
    }
}

