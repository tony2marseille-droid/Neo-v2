//! Reglages de la machine : `neo.toml`.
//!
//! Un seul fichier decrit ce qui change d'une installation a l'autre
//! (dossiers, ports, outils externes). Il alimente les variables
//! d'environnement que Neo lit deja (`LOCALAI_ROOT`, `LOCALAI_SD_DIR`...) :
//! les processus enfants (pont Python, services) en heritent.
//!
//! Ordre de priorite pour chaque reglage :
//! 1. variable d'environnement deja definie (jamais ecrasee) ;
//! 2. valeur de `neo.toml` ;
//! 3. defaut du code, relatif a la racine d'installation.
//!
//! Recherche du fichier : `NEO_CONFIG`, puis `<LOCALAI_ROOT>\neo.toml`, puis
//! le dossier de l'executable et chacun de ses parents.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// (section, cle) -> variables d'environnement alimentees.
pub const CORRESPONDANCES: &[(&str, &str, &[&str])] = &[
    ("chemins", "racine", &["LOCALAI_ROOT"]),
    ("chemins", "donnees", &["LOCALAI_DATA", "LOCALAI_DATA_DIR"]),
    ("chemins", "journal", &["LOCALAI_LOG"]),
    ("chemins", "espace_travail", &["NEO_ESPACE_TRAVAIL"]),
    ("chemins", "sd", &["LOCALAI_SD_DIR"]),
    ("chemins", "retouche", &["NEO_SD_RETOUCHE"]),
    ("chemins", "esrgan", &["LOCALAI_ESRGAN_DIR"]),
    ("chemins", "voix", &["NEO_VOIX_DIR"]),
    ("chemins", "parakeet", &["NEO_PARAKEET_DIR"]),
    ("chemins", "hunyuan", &["LOCALAI_HUNYUAN21_DIR"]),
    ("chemins", "modele_visages", &["NEO_YUNET"]),
    ("chemins", "cache_hf", &["HF_HOME"]),
    ("reseau", "hote", &["LOCALAI_HOST"]),
    ("reseau", "port", &["LOCALAI_PORT"]),
    ("ollama", "url", &["OLLAMA_URL"]),
    ("ollama", "executable", &["OLLAMA_BIN"]),
    ("ollama", "modeles", &["OLLAMA_MODELS"]),
    ("outils", "python", &["NEO_PYTHON"]),
    ("outils", "ffmpeg", &["NEO_FFMPEG"]),
    ("outils", "blender", &["LOCALAI_BLENDER"]),
    ("outils", "openscad", &["LOCALAI_OPENSCAD"]),
    ("outils", "trancheur", &["LOCALAI_SLICER"]),
    ("materiel", "palier_force", &["LOCALAI_FORCE_TIER"]),
];

static APPLIQUE: OnceLock<Option<PathBuf>> = OnceLock::new();

/// Chemin du `neo.toml` en vigueur, s'il existe.
pub fn fichier() -> Option<PathBuf> {
    if let Ok(c) = std::env::var("NEO_CONFIG") {
        let p = PathBuf::from(c);
        return p.is_file().then_some(p);
    }
    if let Ok(r) = std::env::var("LOCALAI_ROOT") {
        let p = Path::new(&r).join("neo.toml");
        if p.is_file() {
            return Some(p);
        }
    }
    let exe = std::env::current_exe().ok()?;
    exe.ancestors().skip(1).map(|d| d.join("neo.toml")).find(|p| p.is_file())
}

/// Valeur TOML simple -> texte (chaines, nombres, booleens).
fn en_texte(v: &toml::Value) -> Option<String> {
    match v {
        toml::Value::String(s) => Some(s.clone()),
        toml::Value::Integer(i) => Some(i.to_string()),
        toml::Value::Float(f) => Some(f.to_string()),
        toml::Value::Boolean(b) => Some(b.to_string()),
        _ => None,
    }
}

/// Variables a definir d'apres le contenu d'un `neo.toml` situe dans `dossier`.
/// Pur (ne touche pas l'environnement) : teste a part.
pub fn variables(contenu: &str, dossier: &Path) -> Result<Vec<(String, String)>, String> {
    let doc: toml::Table = contenu.parse().map_err(|e| format!("neo.toml invalide : {e}"))?;
    let lire = |section: &str, cle: &str| -> Option<String> {
        doc.get(section)?.as_table()?.get(cle).and_then(en_texte).filter(|s| !s.trim().is_empty())
    };
    let racine = lire("chemins", "racine").unwrap_or_else(|| dossier.to_string_lossy().into_owned());
    let developper = |s: String| s.replace("{racine}", &racine);
    let mut sortie = vec![("LOCALAI_ROOT".to_string(), racine.clone())];
    for (section, cle, vars) in CORRESPONDANCES {
        if (*section, *cle) == ("chemins", "racine") {
            continue;
        }
        if let Some(v) = lire(section, cle) {
            for var in *vars {
                sortie.push((var.to_string(), developper(v.clone())));
            }
        }
    }
    // Reglages avances : [variables] NOM = "valeur"
    if let Some(t) = doc.get("variables").and_then(|v| v.as_table()) {
        for (nom, v) in t {
            if let Some(s) = en_texte(v) {
                sortie.push((nom.clone(), developper(s)));
            }
        }
    }
    Ok(sortie)
}

/// Lit `neo.toml` (une seule fois) et definit les variables d'environnement
/// encore absentes. Sans fichier : rien ne change. Fichier invalide : message
/// sur la sortie d'erreur, rien ne change.
pub fn appliquer() -> Option<PathBuf> {
    APPLIQUE
        .get_or_init(|| {
            let chemin = fichier()?;
            let contenu = std::fs::read_to_string(&chemin).ok()?;
            let dossier = chemin.parent().map(Path::to_path_buf).unwrap_or_default();
            match variables(&contenu, &dossier) {
                Ok(vars) => {
                    for (nom, valeur) in vars {
                        if std::env::var_os(&nom).is_none() {
                            std::env::set_var(&nom, valeur);
                        }
                    }
                    Some(chemin)
                }
                Err(e) => {
                    eprintln!("{e} ({})", chemin.display());
                    None
                }
            }
        })
        .clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trouve<'a>(v: &'a [(String, String)], nom: &str) -> Option<&'a str> {
        v.iter().find(|(n, _)| n == nom).map(|(_, x)| x.as_str())
    }

    #[test]
    fn racine_vide_donne_le_dossier_du_fichier() {
        let v = variables("[chemins]\nracine = \"\"\n", Path::new("/opt/neo")).unwrap();
        assert_eq!(trouve(&v, "LOCALAI_ROOT"), Some("/opt/neo"));
    }

    #[test]
    fn correspondances_et_espace_reserve() {
        let toml = r#"
[chemins]
racine = "D:/Neo"
sd = "{racine}/outils/sd"
cache_hf = "F:/hf"
[reseau]
port = 9000
[variables]
NEO_IMAGE_VERIF = "0"
"#;
        let v = variables(toml, Path::new("/ailleurs")).unwrap();
        assert_eq!(trouve(&v, "LOCALAI_ROOT"), Some("D:/Neo"));
        assert_eq!(trouve(&v, "LOCALAI_SD_DIR"), Some("D:/Neo/outils/sd"));
        assert_eq!(trouve(&v, "HF_HOME"), Some("F:/hf"));
        assert_eq!(trouve(&v, "LOCALAI_PORT"), Some("9000"));
        assert_eq!(trouve(&v, "NEO_IMAGE_VERIF"), Some("0"));
        assert_eq!(trouve(&v, "LOCALAI_BLENDER"), None);
    }

    #[test]
    fn fichier_invalide_refuse_proprement() {
        assert!(variables("[chemins\nracine=", Path::new("/x")).is_err());
    }
}
