import { useCallback, useEffect, useRef, useState } from "react";
import ChatView from "./components/ChatView";
import CollabView from "./components/CollabView";
import DocumentsView from "./components/DocumentsView";
import ErrorBoundary from "./components/ErrorBoundary";
import FineTuneView from "./components/FineTuneView";
import HistoryPanel from "./components/HistoryPanel";
import HomeView from "./components/HomeView";
import ModelsView from "./components/ModelsView";
import OnboardingWizard from "./components/OnboardingWizard";
import SettingsView from "./components/SettingsView";
import StatusPanel from "./components/StatusPanel";
import VideoView from "./components/VideoView";
import TabBar from "./components/TabBar";
import SkinBackground from "./components/SkinBackground";
import { appliquerSkin, lireSkin, type Skin } from "./lib/skins";
import { ActivityProvider } from "./components/ActivityContext";
import ActivityIndicator from "./components/ActivityIndicator";
import type { Conversation, ModelEntry, SystemInfo } from "./lib/api";
import { getJSON, sendJSON } from "./lib/api";
import { normalizeLang, setLang as persistLang, type Lang } from "./lib/i18n";
import type { ToastKind } from "./lib/toast";
import { usePolling } from "./lib/usePolling";
import { estVue, type Vue } from "./lib/vues";

interface Toast {
  id: number;
  msg: string;
  kind: ToastKind;
}

export default function App() {
  // Vue initiale : hash (#/models…) prioritaire, sinon derniere vue ouverte,
  // sinon l'accueil. Deep-linking conserve pour le partage d'URL.
  const [view, setView] = useState<Vue>(() => {
    const h = window.location.hash.replace(/^#\/?/, "");
    if (estVue(h)) return h;
    const memo = localStorage.getItem("neo.view") ?? "";
    return estVue(memo) ? memo : "accueil";
  });
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConv, setSelectedConv] = useState<number | null>(null);
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem("oracle.model") ?? ""
  );
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [prefill, setPrefill] = useState<string | null>(null);
  // Compteur : une meme consigne (bouton 3D) doit pouvoir etre reinjectee.
  const [prefillSeq, setPrefillSeq] = useState(0);
  // Historique des conversations : tiroir lateral masquable.
  const [historyOpen, setHistoryOpen] = useState(false);
  // Etat du systeme / tokens : tiroir de droite.
  const [systemOpen, setSystemOpen] = useState(false);
  // Acces web (pseudo + mot de passe) configure a l'installation.
  const [loginRequis, setLoginRequis] = useState(false);
  const [lang, setLangState] = useState<Lang>(() => normalizeLang(localStorage.getItem("neo.lang")));
  const [skin, setSkinState] = useState<Skin>(lireSkin);
  const [boot, setBoot] = useState<{ loaded: boolean; onboarded: boolean }>({
    loaded: false,
    onboarded: true,
  });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);

  const toast = useCallback((msg: string, kind: Toast["kind"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      const data = await getJSON<{ models: ModelEntry[] }>("/api/models");
      setModels(data.models);
    } catch {
      toast("Impossible de joindre le serveur local", "error");
    }
  }, [toast]);

  const refreshConversations = useCallback(async () => {
    try {
      const data = await getJSON<{ conversations: Conversation[] }>("/api/conversations");
      setConversations(data.conversations);
    } catch {
      /* silent */
    }
  }, []);

  const refreshSystem = useCallback(() => {
    getJSON<SystemInfo>("/api/system/info")
      .then(setSystemInfo)
      .catch(() => undefined);
  }, []);

  // Chargement des reglages (langue + etat de premiere installation).
  const chargerReglages = useCallback(() => {
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => {
        const s = d.settings ?? {};
        const l = normalizeLang(s.language ?? localStorage.getItem("neo.lang"));
        setLangState(l);
        persistLang(l);
        setBoot({ loaded: true, onboarded: s.onboarded === "1" });
      })
      .catch(() => setBoot({ loaded: true, onboarded: true }));
  }, []);

  useEffect(() => {
    void refreshModels();
    void refreshConversations();
    refreshSystem();
    chargerReglages();
  }, [refreshModels, refreshConversations, refreshSystem, chargerReglages]);
  usePolling(refreshSystem, 4000);

  // Acces web : si un pseudo/mot de passe est configure et qu'on n'a pas
  // de token en local -> porte de login. Toute 401 (middleware Bearer)
  // declenche aussi le login (evenement "neo-401" emis par le wrapper fetch).
  useEffect(() => {
    getJSON<{ active: boolean; user: string }>("/api/auth/status")
      .then((s) => {
        const token = localStorage.getItem("neo.token");
        setLoginRequis(Boolean(s.active) && !token);
      })
      .catch(() => undefined);
    const sur401 = () => {
      localStorage.removeItem("neo.token");
      setLoginRequis(true);
    };
    window.addEventListener("neo-401", sur401);
    return () => window.removeEventListener("neo-401", sur401);
  }, []);

  useEffect(() => {
    localStorage.setItem("oracle.model", selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    if (!selectedModel && models.length > 0) {
      const firstInstalled = models.find((m) => m.installed && !m.tag.startsWith("nomic"));
      if (firstInstalled) setSelectedModel(firstInstalled.tag);
    }
  }, [models, selectedModel]);

  // Navigation avec mise a jour du hash (deep-linking).
  const changerVue = useCallback((v: Vue) => {
    setView(v);
    setHistoryOpen(false);
    setSystemOpen(false);
    window.history.replaceState(null, "", `#/${v}`);
  }, []);

  // Memo de la derniere vue + navigation navigateur (hash, retour avant).
  useEffect(() => {
    localStorage.setItem("neo.view", view);
  }, [view]);

  useEffect(() => {
    appliquerSkin(skin);
  }, [skin]);

  useEffect(() => {
    const surHash = () => {
      const h = window.location.hash.replace(/^#\/?/, "");
      if (estVue(h)) setView(h);
    };
    window.addEventListener("hashchange", surHash);
    return () => window.removeEventListener("hashchange", surHash);
  }, []);

  // Raccourcis globaux : Alt+H bascule l'historique, Echap ferme les tiroirs.
  useEffect(() => {
    const surTouche = (e: KeyboardEvent) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === "KeyH") {
        e.preventDefault();
        setHistoryOpen((h) => !h);
      } else if (e.key === "Escape") {
        setHistoryOpen(false);
        setSystemOpen(false);
      }
    };
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, []);

  const changerLangue = useCallback((l: Lang) => {
    setLangState(l);
    persistLang(l);
  }, []);

  const changerSkin = useCallback((s: Skin) => {
    setSkinState(s);
  }, []);

  const newChat = useCallback(
    (prefillMsg?: string) => {
      setSelectedConv(null);
      setPrefill(prefillMsg ?? null);
      setPrefillSeq((n) => n + 1);
      changerVue("chat");
    },
    [changerVue]
  );

  const deleteConv = useCallback(
    async (id: number) => {
      try {
        await sendJSON(`/api/conversations/${id}`, "DELETE");
        setConversations((c) => c.filter((x) => x.id !== id));
        if (selectedConv === id) setSelectedConv(null);
        void refreshConversations();
      } catch {
        toast("Suppression impossible", "error");
      }
    },
    [selectedConv, refreshConversations, toast]
  );

  // Vide tout l'historique (bouton du panneau Historique).
  const clearAllConversations = useCallback(async () => {
    if (!window.confirm("Vider tout l'historique des conversations ? Cette action est définitive.")) {
      return;
    }
    try {
      await sendJSON("/api/conversations", "DELETE");
      setConversations([]);
      setSelectedConv(null);
      setHistoryOpen(false);
      toast("Historique vidé", "success");
    } catch {
      toast("Impossible de vider l'historique", "error");
    }
  }, [toast]);

  // Supprime la conversation affichee (bouton "Vider" du chat).
  const clearCurrentConversation = useCallback(async () => {
    if (selectedConv === null) return;
    if (!window.confirm("Supprimer cette conversation ?")) return;
    try {
      await sendJSON(`/api/conversations/${selectedConv}`, "DELETE");
      setSelectedConv(null);
      void refreshConversations();
      toast("Conversation supprimée", "success");
    } catch {
      toast("Suppression impossible", "error");
    }
  }, [selectedConv, refreshConversations, toast]);

  // Porte de login : l'acces web est protege par pseudo + mot de passe.
  if (loginRequis) {
    const tenter = async (user: string, pass: string) => {
      try {
        const r = await sendJSON<{ ok: boolean; token?: string; error?: string }>(
          "/api/auth/login",
          "POST",
          { user, pass }
        );
        if (r.ok && r.token) {
          localStorage.setItem("neo.token", r.token);
          setLoginRequis(false);
          // Tout ce qui a echoue en 401 au demarrage est recharge.
          void refreshModels();
          void refreshConversations();
          chargerReglages();
        } else {
          const el = document.getElementById("login-err");
          if (el) el.textContent = r.error ?? "Pseudo ou mot de passe incorrect.";
        }
      } catch (err) {
        const el = document.getElementById("login-err");
        if (el) el.textContent = String(err);
      }
    };
    return (
      <div className="login-overlay">
        <form
          className="login-card"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            void tenter(String(f.get("user") ?? ""), String(f.get("pass") ?? ""));
          }}
        >
          <div className="brand">
            <div className="brand-dot" />
            <h1>NEO</h1>
          </div>
          <p className="login-sub">Acc&egrave;s prot&eacute;g&eacute; &mdash; identifie-toi.</p>
          <input name="user" placeholder="Pseudo" autoComplete="username" autoFocus />
          <input
            name="pass"
            type="password"
            placeholder="Mot de passe"
            autoComplete="current-password"
          />
          <button className="primary" type="submit">
            Se connecter
          </button>
          <div id="login-err" className="login-err" />
        </form>
      </div>
    );
  }

  if (!boot.loaded) {
    return (
      <div className="app-boot">
        <div className="core-dot" />
        <span>NÉO</span>
      </div>
    );
  }

  return (
    <ActivityProvider>
      <div className={`app skin-${skin}`}>
        <SkinBackground skin={skin} />
        <TabBar
          view={view}
          onView={changerVue}
          onNewChat={newChat}
          onToggleHistory={() => setHistoryOpen((h) => !h)}
          historyOpen={historyOpen}
          historyCount={conversations.length}
          systemOpen={systemOpen}
          onToggleSystem={() => setSystemOpen((o) => !o)}
          systemInfo={systemInfo}
          lang={lang}
        />
        <HistoryPanel
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          conversations={conversations}
          selectedConv={selectedConv}
          onSelectConv={(id) => {
            setSelectedConv(id);
            changerVue("chat");
            setHistoryOpen(false);
          }}
          onDeleteConv={(id) => void deleteConv(id)}
          onClearAll={() => void clearAllConversations()}
          onNewChat={() => {
            newChat();
            setHistoryOpen(false);
          }}
        />
        <StatusPanel
          open={systemOpen}
          onClose={() => setSystemOpen(false)}
          systemInfo={systemInfo}
          lang={lang}
        />
        <main className="main">
          <div
            className="vue-panneau"
            id="vue-panneau"
            key={view}
            role="tabpanel"
            aria-labelledby={`tab-${view}`}
          >
            <ErrorBoundary viewKey={view}>
            {view === "accueil" && (
              <HomeView
                onView={changerVue}
                onNewChat={newChat}
                systemInfo={systemInfo}
                models={models}
                lang={lang}
              />
            )}
            {view === "chat" && (
              <ChatView
                models={models}
                selectedModel={selectedModel}
                onSelectModel={setSelectedModel}
                conversationId={selectedConv}
                initialPrompt={prefill}
                prefillSeq={prefillSeq}
                onConversationCreated={(id) => {
                  setSelectedConv(id);
                  void refreshConversations();
                }}
                onAfterResponse={() => void refreshConversations()}
                onClearConversation={() => void clearCurrentConversation()}
                toast={toast}
              />
            )}
            {view === "models" && (
              <ModelsView models={models} refreshModels={refreshModels} toast={toast} />
            )}
            {view === "documents" && <DocumentsView toast={toast} />}
            {view === "collab" && <CollabView models={models} toast={toast} />}
            {view === "finetune" && <FineTuneView toast={toast} />}
            {view === "settings" && (
              <SettingsView
                systemInfo={systemInfo}
                refreshSystem={refreshSystem}
                toast={toast}
                lang={lang}
                onLangChange={changerLangue}
                skin={skin}
                onSkinChange={changerSkin}
              />
            )}
            {view === "video" && <VideoView toast={toast} />}
            </ErrorBoundary>
          </div>
        </main>
        {!boot.onboarded && (
          <OnboardingWizard
            toast={toast}
            onDone={(l) => {
              changerLangue(l);
              setBoot({ loaded: true, onboarded: true });
            }}
          />
        )}
        <div className="toast-zone" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.kind}`}>
              {t.msg}
            </div>
          ))}
        </div>
        <ActivityIndicator />
      </div>
    </ActivityProvider>
  );
}