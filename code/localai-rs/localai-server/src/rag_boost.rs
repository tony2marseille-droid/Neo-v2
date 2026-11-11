// RAG boosté : expansion de requêtes + fusion RRF + rerank cross-encoder.
// Repli automatique si le service de rerank est absent.
use serde_json::{json, Value};
use std::collections::HashMap;
use crate::reasoning::chat_boost;

pub fn rrf(lists: &[Vec<(String, String, f32)>], k: f32, top: usize) -> Vec<(String, String, f32)> {
    let mut acc: HashMap<String, (f32, String)> = HashMap::new();
    for l in lists {
        for (r, (id, text, _)) in l.iter().enumerate() {
            acc.entry(id.clone()).or_insert((0.0, text.clone())).0 += 1.0 / (k + r as f32 + 1.0);
        }
    }
    let mut v: Vec<(f32, String, String)> = acc.into_iter().map(|(id, (s, t))| (s, id, t)).collect();
    v.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    v.into_iter().take(top).map(|(s, id, t)| (id, t, s)).collect()
}

pub async fn expand_queries(http: &reqwest::Client, ollama_url: &str, judge: &str, q: &str) -> Vec<String> {
    let mut out = vec![q.to_string()];
    let p = format!("Réécris cette requête de recherche de 3 façons différentes (synonymes, termes techniques, forme déclarative). Réponds uniquement par un tableau JSON de 3 chaînes.\n\nRequête : {q}");
    let m = [json!({"role": "user", "content": p})];
    if let Ok(t) = chat_boost(http, ollama_url, judge, &m, 0.3, 4096, 200).await {
        if let (Some(a), Some(b)) = (t.find('['), t.rfind(']')) {
            if b > a {
                if let Ok(v) = serde_json::from_str::<Vec<String>>(&t[a..=b]) {
                    out.extend(v.into_iter().filter(|s| !s.trim().is_empty()).take(3));
                }
            }
        }
    }
    out
}

pub async fn rerank(http: &reqwest::Client, rerank_url: &str, q: &str,
    hits: Vec<(String, String, f32)>, top_k: usize) -> (Vec<(String, String, f32)>, bool) {
    if hits.is_empty() { return (hits, false); }
    let docs: Vec<&str> = hits.iter().map(|h| h.1.as_str()).collect();
    let body = json!({"query": q, "docs": docs});
    let res = async {
        let v: Value = http.post(format!("{}/rerank", rerank_url))
            .json(&body).timeout(std::time::Duration::from_secs(20))
            .send().await?.error_for_status()?.json().await?;
        Ok::<Value, anyhow::Error>(v)
    }.await;
    let scores: Vec<f32> = match res {
        Ok(v) => v["scores"].as_array()
            .map(|a| a.iter().map(|x| x.as_f64().unwrap_or(0.0) as f32).collect())
            .unwrap_or_default(),
        Err(e) => {
            // Repli attendu quand le service de rerank n'est pas lance :
            // compte, mais ne rend pas le serveur "degrade" (le RAG reste
            // pertinent via RRF).
            crate::metrics::rerank_repli();
            tracing::debug!("rerank indisponible ({e}), repli sur RRF");
            // `false` : les scores sont des scores RRF (~1/61), pas des
            // scores cross-encoder : l'appelant ne doit pas leur appliquer
            // le seuil de pertinence 0,20.
            return (hits.into_iter().take(top_k).collect(), false);
        }
    };
    if scores.len() != hits.len() {
        crate::metrics::rerank_repli();
        return (hits.into_iter().take(top_k).collect(), false);
    }
    crate::metrics::rerank_ok();
    let mut z: Vec<(f32, (String, String, f32))> = scores.into_iter().zip(hits).collect();
    z.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    (
        z.into_iter()
            .take(top_k)
            .map(|(s, (id, t, _))| (id, t, s))
            .collect(),
        true,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_rrf_favorise_doublons() {
        let a = vec![("a".into(), "ta".into(), 1.0), ("b".into(), "tb".into(), 0.8)];
        let b = vec![("c".into(), "tc".into(), 0.9), ("b".into(), "tb".into(), 0.7)];
        let r = rrf(&[a, b], 60.0, 3);
        assert_eq!(r[0].0, "b");
    }
}