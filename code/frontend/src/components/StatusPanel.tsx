import type { SystemInfo } from "../lib/api";
import { type Lang, t } from "../lib/i18n";
import ServicesSupervises from "./ServicesSupervises";
import TokenPanel from "./TokenPanel";

interface Props {
  open: boolean;
  onClose: () => void;
  systemInfo: SystemInfo | null;
  lang: Lang;
}

function duree(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

function Barre({ pct }: { pct: number }) {
  const etat = pct >= 92 ? "crit" : pct >= 80 ? "warn" : "";
  return (
    <span className="sp-bar" aria-hidden="true">
      <i className={`sp-bar-fill ${etat}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </span>
  );
}

/**
 * Tiroir de droite : sante des services, machine et consommation de tokens.
 * Reprend ce que la barre laterale affichait en permanence, mais a la demande.
 */
export default function StatusPanel({ open, onClose, systemInfo, lang }: Props) {
  const texte = (cle: string) => t(lang, cle);
  const gpu = systemInfo?.gpus?.[0];
  const ollamaOk = systemInfo?.ollama.available ?? false;
  const disque =
    systemInfo?.disks?.find((d) => d.mount.toUpperCase().startsWith("E")) ??
    systemInfo?.disks?.[0];
  const ramPct = systemInfo?.ram.percent ?? 0;
  const vramPct = gpu
    ? Math.round((gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100)
    : 0;

  return (
    <aside
      className={`status-panel ${open ? "open" : ""}`}
      aria-label={texte("shell.systemTitle")}
      aria-hidden={!open}
    >
      <div className="sp-head">
        <span>{texte("shell.systemTitle")}</span>
        <button type="button" onClick={onClose} title={texte("shell.close")} aria-label={texte("shell.close")}>
          ✕
        </button>
      </div>

      <div className="sp-scroll">
        <section className="sp-section">
          <h3>{texte("shell.services")}</h3>
          <div className="sp-row">
            <span>Ollama</span>
            <span className={ollamaOk ? "status-ok" : "status-bad"}>
              {ollamaOk
                ? `${texte("status.online")}${systemInfo?.ollama.version ? ` v${systemInfo.ollama.version}` : ""}`
                : texte("status.offline")}
            </span>
          </div>
          <div className="sp-row">
            <span>{texte("shell.server")}</span>
            <span>
              {systemInfo ? `${systemInfo.app.name} ${systemInfo.app.version}` : "—"}
            </span>
          </div>
          <ServicesSupervises active={open} lang={lang} />
          <div className="sp-row">
            <span>{texte("status.uptime")}</span>
            <span>{systemInfo ? duree(systemInfo.uptime_seconds) : "—"}</span>
          </div>
          <div className="sp-row">
            <span>{texte("shell.modelsDir")}</span>
            <span className="sp-path" title={systemInfo?.models_dir ?? ""}>
              {systemInfo?.models_dir ?? "—"}
            </span>
          </div>
        </section>

        <section className="sp-section">
          <h3>{texte("status.gpu")}</h3>
          {gpu ? (
            <>
              <div className="sp-row">
                <span>{gpu.name.replace("NVIDIA GeForce ", "")}</span>
                <span>{gpu.utilization_percent}%</span>
              </div>
              <div className="sp-row">
                <span>{texte("status.vram")}</span>
                <span>
                  {(gpu.vram_used_mb / 1024).toFixed(1)} /{" "}
                  {(gpu.vram_total_mb / 1024).toFixed(0)} Go
                </span>
              </div>
              <Barre pct={vramPct} />
              <div className="sp-row sp-muted">
                <span>{texte("shell.temp")}</span>
                <span>{gpu.temperature_c} °C</span>
              </div>
            </>
          ) : (
            <div className="sp-muted">{texte("shell.noGpu")}</div>
          )}
        </section>

        <section className="sp-section">
          <h3>{texte("shell.machine")}</h3>
          <div className="sp-row">
            <span>{texte("status.ram")}</span>
            <span>
              {systemInfo
                ? `${systemInfo.ram.used_gb.toFixed(1)} / ${systemInfo.ram.total_gb.toFixed(0)} Go`
                : "—"}
            </span>
          </div>
          <Barre pct={ramPct} />
          <div className="sp-row sp-muted">
            <span>{texte("shell.cpu")}</span>
            <span>
              {systemInfo
                ? `${systemInfo.cpu.percent}% · ${systemInfo.cpu.cores_physical} cœurs`
                : "—"}
            </span>
          </div>
          {disque && (
            <>
              <div className="sp-row sp-muted">
                <span>{texte("shell.disk")}</span>
                <span>
                  {disque.free_gb.toFixed(0)} Go {texte("shell.free")} /{" "}
                  {disque.total_gb.toFixed(0)} Go
                </span>
              </div>
              <Barre pct={disque.used_percent} />
            </>
          )}
        </section>

        <TokenPanel active={open} />
      </div>
    </aside>
  );
}
