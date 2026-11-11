//! Pont vers le backend Python pour la logique ML/fichiers complexe.
//! Le Rust possede le HTTP, le Python execute la fonction et rend du JSON.
//! Timeouts stricts : un Python bloque ne doit jamais figer une requete HTTP.
use std::path::Path;
use std::time::Duration;

use crate::error::{ApiError, ApiResult};

/// Timeout par defaut : 5 min (whisper/SD peuvent etre lents au 1er appel).
pub const PY_TIMEOUT: Duration = Duration::from_secs(300);

/// Litteral de chaine Python sur pour n'importe quel texte.
///
/// Un litteral de chaine JSON est aussi un litteral Python valide (memes
/// echappements `\" \\ \n \uXXXX`). C'est la SEULE facon autorisee d'injecter
/// une valeur venant d'une requete dans le code passe a `python -c` :
/// l'ancien `replace('\'', "\\'")` dans `r'...'` se cassait sur un `\` final
/// et permettait d'executer du code arbitraire.
pub fn py_str(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "''".into())
}

/// Chemin de l'interpreteur du venv (unique source de verite).
pub fn venv_python(root: &Path) -> std::path::PathBuf {
    root.join(".venv").join("Scripts").join("python.exe")
}

async fn run_with_timeout(
    root: &Path,
    args: &[&str],
    timeout: Duration,
) -> ApiResult<std::process::Output> {
    let py = venv_python(root);
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal(
            "venv python absent (pont coupe)".into(),
        )));
    }
    let mut cmd = localai_core::processus::commande(&py);
    // Encodage force. Sans cela Windows utilise la page de code ANSI pour
    // stdout et pour la source `-c` : « é » devient « Ã© » (mojibake constate
    // dans la table `memories`). PYTHONUTF8=1 active le mode UTF-8 complet,
    // -X utf8 force aussi la source et les E/S standard.
    cmd.env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .arg("-X")
        .arg("utf8")
        .args(args)
        .current_dir(root);
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    // Tue l'enfant si on abandonne l'attente (timeout) : pas de Python orphelin.
    cmd.kill_on_drop(true);
    let child = cmd.spawn().map_err(|e| {
        ApiError(localai_core::AppError::Internal(format!("pont spawn: {e}")))
    })?;
    let pid = child.id();
    // Futur epingle et emprunte : au delai depasse, le Python est ENCORE
    // vivant quand on tue l'arbre (un parent deja mort rendrait ses enfants
    // introuvables pour `taskkill /T`).
    let attente = child.wait_with_output();
    tokio::pin!(attente);
    match tokio::time::timeout(timeout, &mut attente).await {
        Ok(Ok(out)) => Ok(out),
        Ok(Err(e)) => Err(ApiError(localai_core::AppError::Internal(format!(
            "pont io: {e}"
        )))),
        Err(_) => {
            // L'ARBRE entier : le Python lance des outils (sd-cli, ffmpeg...)
            // qui survivraient a kill_on_drop et garderaient le GPU.
            if let Some(pid) = pid {
                localai_core::processus::tuer_arbre(pid).await;
            }
            Err(ApiError(localai_core::AppError::Internal(format!(
                "pont python : delai depasse ({} s), process tue",
                timeout.as_secs()
            ))))
        }
    }
}

pub async fn py_json(root: &Path, code: &str) -> ApiResult<serde_json::Value> {
    py_json_timeout(root, code, PY_TIMEOUT).await
}

pub async fn py_json_timeout(
    root: &Path,
    code: &str,
    timeout: Duration,
) -> ApiResult<serde_json::Value> {
    let wrapped = format!("import json\n{code}");
    let out = run_with_timeout(root, &["-c", &wrapped], timeout).await?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let err: String = err.chars().take(300).collect();
        return Err(ApiError(localai_core::AppError::Internal(format!(
            "pont python: {err}"
        ))));
    }
    // Derniere valeur JSON (objet, tableau ou chaine) : les modules peuvent
    // imprimer des logs avant le resultat.
    let stdout_str = String::from_utf8_lossy(&out.stdout);
    extraire_json(&stdout_str).ok_or_else(|| {
        let debut: String = stdout_str.trim().chars().take(200).collect();
        ApiError(localai_core::AppError::Internal(format!("pont JSON: sortie illisible: {debut}")))
    })
}

/// Timeout du pont chaud : ces appels sont des lectures d'etat, ils doivent
/// repondre vite. Au-dela, on considere le worker bloque et on repasse par le
/// pont classique.
pub const CHAUD_TIMEOUT_S: u64 = 25;

/// Pont « chaud » : execute le meme code Python que `py_json`, mais dans le
/// worker resident (backend/worker.py) dont les imports sont deja faits.
/// Mesure du gain : le pont classique paye le demarrage Python + imports a
/// chaque appel (0,3 s pour un etat simple, 5 s pour la voix qui charge le
/// modele STT). Repli automatique sur le pont classique si le worker est
/// absent, bloque ou rend une sortie inexploitable : le resultat est identique.
pub async fn py_json_chaud(root: &Path, code: &str) -> ApiResult<serde_json::Value> {
    if let Some(w) = crate::worker::get() {
        let req = serde_json::json!({
            "op": "code",
            "code": format!("import json\n{code}"),
        });
        match w.call(req, CHAUD_TIMEOUT_S).await {
            Ok(v) => {
                let txt = v.as_str().unwrap_or("");
                if let Some(parsed) = extraire_json(txt) {
                    return Ok(parsed);
                }
                tracing::warn!("pont chaud: sortie non JSON, repli sur le pont classique");
            }
            Err(e) => {
                tracing::warn!("pont chaud indisponible ({e}), repli sur le pont classique");
            }
        }
    }
    py_json_timeout(root, code, PY_TIMEOUT).await
}

/// Derniere valeur JSON exploitable d'une sortie Python : les modules peuvent
/// imprimer des logs avant le resultat (meme logique que le pont classique).
fn extraire_json(txt: &str) -> Option<serde_json::Value> {
    let lignes: Vec<&str> = txt.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    for l in lignes.iter().rev() {
        let structurel = (l.starts_with('{') && l.ends_with('}'))
            || (l.starts_with('[') && l.ends_with(']'))
            || (l.starts_with('"') && l.ends_with('"'));
        if structurel {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(l) {
                return Some(v);
            }
        }
    }
    serde_json::from_str::<serde_json::Value>(txt.trim()).ok()
}

/// Extraction de faits d'une conversation (script `extract_facts.py`) en
/// tache de fond : venv et racine configures (plus de chemins de machine en
/// dur), UTF-8, delai borne, erreurs journalisees au lieu d'etre avalees.
pub fn extraire_faits_en_fond(root: &Path, conversation_id: i64) {
    let root = root.to_path_buf();
    tokio::spawn(async move {
        let script = root.join("backend").join("core").join("extract_facts.py");
        let cid = conversation_id.to_string();
        let args = [script.to_string_lossy().to_string(), cid];
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        match run_with_timeout(&root, &args, Duration::from_secs(300)).await {
            Ok(out) if out.status.success() => {}
            Ok(out) => tracing::warn!(
                "extract_facts {conversation_id}: {}",
                String::from_utf8_lossy(&out.stderr).chars().take(300).collect::<String>()
            ),
            Err(e) => tracing::warn!("extract_facts {conversation_id}: {e}"),
        }
    });
}

pub async fn py_text_timeout(
    root: &Path,
    args: &[&str],
    timeout: std::time::Duration,
) -> ApiResult<String> {
    let out = run_with_timeout(root, args, timeout).await?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let err: String = err.chars().take(300).collect();
        return Err(ApiError(localai_core::AppError::Internal(format!(
            "pont python: {err}"
        ))));
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_str_neutralise_les_injections() {
        // Le cas qui cassait l'ancien echappement : `\` final puis `'`.
        assert_eq!(py_str(r"a\"), r#""a\\""#);
        assert_eq!(py_str("l'avion"), r#""l'avion""#);
        assert_eq!(py_str("x\")\nimport os#"), r#""x\")\nimport os#""#);
        assert_eq!(py_str(r"C:\Users\Alice"), "\"C:\\\\Users\\\\Alice\"");
    }

    #[test]
    fn extraire_json_ignore_les_logs() {
        assert_eq!(extraire_json("log\n[1,2]\n"), Some(serde_json::json!([1, 2])));
        assert_eq!(extraire_json("chargement...\n\"txt\""), Some(serde_json::json!("txt")));
        assert_eq!(extraire_json("{\"a\":1}"), Some(serde_json::json!({"a": 1})));
        assert_eq!(extraire_json("rien"), None);
    }
}
