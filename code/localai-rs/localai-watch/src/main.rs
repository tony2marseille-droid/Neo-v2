//! Superviseur unique de Neo (tache planifiee LocalAI_watch_rs, compte SYSTEM, au demarrage).
//!
//! - Services permanents (serveur, Ollama, toolshim, rerank, video...) : lances,
//!   surveilles et relances d'apres `config/services.json` (voir services.rs).
//! - Machine : espace disque, sante SMART, temperature GPU, refus d'outils en
//!   rafale, entrainement fige. Alerte au passage en defaut (journal + Telegram
//!   si configure), pas a chaque minute.
//!
//! Remplace garde_prod.ps1, le chien de garde du toolshim et les taches de
//! lancement individuelles.
use std::path::Path;
use std::time::{Duration, Instant};

mod services;

/// Cadence de supervision des services.
const TICK_SERVICES: Duration = Duration::from_secs(15);
/// Cadence des controles machine (plus couteux : powershell, nvidia-smi).
const TICK_MACHINE: Duration = Duration::from_secs(60);
const DISK_MIN_GB: f64 = 30.0;
const GPU_MAX_C: f64 = 85.0;
/// Un statut d'entrainement `training` non rafraichi depuis ce delai est fige.
const ENTRAINEMENT_FIGE: Duration = Duration::from_secs(20 * 60);
/// Delai maximal d'une commande systeme (powershell, nvidia-smi) : un appel
/// bloque ne doit jamais figer toute la surveillance.
const DELAI_COMMANDE: Duration = Duration::from_secs(30);

fn root() -> String {
    localai_core::config::default_root().to_string_lossy().into_owned()
}

fn log(msg: &str) {
    let line = format!("{} watch: {}\n", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"), msg);
    print!("{line}");
    use std::io::Write;
    let path = Path::new(&root()).join("logs").join("watch_rs.log");
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// Sortie standard d'une commande, ou None (echec, delai depasse).
async fn sortie(exe: &str, args: &[&str]) -> Option<String> {
    let out = localai_core::processus::commande(exe)
        .args(args)
        .kill_on_drop(true)
        .output();
    let out = tokio::time::timeout(DELAI_COMMANDE, out).await.ok()?.ok()?;
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Journal + Telegram (si `telegram_token` et `telegram_alert_chat_id` sont regles).
async fn alerter(client: &reqwest::Client, db_path: &Path, message: &str) {
    log(&format!("ALERTE {message}"));
    let Ok(db) = localai_core::db::Db::open(db_path) else { return };
    let token = db.get_setting("telegram_token", "");
    let chat_id = db.get_setting("telegram_alert_chat_id", "");
    if token.is_empty() || chat_id.is_empty() {
        return;
    }
    let text: String = format!("Neo : {message}").chars().take(500).collect();
    let _ = client
        .post(format!("https://api.telegram.org/bot{token}/sendMessage"))
        .json(&serde_json::json!({ "chat_id": chat_id, "text": text }))
        .send()
        .await;
}

/// Lettre du disque qui porte l'installation (`C` a defaut).
fn lecteur() -> char {
    let r = root();
    let mut c = r.chars();
    match (c.next(), c.next()) {
        (Some(l), Some(':')) if l.is_ascii_alphabetic() => l.to_ascii_uppercase(),
        _ => 'C',
    }
}

async fn disk_gb() -> Option<f64> {
    let commande = format!("[math]::Round((Get-PSDrive {}).Free/1GB,1)", lecteur());
    sortie("powershell", &["-NoProfile", "-Command", &commande])
        .await?
        .parse()
        .ok()
}

async fn disk_health() -> Option<String> {
    // SMART via Get-PhysicalDisk : renvoie les disques non sains.
    let t = sortie("powershell", &["-NoProfile", "-Command",
        "Get-PhysicalDisk | Where-Object { ($_.HealthStatus -ne 'OK') -and ($_.HealthStatus -ne 'Healthy') } | ForEach-Object { $_.FriendlyName + ':' + $_.HealthStatus }"])
        .await?;
    (!t.is_empty()).then_some(t)
}

async fn gpu_temp() -> Option<f64> {
    sortie("nvidia-smi", &["--query-gpu=temperature.gpu", "--format=csv,noheader"])
        .await?
        .parse()
        .ok()
}

/// Refus d'outils (tool_traces status='denied') sur 10 min glissantes :
/// Some(resume) si le seuil de 5 est atteint.
fn refus_outils(db_path: &Path) -> Option<String> {
    let db = match localai_core::db::Db::open(db_path) {
        Ok(d) => d,
        Err(e) => {
            log(&format!("db KO {e}"));
            return None;
        }
    };
    // Fenetre glissante ISO : now - 600 s (lexicographique == chronologique).
    let depuis = (chrono::Local::now() - chrono::Duration::seconds(600))
        .format("%Y-%m-%dT%H:%M:%S")
        .to_string();
    let rows = db
        .query_all_json(
            "SELECT tool_name, reason FROM tool_traces WHERE status='denied' AND created_at >= ?1",
            &[&depuis.as_str()],
        )
        .unwrap_or_default();
    if rows.len() < 5 {
        return None;
    }
    let details: Vec<String> = rows
        .iter()
        .take(5)
        .map(|r| {
            format!(
                "{}: {}",
                r.get("tool_name").and_then(|v| v.as_str()).unwrap_or("?"),
                r.get("reason").and_then(|v| v.as_str()).unwrap_or("")
            )
        })
        .collect();
    Some(format!("{} refus d'outils en 10 min\n{}", rows.len(), details.join("\n")))
}

/// Statuts d'entrainement (`logs/train_v*_status.json`) en phase `training`
/// mais non rafraichis depuis 20 min.
fn entrainements_figes(logs: &Path) -> Vec<String> {
    let Ok(entrees) = std::fs::read_dir(logs) else { return Vec::new() };
    entrees
        .flatten()
        .filter(|e| {
            let n = e.file_name().to_string_lossy().to_string();
            n.starts_with("train_v") && n.ends_with("_status.json")
        })
        .filter_map(|e| {
            let age = e.metadata().ok()?.modified().ok()?.elapsed().ok()?;
            if age < ENTRAINEMENT_FIGE {
                return None;
            }
            let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(e.path()).ok()?).ok()?;
            (v.get("phase").and_then(|p| p.as_str()) == Some("training"))
                .then(|| format!("{} fige depuis {} min", e.file_name().to_string_lossy(), age.as_secs() / 60))
        })
        .collect()
}

/// Etat d'alerte par cle : n'alerte qu'au passage en defaut.
#[derive(Default)]
struct Alertes(std::collections::HashSet<String>);

impl Alertes {
    /// Vrai si `cle` vient de passer en defaut.
    fn passe_en_defaut(&mut self, cle: &str, defaut: bool) -> bool {
        if defaut {
            self.0.insert(cle.to_string())
        } else {
            self.0.remove(cle);
            false
        }
    }
}

async fn controles_machine(client: &reqwest::Client, db_path: &Path, logs: &Path, etat: &mut Alertes) {
    if let Some(libre) = disk_gb().await {
        if etat.passe_en_defaut("disque", libre < DISK_MIN_GB) {
            alerter(client, db_path, &format!("disque {}: {libre} Go libres (< {DISK_MIN_GB})", lecteur())).await;
        }
    }
    let smart = disk_health().await;
    if etat.passe_en_defaut("smart", smart.is_some()) {
        alerter(client, db_path, &format!("SMART disque : {}", smart.unwrap_or_default())).await;
    }
    if let Some(t) = gpu_temp().await {
        if etat.passe_en_defaut("gpu", t > GPU_MAX_C) {
            alerter(client, db_path, &format!("GPU {t} C (> {GPU_MAX_C})")).await;
        }
    }
    let refus = refus_outils(db_path);
    if etat.passe_en_defaut("refus", refus.is_some()) {
        alerter(client, db_path, &refus.unwrap_or_default()).await;
    }
    let figes = entrainements_figes(logs);
    if etat.passe_en_defaut("entrainement", !figes.is_empty()) {
        alerter(client, db_path, &format!("entrainement : {}", figes.join(", "))).await;
    }
}

#[tokio::main]
async fn main() {
    // Lance par une tache planifiee, le processus peut heriter d'une priorite
    // basse (disque et memoire compris) : retour a la normale avant tout.
    localai_core::processus::priorite_normale();
    tracing_subscriber::fmt().init();
    let root = root();
    let racine = Path::new(&root).to_path_buf();
    let logs = racine.join("logs");
    let data = std::env::var("LOCALAI_DATA").unwrap_or_else(|_| format!(r"{root}\data"));
    let db_path = Path::new(&data).join("localai.db");
    log(&format!("demarrage du superviseur (root={root}, base={})", db_path.display()));
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap_or_default();
    let mut superviseur = services::Superviseur::nouveau(&racine);
    let mut alertes = Alertes::default();
    let mut dernier_machine: Option<Instant> = None;
    let mut dernier_battement = Instant::now();
    // NEO_SUPERVISEUR_TICK_S : cadence acceleree pour les essais hors production.
    let tick = std::env::var("NEO_SUPERVISEUR_TICK_S")
        .ok()
        .and_then(|v| v.parse().ok())
        .map(Duration::from_secs)
        .unwrap_or(TICK_SERVICES);
    loop {
        let mut messages = Vec::new();
        superviseur.passage(&mut messages).await;
        for m in messages {
            alerter(&client, &db_path, &m).await;
        }
        if dernier_machine.is_none_or(|t| t.elapsed() >= TICK_MACHINE) {
            controles_machine(&client, &db_path, &logs, &mut alertes).await;
            dernier_machine = Some(Instant::now());
        }
        // Preuve de vie dans le journal : une ligne par heure.
        if dernier_battement.elapsed() >= Duration::from_secs(3600) {
            let etat = superviseur.etat_json();
            let resume: Vec<String> = etat["services"]
                .as_array()
                .map(|v| v.iter().map(|s| format!("{}={}", s["nom"].as_str().unwrap_or("?"), s["etat"].as_str().unwrap_or("?"))).collect())
                .unwrap_or_default();
            log(&format!("battement : {}", resume.join(" ")));
            dernier_battement = Instant::now();
        }
        tokio::time::sleep(tick).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alerte_seulement_au_passage_en_defaut() {
        let mut a = Alertes::default();
        assert!(a.passe_en_defaut("gpu", true));
        assert!(!a.passe_en_defaut("gpu", true));
        assert!(!a.passe_en_defaut("gpu", false));
        assert!(a.passe_en_defaut("gpu", true));
    }
}
