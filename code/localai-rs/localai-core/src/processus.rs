//! Creation de processus SANS fenetre console.
//!
//! Sous Windows, un programme console (python, nvidia-smi, ffmpeg, tasklist...)
//! lance depuis un processus sans console visible ouvre sa propre fenetre :
//! chaque appel au pont Python faisait clignoter une console. Toute creation
//! de processus du projet passe par ces deux fonctions, qui posent le drapeau
//! `CREATE_NO_WINDOW` ; les enfants heritent de cette console invisible (les
//! sorties restent capturees normalement).

/// Drapeau Win32 `CREATE_NO_WINDOW`.
#[cfg(windows)]
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Commande asynchrone (tokio) sans fenetre.
pub fn commande(programme: impl AsRef<std::ffi::OsStr>) -> tokio::process::Command {
    #[allow(unused_mut)]
    let mut c = tokio::process::Command::new(programme);
    #[cfg(windows)]
    c.creation_flags(CREATE_NO_WINDOW);
    c
}

/// Tue un processus ET tous ses descendants (`taskkill /T /F`).
///
/// Tuer seulement le processus direct laisse ses enfants orphelins : le
/// 09/11/2026, un `sd-cli.exe` lance par un pont Python abandonne (delai
/// depasse) a garde 15 Go de VRAM pendant des heures et bloque toutes les
/// generations suivantes. 0 (inactif) et 4 (System) ne sont jamais vises.
pub async fn tuer_arbre(pid: u32) {
    if pid <= 4 {
        return;
    }
    let _ = commande("taskkill")
        .args(["/T", "/F", "/PID", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .await;
}

/// Commande synchrone (std) sans fenetre.
pub fn commande_std(programme: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    #[allow(unused_mut)]
    let mut c = std::process::Command::new(programme);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(CREATE_NO_WINDOW);
    }
    c
}

/// Etat de priorite du processus courant : (classe Win32, priorite disque,
/// priorite memoire). `None` hors Windows ou si la lecture echoue.
#[cfg(windows)]
pub fn etat_priorite() -> Option<(u32, u32, u32)> {
    // SAFETY : appels Win32 sur le pseudo-handle du processus courant, avec
    // des tampons de 4 octets que nous possedons.
    unsafe {
        let moi = priorite_win::GetCurrentProcess();
        let classe = priorite_win::GetPriorityClass(moi);
        let (mut disque, mut memoire) = (0u32, 0u32);
        let a = priorite_win::NtQueryInformationProcess(
            moi, priorite_win::DISQUE, (&mut disque as *mut u32).cast(), 4, std::ptr::null_mut());
        let b = priorite_win::NtQueryInformationProcess(
            moi, priorite_win::MEMOIRE, (&mut memoire as *mut u32).cast(), 4, std::ptr::null_mut());
        (classe != 0 && a == 0 && b == 0).then_some((classe, disque, memoire))
    }
}

/// Remet le processus courant en priorite NORMALE (processeur, disque,
/// memoire) ; les programmes lances ensuite en heritent.
///
/// Les services sont lances par des taches planifiees, de priorite 7 par
/// defaut : classe « inferieure a la normale », lecture disque basse, memoire
/// 2 sur 5. Mesure du 11/11/2026 : un clip video en 350 s ainsi contre 92 s en
/// priorite normale (chargement des poids 262 s contre 1,8 s). Le reglage des
/// taches (`scripts/priorite_taches.ps1`) corrige la source ; ceci protege si
/// une tache est recreee avec la priorite par defaut, ou si un service est
/// relance par un ancien superviseur. On ne fait que REMONTER vers la normale.
#[cfg(windows)]
pub fn priorite_normale() {
    if std::env::var("NEO_PRIORITE_NORMALE").map(|v| v == "0").unwrap_or(false) {
        return;
    }
    // SAFETY : memes appels que `etat_priorite`, en ecriture, sur notre propre
    // processus ; un echec laisse simplement la priorite telle quelle.
    unsafe {
        let moi = priorite_win::GetCurrentProcess();
        let classe = priorite_win::GetPriorityClass(moi);
        if classe == priorite_win::IDLE || classe == priorite_win::INFERIEURE {
            priorite_win::SetPriorityClass(moi, priorite_win::NORMALE);
        }
        for (info, normal) in [(priorite_win::DISQUE, 2u32), (priorite_win::MEMOIRE, 5u32)] {
            let mut actuel = 0u32;
            let lu = priorite_win::NtQueryInformationProcess(
                moi, info, (&mut actuel as *mut u32).cast(), 4, std::ptr::null_mut());
            if lu == 0 && actuel < normal {
                let mut v = normal;
                priorite_win::NtSetInformationProcess(moi, info, (&mut v as *mut u32).cast(), 4);
            }
        }
    }
}

/// Hors Windows : rien a faire.
#[cfg(not(windows))]
pub fn priorite_normale() {}

#[cfg(windows)]
mod priorite_win {
    use std::ffi::c_void;

    pub const IDLE: u32 = 0x40;
    pub const INFERIEURE: u32 = 0x4000;
    pub const NORMALE: u32 = 0x20;
    /// `ProcessIoPriority` et `ProcessPagePriority`.
    pub const DISQUE: u32 = 33;
    pub const MEMOIRE: u32 = 39;

    #[link(name = "kernel32")]
    extern "system" {
        pub fn GetCurrentProcess() -> *mut c_void;
        pub fn GetPriorityClass(processus: *mut c_void) -> u32;
        pub fn SetPriorityClass(processus: *mut c_void, classe: u32) -> i32;
    }

    #[link(name = "ntdll")]
    extern "system" {
        pub fn NtQueryInformationProcess(
            processus: *mut c_void, classe: u32, tampon: *mut c_void, taille: u32, rendu: *mut u32,
        ) -> i32;
        pub fn NtSetInformationProcess(
            processus: *mut c_void, classe: u32, tampon: *mut c_void, taille: u32,
        ) -> i32;
    }
}

#[cfg(all(test, windows))]
mod tests_priorite {
    use super::*;

    #[test]
    fn remontee_a_la_priorite_normale() {
        // Comme une tache planifiee de priorite 7 : classe inferieure, disque bas, memoire 2.
        unsafe {
            let moi = priorite_win::GetCurrentProcess();
            priorite_win::SetPriorityClass(moi, priorite_win::INFERIEURE);
            for (info, bas) in [(priorite_win::DISQUE, 1u32), (priorite_win::MEMOIRE, 2u32)] {
                let mut v = bas;
                priorite_win::NtSetInformationProcess(moi, info, (&mut v as *mut u32).cast(), 4);
            }
        }
        assert_eq!(etat_priorite(), Some((priorite_win::INFERIEURE, 1, 2)));
        priorite_normale();
        assert_eq!(etat_priorite(), Some((priorite_win::NORMALE, 2, 5)));
        // Deja normale : rien ne bouge.
        priorite_normale();
        assert_eq!(etat_priorite(), Some((priorite_win::NORMALE, 2, 5)));
    }
}
