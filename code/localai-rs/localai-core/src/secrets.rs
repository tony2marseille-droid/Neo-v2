//! Secrets au repos : chiffrement authentifie (ChaCha20-Poly1305) des
//! valeurs sensibles dans `app_settings`, via une cle machine unique stockee
//! dans `data/.neo_keystore`.
//!
//! Principe de compatibilite : une valeur sans le prefixe `enc:v1:` est
//! consideree comme texto clair (anciennes versions). La migration a lieu au
//! demarrage du serveur Rust. Si aucune cle n'est disponible, tout repli vers
//! le passage en clair : jamais de perte de donnees ni de corruption.

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use ring::aead::{self, Nonce, LessSafeKey, UnboundKey};
use ring::rand::{SecureRandom, SystemRandom};
use std::path::PathBuf;

pub const PREFIX: &str = "enc:v1:";

/// Cle machine : renvoyee seulement si elle existe ou a pu etre creee.
/// Le chemin est dans le meme dossier que la base pour qu'un transfert de la
/// base n'emporte pas la cle (elle reste locale au poste).
pub fn keystore_path(db_path: &std::path::Path) -> PathBuf {
    db_path.parent().unwrap_or(db_path).join(".neo_keystore")
}

fn lire_cle(db_path: &std::path::Path) -> Option<[u8; 32]> {
    let chemin = keystore_path(db_path);
    let octets = std::fs::read(chemin).ok()?;
    if octets.len() != 32 {
        return None;
    }
    let mut cle = [0u8; 32];
    cle.copy_from_slice(&octets);
    Some(cle)
}

fn creer_cle(db_path: &std::path::Path) -> Option<[u8; 32]> {
    let chemin = keystore_path(db_path);
    if let Some(cle) = lire_cle(db_path) {
        return Some(cle);
    }
    let rng = SystemRandom::new();
    let mut cle = [0u8; 32];
    if rng.fill(&mut cle).is_err() {
        return None;
    }
    let temp = chemin.with_extension("tmp");
    if std::fs::write(&temp, cle).is_err() {
        return None;
    }
    let _ = std::fs::rename(&temp, &chemin);
    lire_cle(db_path)
}

/// Cle optionnelle : en lecture on accepte l'absence (valeur renvoyee telle
/// quelle), en ecriture on tente la creation.
pub fn cle(db_path: &std::path::Path, creer: bool) -> Option<[u8; 32]> {
    let c = lire_cle(db_path);
    if c.is_some() {
        return c;
    }
    if creer {
        return creer_cle(db_path);
    }
    None
}

/// Vrai si la cle designe un secret qui doit etre au repos sous forme
/// cifree. Motifs + liste explicite, alignee avec `routes/settings.rs`.
pub fn est_secret(cle_setting: &str) -> bool {
    const CLES: &[&str] = &[
        "api_token",
        "webui_pass",
        "telegram_token",
        "livekit_api_key",
        "livekit_api_secret",
        "connecteur_meshy_token",
        "connecteur_telegram_token",
        "connecteur_discord_token",
    ];
    if CLES.contains(&cle_setting) {
        return true;
    }
    if cle_setting.starts_with("pubapi_key.") {
        return true;
    }
    if cle_setting.starts_with("provider_") && cle_setting.ends_with("_key") {
        return true;
    }
    let minuscule = cle_setting.to_ascii_lowercase();
    ["token", "secret", "password", "passwd", "_pass", "apikey", "api_key", "cle_api"]
        .iter()
        .any(|motif| minuscule.contains(motif))
}

/// Chiffre la valeur si le setting est secreet et une cle machine est
/// disponible. Sinon passage en clair (pas de perte).
pub fn chiffrer(db_path: &std::path::Path, cle_setting: &str, valeur: &str) -> String {
    if !est_secret(cle_setting) || valeur.is_empty() || valeur.starts_with(PREFIX) {
        return valeur.to_string();
    }
    let cle_bytes = match cle(db_path, true) {
        Some(c) => c,
        None => return valeur.to_string(),
    };
    let unbound = match UnboundKey::new(&aead::CHACHA20_POLY1305, &cle_bytes) {
        Ok(u) => u,
        Err(_) => return valeur.to_string(),
    };
    let key = LessSafeKey::new(unbound);
    let mut nonce_bytes = [0u8; 12];
    if SystemRandom::new().fill(&mut nonce_bytes).is_err() {
        return valeur.to_string();
    }
    let nonce = Nonce::assume_unique_for_key(nonce_bytes);
    let mut in_out = valeur.as_bytes().to_vec();
    let tag = match key.seal_in_place_separate_tag(nonce, aead::Aad::empty(), &mut in_out) {
        Ok(t) => t,
        Err(_) => return valeur.to_string(),
    };
    let mut combined = nonce_bytes.to_vec();
    combined.extend_from_slice(&in_out);
    combined.extend_from_slice(tag.as_ref());
    format!("{}{}", PREFIX, STANDARD.encode(combined))
}

/// Dechiffre une valeur selon le setting et la cle machine. Les valeurs en
/// clair passent telles quelles.
pub fn dechiffrer(db_path: &std::path::Path, cle_setting: &str, valeur: &str) -> String {
    if !est_secret(cle_setting) || !valeur.starts_with(PREFIX) {
        return valeur.to_string();
    }
    let cle_bytes = match cle(db_path, false) {
        Some(c) => c,
        None => return valeur.to_string(),
    };
    let combined = match STANDARD.decode(&valeur[PREFIX.len()..]) {
        Ok(c) => c,
        Err(_) => return valeur.to_string(),
    };
    if combined.len() < 12 + 16 {
        return valeur.to_string();
    }
    let (nonce_bytes, reste) = combined.split_at(12);
    let mut nonce_arr = [0u8; 12];
    nonce_arr.copy_from_slice(nonce_bytes);
    let nonce = Nonce::assume_unique_for_key(nonce_arr);
    let unbound = match UnboundKey::new(&aead::CHACHA20_POLY1305, &cle_bytes) {
        Ok(u) => u,
        Err(_) => return valeur.to_string(),
    };
    let key = LessSafeKey::new(unbound);
    let mut in_out = reste.to_vec();
    match key.open_in_place(nonce, aead::Aad::empty(), &mut in_out) {
        Ok(plain) => String::from_utf8(plain.to_vec()).unwrap_or_else(|_| valeur.to_string()),
        Err(_) => valeur.to_string(),
    }
}

/// Migre les valeurs en clair des secrets vers la forme cifree. Idempotent.
pub fn migrer(db_path: &std::path::Path, db: &crate::db::Db) {
    // cree la cle machine si possible, sinon rien a faire
    if cle(db_path, true).is_none() {
        return;
    }
    let rows = match db.query_all_json("SELECT key, value FROM app_settings", &[]) {
        Ok(r) => r,
        Err(_) => return,
    };
    for r in rows {
        let cle_setting = r.get("key").and_then(|x| x.as_str()).unwrap_or("");
        let valeur = r.get("value").and_then(|x| x.as_str()).unwrap_or("");
        if est_secret(cle_setting) && !valeur.is_empty() && !valeur.starts_with(PREFIX) {
            let chiffree = chiffrer(db_path, cle_setting, valeur);
            let _ = db.execute(
                "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                &[&cle_setting, &chiffree.as_str()],
            );
        }
    }
}
