//! Superviseur unique des services permanents de Neo.
//!
//! La liste vient de `config/services.json` (relue a chaud). Pour chaque
//! service, a chaque passage :
//! - sante OK -> `ok` (un service deja lance par ailleurs est simplement adopte) ;
//! - port ferme -> lancement immediat (sans fenetre, journal redirige), sauf
//!   pendant le delai de demarrage d'un lancement en cours ;
//! - port ouvert mais sante KO -> `degrade` ; apres `echecs_avant_arret`
//!   controles consecutifs, le processus est tue (arbre complet) puis relance.
//!   Un serveur occupe par une longue generation repond toujours a sa route de
//!   sante : seul un processus reellement bloque atteint ce seuil.
//!
//! Les relances successives sont espacees (10 s, 20 s, 40 s... 5 min max) ;
//! au-dela de 5 tentatives sans succes, le service passe `en_echec` (alerte).
//! L'etat est ecrit dans `logs/services_etat.json` (lu par /api/system/services).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime};

use serde::Deserialize;
use serde_json::{json, Value};

const DELAI_SANTE: Duration = Duration::from_secs(10);
const RELANCE_MIN: Duration = Duration::from_secs(10);
const RELANCE_MAX: Duration = Duration::from_secs(300);
/// Tentatives sans succes avant l'etat `en_echec` (et l'alerte).
const TENTATIVES_ALERTE: u32 = 5;
/// Temps en bonne sante apres lequel le compteur de tentatives repart a zero.
const STABLE_APRES: Duration = Duration::from_secs(120);
/// Taille d'un journal au-dela de laquelle il est archive (.1) au lancement.
const JOURNAL_MAX: u64 = 50 * 1024 * 1024;

fn defaut_delai_demarrage() -> u64 {
    60
}
fn defaut_echecs() -> u32 {
    8
}
fn vrai() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Definition {
    pub nom: String,
    #[serde(default)]
    pub libelle: String,
    pub commande: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub dossier: Option<String>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    pub port: u16,
    pub sante: String,
    #[serde(default)]
    pub journal: Option<String>,
    #[serde(default = "defaut_delai_demarrage")]
    pub delai_demarrage_s: u64,
    #[serde(default = "defaut_echecs")]
    pub echecs_avant_arret: u32,
    #[serde(default)]
    pub delai_initial_s: u64,
    #[serde(default = "vrai")]
    pub actif: bool,
}

#[derive(Debug, Deserialize)]
struct Fichier {
    services: Vec<Definition>,
}

/// Lit et valide la configuration (noms et ports uniques).
pub fn lire_config(chemin: &Path) -> Result<Vec<Definition>, String> {
    let texte = std::fs::read_to_string(chemin).map_err(|e| format!("{}: {e}", chemin.display()))?;
    // BOM toleree : le Bloc-notes et PowerShell 5.1 en ajoutent un en UTF-8.
    let f: Fichier = serde_json::from_str(texte.trim_start_matches('\u{feff}'))
        .map_err(|e| format!("{}: {e}", chemin.display()))?;
    let mut noms = std::collections::HashSet::new();
    let mut ports = std::collections::HashSet::new();
    for d in &f.services {
        if !noms.insert(d.nom.clone()) {
            return Err(format!("service en double : {}", d.nom));
        }
        if d.actif && !ports.insert(d.port) {
            return Err(format!("port {} utilise par deux services actifs", d.port));
        }
    }
    Ok(f.services)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Etat {
    Inconnu,
    Ok,
    Demarrage,
    Degrade,
    Arrete,
    EnEchec,
    Desactive,
}

impl Etat {
    fn texte(self) -> &'static str {
        match self {
            Etat::Inconnu => "inconnu",
            Etat::Ok => "ok",
            Etat::Demarrage => "demarrage",
            Etat::Degrade => "degrade",
            Etat::Arrete => "arrete",
            Etat::EnEchec => "en_echec",
            Etat::Desactive => "desactive",
        }
    }
}

/// Decision d'un passage, calculee sans effet de bord (testable).
#[derive(Debug, PartialEq, Eq)]
pub enum Action {
    Rien,
    Lancer,
    TuerPuisLancer,
}

/// Observation d'un passage.
pub struct Constat {
    pub sante_ok: bool,
    pub port_ouvert: bool,
    /// Notre processus vit encore et son delai de demarrage n'est pas ecoule.
    pub en_demarrage: bool,
    /// Une relance est permise maintenant (espacement respecte).
    pub relance_permise: bool,
}

/// Regle de supervision : nouvel etat, compteur d'echecs, action.
pub fn decider(c: &Constat, echecs: u32, seuil: u32) -> (Etat, u32, Action) {
    if c.sante_ok {
        return (Etat::Ok, 0, Action::Rien);
    }
    if c.en_demarrage {
        return (Etat::Demarrage, 0, Action::Rien);
    }
    if !c.port_ouvert {
        let action = if c.relance_permise { Action::Lancer } else { Action::Rien };
        return (Etat::Arrete, 0, action);
    }
    let echecs = echecs + 1;
    if echecs >= seuil && c.relance_permise {
        return (Etat::Degrade, 0, Action::TuerPuisLancer);
    }
    (Etat::Degrade, echecs, Action::Rien)
}

/// Espacement apres `n` tentatives : 10 s, 20 s, 40 s... plafonne a 5 min.
pub fn espacement(n: u32) -> Duration {
    let facteur = 1u64 << n.saturating_sub(1).min(5);
    (RELANCE_MIN * facteur as u32).min(RELANCE_MAX)
}

struct Suivi {
    def: Definition,
    etat: Etat,
    depuis: chrono::DateTime<chrono::Local>,
    enfant: Option<tokio::process::Child>,
    lance_a: Option<Instant>,
    echecs: u32,
    tentatives: u32,
    prochaine_relance: Instant,
    ok_depuis: Option<Instant>,
    relances: u32,
    derniere_relance: Option<chrono::DateTime<chrono::Local>>,
    message: String,
    alerte_envoyee: bool,
}

impl Suivi {
    fn nouveau(def: Definition, debut: Instant) -> Self {
        let delai = Duration::from_secs(def.delai_initial_s);
        Suivi {
            def,
            etat: Etat::Inconnu,
            depuis: chrono::Local::now(),
            enfant: None,
            lance_a: None,
            echecs: 0,
            tentatives: 0,
            prochaine_relance: debut + delai,
            ok_depuis: None,
            relances: 0,
            derniere_relance: None,
            message: String::new(),
            alerte_envoyee: false,
        }
    }

    fn changer(&mut self, etat: Etat) {
        if self.etat != etat {
            self.etat = etat;
            self.depuis = chrono::Local::now();
        }
    }

    fn pid(&self) -> Option<u32> {
        self.enfant.as_ref().and_then(|c| c.id())
    }
}

pub struct Superviseur {
    racine: PathBuf,
    config: PathBuf,
    config_date: Option<SystemTime>,
    suivis: Vec<Suivi>,
    client: reqwest::Client,
    debut: Instant,
}

impl Superviseur {
    pub fn nouveau(racine: &Path) -> Self {
        Superviseur {
            racine: racine.to_path_buf(),
            config: racine.join("config").join("services.json"),
            config_date: None,
            suivis: Vec::new(),
            client: reqwest::Client::builder()
                .timeout(DELAI_SANTE)
                .build()
                .unwrap_or_default(),
            debut: Instant::now(),
        }
    }

    /// Recharge la configuration si le fichier a change. Un service retire ou
    /// modifie n'est pas tue : il sera simplement relance selon sa nouvelle
    /// definition s'il tombe.
    fn recharger(&mut self) {
        let date = std::fs::metadata(&self.config).and_then(|m| m.modified()).ok();
        if date.is_some() && date == self.config_date {
            return;
        }
        match lire_config(&self.config) {
            Ok(defs) => {
                let mut anciens: HashMap<String, Suivi> =
                    self.suivis.drain(..).map(|s| (s.def.nom.clone(), s)).collect();
                for def in defs {
                    let suivi = match anciens.remove(&def.nom) {
                        Some(mut s) => {
                            s.def = def;
                            s
                        }
                        None => Suivi::nouveau(def, self.debut),
                    };
                    self.suivis.push(suivi);
                }
                crate::log(&format!(
                    "configuration chargee : {} service(s) ({})",
                    self.suivis.len(),
                    self.suivis.iter().filter(|s| s.def.actif).map(|s| s.def.nom.as_str()).collect::<Vec<_>>().join(", ")
                ));
                self.config_date = date;
            }
            Err(e) => {
                // Configuration invalide : on garde la precedente (jamais de trou de surveillance).
                if self.config_date != date {
                    crate::log(&format!("ALERTE configuration des services invalide, ancienne conservee : {e}"));
                    self.config_date = date;
                }
            }
        }
    }

    /// Un passage de supervision sur tous les services actifs.
    pub async fn passage(&mut self, alertes: &mut Vec<String>) {
        self.recharger();
        // Observations en parallele : un service bloque (sante qui expire au
        // bout de 10 s) ne retarde ni les autres ni l'ecriture de l'etat.
        let observations = futures_util::future::join_all(self.suivis.iter().map(|s| {
            let client = self.client.clone();
            let (actif, url, port) = (s.def.actif, s.def.sante.clone(), s.def.port);
            async move {
                if !actif {
                    return (false, false);
                }
                let ok = matches!(client.get(&url).send().await, Ok(r) if r.status().is_success());
                (ok, ok || port_ouvert(port).await)
            }
        }))
        .await;
        let maintenant = Instant::now();
        for (i, (sante_ok, port_ouvert)) in observations.into_iter().enumerate() {
            if !self.suivis[i].def.actif {
                self.suivis[i].changer(Etat::Desactive);
                continue;
            }
            let s = &mut self.suivis[i];
            // Notre processus s'est-il termine ?
            if let Some(enfant) = s.enfant.as_mut() {
                if let Ok(Some(code)) = enfant.try_wait() {
                    s.message = format!("processus termine ({code})");
                    crate::log(&format!("{} : {}", s.def.nom, s.message));
                    s.enfant = None;
                }
            }
            let en_demarrage = s.enfant.is_some()
                && s.lance_a.is_some_and(|t| maintenant.duration_since(t) < Duration::from_secs(s.def.delai_demarrage_s));
            let constat = Constat {
                sante_ok,
                port_ouvert,
                en_demarrage,
                relance_permise: maintenant >= s.prochaine_relance,
            };
            let (etat, echecs, action) = decider(&constat, s.echecs, s.def.echecs_avant_arret.max(1));
            s.echecs = echecs;
            if etat == Etat::Ok {
                let depuis = *s.ok_depuis.get_or_insert(maintenant);
                if maintenant.duration_since(depuis) >= STABLE_APRES {
                    s.tentatives = 0;
                }
                if s.alerte_envoyee {
                    alertes.push(format!("{} retabli", s.def.libelle_ou_nom()));
                    s.alerte_envoyee = false;
                }
                s.message.clear();
            } else {
                s.ok_depuis = None;
            }
            let etat = if etat != Etat::Ok && etat != Etat::Demarrage && s.tentatives >= TENTATIVES_ALERTE {
                Etat::EnEchec
            } else {
                etat
            };
            s.changer(etat);
            if etat == Etat::EnEchec && !s.alerte_envoyee {
                alertes.push(format!(
                    "{} en echec apres {} tentatives : {}",
                    s.def.libelle_ou_nom(),
                    s.tentatives,
                    if s.message.is_empty() { "aucune reponse" } else { &s.message }
                ));
                s.alerte_envoyee = true;
            }
            match action {
                Action::Rien => {}
                Action::Lancer => self.relancer(i, false).await,
                Action::TuerPuisLancer => self.relancer(i, true).await,
            }
        }
        self.ecrire_etat();
    }

    async fn relancer(&mut self, i: usize, tuer: bool) {
        let racine = self.racine.clone();
        let s = &mut self.suivis[i];
        if tuer {
            crate::log(&format!("{} : bloque (port {} ouvert, sante KO) -> arret force", s.def.nom, s.def.port));
            let pids = match s.pid() {
                Some(pid) => vec![pid],
                None => pids_a_tuer(&s.def).await,
            };
            if pids.is_empty() {
                s.message = format!("port {} tenu par un processus etranger : non tue", s.def.port);
                crate::log(&format!("{} : {}", s.def.nom, s.message));
            }
            for pid in pids {
                crate::log(&format!("{} : arret de l'arbre du pid {pid}", s.def.nom));
                tuer_arbre(pid).await;
            }
            s.enfant = None;
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
        s.tentatives += 1;
        s.prochaine_relance = Instant::now() + espacement(s.tentatives);
        s.relances += 1;
        s.derniere_relance = Some(chrono::Local::now());
        match lancer(&s.def, &racine) {
            Ok(enfant) => {
                crate::log(&format!(
                    "{} : lance (pid {}, tentative {})",
                    s.def.nom,
                    enfant.id().unwrap_or(0),
                    s.tentatives
                ));
                s.enfant = Some(enfant);
                s.lance_a = Some(Instant::now());
                s.changer(Etat::Demarrage);
            }
            Err(e) => {
                s.message = format!("lancement impossible : {e}");
                crate::log(&format!("{} : {}", s.def.nom, s.message));
            }
        }
    }

    pub fn etat_json(&self) -> Value {
        let services: Vec<Value> = self
            .suivis
            .iter()
            .map(|s| {
                json!({
                    "nom": s.def.nom,
                    "libelle": s.def.libelle_ou_nom(),
                    "port": s.def.port,
                    "etat": s.etat.texte(),
                    "depuis": s.depuis.to_rfc3339(),
                    "gere_par": if s.enfant.is_some() { "superviseur" } else if s.etat == Etat::Ok { "externe" } else { "" },
                    "pid": s.pid(),
                    "relances": s.relances,
                    "derniere_relance": s.derniere_relance.map(|d| d.to_rfc3339()),
                    "message": s.message,
                })
            })
            .collect();
        json!({
            "maj": chrono::Local::now().to_rfc3339(),
            "superviseur_pid": std::process::id(),
            "services": services,
        })
    }

    /// Ecriture atomique (fichier temporaire puis renommage) : le serveur ne
    /// lit jamais un JSON a moitie ecrit.
    fn ecrire_etat(&self) {
        let dossier = self.racine.join("logs");
        let tmp = dossier.join("services_etat.json.tmp");
        let cible = dossier.join("services_etat.json");
        let texte = serde_json::to_string_pretty(&self.etat_json()).unwrap_or_default();
        if std::fs::write(&tmp, texte).is_ok() {
            let _ = std::fs::rename(&tmp, &cible);
        }
    }
}

impl Definition {
    fn libelle_ou_nom(&self) -> &str {
        if self.libelle.is_empty() {
            &self.nom
        } else {
            &self.libelle
        }
    }
}

async fn port_ouvert(port: u16) -> bool {
    tokio::task::spawn_blocking(move || {
        let adresse = std::net::SocketAddr::from(([127, 0, 0, 1], port));
        std::net::TcpStream::connect_timeout(&adresse, Duration::from_secs(2)).is_ok()
    })
    .await
    .unwrap_or(false)
}

/// Ouvre le journal en ajout (archive en `.1` au-dela de 50 Mo).
fn ouvrir_journal(racine: &Path, nom: &str) -> std::io::Result<std::fs::File> {
    let chemin = racine.join("logs").join(nom);
    if std::fs::metadata(&chemin).map(|m| m.len() > JOURNAL_MAX).unwrap_or(false) {
        let _ = std::fs::rename(&chemin, chemin.with_extension("log.1"));
    }
    std::fs::OpenOptions::new().create(true).append(true).open(chemin)
}

/// Lance un service sans fenetre. Le processus survit a l'arret du
/// superviseur (pas de kill_on_drop) : un redeploiement du superviseur ne
/// coupe pas les services, le nouveau les adopte.
fn lancer(def: &Definition, racine: &Path) -> std::io::Result<tokio::process::Child> {
    let mut c = localai_core::processus::commande(&def.commande);
    c.args(&def.args).envs(&def.env).stdin(std::process::Stdio::null());
    if let Some(d) = &def.dossier {
        c.current_dir(d);
    }
    match def.journal.as_deref().map(|n| ouvrir_journal(racine, n)) {
        Some(Ok(f)) => {
            let f2 = f.try_clone()?;
            c.stdout(f).stderr(f2);
        }
        _ => {
            c.stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
        }
    }
    c.spawn()
}

/// Tue un processus et ses descendants (le python.exe d'un venv n'est qu'un
/// lanceur : l'interpreteur reel est son enfant et tient le port).
async fn tuer_arbre(pid: u32) {
    // 0 (inactif) et 4 (System) peuvent apparaitre comme proprietaires d'un
    // socket orphelin : le garde-fou est dans localai_core.
    localai_core::processus::tuer_arbre(pid).await;
}

/// Signature d'un service dans une ligne de commande : ses arguments tels que
/// Windows les ecrit, ou, sans argument, le chemin de l'executable.
pub fn motif_commande(def: &Definition) -> String {
    let motif = if def.args.is_empty() {
        def.commande.clone()
    } else {
        def.args
            .iter()
            .map(|a| if a.contains(' ') { format!("\"{a}\"") } else { a.clone() })
            .collect::<Vec<_>>()
            .join(" ")
    };
    motif.to_lowercase()
}

/// PID d'un service lance hors du superviseur (adopte puis bloque) :
/// 1. le processus qui ecoute sur son port, si son executable porte le nom
///    attendu ;
/// 2. sinon les processus de ce nom dont la ligne de commande contient la
///    signature du service.
///
/// Un programme etranger n'est jamais designe.
async fn pids_a_tuer(def: &Definition) -> Vec<u32> {
    let Some(nom) = Path::new(&def.commande).file_name().map(|n| n.to_string_lossy().to_string()) else {
        return Vec::new();
    };
    let echapper = |s: &str| s.replace('\'', "''");
    let script = format!(
        "$n = '{nom}'; $m = '{motif}'; $t = @(); \
         $c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; \
         if ($c) {{ $q = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; \
           if ($q -and $q.Path -and ((Split-Path $q.Path -Leaf) -ieq $n)) {{ $t += $c.OwningProcess }} }}; \
         if (-not $t) {{ $t = @(Get-CimInstance Win32_Process | \
           Where-Object {{ ($_.Name -ieq $n) -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($m) }} | ForEach-Object {{ $_.ProcessId }}) }}; \
         $t -join ','",
        nom = echapper(&nom),
        motif = echapper(&motif_commande(def)),
        port = def.port,
    );
    let Some(texte) = crate::sortie("powershell", &["-NoProfile", "-Command", &script]).await else {
        return Vec::new();
    };
    texte.split(',').filter_map(|p| p.trim().parse().ok()).filter(|&p| p > 4).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn constat(sante_ok: bool, port_ouvert: bool, en_demarrage: bool, relance_permise: bool) -> Constat {
        Constat { sante_ok, port_ouvert, en_demarrage, relance_permise }
    }

    #[test]
    fn sain_remet_a_zero() {
        assert_eq!(decider(&constat(true, true, false, false), 5, 8), (Etat::Ok, 0, Action::Rien));
    }

    #[test]
    fn absent_lance_tout_de_suite_sauf_espacement() {
        assert_eq!(decider(&constat(false, false, false, true), 0, 8), (Etat::Arrete, 0, Action::Lancer));
        assert_eq!(decider(&constat(false, false, false, false), 0, 8), (Etat::Arrete, 0, Action::Rien));
    }

    #[test]
    fn demarrage_en_cours_laisse_faire() {
        assert_eq!(decider(&constat(false, false, true, true), 3, 8), (Etat::Demarrage, 0, Action::Rien));
    }

    #[test]
    fn bloque_tue_seulement_au_seuil() {
        let c = constat(false, true, false, true);
        assert_eq!(decider(&c, 0, 3), (Etat::Degrade, 1, Action::Rien));
        assert_eq!(decider(&c, 1, 3), (Etat::Degrade, 2, Action::Rien));
        assert_eq!(decider(&c, 2, 3), (Etat::Degrade, 0, Action::TuerPuisLancer));
    }

    #[test]
    fn espacement_croissant_plafonne() {
        assert_eq!(espacement(1), Duration::from_secs(10));
        assert_eq!(espacement(2), Duration::from_secs(20));
        assert_eq!(espacement(4), Duration::from_secs(80));
        assert_eq!(espacement(20), Duration::from_secs(300));
    }

    #[test]
    fn config_du_depot_valide() {
        let chemin = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/services.json");
        let defs = lire_config(&chemin).expect("config/services.json invalide");
        assert!(defs.iter().any(|d| d.nom == "serveur" && d.port == 8320 && d.actif));
        assert!(defs.iter().all(|d| d.sante.starts_with("http://127.0.0.1:")));
    }

    #[test]
    fn motif_de_ligne_de_commande() {
        let mut d: Definition = serde_json::from_str(
            r#"{"nom":"r","commande":"E:\\ia\\.venv\\Scripts\\python.exe","args":["-u","E:\\ia\\rerank_server.py"],"port":1,"sante":"http://127.0.0.1:1/"}"#,
        )
        .unwrap();
        assert_eq!(motif_commande(&d), r"-u e:\ia\rerank_server.py");
        d.args = vec!["main.py".into(), "C:\\Mes Documents\\x".into()];
        assert_eq!(motif_commande(&d), "main.py \"c:\\mes documents\\x\"");
        d.args.clear();
        assert_eq!(motif_commande(&d), r"e:\ia\.venv\scripts\python.exe");
    }

    #[test]
    fn config_refuse_les_ports_en_double() {
        let dir = std::env::temp_dir().join(format!("neo_services_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("services.json");
        std::fs::write(&f, r#"{"services":[
            {"nom":"a","commande":"x","port":1,"sante":"http://127.0.0.1:1/"},
            {"nom":"b","commande":"y","port":1,"sante":"http://127.0.0.1:1/"}]}"#).unwrap();
        assert!(lire_config(&f).unwrap_err().contains("port 1"));
        // Fichier enregistre avec BOM (Bloc-notes) : accepte.
        std::fs::write(&f, "\u{feff}{\"services\":[{\"nom\":\"a\",\"commande\":\"x\",\"port\":1,\"sante\":\"http://127.0.0.1:1/\"}]}").unwrap();
        assert_eq!(lire_config(&f).unwrap().len(), 1);
        let _ = std::fs::remove_dir_all(dir);
    }
}
