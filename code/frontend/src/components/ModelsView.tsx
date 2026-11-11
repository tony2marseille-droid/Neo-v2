import { useCallback, useEffect, useState } from "react";
import type { BenchmarkSummary, DiscoveredModel, ModelEntry } from "../lib/api";
import type { ToastFn } from "../lib/toast";
import { getJSON, sendJSON, streamSSE } from "../lib/api";

interface Props {
  models: ModelEntry[];
  refreshModels: () => Promise<void>;
  toast: ToastFn;
}

type ProgressState = { percent: number; status: string };

/* --- Filtre T1..T5. Le tier vient du serveur (source de verite) : c'est le
   seul composant qui connait a la fois le catalogue et les modeles installes.
   Le calcul local n'est qu'un repli si l'API ne le fournit pas. --- */
const SEUILS_TIER: Array<[number, string]> = [
  [40, "T5"], // >= 40B : tres lourds, souvent hors config en local
  [20, "T4"], // 20-39B : gros denses et MoE
  [10, "T3"], // 10-19B : haut de gamme tenant en VRAM
  [5, "T2"], // 5-9B : chevaux de trait
]; // en dessous de 5B : T1

/** Nombre de milliards de parametres, deduit de ce que l'API expose. */
function paramB(m: ModelEntry): number {
  if (typeof m.params_b === "number" && m.params_b > 0) return m.params_b;
  // Repli : "8B" -> 8, "137M" -> 0,137. Sans ce repli, un modele du catalogue
  // non installe (parameter_size vide, size_label rempli) tombait en T1.
  const s = (m.parameter_size || m.size_label || "").toUpperCase().trim();
  const v = parseFloat(s) || 0;
  if (s.endsWith("M")) return v / 1000;
  return v;
}

/** Tier du modele ; repli local calque sur l'echelle du serveur. */
function tierDe(m: ModelEntry): string {
  if (m.tier) return m.tier;
  const t = m.tag.toLowerCase();
  if (t.includes("nomic") || t.includes("embed")) return "Embed";
  if (m.capabilities.includes("embeddings")) return "Embed";
  if (t.startsWith("cloud:") || m.source === "cloud") return "Cloud";
  const b = paramB(m);
  for (const [seuil, tier] of SEUILS_TIER) {
    if (b >= seuil) return tier;
  }
  return "T1";
}

const TIERS = ["", "T1", "T2", "T3", "T4", "T5", "Embed", "Cloud"];

// Icône selon le type de modèle
function modelIcon(m: ModelEntry): string {
  const caps = m.capabilities || [];
  const fam = (m.family || "").toLowerCase();
  const tag = m.tag.toLowerCase();
  
  if (caps.includes("embeddings")) return "🔍";      // Embeddings
  if (caps.includes("vision")) return "👁️";        // Vision
  if (caps.includes("coding")) return "💻";         // Code
  if (caps.includes("raisonnement")) return "🧠";   // Reasoning
  if (tag.startsWith("cloud:")) return "☁️";        // Cloud
  if (fam.includes("qwen")) return "🐉";            // Qwen
  if (fam.includes("llama")) return "🦙";           // Llama
  if (fam.includes("gemma")) return "💎";           // Gemma
  if (fam.includes("mistral")) return "🌬️";         // Mistral
  if (fam.includes("phi")) return "🔮";             // Phi
  if (fam.includes("deepseek")) return "🔎";        // DeepSeek
  if (fam.includes("command")) return "📋";         // Command R
  if (fam.includes("granite")) return "🏗️";         // Granite
  if (fam.includes("nomic")) return "🔍";           // Nomic
  return "🤖";                                      // Défaut
}

function formatSize(bytes: number): string {
  if (!bytes) return "—";
  return `${(bytes / 1e9).toFixed(1)} Go`;
}

export default function ModelsView({ models, refreshModels, toast }: Props) {
  const [progress, setProgress] = useState<Record<string, ProgressState>>({});
  const [busyTag, setBusyTag] = useState("");
  const [customTag, setCustomTag] = useState("");
  const [benchmarking, setBenchmarking] = useState(false);
  const [benchResults, setBenchResults] = useState<BenchmarkSummary[]>([]);
  const [benchCurrent, setBenchCurrent] = useState("");
  const [optionnels, setOptionnels] = useState<string[]>([]);
  const [tierFiltre, setTierFiltre] = useState("");
  const [decouverte, setDecouverte] = useState<DiscoveredModel[] | null>(null);
  const [decouvreEnCours, setDecouvreEnCours] = useState(false);

  useEffect(() => {
    getJSON<{ optionnels: string[] }>("/api/system/hardware/pulls")
      .then((d) => setOptionnels(d.optionnels || []))
      .catch(() => undefined);
  }, []);

  const estInstalle = (tag: string) =>
    models.some((m) => m.tag.toLowerCase() === tag.toLowerCase());

  // Consigne : recherche de nouveaux modeles disponibles (bibliotheque
  // publique Ollama) - bouton en haut de la fenetre Modeles.
  const decouvrir = useCallback(async () => {
    setDecouvreEnCours(true);
    try {
      const d = await sendJSON<{ nouveaux: DiscoveredModel[]; total: number }>(
        "/api/models/discover",
        "POST"
      );
      setDecouverte(d.nouveaux ?? []);
      toast(
        `${d.nouveaux?.length ?? 0} nouveaux modeles disponibles (sur ${d.total} scannes)`,
        "success"
      );
    } catch (err) {
      toast(`Recherche impossible : ${err}`, "error");
    } finally {
      setDecouvreEnCours(false);
    }
  }, [toast]);

  const installModel = useCallback(
    async (tag: string) => {
      setBusyTag(tag);
      setProgress((p) => ({ ...p, [tag]: { percent: 0, status: "Démarrage…" } }));
      try {
        await streamSSE("/api/models/install", { tag }, (event) => {
          if (event.type === "progress") {
            setProgress((p) => ({
              ...p,
              [tag]: {
                percent: event.percent as number,
                status:
                  (event.status as string) === "pulling manifest"
                    ? "Récupération du manifeste…"
                    : `Téléchargement ${(event.percent as number).toFixed(0)}%`,
              },
            }));
          } else if (event.type === "done") {
            setProgress((p) => ({ ...p, [tag]: { percent: 100, status: "Terminé" } }));
          } else if (event.type === "error") {
            throw new Error(String(event.error));
          }
        });
        await refreshModels();
        toast(`Modèle ${tag} installé`, "success");
      } catch (err) {
        toast(`Installation échouée : ${err}`, "error");
      } finally {
        setBusyTag("");
        setTimeout(() => setProgress((p) => omit(p, tag)), 1500);
      }
    },
    [refreshModels, toast]
  );

  const updateAll = useCallback(async () => {
    const installedTags = models.filter((m) => m.installed).map((m) => m.tag);
    if (installedTags.length === 0) return;
    let updated = 0;
    let failed = 0;
    for (const tag of installedTags) {
      setBusyTag(tag);
      setProgress((p) => ({ ...p, [tag]: { percent: 0, status: "Vérification…" } }));
      try {
        let confirmed = false;
        let serverError = "";
        await streamSSE("/api/models/update", { tags: [tag] }, (event) => {
          if (event.type === "model_done" && event.tag === tag) {
            confirmed = event.ok === true;
          } else if (event.type === "error") {
            serverError = String(event.error ?? "Erreur Ollama");
          }
        });
        if (!confirmed) {
          throw new Error(serverError || "Ollama n’a pas confirmé la mise à jour");
        }
        updated += 1;
        setProgress((p) => ({ ...p, [tag]: { percent: 100, status: "À jour" } }));
      } catch (err) {
        failed += 1;
        setProgress((p) => ({
          ...p,
          [tag]: { percent: 0, status: `Échec : ${String(err)}` },
        }));
      }
    }
    setBusyTag("");
    await refreshModels();
    toast(
      `Mises à jour : ${updated} réussie(s), ${failed} échec(s)`,
      failed > 0 ? "error" : "success"
    );
  }, [models, refreshModels, toast]);

  const loadModel = useCallback(
    async (tag: string) => {
      try {
        toast(`Chargement de ${tag} — Ollama répartit automatiquement VRAM/RAM…`);
        await sendJSON("/api/models/load", "POST", { tag });
        await refreshModels();
      } catch (err) {
        toast(`Chargement échoué : ${err}`, "error");
      }
    },
    [refreshModels, toast]
  );

  const unloadModel = useCallback(
    async (tag: string) => {
      try {
        await sendJSON("/api/models/unload", "POST", { tag });
        await refreshModels();
      } catch (err) {
        toast(`Déchargement échoué : ${err}`, "error");
      }
    },
    [refreshModels, toast]
  );

  const deleteModel = useCallback(
    async (tag: string) => {
      if (!window.confirm(`Supprimer définitivement ${tag} du disque ?`)) return;
      try {
        await sendJSON(`/api/models/${encodeURIComponent(tag)}`, "DELETE");
        await refreshModels();
        toast(`Modèle ${tag} supprimé`, "success");
      } catch (err) {
        toast(`Suppression échouée : ${err}`, "error");
      }
    },
    [refreshModels, toast]
  );

  const runBenchmark = useCallback(async () => {
    const installedTags = models
      .filter((m) => m.installed && !m.tag.includes("embed"))
      .map((m) => m.tag);
    if (installedTags.length === 0) return;
    setBenchmarking(true);
    setBenchResults([]);
    try {
      await streamSSE("/api/benchmark", { tags: installedTags }, (event) => {
        if (event.type === "model_start") {
          setBenchCurrent(event.tag as string);
        } else if (event.type === "model_done") {
          setBenchResults((prev) => [
            ...prev,
            {
              tag: event.tag as string,
              score_percent: event.score_percent as number,
              avg_tokens_per_second: event.avg_tokens_per_second as number,
              avg_latency_ms: event.avg_latency_ms as number,
            },
          ]);
        }
      });
      toast("Benchmark terminé", "success");
    } catch (err) {
      toast(`Benchmark échoué : ${err}`, "error");
    } finally {
      setBenchmarking(false);
      setBenchCurrent("");
    }
  }, [models, toast]);

  // Nombre de modeles par tier : rend visible le remplissage des paliers et
  // se met a jour des que /api/models repond.
  const compteParTier = useCallback(
    (t: string) => models.filter((m) => tierDe(m) === t).length,
    [models]
  );

  const listeAffichee = tierFiltre
    ? models.filter((m) => tierDe(m) === tierFiltre)
    : models;

  return (
    <>
      <div className="view-header">
        <h2>Modèles</h2>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
          {models.filter((m) => m.installed).length} installés
        </span>
      </div>
      <div className="content-scroll">
        {/* Consigne : filtre par tier + recherche de nouveaux modeles */}
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <span style={{ color: "var(--text-dim)", fontSize: 12.5 }}>Tier :</span>
          <div className="tier-chips">
            {TIERS.map((t) => (
              <button
                key={t || "tous"}
                className={`tier-chip ${tierFiltre === t ? "active" : ""} ${
                  t && compteParTier(t) === 0 ? "vide" : ""
                }`}
                title={
                  t
                    ? `${compteParTier(t)} modele(s) en ${t}`
                    : `${models.length} modeles au catalogue`
                }
                onClick={() => setTierFiltre(t)}
              >
                {t || "Tous"} ({t ? compteParTier(t) : models.length})
              </button>
            ))}
          </div>
          <span style={{ flex: 1 }} />
          <button
            className="primary"
            disabled={decouvreEnCours}
            onClick={() => void decouvrir()}
          >
            {decouvreEnCours ? "Recherche en cours..." : "Nouveaux modeles"}
          </button>
        </div>
        {decouverte !== null && (
          <div className="panel discover-panel" style={{ marginBottom: 14 }}>
            <h3>Nouveaux modeles disponibles ({decouverte.length})</h3>
            {decouverte.length === 0 ? (
              <div style={{ color: "var(--text-dim)", fontSize: 12.5 }}>
                Aucun nouveau modele trouve au-dela du catalogue local.
              </div>
            ) : (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {decouverte.map((m) => (
                  <button
                    key={m.tag}
                    className="disc-chip"
                    disabled={!!busyTag}
                    title={`Installer ${m.tag}`}
                    onClick={() => void installModel(m.tag)}
                  >
                    + {m.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {optionnels.length > 0 && (
          <div className="toolbar" style={{ flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
            <span style={{ color: "var(--text-dim)", fontSize: 12.5, width: "100%" }}>
              Optionnels recommandés pour cette machine (GLM, DeepSeek-R1, Phi…) :
            </span>
            {optionnels.map((tag) => {
              const installe = estInstalle(tag);
              return (
                <button
                  key={tag}
                  className={installe ? "" : "primary"}
                  disabled={installe || !!busyTag}
                  onClick={() => void installModel(tag)}
                  style={{ fontSize: 12, padding: "4px 10px", borderRadius: 14 }}
                >
                  {installe ? `✓ ${tag}` : busyTag === tag ? "…" : `+ ${tag}`}
                </button>
              );
            })}
          </div>
        )}
        <div className="toolbar">
          <input
            placeholder="Tag Ollama personnalisé (ex: llama3.2:3b)"
            value={customTag}
            onChange={(e) => setCustomTag(e.target.value)}
            style={{ width: 320 }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && customTag.trim()) {
                void installModel(customTag.trim());
                setCustomTag("");
              }
            }}
          />
          <button
            className="primary"
            disabled={!customTag.trim() || !!busyTag}
            onClick={() => {
              void installModel(customTag.trim());
              setCustomTag("");
            }}
          >
            Installer ce modèle
          </button>
          <span style={{ flex: 1 }} />
          <button disabled={!!busyTag} onClick={() => void updateAll()}>
            Tout mettre à jour
          </button>
          <button disabled={benchmarking} onClick={() => void runBenchmark()}>
            Lancer le benchmark
          </button>
          <button onClick={() => void refreshModels()}>Rafraîchir</button>
        </div>

        {benchResults.length > 0 && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <h3>Benchmark {benchCurrent ? `— en cours : ${benchCurrent}` : ""}</h3>
            {[...benchResults]
              .sort((a, b) => b.score_percent - a.score_percent)
              .map((r) => (
                <div key={r.tag} style={{ marginBottom: 10 }}>
                  <div className="progress-label" style={{ marginBottom: 3 }}>
                    <span>{r.tag}</span>
                    <span>
                      score {r.score_percent}% · {r.avg_tokens_per_second} tok/s ·{" "}
                      {(r.avg_latency_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${r.score_percent}%` }} />
                  </div>
                </div>
              ))}
            <div style={{ color: "var(--text-faint)", fontSize: 11.5 }}>
              Tests : maths, code, raisonnement, français — température 0.
            </div>
          </div>
        )}

        {listeAffichee.length === 0 && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
              Aucun modele dans {tierFiltre}. Utilisez « Nouveaux modeles » pour en
              decouvrir, ou choisissez un autre tier.
            </span>
          </div>
        )}
        <div className="models-grid">
          {listeAffichee
            .map((m) => (
            <div key={m.tag} className={`model-card ${m.loaded ? "running" : ""}`}>
              <div className="model-head">
                <span className="model-name" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 18 }}>{modelIcon(m)}</span>
                {m.name}
              </span>
                {m.loaded ? (
                  <span className="chip green">en mémoire</span>
                ) : m.installed ? (
                  <span className="chip accent">installé</span>
                ) : (
                  <span className="chip">non installé</span>
                )}
              </div>
              <div className="model-tag">
              {m.tag}
              {m.hors_config ? (
                <span className="chip" style={{ marginLeft: 8, color: "var(--red)", borderColor: "rgba(var(--danger-rgb), 0.4)" }}>
                  hors config
                </span>
              ) : m.fit === "moe_offload" ? (
                <span className="chip" style={{ marginLeft: 8, color: "var(--orange)" }}>MoE + RAM</span>
              ) : m.fit === "gpu_partial" ? (
                <span className="chip" style={{ marginLeft: 8, color: "var(--orange)" }}>GPU partiel</span>
              ) : m.fit === "gpu_full" ? (
                <span className="chip green" style={{ marginLeft: 8 }}>100% GPU</span>
              ) : null}
            </div>
              {m.description && <div className="model-desc">{m.description}</div>}
              <div className="model-specs">
                {(m.parameter_size || m.size_label) && (
                  <span className="chip">{m.parameter_size || m.size_label}</span>
                )}
                {m.quantization && <span className="chip">{m.quantization}</span>}
                {m.vram_gb > 0 && <span className="chip">≈{m.vram_gb} Go VRAM</span>}
                {m.context > 0 && (
                <span className="chip" title="Fenêtre de contexte" style={{ cursor: "help" }}>📏 {Math.round(m.context / 1024)}k ctx</span>
              )}
                {m.installed && m.size_bytes > 0 && (
                  <span className="chip">disque : {formatSize(m.size_bytes)}</span>
                )}
                {m.loaded && m.runtime && m.runtime.gpu_memory_share_pct != null && (
                  <span className="chip green">GPU {m.runtime.gpu_memory_share_pct}%</span>
                )}
                {m.loaded && m.runtime && m.runtime.cpu_bytes_estimated > 0 && (
                  <span className="chip">RAM ≈{formatSize(m.runtime.cpu_bytes_estimated)}</span>
                )}
                {m.loaded && m.runtime && m.runtime.context_length != null && (
                  <span className="chip">contexte actif {Math.round(m.runtime.context_length / 1024)}k</span>
                )}
                {m.capabilities.map((c) => (
                  <span key={c} className="chip">
                    {c}
                  </span>
                ))}
              </div>

              {progress[m.tag] && (
                <div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${progress[m.tag].percent}%` }} />
                  </div>
                  <div className="progress-label">
                    <span>{progress[m.tag].status}</span>
                    <span>{progress[m.tag].percent.toFixed(0)}%</span>
                  </div>
                </div>
              )}

              <div className="model-actions">
                {!m.installed && !m.hors_config && (
                  <button
                    className="primary"
                    disabled={!!busyTag}
                    onClick={() => void installModel(m.tag)}
                  >
                    Installer
                  </button>
                )}
                {!m.installed && m.hors_config && (
                  <button disabled title="Modèle trop grand pour cette machine (RAM + VRAM insuffisantes)">
                    Hors config
                  </button>
                )}
                {m.installed && !m.loaded && (
                  <button onClick={() => void loadModel(m.tag)}>Charger</button>
                )}
                {m.loaded && (
                  <button onClick={() => void unloadModel(m.tag)}>Décharger</button>
                )}
                {m.installed && (
                  <button
                    className="danger"
                    disabled={!!busyTag}
                    onClick={() => void deleteModel(m.tag)}
                  >
                    Supprimer
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function omit(obj: Record<string, ProgressState>, key: string): Record<string, ProgressState> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}
