//! neo-silencieux : lance une commande SANS aucune fenetre console.
//!
//! Usage (action d'une tache planifiee) :
//!   neo-silencieux.exe <programme> [arguments...]
//!   ex. neo-silencieux.exe powershell.exe -NoProfile -File E:\ia\garde_prod.ps1
//!       neo-silencieux.exe cmd.exe /c E:\ia\tools\toolshim\watchdog_run.bat
//!
//! Le lanceur lui-meme est une application « fenetre » (aucune console n'est
//! creee) et demarre l'enfant avec CREATE_NO_WINDOW : un script PowerShell,
//! un .bat ou un python.exe tourne alors sans jamais rien afficher. Il attend
//! la fin de l'enfant et renvoie son code de sortie, pour que le
//! Planificateur de taches voie l'etat reel (et n'empile pas les executions).
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::process::{Command, ExitCode, Stdio};

fn main() -> ExitCode {
    let mut args = std::env::args_os().skip(1);
    let Some(programme) = args.next() else {
        return ExitCode::from(2);
    };
    let mut commande = Command::new(programme);
    commande.args(args).stdin(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        commande.creation_flags(CREATE_NO_WINDOW);
    }
    match commande.status() {
        Ok(statut) => ExitCode::from(statut.code().unwrap_or(1).clamp(0, 255) as u8),
        Err(_) => ExitCode::from(127),
    }
}
