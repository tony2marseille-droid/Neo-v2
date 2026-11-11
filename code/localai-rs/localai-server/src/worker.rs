//! Worker Python resident : un seul process chaud pour les outils du pont.
//!
//! Un seul process Python chaud (backend/worker.py) execute les operations
//! frequentes du pont (outils courts, code d'etat). Supprime le cout de
//! spawn (1-4 s) du pont classique. Le worker est relance automatiquement
//! s'il tombe ; en cas d'erreur, les appelants retombent sur pybridge.
//!
//! Protocole (une ligne JSON par message) : requete `{op, id, ...}`,
//! reponse `{id, ok, out | err}`.
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{oneshot, Mutex};

type Reponse = Result<serde_json::Value, String>;
/// Requetes en attente d'UN process : chaque generation a sa propre table,
/// la mort d'un ancien worker n'echoue donc jamais les requetes du nouveau.
type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Reponse>>>>;

/// Process vivant et ce qu'il faut pour lui parler.
struct Process {
    child: tokio::process::Child,
    stdin: tokio::process::ChildStdin,
    pending: Pending,
}

pub struct Worker {
    root: std::path::PathBuf,
    /// Un seul verrou pour l'etat du process : deux appels concurrents ne
    /// peuvent plus lancer deux workers (le second tuait le premier).
    process: Mutex<Option<Process>>,
    next_id: AtomicU64,
}

impl Worker {
    fn new(root: std::path::PathBuf) -> Arc<Self> {
        Arc::new(Self {
            root,
            process: Mutex::new(None),
            next_id: AtomicU64::new(1),
        })
    }

    fn lancer(&self) -> Result<Process, String> {
        let py = crate::pybridge::venv_python(&self.root);
        if !py.is_file() {
            return Err("venv python absent (worker)".into());
        }
        // stderr dans un fichier : un worker qui meurt reste diagnosticable.
        let logs = self.root.join("logs");
        let _ = std::fs::create_dir_all(&logs);
        let stderr = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(logs.join("worker_python.log"))
            .map(Stdio::from)
            .unwrap_or_else(|_| Stdio::null());
        // PYTHONPATH : `python backend/worker.py` place E:\ia\backend en
        // sys.path[0] ; sans la racine, `import backend.*` echoue en silence.
        // `-X utf8` AVANT le script (apres, il serait passe en argv au script).
        let mut child = localai_core::processus::commande(&py)
            .args(["-X", "utf8", "-u", "backend/worker.py"])
            .env("PYTHONPATH", &self.root)
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .current_dir(&self.root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(stderr)
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| format!("worker spawn: {e}"))?;
        let stdin = child.stdin.take().ok_or("worker stdin absent")?;
        let stdout = child.stdout.take().ok_or("worker stdout absent")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let lecteur = pending.clone();
        tokio::spawn(async move {
            let mut lignes = BufReader::new(stdout).lines();
            while let Ok(Some(ligne)) = lignes.next_line().await {
                let Ok(v) = serde_json::from_str::<serde_json::Value>(&ligne) else { continue };
                let Some(id) = v.get("id").and_then(|x| x.as_u64()) else { continue };
                if let Some(tx) = lecteur.lock().await.remove(&id) {
                    let res = if v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false) {
                        Ok(v.get("out").cloned().unwrap_or(serde_json::Value::Null))
                    } else {
                        Err(v.get("err").and_then(|x| x.as_str()).unwrap_or("erreur worker").to_string())
                    };
                    let _ = tx.send(res);
                }
            }
            // Process termine : SES requetes en attente echouent tout de suite.
            for (_, tx) in lecteur.lock().await.drain() {
                let _ = tx.send(Err("worker termine".into()));
            }
        });
        Ok(Process { child, stdin, pending })
    }

    pub async fn call(self: &Arc<Self>, mut req: serde_json::Value, timeout_s: u64) -> Reponse {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        if let Some(obj) = req.as_object_mut() {
            obj.insert("id".into(), serde_json::json!(id));
        }
        let ligne = serde_json::to_string(&req).map_err(|e| e.to_string())? + "\n";
        let (tx, rx) = oneshot::channel();
        let pending = {
            let mut garde = self.process.lock().await;
            let vivant = garde
                .as_mut()
                .map(|p| p.child.try_wait().ok().flatten().is_none())
                .unwrap_or(false);
            if !vivant {
                *garde = Some(self.lancer()?);
            }
            let p = garde.as_mut().expect("process present");
            p.pending.lock().await.insert(id, tx);
            if let Err(e) = p.stdin.write_all(ligne.as_bytes()).await {
                p.pending.lock().await.remove(&id);
                *garde = None;
                return Err(format!("worker write: {e}"));
            }
            let _ = p.stdin.flush().await;
            p.pending.clone()
        };
        match tokio::time::timeout(std::time::Duration::from_secs(timeout_s), rx).await {
            Ok(Ok(res)) => res,
            Ok(Err(_)) => Err("worker: reponse perdue".into()),
            Err(_) => {
                // Appel bloque : on tue CE worker (s'il est toujours le
                // courant) au lieu de le laisser empoisonner les appels
                // suivants ; le prochain appel le relance.
                pending.lock().await.remove(&id);
                let mut garde = self.process.lock().await;
                if garde.as_ref().is_some_and(|p| Arc::ptr_eq(&p.pending, &pending)) {
                    *garde = None;
                }
                Err(format!("worker: delai depasse ({timeout_s} s), worker relance"))
            }
        }
    }
}

static GLOBAL: OnceLock<Arc<Worker>> = OnceLock::new();

pub fn init(root: std::path::PathBuf) {
    let _ = GLOBAL.set(Worker::new(root));
}

pub fn get() -> Option<&'static Arc<Worker>> {
    GLOBAL.get()
}
