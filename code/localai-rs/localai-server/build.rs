//! Grave l'identite du binaire dans les variables lues par `/api/version`.
//!
//! Ordre de resolution du commit :
//! 1. `NEO_GIT_COMMIT` (injecte par le script de deploiement) ;
//! 2. fichier `COMMIT` a la racine du workspace (pose lors de la synchro
//!    depuis le miroir git, car l'espace de travail de production n'est pas un depot) ;
//! 3. `git rev-parse` si le workspace est versionne ;
//! 4. `inconnu`.
//!
//! La date de compilation est calculee ici : `env!("...")` ne peut pas
//! appeler l'horloge a l'execution, et une date figee a la main mentirait
//! des le premier rebuild.

use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

/// Date UTC au format RFC 3339 a partir de secondes Unix.
fn iso_utc(secondes: u64) -> String {
    let jours = (secondes / 86_400) as i64;
    let reste = secondes % 86_400;
    let heures = reste / 3_600;
    let minutes = (reste % 3_600) / 60;
    let secs = reste % 60;

    // Jours depuis 1970 -> calendrier gregorien (algorithme de H. Hinnant).
    let z = jours + 719_468;
    let ere = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - ere * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let annee = yoe + ere * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let jour = doy - (153 * mp + 2) / 5 + 1;
    let mois = if mp < 10 { mp + 3 } else { mp - 9 };
    let annee = if mois <= 2 { annee + 1 } else { annee };

    format!("{annee:04}-{mois:02}-{jour:02}T{heures:02}:{minutes:02}:{secs:02}Z")
}

/// Contenu non vide d'un fichier a la racine du workspace.
fn lire_racine(nom: &str) -> Option<String> {
    let manifeste = std::env::var("CARGO_MANIFEST_DIR").ok()?;
    let chemin = std::path::Path::new(&manifeste).parent()?.join(nom);
    let texte = std::fs::read_to_string(chemin).ok()?;
    let texte = texte.trim().to_string();
    if texte.is_empty() {
        None
    } else {
        Some(texte)
    }
}

fn commit_git() -> Option<String> {
    let manifeste = std::env::var("CARGO_MANIFEST_DIR").ok()?;
    let racine = std::path::Path::new(&manifeste).parent()?.to_path_buf();
    let sortie = Command::new("git")
        .arg("-C")
        .arg(racine)
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()?;
    if !sortie.status.success() {
        return None;
    }
    let texte = String::from_utf8_lossy(&sortie.stdout).trim().to_string();
    if texte.is_empty() {
        None
    } else {
        Some(texte)
    }
}

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=../COMMIT");
    println!("cargo:rerun-if-env-changed=NEO_GIT_COMMIT");
    println!("cargo:rerun-if-env-changed=NEO_BUILD_DATE");

    let commit = std::env::var("NEO_GIT_COMMIT")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .or_else(|| lire_racine("COMMIT"))
        .or_else(commit_git)
        .unwrap_or_else(|| "inconnu".to_string());
    println!("cargo:rustc-env=NEO_GIT_COMMIT={commit}");

    let date = std::env::var("NEO_BUILD_DATE")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| {
            let secondes = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            iso_utc(secondes)
        });
    println!("cargo:rustc-env=NEO_BUILD_DATE={date}");
}
