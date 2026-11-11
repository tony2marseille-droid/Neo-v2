//! Memoire persistante de l'assistant.
//!
//! Deux natures de lignes cohabitent, distinguees par `tag` :
//! - memoire utilisateur (`user`, `fait`, `preference`, `projet`) : injectee
//!   dans le prompt de conversation ;
//! - notes techniques (`outil`, `agent`, `debug`, `trace`) : jamais injectees,
//!   consultables uniquement via `memory_search`.
//!
//! Le filtrage vit aussi cote chat (`routes/chat.rs`) : les deux endroits
//! doivent rester coherents.
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    routing::{delete, get, post, put},
    Json, Router,
};

use localai_core::db::now_iso;
use localai_core::AppError;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};

/// Tags qui remontent dans le prompt de conversation.
pub const TAGS_UTILISATEUR: [&str; 4] = ["user", "fait", "preference", "projet"];
/// Tags techniques : stockes, mais jamais injectes.
pub const TAGS_TECHNIQUES: [&str; 4] = ["outil", "agent", "debug", "trace"];

fn tag_valide(t: &str) -> bool {
    TAGS_UTILISATEUR.contains(&t) || TAGS_TECHNIQUES.contains(&t)
}

/// Normalise un tag libre vers un tag connu.
fn normaliser(t: Option<&str>) -> String {
    let brut = t.unwrap_or("user").trim().to_lowercase();
    let t = match brut.as_str() {
        "utilisateur" | "user" => "user",
        "fait" | "fact" => "fait",
        "preference" | "préférence" | "pref" => "preference",
        "projet" | "project" => "projet",
        "outil" | "tool" => "outil",
        "agent" => "agent",
        "debug" => "debug",
        "trace" => "trace",
        "" => "user",
        _ => return "user".to_string(),
    };
    t.to_string()
}

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/memory", get(list).post(add))
        .route("/api/memory/stats", get(stats))
        .route("/api/memory/:id", delete(remove))
        .route("/api/memory/:id", put(update))
        .route("/api/memory/extract", post(extraire))
}

#[derive(Debug, Deserialize)]
pub struct CreateBody {
    pub content: String,
    pub tag: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ListQuery {
    pub tag: Option<String>,
    pub limit: Option<i64>,
}

fn as_i64(v: &Value, cle: &str) -> i64 {
    v.get(cle).and_then(|x| x.as_i64()).unwrap_or(0)
}

async fn list(
    State(st): State<Arc<crate::AppState>>,
    Query(q): Query<ListQuery>,
) -> ApiResult<Json<Value>> {
    let limite = q.limit.unwrap_or(200).clamp(1, 1000);
    let rows = match q.tag.as_deref() {
        Some(t) if !t.trim().is_empty() => {
            let tag = normaliser(Some(t));
            st.db.query_all_json(
                "SELECT id, content, tag, created_at FROM memories \
                 WHERE tag = ? ORDER BY id DESC LIMIT ?",
                &[&tag.as_str(), &limite],
            )?
        }
        _ => st.db.query_all_json(
            "SELECT id, content, tag, created_at FROM memories ORDER BY id DESC LIMIT ?",
            &[&limite],
        )?,
    };
    Ok(Json(json!({ "memories": rows })))
}

async fn add(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<CreateBody>,
) -> ApiResult<Json<Value>> {
    // 500 caracteres : au-dela, ce n'est plus une memoire mais un document.
    let contenu: String = b.content.trim().chars().take(500).collect();
    if contenu.is_empty() {
        return Err(ApiError(AppError::BadRequest("Contenu vide".into())));
    }
    let tag = normaliser(b.tag.as_deref());
    if !tag_valide(&tag) {
        return Err(ApiError(AppError::BadRequest(format!("tag inconnu: {tag}"))));
    }

    // Deduplication exacte : evite les rafales d'insertions identiques
    // (l'agent dev rejouait la meme lecon a chaque tentative).
    if let Ok(deja) = st.db.query_all_json(
        "SELECT id FROM memories WHERE content = ? LIMIT 1",
        &[&contenu.as_str()],
    ) {
        if let Some(premier) = deja.first() {
            return Ok(Json(json!({
                "id": as_i64(premier, "id"),
                "tag": tag,
                "duplicate": true,
            })));
        }
    }

    let now = now_iso();
    let id = st.db.execute(
        "INSERT INTO memories (content, tag, created_at) VALUES (?,?,?)",
        &[&contenu.as_str(), &tag.as_str(), &now.as_str()],
    )?;
    Ok(Json(json!({ "id": id, "tag": tag, "duplicate": false })))
}

async fn remove(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    st.db.execute("DELETE FROM memories WHERE id=?", &[&id])?;
    Ok(Json(json!({ "deleted": id })))
}

/// PUT /api/memory/:id : modifie le contenu et/ou le tag d'une memoire
/// existante. Verifie la presence avant mise a jour.
async fn update(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
    Json(b): Json<UpdateBody>,
) -> ApiResult<Json<Value>> {
    let contenu: String = b.content.trim().chars().take(500).collect();
    if contenu.is_empty() {
        return Err(ApiError(AppError::BadRequest("Contenu vide".into())));
    }
    let tag = normaliser(b.tag.as_deref());
    if !tag_valide(&tag) {
        return Err(ApiError(AppError::BadRequest(format!("tag inconnu: {tag}"))));
    }

    // Verifier que la memoire existe
    let existe = st
        .db
        .query_one_json("SELECT id FROM memories WHERE id=?", &[&id])?
        .is_some();
    if !existe {
        return Err(ApiError(AppError::NotFound("Memoire introuvable".into())));
    }

    st.db.execute(
        "UPDATE memories SET content=?, tag=? WHERE id=?",
        &[&contenu.as_str(), &tag.as_str(), &id],
    )?;
    Ok(Json(json!({ "id": id, "tag": tag, "updated": true })))
}

#[derive(Debug, Deserialize)]
pub struct UpdateBody {
    pub content: String,
    pub tag: Option<String>,
}

/// GET /api/memory/stats : repartition par tag + signal d'alerte si la
/// memoire utilisateur est vide alors que des notes techniques existent.
async fn stats(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let par_tag = st.db.query_all_json(
        "SELECT tag, COUNT(*) AS n FROM memories GROUP BY tag ORDER BY n DESC",
        &[],
    )?;
    let total: i64 = par_tag
        .iter()
        .map(|r| as_i64(r, "n"))
        .sum();
    let utilisateur: i64 = par_tag
        .iter()
        .filter(|r| {
            r.get("tag")
                .and_then(|t| t.as_str())
                .map(|t| TAGS_UTILISATEUR.contains(&t))
                .unwrap_or(false)
        })
        .map(|r| as_i64(r, "n"))
        .sum();
    Ok(Json(json!({
        "total": total,
        "utilisateur": utilisateur,
        "technique": total - utilisateur,
        "par_tag": par_tag,
        "alerte": utilisateur == 0 && total > 0,
    })))
}

#[derive(Debug, Deserialize)]
pub struct ExtractBody {
    pub conversation_id: i64,
}

/// POST /api/memory/extract : declenche l'extraction automatique de faits
/// depuis une conversation donnee (appelle le script Python d'extraction).
async fn extraire(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ExtractBody>,
) -> ApiResult<Json<Value>> {
    let cid = b.conversation_id;
    crate::pybridge::extraire_faits_en_fond(&st.settings.root_dir, cid);
    Ok(Json(json!({ "status": "demarree", "conversation_id": cid })))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support;

    #[test]
    fn normalisation_des_tags() {
        assert_eq!(normaliser(None), "user");
        assert_eq!(normaliser(Some("  UTILISATEUR ")), "user");
        assert_eq!(normaliser(Some("fact")), "fait");
        assert_eq!(normaliser(Some("préférence")), "preference");
        assert_eq!(normaliser(Some("project")), "projet");
        assert_eq!(normaliser(Some("tool")), "outil");
        assert_eq!(normaliser(Some("")), "user");
        assert_eq!(normaliser(Some("inconnu")), "user");
    }

    #[test]
    fn tags_utilisateur_et_techniques_sont_disjoints() {
        for t in TAGS_UTILISATEUR {
            assert!(tag_valide(t));
            assert!(!TAGS_TECHNIQUES.contains(&t));
        }
        for t in TAGS_TECHNIQUES {
            assert!(tag_valide(t));
            assert!(!TAGS_UTILISATEUR.contains(&t));
        }
        assert!(!tag_valide("inconnu"));
    }

    #[tokio::test]
    async fn ajout_tronque_dedoublonne_et_refuse_le_vide() {
        let st = test_support::etat("memoire_ajout");
        let long = "x".repeat(700);
        let Json(v) = add(
            State(st.clone()),
            Json(CreateBody { content: long.clone(), tag: Some("fait".into()) }),
        )
        .await
        .expect("ajout");
        assert_eq!(v["tag"], json!("fait"));
        assert_eq!(v["duplicate"], json!(false));
        let lignes = st
            .db
            .query_all_json("SELECT content FROM memories", &[])
            .expect("lecture");
        assert_eq!(lignes.len(), 1);
        assert_eq!(lignes[0]["content"].as_str().unwrap().chars().count(), 500);

        // Meme contenu : renvoye comme doublon, pas reinsere.
        let Json(v) = add(
            State(st.clone()),
            Json(CreateBody { content: long, tag: Some("fait".into()) }),
        )
        .await
        .expect("doublon");
        assert_eq!(v["duplicate"], json!(true));
        let total = st
            .db
            .query_one_json("SELECT COUNT(*) AS n FROM memories", &[])
            .expect("count")
            .unwrap();
        assert_eq!(total["n"], json!(1));

        let err = add(
            State(st),
            Json(CreateBody { content: "   ".into(), tag: None }),
        )
        .await
        .expect_err("contenu vide");
        assert!(err.to_string().contains("Contenu vide"));
    }

    #[tokio::test]
    async fn liste_filtre_par_tag_et_respecte_la_limite() {
        let st = test_support::etat("memoire_liste");
        for (contenu, tag) in [
            ("aime le cafe", "preference"),
            ("habite Marseille", "fait"),
            ("note technique", "debug"),
        ] {
            let _ = add(
                State(st.clone()),
                Json(CreateBody { content: contenu.into(), tag: Some(tag.into()) }),
            )
            .await
            .expect("ajout");
        }

        let Json(v) = list(
            State(st.clone()),
            Query(ListQuery { tag: Some("fait".into()), limit: None }),
        )
        .await
        .expect("liste filtree");
        let memories = v["memories"].as_array().unwrap();
        assert_eq!(memories.len(), 1);
        assert_eq!(memories[0]["content"], json!("habite Marseille"));

        let Json(v) = list(
            State(st.clone()),
            Query(ListQuery { tag: None, limit: Some(2) }),
        )
        .await
        .expect("liste limitee");
        assert_eq!(v["memories"].as_array().unwrap().len(), 2);

        // Limite hors bornes : clampee, jamais 0 ni negative.
        let Json(v) = list(State(st), Query(ListQuery { tag: None, limit: Some(-5) }))
            .await
            .expect("liste bornee");
        assert_eq!(v["memories"].as_array().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn stats_separent_utilisateur_et_technique() {
        let st = test_support::etat("memoire_stats");
        for (contenu, tag) in [
            ("prefere le the", "preference"),
            ("projet neo", "projet"),
            ("trace outil", "trace"),
        ] {
            let _ = add(
                State(st.clone()),
                Json(CreateBody { content: contenu.into(), tag: Some(tag.into()) }),
            )
            .await
            .expect("ajout");
        }
        let Json(v) = stats(State(st)).await.expect("stats");
        assert_eq!(v["total"], json!(3));
        assert_eq!(v["utilisateur"], json!(2));
        assert_eq!(v["technique"], json!(1));
        assert_eq!(v["alerte"], json!(false));
    }

    #[tokio::test]
    async fn alerte_quand_seules_les_notes_techniques_existent() {
        let st = test_support::etat("memoire_alerte");
        let _ = add(
            State(st.clone()),
            Json(CreateBody { content: "trace".into(), tag: Some("trace".into()) }),
        )
        .await
        .expect("ajout");
        let Json(v) = stats(State(st)).await.expect("stats");
        assert_eq!(v["utilisateur"], json!(0));
        assert_eq!(v["alerte"], json!(true));
    }

    #[tokio::test]
    async fn suppression_par_id() {
        let st = test_support::etat("memoire_suppr");
        let Json(v) = add(
            State(st.clone()),
            Json(CreateBody { content: "a oublier".into(), tag: Some("user".into()) }),
        )
        .await
        .expect("ajout");
        let id = v["id"].as_i64().expect("id");
        let Json(v) = remove(State(st.clone()), Path(id)).await.expect("suppression");
        assert_eq!(v["deleted"], json!(id));
        let reste = st
            .db
            .query_one_json("SELECT COUNT(*) AS n FROM memories", &[])
            .expect("count")
            .unwrap();
        assert_eq!(reste["n"], json!(0));
    }
}

