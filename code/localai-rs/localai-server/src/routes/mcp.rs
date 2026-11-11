//! Serveur MCP (Model Context Protocol) minimal, en JSON-RPC 2.0 sur HTTP.
//!
//! Permet a un client MCP (Claude Desktop, Cursor, Continue.dev...) de
//! decouvrir et d'appeler les 58 outils de Neo avec le meme pipeline que
//! `/api/outil/execute` : memes permissions (`tool_level`), meme sandbox
//! Rust, meme execution Python.
//!
//! Methodes implementees :
//! - `initialize` / `notifications/initialized` / `ping`
//! - `tools/list` / `tools/call`
//! - `resources/list` et `prompts/list` : listes vides (capacites non
//!   annoncees, mais certains clients les interrogent d'office)
//!
//! Volontairement sans streaming ni sessions : le transport HTTP simple
//! suffit a un client local. `/mcp` est soumis au meme jeton Bearer que
//! `/api/*` et `/v1/*`.

use std::sync::Arc;

use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::AppState;

const PROTOCOLS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/mcp", post(rpc))
}

#[derive(Debug, Deserialize)]
struct Rpc {
    #[serde(default)]
    jsonrpc: String,
    /// Absent = notification : pas de reponse JSON-RPC.
    #[serde(default)]
    id: Option<Value>,
    method: String,
    #[serde(default)]
    params: Option<Value>,
}

fn reponse(id: Value, result: Value) -> Response {
    Json(json!({ "jsonrpc": "2.0", "id": id, "result": result })).into_response()
}

fn erreur(id: Value, code: i64, message: &str) -> Response {
    Json(json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": { "code": code, "message": message }
    }))
    .into_response()
}

/// Convertit une spec Ollama (`{"type":"function","function":{...}}`) en
/// descripteur MCP (`{"name","description","inputSchema"}`).
fn vers_mcp(spec: &Value) -> Option<Value> {
    let f = spec.get("function")?;
    let nom = f.get("name")?.as_str()?;
    let description = f
        .get("description")
        .and_then(|d| d.as_str())
        .unwrap_or("Outil de Neo");
    let schema = f
        .get("parameters")
        .cloned()
        .unwrap_or_else(|| json!({ "type": "object", "properties": {} }));
    Some(json!({
        "name": nom,
        "description": description,
        "inputSchema": schema,
    }))
}

async fn rpc(State(st): State<Arc<AppState>>, Json(req): Json<Rpc>) -> Response {
    // Notification : aucun corps de reponse attendu.
    let Some(id) = req.id.clone() else {
        return axum::http::StatusCode::ACCEPTED.into_response();
    };

    if !req.jsonrpc.is_empty() && req.jsonrpc != "2.0" {
        return erreur(id, -32600, "jsonrpc doit valoir \"2.0\"");
    }

    match req.method.as_str() {
        "initialize" => {
            let demandee = req
                .params
                .as_ref()
                .and_then(|p| p.get("protocolVersion"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let version = if PROTOCOLS.contains(&demandee) {
                demandee
            } else {
                PROTOCOLS[0]
            };
            reponse(
                id,
                json!({
                    "protocolVersion": version,
                    "capabilities": { "tools": { "listChanged": false } },
                    "serverInfo": {
                        "name": "neo",
                        "version": localai_core::APP_VERSION,
                    },
                    "instructions": "Outils locaux de Neo (fichiers, web, images, 3D). \
                                     Les appels respectent le niveau de permission configure."
                }),
            )
        }
        "ping" => reponse(id, json!({})),
        "tools/list" => match crate::tools::specs_tous(&st.settings.root_dir).await {
            Ok(specs) => {
                let outils: Vec<Value> = specs.iter().filter_map(vers_mcp).collect();
                reponse(id, json!({ "tools": outils }))
            }
            Err(e) => erreur(id, -32603, &format!("liste des outils indisponible: {e}")),
        },
        "tools/call" => {
            let p = req.params.unwrap_or_else(|| json!({}));
            let nom = p.get("name").and_then(|v| v.as_str()).unwrap_or("").trim();
            if nom.is_empty() {
                return erreur(id, -32602, "parametre \"name\" manquant");
            }
            let args = p
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));
            let niveau = crate::tools::tool_level(&st.db);
            let texte = crate::tools::execute(&st.settings.root_dir, nom, &args, &niveau).await.text;
            let echec = texte.starts_with("Erreur")
                || texte.contains("PERMISSION REFUSEE")
                || texte.contains("ACCES REFUSE")
                || texte.contains("COMMANDE BLOQUEE");
            reponse(
                id,
                json!({
                    "content": [{ "type": "text", "text": texte }],
                    "isError": echec,
                }),
            )
        }
        "resources/list" => reponse(id, json!({ "resources": [] })),
        "prompts/list" => reponse(id, json!({ "prompts": [] })),
        autre => erreur(id, -32601, &format!("methode inconnue: {autre}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn conversion_spec_ollama_vers_mcp() {
        let spec = json!({
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Calcule une expression",
                "parameters": { "type": "object", "properties": { "expr": { "type": "string" } } }
            }
        });
        let m = vers_mcp(&spec).expect("spec valide");
        assert_eq!(m["name"], "calculator");
        assert_eq!(m["inputSchema"]["type"], "object");
        assert_eq!(m["description"], "Calcule une expression");
    }

    #[test]
    fn spec_incomplete_ignoree() {
        assert!(vers_mcp(&json!({ "type": "function" })).is_none());
        assert!(vers_mcp(&json!({ "function": { "name": "x" } })).is_some());
    }
}
