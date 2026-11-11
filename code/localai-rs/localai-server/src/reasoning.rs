// Best-of-N raisonnement (neo-boost intégré). Autofinancé, zéro config externe.
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::OnceLock;
use regex::Regex;

pub const SUFFIX: &str = "\n\nRaisonne étape par étape, vérifie ton résultat, puis termine par une dernière ligne exactement au format :\nRÉPONSE_FINALE: <réponse courte>";

#[derive(Clone, Debug, serde::Serialize)]
pub struct Candidate {
    pub text: String,
    pub final_answer: Option<String>,
}

#[derive(Debug, serde::Serialize)]
pub struct Outcome {
    pub answer: String,
    pub final_answer: Option<String>,
    pub method: &'static str,
    pub votes: usize,
    pub generated: usize,
}

pub fn should_boost(prompt: &str) -> bool {
    static KO: OnceLock<Regex> = OnceLock::new();
    static OK: OnceLock<Regex> = OnceLock::new();
    let ko = KO.get_or_init(|| {
        Regex::new(r"(?i)(génère|genere|montre[- ]moi|image|photo|\b3d\b|3mf|dessine|souviens|fiche|téléphone|telegram|vidéo)").unwrap()
    });
    let ok = OK.get_or_init(|| {
        Regex::new(r"(?i)(calcul|combien|démontr|prouv|raisonn|étape|planifi|optimis|logique|énigme|probabilit|équation|déduis|algorithm|pourquoi|compare|reste|divis|quel est|quel était|prix|coût|coûtent|distance|vitesse|poids|âge|nombre|somme|différence|produit|résultat|quel âge|combien de|trouve|détermine|résous)").unwrap()
    });
    prompt.chars().count() >= 25 && !ko.is_match(prompt) && ok.is_match(prompt)
}

pub fn extract_final(t: &str) -> Option<String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"(?im)R[ÉE]PONSE_FINALE\s*:\s*(.+)$").unwrap());
    re.captures_iter(t)
        .last()
        .map(|c| c[1].trim().trim_matches(|ch: char| ch == '*' || ch == '`').trim().to_string())
        .filter(|s| !s.is_empty())
}

pub fn normalize(s: &str) -> String {
    static N: OnceLock<Regex> = OnceLock::new();
    static TH: OnceLock<Regex> = OnceLock::new();
    let th = TH.get_or_init(|| Regex::new(r"(\d)[ \u{a0}\u{202f}](\d{3})\b").unwrap());
    let n = N.get_or_init(|| Regex::new(r"-?\d+(?:\.\d+)?").unwrap());
    let mut t = s.to_lowercase().replace(',', ".");
    for _ in 0..2 { t = th.replace_all(&t, "${1}${2}").into_owned(); }
    if let Some(m) = n.find(&t) {
        let v = m.as_str();
        return if v.contains('.') { v.trim_end_matches('0').trim_end_matches('.').to_string() } else { v.to_string() };
    }
    t.chars().filter(|c| c.is_alphanumeric()).collect()
}

pub fn vote(c: &[Candidate]) -> Option<(usize, usize)> {
    let mut m: HashMap<String, (usize, usize)> = HashMap::new();
    for (i, x) in c.iter().enumerate() {
        if let Some(f) = &x.final_answer { m.entry(normalize(f)).or_insert((i, 0)).1 += 1; }
    }
    let mut v: Vec<(usize, usize)> = m.into_values().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    match v.as_slice() {
        [] => None,
        [a] => Some(*a),
        [a, b, ..] => (a.1 > b.1).then_some(*a),
    }
}

pub async fn chat_boost(
    http: &reqwest::Client, ollama_url: &str, model: &str,
    messages: &[Value], temperature: f32, num_ctx: u32, num_predict: i32,
) -> anyhow::Result<String> {
    let num_ctx = localai_core::ollama::contexte_pour(model, num_ctx as usize);
    let body = json!({"model": model, "messages": messages, "stream": false, "think": false,
        "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict}});
    let r: Value = http.post(format!("{}/api/chat", ollama_url))
        .json(&body).send().await?.error_for_status()?.json().await?;
    Ok(r["message"]["content"].as_str().unwrap_or("").to_string())
}

async fn judge(http: &reqwest::Client, url: &str, jm: &str, q: &str, c: &[Candidate]) -> usize {
    let mut p = format!("Question :\n{}\n\nVoici {} réponses candidates. Choisis la plus correcte.\n", q, c.len());
    for (i, x) in c.iter().enumerate() {
        let ch: Vec<char> = x.text.chars().collect();
        let t: String = ch[ch.len().saturating_sub(1500)..].iter().collect();
        p += &format!("\n--- Candidat {} ---\n{}\n", i, t);
    }
    p += "\nRéponds uniquement par JSON : {\"best\": <numéro>}";
    let m = [json!({"role": "user", "content": p})];
    let Ok(t) = chat_boost(http, url, jm, &m, 0.0, 4096, 30).await else { return 0 };
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r#""best"\s*:\s*(\d+)"#).unwrap());
    re.captures(&t).and_then(|c| c[1].parse::<usize>().ok()).filter(|i| *i < c.len()).unwrap_or(0)
}

fn with_suffix(mut m: Vec<Value>) -> Vec<Value> {
    if let Some(l) = m.iter_mut().rev().find(|x| x["role"] == "user") {
        if let Some(c) = l["content"].as_str() {
            l["content"] = Value::String(format!("{}{}", c, SUFFIX));
        }
    }
    m
}

async fn gen(http: &reqwest::Client, url: &str, model: &str, msgs: &[Value], t: f32, ctx: u32) -> Option<Candidate> {
    let text = chat_boost(http, url, model, msgs, t, ctx, 3072).await.ok()?;
    Some(Candidate { final_answer: extract_final(&text), text })
}

#[allow(clippy::too_many_arguments)]
pub async fn best_of_n(
    http: &reqwest::Client, ollama_url: &str, model: &str, judge_model: &str,
    messages: &[Value], n: usize, parallel: usize, temperature: f32, num_ctx: u32,
) -> anyhow::Result<Outcome> {
    let msgs = with_suffix(messages.to_vec());
    use futures_util::stream::{self, StreamExt};
    let cands: Vec<Candidate> = stream::iter(0..n.max(1))
        .map(|_| gen(http, ollama_url, model, &msgs, temperature, num_ctx))
        .buffer_unordered(parallel.max(1))
        .filter_map(|x| async move { x })
        .collect().await;
    if cands.is_empty() { anyhow::bail!("aucun candidat généré"); }
    let generated = cands.len();
    let (idx, votes, method) = if cands.len() == 1 {
        (0, 1, "seul")
    } else {
        let q = messages.iter().rev().find(|m| m["role"] == "user")
            .and_then(|m| m["content"].as_str()).unwrap_or("").to_string();
        match vote(&cands) {
            Some((i, v)) if v >= 2 => (i, v, "vote"),
            _ => (judge(http, ollama_url, judge_model, &q, &cands).await, 1, "juge"),
        }
    };
    let best = cands.into_iter().nth(idx).unwrap();
    Ok(Outcome { answer: best.text, final_answer: best.final_answer, method, votes, generated })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn c(f: Option<&str>) -> Candidate { Candidate { text: "t".into(), final_answer: f.map(String::from) } }
    #[test] fn test_extrait() {
        assert_eq!(extract_final("a\nRÉPONSE_FINALE: **231 km**").as_deref(), Some("231 km"));
        assert_eq!(extract_final("rien"), None);
    }
    #[test] fn test_normalise() {
        assert_eq!(normalize("231 km"), "231");
        assert_eq!(normalize("1 267,50"), "1267.5");
        assert_eq!(normalize("Cinq"), "cinq");
    }
    #[test] fn test_vote_majoritaire() {
        assert_eq!(vote(&[c(Some("5")), c(Some("10")), c(Some("5 min"))]), Some((0, 2)));
        assert_eq!(vote(&[c(Some("5")), c(Some("10"))]), None);
        assert_eq!(vote(&[c(None)]), None);
    }
    #[test] fn test_declenchement() {
        assert!(should_boost("Combien de nombres premiers y a-t-il entre 30 et 60 ?"));
        assert!(!should_boost("génère-moi une image 4k d'un chat"));
        assert!(!should_boost("montre-moi un support de téléphone"));
    }
}