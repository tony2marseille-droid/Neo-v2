import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON, VIDEO_API } from "../lib/api";
import type { ToastFn } from "../lib/toast";
import { useActivity } from "./ActivityContext";

// L'API vidéo (VIDEO_API) tourne sur un serveur FastAPI dédié (port 8321),
// séparé du serveur Rust (port 8320).

// Temps moyen par opération (mesuré en pratique) pour l'estimation.
const SEC_PAR_CLIP = 60;       // génération d'un clip (image sdgen + ffmpeg) — mesuré ~48 s
const SEC_STORYBOARD = 25;    // découpe de l'histoire par Ollama — mesuré ~23 s
const SEC_RENDER = 5;         // concaténation ffmpeg sans ré-encodage — mesuré ~1 s

interface StoryboardShot {
  id: number;
  setting: string;
  imagePrompt: string;
  videoPrompt: string;
  dialogue?: { character: string; text: string };
  durationSeconds: number;
}

interface StoryboardData {
  title: string;
  language: string;
  characterDescription: string;
  shots: StoryboardShot[];
}

/** Formate une durée en "1 min 20 s" ou "45 s". */
function dureeLisible(sec: number): string {
  if (sec < 60) return `${Math.round(sec)} s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return s > 0 ? `${m} min ${s} s` : `${m} min`;
}

function ShotCard({ shot, shotEnCours, genererClip, auto }: {
  shot: StoryboardShot;
  shotEnCours: number | null;
  genererClip: (s: StoryboardShot) => void;
  auto: boolean;
}) {
  const enCours = shotEnCours === shot.id;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 10, background: "var(--bg-card)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
        <span style={{ fontWeight: 600, color: "var(--accent)" }}>Plan {shot.id}</span>
        <span style={{ fontSize: 11, color: "var(--text-faint)" }}>{shot.durationSeconds}s</span>
      </div>
      <div style={{ marginBottom: 4, fontSize: 12.5, color: "var(--text-dim)" }}>
        <strong>Lieu :</strong> {shot.setting}
      </div>
      <div style={{ marginBottom: 4, fontSize: 12.5, color: "var(--text)" }}>
        <strong>Image :</strong> {shot.imagePrompt}
      </div>
      <div style={{ marginBottom: 6, fontSize: 12.5, color: "var(--text)" }}>
        <strong>Video :</strong> {shot.videoPrompt}
      </div>
      {shot.dialogue && (
        <div style={{ fontSize: 12, color: "var(--accent-soft)", fontStyle: "italic" }}>
          "{shot.dialogue.text}" - {shot.dialogue.character}
        </div>
      )}
      <button
        onClick={() => void genererClip(shot)}
        disabled={enCours || auto}
        style={{ fontSize: 12, padding: "4px 10px" }}
      >
        {enCours ? `Génération… (≈${SEC_PAR_CLIP}s)` : "Generer le clip"}
      </button>
    </div>
  );
}

export default function VideoView({ toast }: { toast: ToastFn }) {
  const { startActivity, updateActivity, endActivity } = useActivity();
  const [histoire, setHistoire] = useState("");
  const [modele, setModele] = useState("");
  const [dureeTotale, setDureeTotale] = useState(0); // 0 = auto (3-6 plans)
  const [storyboard, setStoryboard] = useState<StoryboardData | null>(null);
  const [storyboardPath, setStoryboardPath] = useState("");
  // Timestamp (epoch s) du storyboard courant : le render ne monte que les
  // clips crees apres cette date (evite de melanger plusieurs projets).
  const [storyboardTime, setStoryboardTime] = useState(0);
  const [shotEnCours, setShotEnCours] = useState<number | null>(null);
  const [autoEnCours, setAutoEnCours] = useState(false);
  const [renderEnCours, setRenderEnCours] = useState(false);
  const [fichiers, setFichiers] = useState<{ nom: string; chemin: string; taille: number }[]>([]);
  const [projectName, setProjectName] = useState("video_" + Date.now());
  const [audioTexte, setAudioTexte] = useState("");
  const [status, setStatus] = useState<any>(null);

  const rafraichirListe = useCallback(async () => {
    try {
      const data = await getJSON<{ ok: boolean; fichiers: any[] }>(VIDEO_API + "/liste?pattern=*");
      setFichiers(data.fichiers ?? []);
    } catch {
      /* silent */
    }
  }, []);

  const genererStoryboard = useCallback(async () => {
    if (!histoire.trim()) {
      toast("Saisissez une histoire d'abord", "error");
      return;
    }
    startActivity("generating_storyboard", "Génère un storyboard…", `estimation ≈ ${dureeLisible(SEC_STORYBOARD)}`);
    try {
      const res = await sendJSON<{ ok: boolean; resultat: string }>(
        VIDEO_API + "/storyboard",
        "POST",
        { histoire, modele, duree_totale: dureeTotale },
      );
      if (!res.ok) throw new Error(res.resultat || "Echec storyboard");

      const liste = await getJSON<{ ok: boolean; fichiers: any[] }>(VIDEO_API + "/liste?pattern=storyboard_*.json");
      // Le serveur trie du plus récent au plus ancien.
      const sb = liste.fichiers?.[0];
      if (sb && sb.url) {
        const fileUrl = VIDEO_API + '/' + sb.url;
        const data = await getJSON<StoryboardData>(fileUrl);
        setStoryboard(data);
        setStoryboardPath(sb.chemin);
        // Marque le debut du projet : seuls les clips crees apres seront montes.
        setStoryboardTime(Date.now() / 1000 - 5);
        const total = data.shots.reduce((a, s) => a + (s.durationSeconds || 0), 0);
        toast(`Storyboard : ${data.shots.length} plans, ${total}s au total`, "success");
      } else {
        toast("Storyboard genere (voir console)", "info");
      }
    } catch (err) {
      toast(`Erreur storyboard : ${err}`, "error");
    } finally {
      endActivity();
    }
  }, [histoire, modele, dureeTotale, toast, startActivity, endActivity]);

  const genererClip = useCallback(async (shot: StoryboardShot): Promise<boolean> => {
    if (!storyboardPath) {
      toast("Generez le storyboard d'abord", "error");
      return false;
    }
    setShotEnCours(shot.id);
    try {
      const res = await sendJSON<{ ok: boolean; resultat: string }>(
        VIDEO_API + "/clip",
        "POST",
        { storyboard_path: storyboardPath, shot_id: shot.id },
      );
      if (!res.ok) throw new Error(res.resultat || "Echec clip");
      return true;
    } catch (err) {
      toast(`Erreur clip plan ${shot.id} : ${err}`, "error");
      return false;
    } finally {
      setShotEnCours(null);
    }
  }, [storyboardPath, toast]);

  const genererClipUnique = useCallback(async (shot: StoryboardShot) => {
    startActivity("generating_clip", `Génère le clip du plan ${shot.id}…`, `estimation ≈ ${dureeLisible(SEC_PAR_CLIP)}`);
    try {
      const ok = await genererClip(shot);
      if (ok) {
        toast(`Clip plan ${shot.id} genere`, "success");
        await rafraichirListe();
      }
    } finally {
      endActivity();
    }
  }, [genererClip, rafraichirListe, toast, startActivity, endActivity]);

  const genererTousLesClips = useCallback(async () => {
    if (!storyboard || !storyboardPath) {
      toast("Generez le storyboard d'abord", "error");
      return;
    }
    const shots = storyboard.shots;
    const estimation = shots.length * SEC_PAR_CLIP;
    setAutoEnCours(true);
    startActivity(
      "generating_clip",
      `Auto : génération des ${shots.length} clips…`,
      `estimation ≈ ${dureeLisible(estimation)}`,
    );
    let ok = 0;
    const t0 = Date.now();
    try {
      for (let i = 0; i < shots.length; i++) {
        const shot = shots[i];
        const restant = shots.length - i;
        // Fait réel restant : moyenne constatée × clips restants (si au moins 1 fait).
        const fait = Date.now() - t0;
        const moyenMs = i > 0 ? fait / i : SEC_PAR_CLIP * 1000;
        const restantSec = restant * (moyenMs / 1000);
        updateActivity({
          progress: Math.round((i / shots.length) * 100),
          label: `Auto : clip ${i + 1}/${shots.length} (plan ${shot.id})…`,
          detail: `temps restant ≈ ${dureeLisible(restantSec)}`,
        });
        const success = await genererClip(shot);
        if (success) ok++;
      }
      updateActivity({ progress: 100, label: "Auto : terminé", detail: `${ok}/${shots.length} clips générés` });
      toast(`Generation auto : ${ok}/${shots.length} clips generes`, ok === shots.length ? "success" : "info");
      await rafraichirListe();
    } finally {
      setAutoEnCours(false);
      endActivity();
    }
  }, [storyboard, storyboardPath, genererClip, rafraichirListe, toast, startActivity, updateActivity, endActivity]);

  const lancerRender = useCallback(async () => {
    if (!projectName.trim()) {
      toast("Donnez un nom de projet", "error");
      return;
    }
    setRenderEnCours(true);
    const nbClips = storyboard?.shots.length ?? 0;
    startActivity(
      "rendering_video",
      "Rendu vidéo en cours…",
      nbClips > 0 ? `estimation ≈ ${dureeLisible(SEC_RENDER)} (${nbClips} clips)` : projectName,
    );
    try {
      const res = await sendJSON<{ ok: boolean; resultat: string }>(
        VIDEO_API + "/render",
        "POST",
        { project_name: projectName, audio_texte: audioTexte, duree_totale: dureeTotale, since: storyboardTime },
      );
      if (!res.ok) throw new Error(res.resultat || "Echec render");
      toast("Video finale generee", "success");
      await rafraichirListe();
    } catch (err) {
      toast(`Erreur render : ${err}`, "error");
    } finally {
      setRenderEnCours(false);
      endActivity();
    }
  }, [projectName, audioTexte, dureeTotale, storyboard, storyboardTime, rafraichirListe, toast, startActivity, endActivity]);

  const verifierStatus = useCallback(async () => {
    try {
      const data = await getJSON<{ ok: boolean; statut: any }>(VIDEO_API + "/status");
      return data.statut;
    } catch {
      return null;
    }
  }, []);

  // A l'ouverture de l'onglet, charger la liste des fichiers et l'etat des
  // dependances : sinon la section « Fichiers generes » restait vide alors
  // que des MP4 existent deja sur le disque.
  useEffect(() => {
    void rafraichirListe();
    void verifierStatus().then(setStatus);
  }, [rafraichirListe, verifierStatus]);

  const totalStoryboard = storyboard
    ? storyboard.shots.reduce((a, s) => a + (s.durationSeconds || 0), 0)
    : 0;

  return (
    <div className="video-view">
      <div className="view-header">
        <h2>VIDEO - Generation</h2>
        <span style={{ flex: 1 }} />
        <button onClick={() => void verifierStatus().then(setStatus)}>
          Verifier dependances
        </button>
      </div>

      {status && (
        <div style={{ padding: "10px 22px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", marginBottom: 14 }}>
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap", fontSize: 12, color: "var(--text-dim)" }}>
            <span>[DOSSIER] {status.dossier_sortie}</span>
            <span>{status.comfyui ? "[OK] ComfyUI" : "[KO] ComfyUI"}</span>
            <span>{status.ollama ? "[OK] Ollama" : "[KO] Ollama"}</span>
            <span>{status.ffmpeg ? "[OK] FFmpeg" : "[KO] FFmpeg"}</span>
          </div>
        </div>
      )}

      <div className="settings-grid">
        {/* Etape 1 : Histoire */}
        <div className="panel">
          <h3>1. Histoire</h3>
          <textarea
            value={histoire}
            onChange={(e) => setHistoire(e.target.value)}
            placeholder="Ex : Un petit robot explore une foret magique et rencontre un arbre parlant."
            rows={4}
            style={{ width: "100%", fontFamily: "inherit", fontSize: 13, background: "var(--bg-card)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 10 }}
          />
          <div style={{ display: "flex", gap: 10, marginTop: 8, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ fontSize: 12, color: "var(--text-dim)" }}>Modele Ollama :</label>
            <select
              value={modele}
              onChange={(e) => setModele(e.target.value)}
              style={{ fontSize: 12, padding: "4px 8px" }}
            >
              <option value="">Auto (meilleur disponible)</option>
              <option value="qwen2.5vl:7b">Qwen2.5-VL 7B</option>
              <option value="qwen2.5:14b">Qwen2.5 14B</option>
              <option value="qwen2.5:7b">Qwen2.5 7B</option>
              <option value="llama3.1:8b">Llama3.1 8B</option>
            </select>
            <label style={{ fontSize: 12, color: "var(--text-dim)" }}>Durée totale :</label>
            <select
              value={dureeTotale}
              onChange={(e) => setDureeTotale(Number(e.target.value))}
              style={{ fontSize: 12, padding: "4px 8px" }}
            >
              <option value={0}>Auto (3 à 6 plans)</option>
              <option value={10}>10 s</option>
              <option value={15}>15 s</option>
              <option value={20}>20 s</option>
              <option value={30}>30 s</option>
              <option value={45}>45 s</option>
              <option value={60}>60 s</option>
            </select>
            <button className="primary" onClick={() => void genererStoryboard()} disabled={!histoire.trim()}>
              Generer le storyboard
            </button>
          </div>
        </div>

        {/* Etape 2 : Storyboard */}
        {storyboard && (
          <div className="panel">
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
              <h3 style={{ margin: 0 }}>2. Storyboard - {storyboard.title}</h3>
              <span className="chip">{storyboard.shots.length} plans · {totalStoryboard}s</span>
              <span style={{ flex: 1 }} />
              <button
                className="primary"
                onClick={() => void genererTousLesClips()}
                disabled={autoEnCours || shotEnCours !== null}
                title={`Estimation : ≈ ${dureeLisible(storyboard.shots.length * SEC_PAR_CLIP)}`}
              >
                {autoEnCours
                  ? `Generation auto… (≈${dureeLisible((storyboard.shots.length - (shotEnCours ? 1 : 0)) * SEC_PAR_CLIP)})`
                  : `Generer tous les clips (≈${dureeLisible(storyboard.shots.length * SEC_PAR_CLIP)})`}
              </button>
            </div>
            <p style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 10 }}>
              {storyboard.characterDescription}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 400, overflowY: "auto" }}>
              {storyboard.shots.map((shot) => (
                <ShotCard
                  key={shot.id}
                  shot={shot}
                  shotEnCours={shotEnCours}
                  genererClip={genererClipUnique}
                  auto={autoEnCours}
                />
              ))}
            </div>
          </div>
        )}

        {/* Etape 3 : Rendu final */}
        <div className="panel">
          <h3>3. Montage final</h3>
          <div className="settings-row" style={{ marginBottom: 8 }}>
            <label>Nom du projet :</label>
            <input
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              style={{ flex: 1, marginLeft: 8 }}
            />
          </div>
          <div className="settings-row" style={{ marginBottom: 8 }}>
            <label>Texte audio (optionnel) :</label>
            <input
              value={audioTexte}
              onChange={(e) => setAudioTexte(e.target.value)}
              placeholder="Ex : Bonjour, bienvenue dans ma foret magique."
              style={{ flex: 1, marginLeft: 8 }}
            />
          </div>
          <button
            className="primary"
            onClick={() => void lancerRender()}
            disabled={renderEnCours}
            style={{ width: "100%" }}
          >
            {renderEnCours ? `Rendu en cours… (≈${dureeLisible(SEC_RENDER)})` : `Generer la video finale (≈${dureeLisible(SEC_RENDER)})`}
          </button>
        </div>

        {/* Fichiers generes */}
        <div className="panel">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>4. Fichiers generes</h3>
            <button onClick={() => void rafraichirListe()} style={{ fontSize: 12, padding: "2px 10px" }}>
              Rafraichir
            </button>
          </div>
          {fichiers.length === 0 ? (
            <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>Aucun fichier pour l'instant.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 300, overflowY: "auto" }}>
              {fichiers.map((f) => (
                <div key={f.nom} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12, padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                  <span title={f.chemin}>{f.nom}</span>
                  <span style={{ color: "var(--text-faint)" }}>{(f.taille / 1024 / 1024).toFixed(2)} Mo</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
