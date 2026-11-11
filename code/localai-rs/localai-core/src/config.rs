use std::path::PathBuf;

pub const APP_NAME: &str = "Neo";
pub const APP_VERSION: &str = "0.4.1";

fn env_str(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_path(key: &str, default: PathBuf) -> PathBuf {
    std::env::var(key).map(PathBuf::from).unwrap_or(default)
}

/// Racine d'installation : `LOCALAI_ROOT` (eventuellement fournie par
/// `neo.toml`, voir `neoconf`), sinon le dossier qui contient `neo.toml`,
/// sinon le dossier de l'executable. Aucun chemin de machine en dur.
pub fn default_root() -> PathBuf {
    let fichier = crate::neoconf::appliquer();
    if let Ok(r) = std::env::var("LOCALAI_ROOT") {
        return PathBuf::from(r);
    }
    if let Some(dossier) = fichier.as_deref().and_then(std::path::Path::parent) {
        return dossier.to_path_buf();
    }
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(std::path::Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

#[derive(Debug, Clone)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    pub ollama_host: String,
    pub root_dir: PathBuf,
    pub data_dir: PathBuf,
    pub default_model: String,
}

impl Settings {
    pub fn load() -> Self {
        let root = default_root();
        let port: u16 = std::env::var("LOCALAI_PORT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(8320);
        Self {
            // Securite : ecoute locale par defaut. L'acces depuis le reseau local
            // se choisit explicitement (neo.toml : [reseau] hote = "0.0.0.0").
            host: env_str("LOCALAI_HOST", "127.0.0.1"),
            port,
            ollama_host: env_str("OLLAMA_URL", "http://127.0.0.1:11434"),
            data_dir: env_path("LOCALAI_DATA", root.join("data")),
            default_model: env_str("LOCALAI_MODEL", "localai-prog-latest"),
            root_dir: root,
        }
    }

    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("localai.db")
    }

    pub fn ensure_dirs(&self) {
        for d in [&self.data_dir, &self.data_dir.join("tts")] {
            let _ = std::fs::create_dir_all(d);
        }
    }
}
