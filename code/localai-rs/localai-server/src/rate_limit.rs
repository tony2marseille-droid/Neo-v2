//! Limitation de debit par adresse IP (fenetre glissante de 60 s).
//!
//! Protege les routes qui saturent le GPU : `/api/chat`, `/api/outil/execute`
//! et les equivalents OpenAI. Sans limite, un seul client (script en boucle,
//! client OpenAI mal configure) peut monopoliser la carte graphique.
//!
//! Reglage : `rate_limit_par_minute` dans `app_settings`.
//! `0` desactive completement la limitation.

use std::collections::{HashMap, VecDeque};
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub struct RateLimiter {
    fenetres: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    max_par_minute: u32,
}

impl RateLimiter {
    pub fn new(max_par_minute: u32) -> Self {
        Self {
            fenetres: Mutex::new(HashMap::new()),
            max_par_minute,
        }
    }

    pub fn max(&self) -> u32 {
        self.max_par_minute
    }

    /// Vrai si la requete est autorisee. Purge la fenetre au passage :
    /// la memoire reste bornee par le nombre d'IP actives dans la minute.
    pub fn autorise(&self, ip: IpAddr) -> bool {
        if self.max_par_minute == 0 {
            return true;
        }
        let Ok(mut fenetres) = self.fenetres.lock() else {
            // Verrou empoisonne : on ne bloque pas le service pour autant.
            return true;
        };
        let maintenant = Instant::now();
        let fenetre = Duration::from_secs(60);

        // Nettoyage opportuniste : evite que la table grossisse sans fin si
        // un client change d'adresse (IPv6 notamment).
        if fenetres.len() > 1024 {
            fenetres.retain(|_, v| {
                v.back().map(|t| maintenant.duration_since(*t) < fenetre).unwrap_or(false)
            });
        }

        let q = fenetres.entry(ip).or_default();
        while let Some(t) = q.front() {
            if maintenant.duration_since(*t) >= fenetre {
                q.pop_front();
            } else {
                break;
            }
        }
        if q.len() >= self.max_par_minute as usize {
            return false;
        }
        q.push_back(maintenant);
        true
    }

    /// Secondes avant la prochaine place disponible (pour `Retry-After`).
    pub fn attente(&self, ip: IpAddr) -> u64 {
        let Ok(fenetres) = self.fenetres.lock() else {
            return 1;
        };
        fenetres
            .get(&ip)
            .and_then(|q| q.front())
            .map(|t| {
                let ecoule = Instant::now().duration_since(*t).as_secs();
                60u64.saturating_sub(ecoule).max(1)
            })
            .unwrap_or(1)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().expect("adresse de test valide")
    }

    #[test]
    fn zero_desactive_la_limite() {
        let l = RateLimiter::new(0);
        for _ in 0..10_000 {
            assert!(l.autorise(ip("127.0.0.1")));
        }
    }

    #[test]
    fn bloque_au_dela_du_plafond() {
        let l = RateLimiter::new(3);
        let a = ip("192.168.50.10");
        assert!(l.autorise(a));
        assert!(l.autorise(a));
        assert!(l.autorise(a));
        assert!(!l.autorise(a), "la quatrieme requete doit etre refusee");
        // Une autre adresse garde son propre quota.
        assert!(l.autorise(ip("192.168.50.11")));
    }

    #[test]
    fn attente_bornee_entre_1_et_60() {
        let l = RateLimiter::new(1);
        let a = ip("10.0.0.1");
        assert!(l.autorise(a));
        assert!(!l.autorise(a));
        let s = l.attente(a);
        assert!((1..=60).contains(&s), "Retry-After hors bornes : {s}");
        // Sans historique, l'attente minimale reste 1 s.
        assert_eq!(l.attente(ip("10.0.0.2")), 1);
    }
}
