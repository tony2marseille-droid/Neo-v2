//! Prompt systeme : estimation de tokens, format des messages Ollama et
//! detection des modeles vision.

use localai_core::ollama::{Msg, DEFAULT_NUM_CTX};
use serde_json::json;

/// Chantier 4 : contexte modele (num_ctx du Modelfile v9.2 actuel = 8192).
pub(super) const NUM_CTX: usize = DEFAULT_NUM_CTX;
/// Troncature des sorties d'outils avant reinjection (le complet reste en base).
pub(super) const TOOL_OUT_LIMIT: usize = 4000;

/// Estimation grossiere du nombre de tokens (FR + code : ~3 car./token).
pub(super) fn est_tokens(messages: &[Msg]) -> usize {
    messages
        .iter()
        .map(|m| m.content.chars().count() / 3)
        .sum::<usize>()
        + messages.len() * 8
}

/// Messages pour Ollama : images jointes au DERNIER message user (vision).
pub(super) fn messages_json(history: &[Msg], images: &Option<Vec<String>>) -> serde_json::Value {
    let mut arr: Vec<serde_json::Value> = history
        .iter()
        .map(|m| json!({ "role": m.role, "content": m.content }))
        .collect();
    if let Some(imgs) = images {
        if !imgs.is_empty() {
            for m in arr.iter_mut().rev() {
                if m.get("role").and_then(|r| r.as_str()) == Some("user") {
                    m["images"] = json!(imgs);
                    break;
                }
            }
        }
    }
    serde_json::Value::Array(arr)
}

