import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { calculerCapacites, type ModuleLourd } from "../lib/capacites";
import type { SystemInfo } from "../lib/api";
import { type Lang, t } from "../lib/i18n";
import type { Vue } from "../lib/vues";
import Clock from "./Clock";

interface Props {
  view: Vue;
  onView: (v: Vue) => void;
  onNewChat: (prefill?: string) => void;
  onToggleHistory: () => void;
  historyOpen: boolean;
  historyCount: number;
  systemOpen: boolean;
  onToggleSystem: () => void;
  systemInfo: SystemInfo | null;
  lang: Lang;
}

/** Icones tracees a la main (aucune dependance graphique). */
const ICONES: Record<Vue, { d: string; d2?: string }> = {
  accueil: {
    d: "M3 9.5 10 3.5l7 6v7a1 1 0 0 1-1 1h-3.5v-5h-5v5H4a1 1 0 0 1-1-1z",
  },
  chat: {
    d: "M3.5 4.5h13a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H9.5L6 16.5v-3H3.5a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z",
    d2: "M6.5 8.5h7M6.5 11h4",
  },
  collab: {
    d: "M7.5 9.5a2.8 2.8 0 1 0 0-5.6 2.8 2.8 0 0 0 0 5.6Z",
    d2: "M2 16.5c0-2.4 2.4-4 5.5-4s5.5 1.6 5.5 4M13.2 4.4a2.4 2.4 0 0 1 0 4.7M14.6 12.8c2.1.3 3.4 1.6 3.4 3.7",
  },
  documents: {
    d: "M5 3.5h6.5L15.5 7.5v9a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1z",
    d2: "M11.5 3.5v4h4M7 11h6M7 13.5h4",
  },
  models: {
    d: "M6.5 6.5h7v7h-7z",
    d2: "M8.5 3v3.5M12 3v3.5M8.5 13.5V17M12 13.5V17M3 8.5h3.5M3 12h3.5M13.5 8.5H17M13.5 12H17",
  },
  finetune: {
    d: "M3 6.5h9M15 6.5h2M3 13.5h4M10 13.5h7",
    d2: "M13 4.5v4M8 11.5v4",
  },
  settings: {
    d: "M10 12.6a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2Z",
    d2: "M10 2.5v1.8M10 15.7v1.8M17.5 10h-1.8M4.3 10H2.5M15.3 4.7l-1.3 1.3M6 14l-1.3 1.3M15.3 15.3 14 14M6 6 4.7 4.7",
  },
  video: {
    d: "M4 7.5h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z",
    d2: "M7 11v2M10 11v2M13 11v2",
  },
};

const TABS: { id: Vue; cle: string }[] = [
  { id: "accueil", cle: "nav.home" },
  { id: "chat", cle: "nav.chat" },
  { id: "collab", cle: "nav.workshop" },
  { id: "documents", cle: "nav.documents" },
  { id: "models", cle: "nav.models" },
  { id: "finetune", cle: "nav.finetune" },
  { id: "settings", cle: "nav.settings" },
  { id: "video", cle: "nav.video" },
];

function Icone({ d, d2 }: { d: string; d2?: string }) {
  return (
    <svg
      className="tab-ico"
      viewBox="0 0 20 20"
      width="17"
      height="17"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
      {d2 && <path d={d2} />}
    </svg>
  );
}

/**
 * Barre superieure : navigation par onglets, actions rapides et sante
 * du systeme. Remplace l'ancienne barre laterale en petits boutons.
 */
export default function TabBar({
  view,
  onView,
  onNewChat,
  onToggleHistory,
  historyOpen,
  historyCount,
  systemOpen,
  onToggleSystem,
  systemInfo,
  lang,
}: Props) {
  const texte = (cle: string) => t(lang, cle);
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusDemande = useRef<number | null>(null);
  const griseRef = useRef<(id: Vue) => string>(() => "");

  // Alt+1..7 : acces direct a chaque onglet depuis n'importe ou.
  useEffect(() => {
    const surTouche = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      if (!e.code.startsWith("Digit")) return;
      const n = Number(e.code.slice(5));
      if (!Number.isInteger(n) || n < 1 || n > TABS.length) return;
      e.preventDefault();
      if (griseRef.current(TABS[n - 1].id)) return;
      focusDemande.current = n - 1;
      onView(TABS[n - 1].id);
    };
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [onView]);

  // Le focus suit le changement de vue demande au clavier.
  useEffect(() => {
    const i = focusDemande.current;
    if (i !== null) {
      focusDemande.current = null;
      refs.current[i]?.focus();
    }
  }, [view]);

  const surTouchesOnglets = (e: ReactKeyboardEvent<HTMLElement>) => {
    const i = TABS.findIndex((x) => x.id === view);
    let suite = -1;
    if (e.key === "ArrowRight") suite = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") suite = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") suite = 0;
    else if (e.key === "End") suite = TABS.length - 1;
    if (suite < 0) return;
    e.preventDefault();
    focusDemande.current = suite;
    onView(TABS[suite].id);
  };

  // Onglets des modules lourds : grises quand la machine ne peut pas les faire tourner.
  const capacites = calculerCapacites(systemInfo);
  const moduleDe = (id: Vue): ModuleLourd | null =>
    id === "video" ? "video" : id === "finetune" ? "finetune" : null;
  const grise = (id: Vue) => {
    const m = moduleDe(id);
    return m && !capacites[m].ok ? capacites[m].raison : "";
  };

  useEffect(() => {
    griseRef.current = grise;
  });

  const gpu = systemInfo?.gpus?.[0];
  const ollamaOk = systemInfo?.ollama.available ?? false;
  const vramTxt = gpu
    ? `${(gpu.vram_used_mb / 1024).toFixed(1)}/${(gpu.vram_total_mb / 1024).toFixed(0)} Go`
    : "—";
  const vramPct = gpu
    ? Math.min(100, Math.round((gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100))
    : 0;

  return (
    <header className="tabbar">
      <div className="tb-brand" title="Néo — assistant local">
        <span className="brand-dot" />
        <span className="tb-logo">NÉO</span>
      </div>

      <nav
        className="tb-tabs"
        role="tablist"
        aria-label={texte("shell.tabs")}
        onKeyDown={surTouchesOnglets}
      >
        {TABS.map((tab, i) => (
          <button
            key={tab.id}
            id={`tab-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={view === tab.id}
            aria-controls="vue-panneau"
            tabIndex={view === tab.id ? 0 : -1}
            ref={(el) => {
              refs.current[i] = el;
            }}
            className={`tb-tab ${view === tab.id ? "active" : ""} ${grise(tab.id) ? "tb-tab-grise" : ""}`}
            aria-disabled={grise(tab.id) ? true : undefined}
            title={grise(tab.id) || `${texte(tab.cle)} — Alt+${i + 1}`}
            onClick={() => {
              if (!grise(tab.id)) onView(tab.id);
            }}
          >
            <Icone {...ICONES[tab.id]} />
            <span className="tb-tab-txt">{texte(tab.cle)}</span>
            <span className="tb-key" aria-hidden="true">
              {i + 1}
            </span>
          </button>
        ))}
      </nav>

      <div className="tb-right">
        <button
          type="button"
          className="tb-action primary"
          onClick={() => onNewChat()}
          aria-label={texte("shell.new")}
          title={texte("nav.newChat")}
        >
          <svg
            viewBox="0 0 16 16"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M8 3v10M3 8h10" />
          </svg>
          <span className="tb-act-txt">{texte("shell.new")}</span>
        </button>

        <button
          type="button"
          className="tb-action"
          onClick={() => onNewChat("Crée un objet 3D : ")}
          aria-label="Créer un objet 3D"
          title="Créer un objet 3D (contrôle qualité + export STL/3MF)"
        >
          <svg
            viewBox="0 0 16 16"
            width="15"
            height="15"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M8 1.8 14 5v6.2L8 14.4 2 11.2V5z" />
            <path d="M2 5l6 3.2L14 5M8 8.2v6.2" />
          </svg>
          <span className="tb-act-txt">3D</span>
        </button>

        <button
          type="button"
          className={`tb-action ${historyOpen ? "active" : ""}`}
          onClick={onToggleHistory}
          aria-expanded={historyOpen}
          aria-label={texte("nav.history")}
          title={texte("nav.history")}
        >
          <svg
            viewBox="0 0 16 16"
            width="15"
            height="15"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M2.5 8a5.5 5.5 0 1 0 1.7-4" />
            <path d="M2.2 2.6v3.2h3.2" />
            <path d="M8 5.2V8l2 1.4" />
          </svg>
          <span className="tb-act-txt">{texte("nav.history")}</span>
          {historyCount > 0 && (
            <span className="tb-badge" aria-hidden="true">
              {historyCount > 99 ? "99+" : historyCount}
            </span>
          )}
        </button>

        <div className="tb-divider" />

        <button
          type="button"
          className={`tb-health ${ollamaOk ? "ok" : "bad"} ${systemOpen ? "active" : ""}`}
          onClick={onToggleSystem}
          aria-expanded={systemOpen}
          title={`${texte("shell.systemTitle")} — Ollama ${texte(
            ollamaOk ? "status.online" : "status.offline"
          )}${gpu ? ` · VRAM ${vramTxt}` : ""}`}
        >
          <span className={`hud-dot ${ollamaOk ? "ok" : "bad"}`} />
          <span className="tb-health-txt">Ollama</span>
          {gpu && (
            <>
              <span className="tb-vram">{vramTxt}</span>
              <span className="tb-vram-bar" aria-hidden="true">
                <i style={{ width: `${vramPct}%` }} />
              </span>
            </>
          )}
        </button>

        <Clock />
      </div>
    </header>
  );
}
