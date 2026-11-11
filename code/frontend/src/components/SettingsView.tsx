import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MemoryItem, SystemInfo, Voice } from "../lib/api";
import { getJSON, sendJSON } from "../lib/api";
import { LANGS, type Lang, t } from "../lib/i18n";
import type { ToastFn } from "../lib/toast";
import { APERCU, SKINS, type Skin } from "../lib/skins";

interface Props {
  systemInfo: SystemInfo | null;
  refreshSystem: () => void;
  toast: ToastFn;
  lang: Lang;
  onLangChange: (lang: Lang) => void;
  skin: Skin;
  onSkinChange: (skin: Skin) => void;
}

interface FeedbackStats {
  total: number;
  moyenne: number;
  auto_evals?: { n: number; moyenne: number };
}
interface Regle {
  id: number;
  regle: string;
  source: string;
  created_at: string;
}

function Meter({ value }: { value: number }) {
  const cls = value >= 90 ? "crit" : value >= 70 ? "warn" : "";
  return (
    <div className="meter">
      <div className={`meter-fill ${cls}`} style={{ width: `${Math.min(100, value)}%` }} />
    </div>
  );
}

export default function SettingsView({
  systemInfo,
  refreshSystem,
  toast,
  lang,
  onLangChange,
  skin,
  onSkinChange,
}: Props) {
  const [settings, setSettings] = useState<Record<string, string>>({});
  // Dernieres valeurs connues du serveur : un champ n'est enregistre que
  // s'il a reellement change.
  const enregistres = useRef<Record<string, string>>({});
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [rate, setRate] = useState(185);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [newMemory, setNewMemory] = useState("");
  const [editingMemoryId, setEditingMemoryId] = useState<number | null>(null);
  const [editingMemoryContent, setEditingMemoryContent] = useState("");
  const [feedback, setFeedback] = useState<FeedbackStats | null>(null);
  const [regles, setRegles] = useState<Regle[]>([]);
  const [coaching, setCoaching] = useState(false);
  // Connecteurs : Meshy / Telegram / Discord (cles locales, jamais lues en clair).
  const [connecteurs, setConnecteurs] = useState<Record<string, boolean>>({});
  const [connecteurSaisie, setConnecteurSaisie] = useState<Record<string, string>>({});
  const [testsEnCours, setTestsEnCours] = useState<Record<string, boolean>>({});
  const [testResultats, setTestResultats] = useState<Record<string, string>>({});
  const [fournisseurs, setFournisseurs] = useState<
    { id: string; nom: string; configue: boolean; modele: string }[]
  >([]);
  const [fournisseurSaisie, setFournisseurSaisie] = useState<Record<string, string>>({});
  const [fournisseurTests, setFournisseurTests] = useState<Record<string, string>>({});
  // public-apis : catalogue d'APIs publiques + gestion des cles par service.
  const [publicApis, setPublicApis] = useState<
    { name: string; description: string; auth: string; https: boolean; cors: string; link: string; category: string; kind: string }[]
  >([]);
  const [paUpdated, setPaUpdated] = useState("");
  const [paLoading, setPaLoading] = useState(false);
  const [paRefreshing, setPaRefreshing] = useState(false);
  const [paFilter, setPaFilter] = useState("");
  const [paCategory, setPaCategory] = useState("");
  const [paAuth, setPaAuth] = useState("all");
  const [paKind, setPaKind] = useState("all");
  const [paVisible, setPaVisible] = useState(40);
  const [paKeys, setPaKeys] = useState<Record<string, { configured: boolean; masked: string }>>({});
  const [paKeyInput, setPaKeyInput] = useState<Record<string, string>>({});
  const [paKeyBusy, setPaKeyBusy] = useState<Record<string, boolean>>({});  // Consigne §6 : compteur global de tokens depuis l'installation.
  const [usage, setUsage] = useState<{
    total_messages: number;
    tokens_in: number;
    tokens_out: number;
    premiere_utilisation: string;
    par_modele: { model: string; n: number; tin: number; tout: number }[];
  } | null>(null);

  const texte = useCallback((key: string) => t(lang, key), [lang]);

  const refreshMemories = useCallback(() => {
    getJSON<{ memories: MemoryItem[] }>("/api/memory")
      .then((d) => setMemories(d.memories ?? []))
      .catch(() => undefined);
  }, []);

  const refreshUsage = useCallback(() => {
    getJSON<{
      total_messages: number;
      tokens_in: number;
      tokens_out: number;
      premiere_utilisation: string;
      par_modele: { model: string; n: number; tin: number; tout: number }[];
    }>("/api/tokens/usage")
      .then(setUsage)
      .catch(() => undefined);
  }, []);

  const refreshFeedback = useCallback(() => {
    getJSON<FeedbackStats>("/api/feedback/stats")
      .then(setFeedback)
      .catch(() => undefined);
    getJSON<{ regles: Regle[] }>("/api/selfimprove/rules")
      .then((d) => setRegles(d.regles ?? []))
      .catch(() => undefined);
  }, []);

  const refreshConnecteurs = useCallback(() => {
    getJSON<{ services: { service: string; champs: { champ: string; present: boolean }[] }[] }>(
      "/api/connecteurs"
    )
      .then((d) => {
        const etat: Record<string, boolean> = {};
        for (const s of d.services ?? []) {
          for (const c of s.champs ?? []) {
            etat[`${s.service}_${c.champ}`] = c.present;
          }
        }
        setConnecteurs(etat);
      })
      .catch(() => undefined);
  }, []);

  const refreshFournisseurs = useCallback(() => {
    getJSON<{ fournisseurs: { id: string; nom: string; configue: boolean; modele: string }[] }>(
      "/api/fournisseurs"
    )
      .then((d) => setFournisseurs(d.fournisseurs ?? []))
      .catch(() => undefined);
  }, []);

  const slugifyPa = useCallback((s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""), []);

  const refreshPublicApis = useCallback(() => {
    setPaLoading(true);
    getJSON<{ entries: typeof publicApis; updated_at: string }>("/api/public-apis")
      .then((d) => {
        setPublicApis((d.entries ?? []) as typeof publicApis);
        setPaUpdated(d.updated_at ?? "");
      })
      .catch(() => undefined)
      .finally(() => setPaLoading(false));
  }, []);

  const rafraichirPublicApis = useCallback(async () => {
    setPaRefreshing(true);
    try {
      const d = await sendJSON<{ entries: typeof publicApis; updated_at: string }>(
        "/api/public-apis/refresh",
        "POST"
      );
      setPublicApis((d.entries ?? []) as typeof publicApis);
      setPaUpdated(d.updated_at ?? "");
      toast("Catalogue mis à jour", "success");
    } catch (err) {
      toast(`Erreur : ${err}`, "error");
    } finally {
      setPaRefreshing(false);
    }
  }, [toast]);

  const refreshPaKeys = useCallback(() => {
    getJSON<{ keys: Record<string, { configured: boolean; masked: string }> }>("/api/public-apis/keys")
      .then((d) => setPaKeys(d.keys ?? {}))
      .catch(() => undefined);
  }, []);

  const enregistrerPaCle = useCallback(
    async (slug: string) => {
      const val = (paKeyInput[slug] ?? "").trim();
      // Champ vide : rien a enregistrer (la suppression a son bouton dedie).
      if (!val) {
        toast("Saisissez une clé avant d'enregistrer", "info");
        return;
      }
      setPaKeyBusy((s) => ({ ...s, [slug]: true }));
      try {
        await sendJSON(`/api/public-apis/keys/${slug}`, "PUT", { value: val });
        toast("Clé enregistrée", "success");
        setPaKeyInput((s) => ({ ...s, [slug]: "" }));
        refreshPaKeys();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      } finally {
        setPaKeyBusy((s) => ({ ...s, [slug]: false }));
      }
    },
    [paKeyInput, toast, refreshPaKeys]
  );

  const supprimerPaCle = useCallback(
    async (slug: string) => {
      setPaKeyBusy((s) => ({ ...s, [slug]: true }));
      try {
        await sendJSON(`/api/public-apis/keys/${slug}`, "DELETE");
        toast("Clé supprimée", "success");
        refreshPaKeys();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      } finally {
        setPaKeyBusy((s) => ({ ...s, [slug]: false }));
      }
    },
    [toast, refreshPaKeys]
  );

  const enregistrerFournisseur = useCallback(
    async (id: string) => {
      const cle = (fournisseurSaisie[`${id}_key`] ?? "").trim();
      const modele = (fournisseurSaisie[`${id}_model`] ?? "").trim();
      try {
        await sendJSON("/api/fournisseurs", "POST", {
          fournisseur: id,
          cle: cle || undefined,
          modele: modele || undefined,
        });
        toast(cle ? "Clé fournisseur enregistrée" : "Modèle mis à jour", "success");
        refreshFournisseurs();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [fournisseurSaisie, refreshFournisseurs, toast]
  );

  const testerFournisseur = useCallback(
    async (id: string) => {
      setFournisseurTests((r) => ({ ...r, [id]: "test…" }));
      try {
        const res = await sendJSON<{ ok: boolean; reponse?: string; detail?: string }>(
          "/api/fournisseurs/test",
          "POST",
          { fournisseur: id }
        );
        setFournisseurTests((r) => ({
          ...r,
          [id]: res.ok ? `✓ ${res.reponse ?? "OK"}` : `✗ ${res.detail ?? "échec"}`,
        }));
      } catch (err) {
        setFournisseurTests((r) => ({ ...r, [id]: `✗ ${err}` }));
      }
    },
    []
  );

  useEffect(() => {
    refreshUsage();
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => {
        const s = d.settings ?? {};
        enregistres.current = { ...s };
        setSettings(s);
        setVoiceId(s.tts_voice ?? "");
        setRate(Number(s.tts_rate ?? 185));
      })
      .catch(() => undefined);
    getJSON<{ voices: Voice[] }>("/api/tts/voices")
      .then((d) => setVoices(d.voices ?? []))
      .catch(() => undefined);
    refreshMemories();
    refreshFeedback();
    refreshConnecteurs();
    refreshFournisseurs();
    refreshPublicApis();
    refreshPaKeys();
  }, [refreshUsage, refreshMemories, refreshFeedback, refreshConnecteurs, refreshFournisseurs, refreshPublicApis, refreshPaKeys]);

  const enregistrerConnecteur = useCallback(
    async (service: string, champ: string) => {
      const valeur = (connecteurSaisie[`${service}_${champ}`] ?? "").trim();
      // Champ vide : ne jamais effacer la cle enregistree par megarde.
      if (!valeur) {
        toast("Saisissez une clé avant d'enregistrer", "info");
        return;
      }
      try {
        await sendJSON("/api/connecteurs", "PUT", { service, champ, valeur });
        toast("Clé enregistrée", "success");
        refreshConnecteurs();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [connecteurSaisie, refreshConnecteurs, toast]
  );

  // Suppression d'une cle : toujours confirmee, la cle quitte la base.
  const supprimerConnecteur = useCallback(
    async (service: string, nom: string) => {
      if (!window.confirm(`Supprimer la clé ${nom} ? Elle sera effacée de ce PC.`)) return;
      try {
        await sendJSON(`/api/connecteurs/${service}`, "DELETE");
        setConnecteurSaisie((s) => ({ ...s, [`${service}_token`]: "" }));
        setTestResultats((r) => ({ ...r, [service]: "" }));
        toast(`Clé ${nom} supprimée`, "success");
        refreshConnecteurs();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [refreshConnecteurs, toast]
  );

  const supprimerFournisseur = useCallback(
    async (id: string, nom: string) => {
      if (!window.confirm(`Supprimer la clé ${nom} ? Elle sera effacée de ce PC.`)) return;
      try {
        await sendJSON(`/api/fournisseurs/${id}`, "DELETE");
        setFournisseurSaisie((s) => ({ ...s, [`${id}_key`]: "" }));
        setFournisseurTests((r) => ({ ...r, [id]: "" }));
        toast(`Clé ${nom} supprimée`, "success");
        refreshFournisseurs();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [refreshFournisseurs, toast]
  );

  const supprimerCleReglage = useCallback(
    async (key: string, nom: string) => {
      if (!window.confirm(`Supprimer la clé ${nom} ? Elle sera effacée de ce PC.`)) return;
      try {
        const res = await sendJSON<{ ok: boolean; error?: string }>(`/api/settings/${key}`, "DELETE");
        if (!res.ok) throw new Error(res.error ?? "suppression refusée");
        enregistres.current[key] = "";
        setSettings((s) => ({ ...s, [key]: "" }));
        toast(`Clé ${nom} supprimée`, "success");
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [toast]
  );

  const testerConnecteur = useCallback(
    async (service: string) => {
      setTestsEnCours((s) => ({ ...s, [service]: true }));
      setTestResultats((r) => ({ ...r, [service]: "" }));
      try {
        const res = await sendJSON<{ ok: boolean; detail: string }>(
          "/api/connecteurs/test",
          "POST",
          { service }
        );
        setTestResultats((r) => ({
          ...r,
          [service]: `${res.ok ? "✓" : "✗"} ${res.detail}`,
        }));
      } catch (err) {
        setTestResultats((r) => ({ ...r, [service]: `✗ ${err}` }));
      } finally {
        setTestsEnCours((s) => ({ ...s, [service]: false }));
      }
    },
    []
  );

  const save = useCallback(
    async (key: string, value: string) => {
      try {
        await sendJSON("/api/settings", "PUT", { key, value });
        enregistres.current[key] = value;
        setSettings((s) => ({ ...s, [key]: value }));
        toast(texte("settings.saved"), "success");
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [toast, texte]
  );

  // Champ texte : saisie locale, enregistrement a la sortie du champ (et non
  // a chaque frappe, qui perdait des caracteres et tronquait les cles).
  const champ = (key: string) => ({
    value: settings[key] ?? "",
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setSettings((s) => ({ ...s, [key]: e.target.value })),
    onBlur: (e: React.FocusEvent<HTMLInputElement>) => {
      if (e.target.value !== (enregistres.current[key] ?? "")) void save(key, e.target.value);
    },
  });

  // Debit TTS : enregistre au relachement (souris, tactile ou clavier).
  const enregistrerDebit = (valeur: string) => {
    if (valeur !== (enregistres.current.tts_rate ?? "")) void save("tts_rate", valeur);
  };

  const changerLangue = useCallback(
    async (code: Lang) => {
      await save("language", code);
      onLangChange(code);
    },
    [save, onLangChange]
  );

  const testerVoix = useCallback(async () => {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: "Bonjour, je suis Néo, votre assistant local.",
          voice: voiceId || undefined,
          rate,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const audio = new Audio(URL.createObjectURL(blob));
      void audio.play();
    } catch (err) {
      toast(`TTS indisponible : ${err}`, "error");
    }
  }, [voiceId, rate, toast]);

  const ajouterMemoire = useCallback(async () => {
    const contenu = newMemory.trim();
    if (!contenu) return;
    try {
      await sendJSON("/api/memory", "POST", { content: contenu });
      setNewMemory("");
      refreshMemories();
      toast("Mémoire ajoutée", "success");
    } catch (err) {
      toast(`Erreur : ${err}`, "error");
    }
  }, [newMemory, refreshMemories, toast]);

  const supprimerMemoire = useCallback(
    async (id: number) => {
      try {
        await sendJSON(`/api/memory/${id}`, "DELETE");
        setMemories((m) => m.filter((x) => x.id !== id));
        if (editingMemoryId === id) setEditingMemoryId(null);
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [toast, editingMemoryId]
  );

  const updateMemoire = useCallback(
    async (id: number) => {
      const contenu = editingMemoryContent.trim();
      if (!contenu) return;
      try {
        await sendJSON(`/api/memory/${id}`, "PUT", { content: contenu });
        setMemories((m) =>
          m.map((x) => (x.id === id ? { ...x, content: contenu } : x))
        );
        setEditingMemoryId(null);
        toast("Mémoire modifiée", "success");
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [editingMemoryContent, toast]
  );

  const lancerCoach = useCallback(async () => {
    setCoaching(true);
    try {
      const r = await sendJSON<{ new_rules: number; analysed_signals: number }>(
        "/api/selfimprove/coach",
        "POST"
      );
      toast(
        `Coach : ${r.new_rules} nouvelle(s) règle(s), ${r.analysed_signals} signaux analysés`,
        "success"
      );
      refreshFeedback();
    } catch (err) {
      toast(`Coach indisponible : ${err}`, "error");
    } finally {
      setCoaching(false);
    }
  }, [refreshFeedback, toast]);

  const gpu = systemInfo?.gpus?.[0];

  const paCategories = useMemo(() => {
    const set = new Set(publicApis.map((e) => e.category).filter(Boolean));
    return [...set].sort();
  }, [publicApis]);

  const paFiltered = useMemo(() => {
    const q = paFilter.trim().toLowerCase();
    return publicApis.filter((e) => {
      if (paCategory && e.category !== paCategory) return false;
      if (paKind !== "all" && e.kind !== paKind) return false;
      const isKey = (e.auth || "").toLowerCase().includes("apikey");
      if (paAuth === "apiKey" && !isKey) return false;
      if (paAuth === "no" && isKey) return false;
      if (q) {
        const hay = `${e.name} ${e.description} ${e.category}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [publicApis, paFilter, paCategory, paKind, paAuth]);

  return (
    <>
      <div className="view-header">
        <h2>{texte("settings.title")}</h2>
        <span style={{ flex: 1 }} />
        <button onClick={refreshSystem}>{texte("settings.refresh")}</button>
      </div>
      <div className="content-scroll">
        <div className="settings-grid">
          {/* Langue */}
          <div className="panel">
            <h3>Utilisation</h3>
        {usage && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <div
              style={{
                display: "flex",
                gap: 22,
                flexWrap: "wrap",
                fontSize: 13,
                marginBottom: 10,
              }}
            >
              <span>
                ↑ entrée <strong>{usage.tokens_in.toLocaleString("fr-FR")}</strong>
              </span>
              <span>
                ↓ sortie <strong>{usage.tokens_out.toLocaleString("fr-FR")}</strong>
              </span>
              <span>
                Σ total{" "}
                <strong>{(usage.tokens_in + usage.tokens_out).toLocaleString("fr-FR")}</strong>
              </span>
              <span>
                {usage.total_messages.toLocaleString("fr-FR")} réponses
              </span>
            </div>
            {usage.premiere_utilisation && (
              <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginBottom: 8 }}>
                Depuis le {usage.premiere_utilisation.slice(0, 10)}
              </div>
            )}
            <div style={{ fontSize: 12 }}>
              {usage.par_modele.slice(0, 6).map((m) => (
                <div
                  key={m.model}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "2px 0",
                    color: "var(--text-dim)",
                  }}
                >
                  <span>{m.model}</span>
                  <span>
                    {m.n} rép. · {(m.tin + m.tout).toLocaleString("fr-FR")} tok.
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        <h3>{texte("settings.language")}</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              {texte("settings.languageHint")}
            </p>
            <select
              value={lang}
              onChange={(e) => void changerLangue(e.target.value as Lang)}
              style={{ width: "100%" }}
            >
              {LANGS.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.flag} {l.label}
                </option>
              ))}
            </select>
          </div>

          {/* Apparence / Skin */}
          <div className="panel">
            <h3>{texte("settings.skinTitle")}</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              {texte("settings.skinHint")}
            </p>
            <div className="skin-galerie">
              {SKINS.map((id) => (
                <button
                  key={id}
                  className={`skin-card ${skin === id ? "active" : ""}`}
                  onClick={() => void onSkinChange(id)}
                  style={{ ["--skin-accent" as string]: APERCU[id][1] }}
                  title={`${texte(`skin.${id}`)} — ${texte(`skin.${id}.desc`)}`}
                >
                  <span className={`skin-apercu apercu-${id}`} aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span className="skin-card-txt">
                    <strong>{texte(`skin.${id}`)}</strong>
                    <em>{texte(`skin.${id}.desc`)}</em>
                  </span>
                  <span className="skin-check">{skin === id ? "✓" : ""}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Système */}
          <div className="panel">
            <h3>{texte("settings.system")}</h3>
            {systemInfo ? (
              <>
                <div className="kv">
                  <span className="k">CPU ({systemInfo.cpu.cores_logical} threads)</span>
                  <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    {systemInfo.cpu.percent}% <Meter value={systemInfo.cpu.percent} />
                  </span>
                </div>
                <div className="kv">
                  <span className="k">RAM</span>
                  <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    {systemInfo.ram.used_gb} / {systemInfo.ram.total_gb} Go{" "}
                    <Meter value={systemInfo.ram.percent} />
                  </span>
                </div>
                {(systemInfo.gpus ?? []).map((g) => (
                  <div className="kv" key={g.name}>
                    <span className="k">{g.name}</span>
                    <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      {(g.vram_used_mb / 1024).toFixed(1)} / {(g.vram_total_mb / 1024).toFixed(0)} Go
                      · {g.temperature_c}°C
                      <Meter
                        value={g.vram_total_mb > 0 ? (g.vram_used_mb / g.vram_total_mb) * 100 : 0}
                      />
                    </span>
                  </div>
                ))}
                {gpu && (
                  <div className="kv">
                    <span className="k">GPU</span>
                    <span>{gpu.utilization_percent}%</span>
                  </div>
                )}
                {(systemInfo.disks ?? []).map((d) => (
                  <div className="kv" key={d.mount}>
                    <span className="k">Disque {d.mount}</span>
                    <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      {d.free_gb} Go libres <Meter value={d.used_percent} />
                    </span>
                  </div>
                ))}
              </>
            ) : (
              <div style={{ color: "var(--text-dim)" }}>{texte("common.loading")}</div>
            )}
          </div>

          {/* Assistant : outils + apprentissage */}
          <div className="panel">
            <h3>Assistant</h3>
            <div className="settings-row">
              <label>{texte("settings.tools")}</label>
              <select
                value={settings.tool_level ?? "USER"}
                onChange={(e) => void save("tool_level", e.target.value)}
              >
                <option value="SAFE">SAFE — calculs/date</option>
                <option value="USER">USER — lecture + web</option>
                <option value="POWER">POWER — écriture + terminal</option>
              </select>
            </div>
            <label className="toggle" style={{ marginTop: 8 }}>
              <input
                type="checkbox"
                checked={(settings.auto_learn ?? "1") === "1"}
                onChange={(e) => void save("auto_learn", e.target.checked ? "1" : "0")}
              />
              {texte("settings.autoLearn")}
            </label>
          </div>

          {/* Clés API publiques gratuites */}
          <div className="panel">
            <h3>Clés API (services externes gratuits)</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Les clés restent sur ce PC (chiffrées au repos). Sans clé, Néo
              fonctionne en mode dégradé (repli local).
            </p>
            <div className="settings-row" style={{ marginBottom: 10 }}>
              <label>ElevenLabs (TTS voix)</label>
              <input
                type="password"
                placeholder="sk_…"
                {...champ("api_key_elevenlabs")}
                style={{ flex: 1, marginRight: 6 }}
              />
              {settings.api_key_elevenlabs && (
                <button aria-label="Supprimer la clé ElevenLabs" title="Supprimer la clé" style={{ marginRight: 6 }} onClick={() => void supprimerCleReglage("api_key_elevenlabs", "ElevenLabs")}>
                  Supprimer
                </button>
              )}
              <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
                10 000 car/mois gratuit
              </span>
            </div>
            <div className="settings-row" style={{ marginBottom: 10 }}>
              <label>DeepL (traduction)</label>
              <input
                type="password"
                placeholder="deepl_key…"
                {...champ("api_key_deepl")}
                style={{ flex: 1, marginRight: 6 }}
              />
              {settings.api_key_deepl && (
                <button aria-label="Supprimer la clé DeepL" title="Supprimer la clé" style={{ marginRight: 6 }} onClick={() => void supprimerCleReglage("api_key_deepl", "DeepL")}>
                  Supprimer
                </button>
              )}
              <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
                500 000 car/mois gratuit
              </span>
            </div>
            <div className="settings-row">
              <label>Unsplash (images)</label>
              <input
                type="password"
                placeholder="access_key…"
                {...champ("api_key_unsplash")}
                style={{ flex: 1, marginRight: 6 }}
              />
              {settings.api_key_unsplash && (
                <button aria-label="Supprimer la clé Unsplash" title="Supprimer la clé" style={{ marginRight: 6 }} onClick={() => void supprimerCleReglage("api_key_unsplash", "Unsplash")}>
                  Supprimer
                </button>
              )}
              <span style={{ fontSize: 11, color: "var(--text-faint)" }}>
                Clé d'application gratuite
              </span>
            </div>
          </div>

          {/* Accès Web / Identité */}
          <div className="panel">
            <h3>{texte("settings.webAccess")}</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              {texte("settings.webUserHint")}
            </p>
            <div className="settings-row">
              <label>{texte("settings.webUser")}</label>
              <input
                type="text"
                placeholder={texte("settings.webUserPlaceholder")}
                {...champ("web_user")}
                maxLength={32}
                pattern="[a-zA-Z0-9-]+"
                title="Lettres, chiffres, tirets uniquement"
              />
            </div>
            <div className="settings-row">
              <label>{texte("settings.displayName")}</label>
              <input
                type="text"
                placeholder={texte("settings.displayNamePlaceholder")}
                {...champ("display_name")}
                maxLength={32}
              />
            </div>
          </div>

          {/* Voix */}
          <div className="panel">
            <h3>{texte("settings.voice")}</h3>
            <div className="settings-row">
              <label>Voix</label>
              <select
                value={voiceId}
                onChange={(e) => {
                  setVoiceId(e.target.value);
                  void save("tts_voice", e.target.value);
                }}
              >
                <option value="">Auto (français)</option>
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="settings-row">
              <label>Débit</label>
              <input
                type="range"
                min={120}
                max={260}
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
                onPointerUp={(e) => enregistrerDebit(e.currentTarget.value)}
                onKeyUp={(e) => enregistrerDebit(e.currentTarget.value)}
              />
              <span className="chip">{rate}</span>
            </div>
            <button onClick={() => void testerVoix()}>{texte("settings.voiceTest")}</button>
          </div>

          {/* Mémoire */}
          <div className="panel">
            <h3>{texte("settings.memoryTitle")}</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              {texte("settings.memoryHint")}
            </p>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <input
                placeholder={texte("settings.memoryPlaceholder")}
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                style={{ flex: 1 }}
                onKeyDown={(e) => e.key === "Enter" && void ajouterMemoire()}
              />
              <button className="primary" onClick={() => void ajouterMemoire()}>
                {texte("settings.remember")}
              </button>
            </div>
            <div className="memory-list">
              {memories.length === 0 && (
                <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>—</div>
              )}
              {memories.map((m) => {
                const enEdition = editingMemoryId === m.id;
                return (
                  <div className="memory-item" key={m.id}>
                    {enEdition ? (
                      <>
                        <input
                          value={editingMemoryContent}
                          onChange={(e) =>
                            setEditingMemoryContent(e.target.value)
                          }
                          autoFocus
                          style={{ flex: 1 }}
                          onKeyDown={(e) =>
                            e.key === "Enter"
                              ? void updateMemoire(m.id)
                              : e.key === "Escape"
                              ? void setEditingMemoryId(null)
                              : null
                          }
                        />
                        <button
                          className="primary"
                          onClick={() => void updateMemoire(m.id)}
                          title={texte("settings.save")}
                        >
                          ✓
                        </button>
                        <button
                          onClick={() => void setEditingMemoryId(null)}
                          title={texte("settings.cancel")}
                          aria-label={texte("settings.cancel")}
                        >
                          ✕
                        </button>
                      </>
                    ) : (
                      <>
                        <span>{m.content}</span>
                        <button
                          onClick={() => {
                            setEditingMemoryContent(m.content);
                            setEditingMemoryId(m.id);
                          }}
                          title={texte("settings.edit")}
                        >
                          ✎
                        </button>
                        <button onClick={() => void supprimerMemoire(m.id)} title="Supprimer" aria-label="Supprimer">
                          ✕
                        </button>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Auto-amélioration */}
          <div className="panel">
            <h3>{texte("settings.coach")}</h3>
            <div className="kv">
              <span className="k">{texte("settings.feedback")}</span>
              <span>{feedback?.total ?? 0}</span>
            </div>
            <div className="kv">
              <span className="k">{texte("settings.average")}</span>
              <span>{feedback ? feedback.moyenne.toFixed(1) : "—"}</span>
            </div>
            <div className="kv">
              <span className="k">{texte("settings.autoEvals")}</span>
              <span>{feedback?.auto_evals?.n ?? 0}</span>
            </div>
            <div className="kv">
              <span className="k">{texte("settings.rules")}</span>
              <span>{regles.length}</span>
            </div>
            {regles.length > 0 && (
              <div className="tool-trace">
                {regles.slice(0, 3).map((r) => (
                  <div className="tool-line" key={r.id}>
                    • {r.regle.slice(0, 120)}
                  </div>
                ))}
              </div>
            )}
            <button
              className="primary"
              style={{ marginTop: 10 }}
              disabled={coaching}
              onClick={() => void lancerCoach()}
            >
              {coaching ? texte("common.loading") : texte("settings.coachRun")}
            </button>
          </div>

          {/* Connexions (cles API optionnelles) */}
          <div className="panel">
            <h3>Connexions</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Services optionnels — tout fonctionne sans eux. Les clés restent sur ce PC.
            </p>

            <div className="kv" style={{ display: "block" }}>
              <span className="k">Meshy — 3D haute qualité (200 crédits/mois gratuits)</span>
              <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                <input
                  type="password"
                  placeholder="msy_…"
                  value={connecteurSaisie.meshy_token ?? ""}
                  onChange={(e) =>
                    setConnecteurSaisie((s) => ({ ...s, meshy_token: e.target.value }))
                  }
                  style={{ flex: 1 }}
                />
                <button aria-label="Enregistrer la clé" title="Enregistrer la clé" onClick={() => void enregistrerConnecteur("meshy", "token")}>💾</button>
                <button disabled={testsEnCours.meshy} onClick={() => void testerConnecteur("meshy")}>
                  Tester
                </button>
                {connecteurs.meshy_token && (
                  <button aria-label="Supprimer la clé Meshy" title="Supprimer la clé" onClick={() => void supprimerConnecteur("meshy", "Meshy")}>
                    Supprimer
                  </button>
                )}
              </div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>
                {connecteurs.meshy_token ? (
                  <span className="status-ok">clé présente · </span>
                ) : (
                  <span style={{ color: "var(--text-faint)" }}>aucune clé · </span>
                )}
                {testResultats.meshy}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
                Usage : demande un modèle 3D « avec Meshy » dans le chat. Sans cette précision, la 3D reste générée sur ce PC.
              </div>
            </div>

            <div className="kv" style={{ display: "block" }}>
              <span className="k">Telegram — contrôle à distance</span>
              <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                <input
                  type="password"
                  placeholder="123456:ABC-…"
                  value={connecteurSaisie.telegram_token ?? ""}
                  onChange={(e) =>
                    setConnecteurSaisie((s) => ({ ...s, telegram_token: e.target.value }))
                  }
                  style={{ flex: 1 }}
                />
                <button aria-label="Enregistrer la clé" title="Enregistrer la clé" onClick={() => void enregistrerConnecteur("telegram", "token")}>💾</button>
                <button disabled={testsEnCours.telegram} onClick={() => void testerConnecteur("telegram")}>
                  Tester
                </button>
                {connecteurs.telegram_token && (
                  <button aria-label="Supprimer la clé Telegram" title="Supprimer la clé" onClick={() => void supprimerConnecteur("telegram", "Telegram")}>
                    Supprimer
                  </button>
                )}
              </div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>
                {connecteurs.telegram_token ? (
                  <span className="status-ok">clé présente · </span>
                ) : (
                  <span style={{ color: "var(--text-faint)" }}>aucune clé · </span>
                )}
                {testResultats.telegram}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
                Créer un bot : Telegram → @BotFather → /newbot → copier le token.
              </div>
            </div>

            <div className="kv" style={{ display: "block" }}>
              <span className="k">Discord — contrôle à distance (à venir)</span>
              <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                <input
                  type="password"
                  placeholder="MTIz…"
                  value={connecteurSaisie.discord_token ?? ""}
                  onChange={(e) =>
                    setConnecteurSaisie((s) => ({ ...s, discord_token: e.target.value }))
                  }
                  style={{ flex: 1 }}
                />
                <button aria-label="Enregistrer la clé" title="Enregistrer la clé" onClick={() => void enregistrerConnecteur("discord", "token")}>💾</button>
                <button disabled={testsEnCours.discord} onClick={() => void testerConnecteur("discord")}>
                  Tester
                </button>
                {connecteurs.discord_token && (
                  <button aria-label="Supprimer la clé Discord" title="Supprimer la clé" onClick={() => void supprimerConnecteur("discord", "Discord")}>
                    Supprimer
                  </button>
                )}
              </div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>
                {connecteurs.discord_token ? (
                  <span className="status-ok">clé présente · </span>
                ) : (
                  <span style={{ color: "var(--text-faint)" }}>aucune clé · </span>
                )}
                {testResultats.discord}
              </div>
            </div>
          </div>
        </div>

        {/* Fournisseurs de modeles cloud (cles API) */}
        <div className="panel" style={{ marginTop: 14 }}>
          <h3>Fournisseurs de modèles (cloud)</h3>
          <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
            Ajoutez une clé API pour rendre un modèle cloud disponible dans le
            sélecteur du chat (préfixe ☁). Sans clé, tout reste 100 % local.
          </p>
          {fournisseurs.map((f) => (
            <div key={f.id} className="kv" style={{ display: "block", marginBottom: 12 }}>
              <span className="k">
                {f.nom} — {f.configue ? "configuré" : "non configuré"}
              </span>
              <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                <input
                  type="password"
                  placeholder="clé API…"
                  value={fournisseurSaisie[`${f.id}_key`] ?? ""}
                  onChange={(e) =>
                    setFournisseurSaisie((s) => ({ ...s, [`${f.id}_key`]: e.target.value }))
                  }
                  style={{ flex: 2 }}
                />
                <input
                  placeholder={f.modele}
                  value={fournisseurSaisie[`${f.id}_model`] ?? ""}
                  onChange={(e) =>
                    setFournisseurSaisie((s) => ({ ...s, [`${f.id}_model`]: e.target.value }))
                  }
                  style={{ flex: 1 }}
                />
                <button aria-label="Enregistrer" title="Enregistrer" onClick={() => void enregistrerFournisseur(f.id)}>💾</button>
                <button disabled={!f.configue} onClick={() => void testerFournisseur(f.id)}>
                  Tester
                </button>
                {f.configue && (
                  <button aria-label={`Supprimer la clé ${f.nom}`} title="Supprimer la clé" onClick={() => void supprimerFournisseur(f.id, f.nom)}>
                    Supprimer
                  </button>
                )}
              </div>
              {fournisseurTests[f.id] && (
                <div style={{ fontSize: 11.5, marginTop: 4 }}>{fournisseurTests[f.id]}</div>
              )}
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 6 }}>
            Les clés restent sur ce PC (base locale). Les modèles cloud n'ont pas
            accès aux outils locaux (chat pur).
          </div>
        </div>

        {/* APIs publiques (public-apis) — catalogue + cles par service */}
        <div className="panel" style={{ marginTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>APIs publiques (public-apis)</h3>
            <span style={{ flex: 1 }} />
            <button disabled={paRefreshing} onClick={() => void rafraichirPublicApis()}>
              {paRefreshing ? "…" : "Rafraîchir"}
            </button>
          </div>
          <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
            Catalogue de {publicApis.length} APIs (annuaire public-apis). Mise à jour
            automatique toutes les 24 h{paUpdated ? ` · ${paUpdated.slice(0, 10)}` : ""}.
            Les clés restent sur ce PC.
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <input
              placeholder="Rechercher (nom, description, catégorie)…"
              value={paFilter}
              onChange={(e) => { setPaFilter(e.target.value); setPaVisible(40); }}
              style={{ flex: 2, minWidth: 200 }}
            />
            <select
              value={paCategory}
              onChange={(e) => { setPaCategory(e.target.value); setPaVisible(40); }}
              style={{ flex: 1, minWidth: 140 }}
            >
              <option value="">Toutes catégories</option>
              {paCategories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <select
              value={paKind}
              onChange={(e) => { setPaKind(e.target.value); setPaVisible(40); }}
              style={{ flex: 1, minWidth: 100 }}
            >
              <option value="all">Tous types</option>
              <option value="api">API REST</option>
              <option value="mcp">MCP</option>
            </select>
            <select
              value={paAuth}
              onChange={(e) => { setPaAuth(e.target.value); setPaVisible(40); }}
              style={{ flex: 1, minWidth: 120 }}
            >
              <option value="all">Toute auth</option>
              <option value="apiKey">Clé API</option>
              <option value="no">Sans clé</option>
            </select>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 8 }}>
            {paLoading ? "Chargement…" : `${paFiltered.length} résultat(s)`}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 420, overflowY: "auto" }}>
            {paFiltered.slice(0, paVisible).map((e) => {
              const slug = slugifyPa(e.name);
              const isKey = (e.auth || "").toLowerCase().includes("apikey");
              const keyInfo = paKeys[slug];
              return (
                <div
                  key={`${e.category}-${e.name}`}
                  className="kv"
                  style={{ display: "block", padding: "8px 10px" }}
                >
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                    <a
                      href={e.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ fontWeight: 600, color: "var(--accent)" }}
                    >
                      {e.name}
                    </a>
                    <span className="chip" style={{ fontSize: 10.5 }}>{e.category}</span>
                    {e.kind === "mcp" && <span className="chip" style={{ fontSize: 10.5 }}>MCP</span>}
                    <span
                      className="chip"
                      style={{
                        fontSize: 10.5,
                        background: isKey ? "rgba(var(--warn-rgb), 0.15)" : "transparent",
                      }}
                    >
                      {e.auth || "No"}
                    </span>
                    {e.https && <span style={{ fontSize: 10.5, color: "var(--text-faint)" }}>HTTPS</span>}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
                    {e.description}
                  </div>
                  {isKey && (
                    <div style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "center", flexWrap: "wrap" }}>
                      <input
                        type="password"
                        placeholder={keyInfo?.configured ? `configurée (${keyInfo.masked})` : "clé API…"}
                        value={paKeyInput[slug] ?? ""}
                        onChange={(ev) =>
                          setPaKeyInput((s) => ({ ...s, [slug]: ev.target.value }))
                        }
                        style={{ flex: 1, minWidth: 180, fontSize: 12 }}
                      />
                      <button
                        disabled={paKeyBusy[slug]}
                        onClick={() => void enregistrerPaCle(slug)}
                        aria-label="Enregistrer la clé"
                        title="Enregistrer la clé"
                      >
                        {paKeyBusy[slug] ? "…" : "💾"}
                      </button>
                      {keyInfo?.configured && (
                        <button
                          disabled={paKeyBusy[slug]}
                          onClick={() => void supprimerPaCle(slug)}
                          aria-label="Supprimer la clé"
                          title="Supprimer la clé"
                        >
                          ✕
                        </button>
                      )}
                      {keyInfo?.configured && (
                        <span className="status-ok" style={{ fontSize: 11 }}>
                          {keyInfo.masked}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {paFiltered.length > paVisible && (
              <button style={{ alignSelf: "center", marginTop: 6 }} onClick={() => setPaVisible((v) => v + 40)}>
                Charger plus ({paFiltered.length - paVisible} restants)
              </button>
            )}
            {paFiltered.length === 0 && !paLoading && (
              <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>Aucun résultat.</div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
