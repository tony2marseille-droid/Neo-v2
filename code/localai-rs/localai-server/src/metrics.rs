//! Compteurs d'echec des maillons externes, exposes sur `/api/health`.
//!
//! Constat de l'audit du 29/10 : une panne d'embedding (Ollama) a ete avalee
//! par le repli silencieux du RAG. Le chat repondait normalement, mais sans
//! contexte documentaire, et rien ne le signalait. Ces compteurs rendent la
//! degradation visible sans fouiller les journaux.
//!
//! Ils sont volontairement globaux (OnceLock) : `rag_block`, `rerank` et le
//! pont Ollama sont appeles depuis plusieurs modules et un parametre
//! supplementaire traverserait toute la chaine sans rien apporter.

use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub struct Metrics {
    pub rag_requetes: AtomicU64,
    pub rag_vides: AtomicU64,
    pub rag_echecs: AtomicU64,
    pub embed_echecs: AtomicU64,
    pub rerank_ok: AtomicU64,
    pub rerank_repli: AtomicU64,
    pub ollama_echecs: AtomicU64,
    pub rate_limited: AtomicU64,
}

static METRICS: std::sync::OnceLock<Metrics> = std::sync::OnceLock::new();

pub fn global() -> &'static Metrics {
    METRICS.get_or_init(Metrics::default)
}

macro_rules! incrementer {
    ($($nom:ident),* $(,)?) => {
        $(
            /// Incremente le compteur correspondant (relache).
            pub fn $nom() {
                global().$nom.fetch_add(1, Ordering::Relaxed);
            }
        )*
    };
}

incrementer!(
    rag_requetes,
    rag_vides,
    rag_echecs,
    embed_echecs,
    rerank_ok,
    rerank_repli,
    ollama_echecs,
    rate_limited,
);

/// Instantane JSON pour `/api/health`.
pub fn snapshot() -> serde_json::Value {
    let m = global();
    serde_json::json!({
        "rag_requetes": m.rag_requetes.load(Ordering::Relaxed),
        "rag_vides": m.rag_vides.load(Ordering::Relaxed),
        "rag_echecs": m.rag_echecs.load(Ordering::Relaxed),
        "embed_echecs": m.embed_echecs.load(Ordering::Relaxed),
        "rerank_ok": m.rerank_ok.load(Ordering::Relaxed),
        "rerank_repli": m.rerank_repli.load(Ordering::Relaxed),
        "ollama_echecs": m.ollama_echecs.load(Ordering::Relaxed),
        "rate_limited": m.rate_limited.load(Ordering::Relaxed),
    })
}

/// Vrai des qu'un maillon a echoue au moins une fois depuis le demarrage.
/// Le serveur reste `ok` (le watchdog ne doit pas le redemarrer), mais
/// l'indicateur signale une qualite degradee.
pub fn degrade() -> bool {
    let m = global();
    m.embed_echecs.load(Ordering::Relaxed) > 0
        || m.rag_echecs.load(Ordering::Relaxed) > 0
        || m.ollama_echecs.load(Ordering::Relaxed) > 0
}

/// Remet les compteurs a zero (tests).
#[cfg(test)]
pub fn reinitialiser() {
    let m = global();
    for c in [
        &m.rag_requetes,
        &m.rag_vides,
        &m.rag_echecs,
        &m.embed_echecs,
        &m.rerank_ok,
        &m.rerank_repli,
        &m.ollama_echecs,
        &m.rate_limited,
    ] {
        c.store(0, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Les compteurs sont globaux : les tests qui les touchent s'executent
    /// en serie, sinon `cargo test` (threads paralleles) les rend instables.
    static VERROU: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn compteurs_snapshot_et_degradation() {
        let _g = VERROU.lock().unwrap_or_else(|e| e.into_inner());
        reinitialiser();
        assert!(!degrade(), "aucun echec : la sante est nominale");

        rag_requetes();
        rag_requetes();
        rag_vides();
        rerank_ok();
        rerank_repli();
        rate_limited();

        let s = snapshot();
        assert_eq!(s["rag_requetes"], serde_json::json!(2));
        assert_eq!(s["rag_vides"], serde_json::json!(1));
        assert_eq!(s["rerank_ok"], serde_json::json!(1));
        assert_eq!(s["rerank_repli"], serde_json::json!(1));
        assert_eq!(s["rate_limited"], serde_json::json!(1));
        // Un repli de rerank est nominal : il ne degrade pas l'indicateur.
        assert!(!degrade());

        embed_echecs();
        assert!(degrade(), "un echec d'embedding doit etre visible");

        reinitialiser();
        assert!(!degrade());
        for (cle, valeur) in snapshot().as_object().expect("objet").iter() {
            assert_eq!(valeur, &serde_json::json!(0), "{cle} doit etre remis a zero");
        }
    }

    #[test]
    fn global_est_un_singleton() {
        assert!(std::ptr::eq(global(), global()));
    }
}

