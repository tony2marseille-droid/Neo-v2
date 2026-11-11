import { useEffect, useState, type ReactNode } from "react";
import type { ModelEntry, SystemInfo } from "../lib/api";
import { getJSON, sendJSON } from "../lib/api";
import { type Lang, t } from "../lib/i18n";
import { estVue, type Vue } from "../lib/vues";

interface Props {
  onView: (v: Vue) => void;
  onNewChat: (prefill?: string) => void;
  systemInfo: SystemInfo | null;
  models: ModelEntry[];
  lang: Lang;
}

interface ReglesResp {
  regles: { regle: string; created_at: string }[];
}
interface FeedbackResp {
  total: number;
  moyenne: number;
  auto_evals?: { n: number; moyenne: number };
}
interface VoiceStatus {
  stt_available: boolean;
  tts_available: boolean;
}
interface AgentDef {
  id: string;
  name: string;
  description: string;
}

interface Dossier {
  vue: Vue | "nouveau-chat" | "image" | "3d";
  glyphe: string;
  titre: string;
  desc: string;
  prefill?: string;
}

const DOSSIERS: Dossier[] = [
  {
    vue: "chat",
    glyphe: "❯_",
    titre: "Chat",
    desc: "Dialogue avec les agents (outils, RAG, vision)",
  },
  {
    vue: "image",
    glyphe: "▣",
    titre: "Image 4K",
    desc: "FLUX + Kontext : photorealisme fidele au sujet",
  },
  {
    vue: "3d",
    glyphe: "⬢",
    titre: "Creation 3D",
    desc: "Objet 3D : controle qualite + export STL/3MF",
    prefill: "Crée un objet 3D : ",
  },
  {
    vue: "documents",
    glyphe: "☰",
    titre: "Documents",
    desc: "RAG : importe, indexe, interroge tes fichiers",
  },
  {
    vue: "video",
    glyphe: "▶",
    titre: "Vidéo",
    desc: "Storyboard, clips et montage final local",
  },
  {
    vue: "models",
    glyphe: "◈",
    titre: "Modeles",
    desc: "Catalogue Ollama : charge, decharge, installe",
  },
  {
    vue: "finetune",
    glyphe: "↻",
    titre: "Auto-amelioration",
    desc: "Cycles corpus > LoRA > bench > promote",
  },
  {
    vue: "collab",
    glyphe: "✦",
    titre: "Ateliers",
    desc: "Workflows multi-agents : brainstorm, revue, debat",
  },
  {
    vue: "nouveau-chat",
    glyphe: "◉",
    titre: "Vocal",
    desc: "Néo : client vocal LAN (wake-word + TTS)",
  },
  {
    vue: "settings",
    glyphe: "⚙",
    titre: "Reglages",
    desc: "Systeme, securite, memoires, ponts Python",
  },
];

/* Vignettes illustrees : une par fonction, affichees dans les cartes de
 * l'anneau d'accueil (elles tournent avec l'anneau). Dessins SVG inline :
 * aucun fichier externe, aucun emoji manquant possible. */
function Vignette({ id }: { id: string }) {
  const p = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  let dessin: ReactNode;
  switch (id) {
    case "chat":
      dessin = (
        <>
          <path {...p} d="M10 8h44a4 4 0 0 1 4 4v15a4 4 0 0 1-4 4H30l-9 6v-6h-11a4 4 0 0 1-4-4V12a4 4 0 0 1 4-4Z" />
          <circle cx="22" cy="19" r="1.8" fill="currentColor" />
          <circle cx="32" cy="19" r="1.8" fill="currentColor" />
          <circle cx="42" cy="19" r="1.8" fill="currentColor" />
        </>
      );
      break;
    case "image":
      dessin = (
        <>
          <rect {...p} x="8" y="7" width="48" height="27" rx="3" />
          <circle {...p} cx="20" cy="16" r="3.4" />
          <path {...p} d="m11 30 11-10 9 8 7-6 15 12" />
        </>
      );
      break;
    case "documents":
      dessin = (
        <>
          <rect {...p} x="15" y="5" width="34" height="30" rx="3" />
          <path {...p} d="M22 13h20M22 19h20M22 25h12" />
          <path {...p} d="M11 11v24a3 3 0 0 0 3 3h24" opacity="0.55" />
        </>
      );
      break;
    case "models":
      dessin = (
        <>
          <rect {...p} x="18" y="10" width="28" height="20" rx="3" />
          <path {...p} d="M24 10V4M32 10V4M40 10V4M24 30v6M32 30v6M40 30v6M18 15h-6M18 22h-6M46 15h6M46 22h6" />
        </>
      );
      break;
    case "finetune":
      dessin = (
        <>
          <path {...p} d="M46 14a17 17 0 1 0 2 12" />
          <path {...p} d="M46 6v9h-9" />
          <path {...p} d="m30 16 2.4 4.6 5 .7-3.7 3.6.9 5-4.6-2.4-4.6 2.4.9-5-3.7-3.6 5-.7Z" />
        </>
      );
      break;
    case "collab":
      dessin = (
        <>
          <circle {...p} cx="32" cy="11" r="5" />
          <circle {...p} cx="15" cy="29" r="5" />
          <circle {...p} cx="49" cy="29" r="5" />
          <path {...p} d="m29 15-11 10M35 15l11 10M20 29h24" />
        </>
      );
      break;
    case "vocal":
      dessin = (
        <>
          <rect {...p} x="26" y="5" width="12" height="21" rx="6" />
          <path {...p} d="M18 21a14 14 0 0 0 28 0M32 35v5M24 40h16" />
        </>
      );
      break;
    case "settings":
      dessin = (
        <>
          <circle {...p} cx="32" cy="20" r="6" />
          <circle {...p} cx="32" cy="20" r="13" opacity="0.5" />
          <path {...p} d="M32 3v5M32 32v5M15 20h-5M54 20h-5M20 8l3.5 3.5M44 32l3.5 3.5M44 8l-3.5 3.5M20 32l-3.5 3.5" />
        </>
      );
      break;
    case "video":
      dessin = (
        <>
          <rect {...p} x="7" y="9" width="50" height="23" rx="3" />
          <path {...p} d="M14 9v23M50 9v23" opacity="0.55" />
          <path {...p} d="m28 15 10 5.5-10 5.5Z" />
        </>
      );
      break;
    case "3d":
      dessin = (
        <>
          <path {...p} d="M32 5 55 17.5v20L32 50 9 37.5v-20Z" />
          <path {...p} d="m9 17.5 23 12.5 23-12.5M32 30v20" />
          <path {...p} d="m20.5 11.2 23 12.6v12.4" opacity="0.5" />
        </>
      );
      break;
    default:
      dessin = <circle {...p} cx="32" cy="20" r="13" />;
  }
  return (
    <svg className="fc-vignette" viewBox="0 0 64 40" aria-hidden="true">
      {dessin}
    </svg>
  );
}

function barre(pct: number, warn = 75, crit = 90): string {  if (pct >= crit) return "crit";
  if (pct >= warn) return "warn";
  return "";
}

/* --- Socles & niveaux : socle (famille de base) + niveau estime /5 --- */
function paramB(m: ModelEntry): number {
  const s = (m.parameter_size || "").toUpperCase();
  const v = parseFloat(s) || 0;
  if (s.includes("M") && !s.includes("B")) return v / 1000;
  return v;
}

function socleDe(m: ModelEntry): string {
  const t = m.tag.toLowerCase();
  if (t.startsWith("oracle-prog") || t.startsWith("oracle-")) return "Qwen3-8B + LoRA Néo";
  if (t.includes("qwen2.5vl")) return "Qwen2.5-VL";
  if (t.includes("qwen3")) return "Qwen3";
  if (t.includes("gemma3")) return "Gemma 3";
  if (t.includes("llama3.1")) return "Llama 3.1";
  if (t.includes("nomic")) return "Nomic Embed";
  return "—";
}

function levelDe(m: ModelEntry): number {
  const t = m.tag.toLowerCase();
  if (t.includes("nomic")) return 1;
  const b = paramB(m);
  let lv = b >= 14 ? 5 : b >= 10 ? 4 : b >= 7 ? 3 : b >= 4 ? 2 : 1;
  if (t.startsWith("oracle-prog") || t.startsWith("oracle-")) lv = Math.min(5, lv + 1);
  return lv;
}

function etoiles(n: number): string {
  return "★".repeat(n) + "☆".repeat(5 - n);
}

export default function HomeView({ onView, onNewChat, systemInfo, models, lang }: Props) {
  const texte = (key: string) => t(lang, key);
  const [regles, setRegles] = useState<ReglesResp | null>(null);
  const [feedback, setFeedback] = useState<FeedbackResp | null>(null);
  const [vocal, setVocal] = useState<VoiceStatus | null>(null);
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [imageFormat, setImageFormat] = useState("tiff");

  useEffect(() => {
    getJSON<ReglesResp>("/api/selfimprove/rules").then(setRegles).catch(() => undefined);
    getJSON<FeedbackResp>("/api/feedback/stats").then(setFeedback).catch(() => undefined);
    getJSON<VoiceStatus>("/api/voice/status").then(setVocal).catch(() => undefined);
    getJSON<{ agents: AgentDef[] }>("/api/agents").then((a) => setAgents(a.agents)).catch(() => undefined);
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => setImageFormat(d.settings?.image_format ?? "tiff"))
      .catch(() => undefined);
  }, []);

  const gpu = systemInfo?.gpus[0];
  const installees = models.filter((m) => m.installed && !m.tag.startsWith("nomic")).length;
  const reglesActives = regles?.regles?.length ?? 0;
  const ok = (b: boolean | undefined) => (b ? "hud-dot ok" : "hud-dot bad");

  const ouvre = (d: Dossier) => {
    if (d.vue === "nouveau-chat") {
      // Vocal : ouvre le chat avec une demande prete (le client LAN fait le STT/TTS).
      onNewChat();
      return;
    }
    if (d.vue === "image") {
      // Consigne §5 : plus de texte prerempli - le chat s'ouvre VIDE,
      // les consignes de qualite (4K, style) sont ajoutees par l'agent.
      onNewChat();
      return;
    }
    if (d.vue === "3d") {
      // Ouvre le chat avec la consigne 3D pre-remplie (pipeline + export).
      onNewChat(d.prefill ?? "Crée un objet 3D : ");
      return;
    }
    if (estVue(d.vue)) onView(d.vue);
  };

  return (
    <section className="home">
      <div className="home-inner">
        {/* ---------- Hero ---------- */}
        <header className="home-hero">
          <div className="home-brand">
            <span className="home-core" />
            <h1 className="home-title">
              NÉO<span className="cursor-blink" />
            </h1>
          </div>
          <p className="home-tagline">
            Intelligence locale. Aucun nuage. {systemInfo?.app.version ? `v${systemInfo.app.version} — ` : ""}
            {agents.length > 0 ? `${agents.length} ${texte("home.agents")}.` : texte("home.agents")}
          </p>
          <div className="home-health">
            <span className={ok(systemInfo?.ollama.available ?? false)}>OLLAMA {systemInfo?.ollama.version ?? ""}</span>
            <span className={ok(true)}>SERVEUR :8320</span>
            <span className={ok(vocal?.stt_available)}>STT</span>
            <span className={ok(vocal?.tts_available)}>TTS</span>
            <span className={ok(true)}>DOCUMENTS RAG ✓</span>
          </div>
        </header>

        {/* ---------- Dossiers tournants (anneau 3D) ---------- */}
        <div className="ring-scene">
          <div className="ring-center">
            <div className="reticle" />
            <div className="reticle-2" />
            <div className="core-label">
              <div className="core-dot" />
              <span>NOYAU</span>
            </div>
          </div>
          <div
            className="ring"
            style={{ "--pas": `${360 / DOSSIERS.length}deg` } as React.CSSProperties}
          >
            {DOSSIERS.map((d, i) => (
              <button
                key={d.titre}
                type="button"
                className="folder-card"
                style={{ "--i": i } as React.CSSProperties}
                onClick={() => ouvre(d)}
                title={d.desc}
              >
                <span className="fc-icon"><Vignette id={d.vue} /></span>
                <span className="fc-title">{d.titre}</span>
                <span className="fc-desc">{d.desc}</span>
                <span className="fc-open">▸ ouvrir</span>
              </button>
            ))}
          </div>
        </div>

        {/* ---------- HUD : etat du systeme ---------- */}
        <div className="hud-grid">
          <div className="hud-panel">
            <div className="hud-head">
              <span className="hud-title">{texte("home.system")}</span>
              <span className="hud-label hud-mono">
                uptime {systemInfo
                  ? `${Math.floor(systemInfo.uptime_seconds / 3600)}h${String(
                      Math.floor((systemInfo.uptime_seconds % 3600) / 60)
                    ).padStart(2, "0")}`
                  : "—"}
              </span>
            </div>
            <div className="hud-row">
              <span>CPU {systemInfo ? `${systemInfo.cpu.percent} %` : "—"}</span>
              <span className="hud-label">{systemInfo ? `${systemInfo.cpu.cores_logical} cœurs` : ""}</span>
            </div>
            <div className="hud-bar">
              <div className={`hud-bar-fill ${barre(systemInfo?.cpu.percent ?? 0)}`} style={{ width: `${systemInfo?.cpu.percent ?? 0}%` }} />
            </div>
            <div className="hud-row">
              <span>RAM {systemInfo ? `${systemInfo.ram.used_gb} / ${systemInfo.ram.total_gb} Go` : "—"}</span>
              <span className="hud-label">{systemInfo ? `${systemInfo.ram.percent} %` : ""}</span>
            </div>
            <div className="hud-bar">
              <div className={`hud-bar-fill ${barre(systemInfo?.ram.percent ?? 0)}`} style={{ width: `${systemInfo?.ram.percent ?? 0}%` }} />
            </div>
            {gpu && (
              <>
                <div className="hud-row">
                  <span>{gpu.name.replace("NVIDIA GeForce ", "")} {gpu.temperature_c ? `${gpu.temperature_c}°C` : ""}</span>
                  <span className="hud-label">{gpu.utilization_percent} % util.</span>
                </div>
                <div className="hud-row">
                  <span>VRAM {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} Go</span>
                  <span className="hud-label">{Math.round((gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100)} %</span>
                </div>
                <div className="hud-bar">
                  <div
                    className={`hud-bar-fill ${barre((gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100, 85, 95)}`}
                    style={{ width: `${(gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100}%` }}
                  />
                </div>
              </>
            )}
          </div>

          <div className="hud-panel">
            <div className="hud-head">
              <span className="hud-title">{texte("home.auto")}</span>
              <span className="hud-label">cycle hebdo</span>
            </div>
            <div className="hud-kv">
              <span>Regles actives</span>
              <span className="hud-strong">{reglesActives}</span>
            </div>
            <div className="hud-kv">
              <span>Retours utilisateur</span>
              <span className="hud-strong">{feedback?.total ?? 0}</span>
            </div>
            <div className="hud-kv">
              <span>Moyenne / 5</span>
              <span className="hud-strong">{feedback ? feedback.moyenne.toFixed(1) : "—"}</span>
            </div>
            <div className="hud-kv">
              <span>Auto-evaluations</span>
              <span className="hud-strong">{feedback?.auto_evals?.n ?? 0}</span>
            </div>
            {reglesActives > 0 && (
              <div className="hud-regle">« {regles?.regles[0]?.regle.slice(0, 110)}… »</div>
            )}
          </div>

          <div className="hud-panel">
            <div className="hud-head">
              <span className="hud-title">{texte("home.models")}</span>
              <span className="hud-label">100 % local</span>
            </div>
            <div className="hud-kv">
              <span>Modeles installes</span>
              <span className="hud-strong">{installees}</span>
            </div>
            <div className="hud-kv">
              <span>Agents</span>
              <span className="hud-strong">{agents.length || 7}</span>
            </div>
            <div className="hud-kv">
              <span>Dispatcher rapide</span>
              <span className="hud-strong hud-mono">qwen3:8b</span>
            </div>
            <div className="hud-kv">
              <span>Cerveau fine-tune</span>
              <span className="hud-strong hud-mono">oracle-prog-latest</span>
            </div>
            <div className="hud-kv">
              <span>Pont /api/outil (jobs)</span>
              <span className="hud-strong">actif</span>
            </div>
<div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
              <div className="hud-label" style={{ marginBottom: 6 }}>
                Socles & niveaux (estimation taille + capacités + fine-tune)
              </div>
              {models
                .filter((m) => m.installed)
                .sort((a, b) => levelDe(b) - levelDe(a) || a.tag.localeCompare(b.tag))
                .map((m) => (
                  <div
                    key={m.tag}
                    className="hud-kv"
                    title={`Socle : ${socleDe(m)} — niveau ${levelDe(m)}/5`}
                  >
                    <span
                      className="hud-mono"
                      style={{ overflow: "hidden", textOverflow: "ellipsis", maxWidth: 165, whiteSpace: "nowrap", fontSize: 11 }}
                    >
                      {m.tag}{m.loaded ? " ●" : ""}
                    </span>
                    <span className="hud-label" style={{ whiteSpace: "nowrap" }}>
                      {socleDe(m)}{" "}
                      <span style={{ color: "var(--accent-soft)", letterSpacing: 1 }}>
                        {etoiles(levelDe(m))}
                      </span>{" "}
                      {levelDe(m)}/5
                    </span>
                  </div>
                ))}
              </div>

              <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                <div className="hud-label" style={{ marginBottom: 6 }}>
                  {texte("home.imageFormat")}
                </div>
                <p style={{ color: "var(--text-dim)", fontSize: 11.5, marginBottom: 8 }}>
                  {texte("home.imageFormatHint")}
                </p>
                <select
                  value={imageFormat}
                  onChange={(e) => {
                    const fmt = e.target.value;
                    setImageFormat(fmt);
                    sendJSON("/api/settings", "PUT", { key: "image_format", value: fmt }).catch(
                      () => undefined // reglage de confort : un echec reseau est sans consequence
                    );
                  }}
                  style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg)", color: "var(--text)" }}
                >
                  <option value="tiff">TIFF (master sans perte — défaut)</option>
                  <option value="jpeg">JPEG (4K)</option>
                  <option value="png">PNG (sans perte)</option>
                  <option value="webp">WebP (moderne, bon compromis)</option>
                  <option value="exr">EXR (HDR, post-traitement)</option>
                </select>
              </div>
            </div>
          </div>

        {/* ---------- Actions rapides ---------- */}
        <div className="home-actions">
          <button className="primary" onClick={() => onNewChat()}>
            ❯_ {texte("home.newChat")}
          </button>
          <button onClick={() => onView("finetune")}>{texte("home.cycle")}</button>
        </div>
      </div>
    </section>
  );
}
