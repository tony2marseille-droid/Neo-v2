import { useCallback, useEffect, useRef, useState } from "react";
import Avatar3D, { type Avatar3DHandle } from "./Avatar3D";
import type {
  AgentDef,
  ChatStats,
  ModelEntry,
  ToolActivity,
} from "../lib/api";
import { cheminDepuisUrl, fichierUrl, getJSON, sendJSON, streamSSE, transcribeAudio } from "../lib/api";
import { renderMarkdown, srcImage } from "../lib/markdown";
import type { ToastFn } from "../lib/toast";
import PhotoFiltres from "./PhotoFiltres";
import PipelinePanel, { type PipelineStep } from "./PipelinePanel";
import { useImageAttach, buildChatBody } from "./useImageAttach";
import { useActivity } from "./ActivityContext";

interface Props {
  models: ModelEntry[];
  selectedModel: string;
  onSelectModel: (tag: string) => void;
  conversationId: number | null;
  initialPrompt?: string | null;
  prefillSeq?: number;
  onConversationCreated: (id: number) => void;
  onAfterResponse?: () => void;
  onClearConversation?: () => void;
  toast: ToastFn;
}

interface DisplayMessage {
  id?: number;
  role: "user" | "assistant";
  content: string;
  stats?: ChatStats;
  model?: string;
  tools?: ToolActivity[];
  sources?: { filename: string; url?: string }[];
  routedFrom?: string;
  /** Reflexion de Neo avant sa reponse (non enregistree). */
  pensee?: string;
  learned?: boolean;
  score?: { value: number; avis: string };
}

// Ouvre un document source. En local (loopback) le serveur ne demande pas de
// jeton : la navigation directe suffit. Depuis un autre poste, le jeton est
// stocke dans localStorage et doit etre joint a la requete — un simple clic
// sur un lien ne l'envoie pas. On recupere donc la page en memoire avant de
// l'ouvrir dans un onglet.
async function ouvrirSource(e: React.MouseEvent, url: string) {
  const token = localStorage.getItem("neo.token");
  if (!token) return;
  e.preventDefault();
  const ouvrir = (contenu: string) => {
    const blob = new Blob([contenu], { type: "text/html;charset=utf-8" });
    const blobUrl = URL.createObjectURL(blob);
    window.open(blobUrl + (url.includes("#") ? "#" + url.split("#")[1] : ""), "_blank", "noopener");
    // L'onglet a charge la page : l'URL blob peut etre liberee.
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  };
  try {
    const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (r.ok) ouvrir(await r.text());
    else window.open(url, "_blank", "noopener");
  } catch {
    window.open(url, "_blank", "noopener");
  }
}

function SpeakerIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
    </svg>
  );
}

/** Modeles pre-charges par type de requete (inseres dans la zone de saisie). */
const MODELES_REQUETE: { id: string; label: string; texte: string; icone: string }[] = [
  {
    id: "image",
    label: "Image",
    icone: "▣",
    texte: "Génère une image photoréaliste 4K de : ",
  },
  {
    id: "3d",
    label: "Objet 3D",
    icone: "⬢",
    texte: "Crée un objet 3D : ",
  },
  {
    id: "document",
    label: "Document",
    icone: "☰",
    texte: "Résume ce document en 5 points clés : ",
  },
  {
    id: "video",
    label: "Vidéo",
    icone: "▶",
    texte: "Crée une vidéo de 10 secondes montrant : ",
  },
  {
    id: "code",
    label: "Code",
    icone: "❯_",
    texte: "Écris un script Python qui ",
  },
  {
    id: "analyse",
    label: "Analyse d'image",
    icone: "👁",
    texte: "Analyse cette image en détail : décris les objets, le texte et les couleurs.",
  },
];

/** Chemin local encode dans une URL /api/sd/file?path=... */
function cheminDepuisSrc(src: string): string {
  try {
    const url = new URL(src, window.location.origin);
    const p = url.searchParams.get("path");
    if (p) return p;
    if (/^file:/i.test(src)) return decodeURIComponent(src.replace(/^file:\/\/\/?/i, ""));
  } catch {
    // URL relative exotique : on retombe sur une extraction manuelle.
  }
  const m = src.match(/[?&]path=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export default function ChatView({
  models,
  selectedModel,
  onSelectModel,
  conversationId,
  initialPrompt,
  prefillSeq,
  onConversationCreated,
  onAfterResponse,
  onClearConversation,
  toast,
}: Props) {
  const { startActivity, endActivity } = useActivity();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState(initialPrompt ?? "");
  const [streaming, setStreaming] = useState(false);
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [agentId, setAgentId] = useState(() => localStorage.getItem("oracle.agent") ?? "auto");
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [ragEnabled, setRagEnabled] = useState(false);
  const [webEnabled, setWebEnabled] = useState(false);
  const { images: pendingImages, dragging, attachFiles, remove, clear, dragProps } = useImageAttach();
  const [prompts, setPrompts] = useState<{ id: number; name: string; content: string }[]>([]);
  const [autoTts, setAutoTts] = useState(false);
  // Fenetre de choix du format demandee AVANT toute generation d'image.
  const [formatOpen, setFormatOpen] = useState(false);
  const [imageFormat, setImageFormat] = useState(
    () => localStorage.getItem("neo.imageFormat") ?? "jpeg",
  );
  const [imageQuality, setImageQuality] = useState(
    () => localStorage.getItem("neo.imageQuality") ?? "max",
  );
  // Detection de visages dans l'image affichee en plein ecran.
  const [faceBusy, setFaceBusy] = useState(false);
  const [faceResult, setFaceResult] = useState<{
    count: number;
    annotated?: string;
    message: string;
  } | null>(null);

  // Image affichee en plein ecran.
  const [fullscreenImage, setFullscreenImage] = useState<{ src: string; alt: string } | null>(null);
  const [filtresOuverts, setFiltresOuverts] = useState(false);
  // Une autre image, ou la fermeture : le panneau de filtres repart ferme.
  useEffect(() => {
    if (!fullscreenImage) setFiltresOuverts(false);
  }, [fullscreenImage]);
  const abortRef = useRef<AbortController | null>(null);
  // Conversation creee par le flux en cours (evenement meta) : son
  // chargement depuis l'API est saute pour ne pas ecraser la reponse live.
  const creeeParFluxRef = useRef<number | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const avatarRef = useRef<Avatar3DHandle | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [recording, setRecording] = useState(false);
  // Pipeline live : agent/modele/ETA + etapes d'outils (panneau Matrix).
  const [pipeline, setPipeline] = useState<{ agent: string; model: string; etaMs: number | null } | null>(null);
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [imagesInFlight, setImagesInFlight] = useState(0);
  // Fournisseurs cloud configures (cotes cote des modeles locaux Ollama).
  const [fournisseurs, setFournisseurs] = useState<{
    id: string;
    nom: string;
    configue: boolean;
    modele: string;
  }[]>([]);
  // Modele cloud force par l'utilisateur (surcharge selectedModel quand defini).
  const [cloudModel, setCloudModel] = useState<string | null>(null);
  // Indicateur visuel : un boost best-of-N est en cours pour cette reponse.
  const [boostVisible, setBoostVisible] = useState(false);

  // Met a jour le dernier message s'il vient de l'assistant (copie, jamais
  // de mutation : les updaters sont rejoues deux fois en StrictMode).
  const updateLast = useCallback((fn: (m: DisplayMessage) => DisplayMessage) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last || last.role !== "assistant") return prev;
      return [...prev.slice(0, -1), fn(last)];
    });
  }, []);

  useEffect(() => {
    getJSON<{ agents: AgentDef[] }>("/api/agents")
      .then((d) => setAgents(d.agents))
      .catch(() => undefined);
    getJSON<{ prompts: { id: number; name: string; content: string }[] }>("/api/prompts")
      .then((d) => setPrompts(d.prompts))
      .catch(() => undefined);
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => setAutoTts(d.settings.tts_autoplay === "1"))
      .catch(() => undefined);
    getJSON<{ fournisseurs: { id: string; nom: string; configue: boolean; modele: string }[] }>("/api/fournisseurs")
      .then((d) => setFournisseurs(d.fournisseurs ?? []))
      .catch(() => undefined);
  }, []);

  // Demontage : on coupe le flux en cours et on libere le micro.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      const rec = mediaRef.current;
      if (rec) {
        rec.onstop = null;
        if (rec.state !== "inactive") rec.stop();
        rec.stream.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  // Les images du rendu Markdown n'ont aucun gestionnaire inline : l'erreur
  // de chargement (qui ne remonte pas) est captee ici en phase de capture,
  // et le repli n'est tente qu'une fois.
  useEffect(() => {
    const zone = messagesRef.current;
    if (!zone) return;
    const surErreur = (e: Event) => {
      const img = e.target;
      if (!(img instanceof HTMLImageElement)) return;
      const repli = img.dataset.fallback;
      if (repli && !img.dataset.repliTente) {
        img.dataset.repliTente = "1";
        img.src = repli;
      }
    };
    zone.addEventListener("error", surErreur, true);
    return () => zone.removeEventListener("error", surErreur, true);
  }, []);

  // Echap ferme le plein ecran et la fenetre de format.
  useEffect(() => {
    if (!fullscreenImage && !formatOpen) return;
    const surTouche = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setFullscreenImage(null);
      setFaceResult(null);
      setFormatOpen(false);
    };
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [fullscreenImage, formatOpen]);

  useEffect(() => {
    localStorage.setItem("oracle.agent", agentId);
    const agent = agents.find((a) => a.id === agentId);
    if (agent) setRagEnabled(agent.rag);
  }, [agentId, agents]);

  useEffect(() => {
    // Conversation que le flux en cours vient de creer : la liste affichee
    // est deja la bonne, la recharger couperait la reponse en cours.
    if (conversationId !== null && creeeParFluxRef.current === conversationId) {
      creeeParFluxRef.current = null;
      return;
    }
    // Changement de conversation par l'utilisateur : le flux en cours
    // appartient a l'ancienne, on l'interrompt.
    abortRef.current?.abort();
    if (conversationId === null) {
      setMessages([]);
      return;
    }
    let ignore = false;
    getJSON<{ messages: (DisplayMessage | { role: "system" })[] }>(
      `/api/conversations/${conversationId}/messages`
    )
      .then((data) => {
        if (ignore) return;
        setMessages(
          data.messages
            .filter((m): m is DisplayMessage => m.role !== "system")
            .map((m) => ({ id: m.id, role: m.role, content: m.content, model: m.model }))
        );
      })
      .catch(() => {
        if (!ignore) toast("Impossible de charger la conversation", "error");
      });
    return () => {
      ignore = true;
    };
  }, [conversationId, toast]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Consigne pre-remplie (carte 3D, bouton 3D de la barre) : on l'injecte
  // meme si le chat est deja monte et si la consigne est identique.
  useEffect(() => {
    setInput(initialPrompt ?? "");
  }, [initialPrompt, prefillSeq]);

  const speak = useCallback(async (text: string, quiet = false) => {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      // Avatar 3D (lip-sync audio-driven) prend la main s'il est pret.
      const ok = await avatarRef.current?.speak(blob, text);
      if (ok) return;
      // Repli : lecture audio classique (pas de lip-sync).
      audioRef.current?.pause();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (err) {
      if (!quiet) toast(`Synthèse vocale impossible : ${err}`, "error");
    }
  }, [toast]);

  const copyMessage = useCallback((text: string) => {
    void navigator.clipboard.writeText(text).then(
      () => toast("Copié dans le presse-papiers", "success"),
      () => toast("Copie impossible", "error")
    );
  }, [toast]);

  const sendToTelegram = useCallback(async (content: string) => {
    // Extraction des images du message (meme logique que renderMarkdown) :
    // liens markdown file:// + chemins locaux nus (E:\..., data\..., workspace\...).
    const images: string[] = [];
    const mdRe = /\[([^\]]{1,120})\]\((file:\/\/[^\s)]+)\)/g;
    let m: RegExpExecArray | null;
    while ((m = mdRe.exec(content)) !== null) {
      if (/\.(png|jpe?g|webp|tiff?|exr)(\?.*)?$/i.test(m[2])) {
        const brut = decodeURIComponent(m[2].replace(/^file:\/\//i, "").replace(/^\//, ""));
        const p = srcImage(brut);
        if (!images.some((x) => x.toLowerCase() === p.toLowerCase())) images.push(p);
      }
    }
    const bareRe = /\b((?:[A-Za-z]:[/\\\\]|data[/\\]|workspace[/\\])[^\s<>"'`*]+?\.(?:png|jpe?g|webp|tiff?|exr))\b/gi;
    while ((m = bareRe.exec(content)) !== null) {
      const p = srcImage(m![1]);
      if (!images.some((x) => x.toLowerCase() === p.toLowerCase())) images.push(p);
    }
    // Texte nettoye : sans balises d'images ni chemins bruts.
    let text = content
      .replace(/\[([^\]]{1,120})\]\(file:\/\/[^\s)]+\)/g, "$1")
      .replace(/\b((?:[A-Za-z]:[/\\\\]|data[/\\]|workspace[/\\])[^\s<>"'`*]+?\.(?:png|jpe?g|webp|tiff?|exr))\b/gi, "")
      .trim();
    try {
      const resp = await fetch("/api/telegram/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, images }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.ok) {
        const parts = [data.sent ? "message" : null, data.images ? `${data.images} image(s)` : null].filter(Boolean);
        toast(parts.length ? `Envoyé sur Telegram ✓ (${parts.join(" + ")})` : "Envoyé sur Telegram ✓", "success");
      } else {
        toast(`Erreur : ${data.error}`, "error");
      }
    } catch (err) {
      toast(`Telegram : ${err}`, "error");
    }
  }, [toast]);

  const exportMd = useCallback(async () => {
    if (!conversationId) return;
    try {
      const resp = await fetch(`/api/conversations/${conversationId}/export`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `conversation-${conversationId}.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      toast(`Export impossible : ${err}`, "error");
    }
  }, [conversationId, toast]);

  const send = useCallback(async (overrideText?: string, isRegenerate = false, parler = false) => {
    const text = (overrideText ?? input).trim();
    const images = pendingImages.map((p) => p.preview);
    if ((!text && images.length === 0 && !isRegenerate) || streaming) return;

    // Texte saisi : on vide la zone. Texte fourni (vocal, regeneration) :
    // la saisie en cours est conservee.
    if (overrideText === undefined) setInput("");
    if (isRegenerate) {
      setMessages((prev) => {
        const copy = [...prev];
        while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
        return copy;
      });
    }
    setStreaming(true);
    setImagesInFlight(pendingImages.length);
    clear();
    const assistantMsg: DisplayMessage = { role: "assistant", content: "", tools: [] };
    if (!isRegenerate) {
      setMessages((prev) => [...prev, { role: "user", content: text + (pendingImages.length ? ` [${pendingImages.length} image${pendingImages.length > 1 ? "s" : ""}]` : "") }, assistantMsg]);
    } else {
      setMessages((prev) => [...prev, assistantMsg]);
    }
    setPipeline(null);
    setSteps([]);

    // Indicateur d'activité global
    startActivity("generating_response", "Génère une réponse…", images.length > 0 ? `${images.length} image(s) en analyse` : undefined);

    const controller = new AbortController();
    abortRef.current = controller;
    let finalContent = "";

    try {
      let convId = conversationId;
      await streamSSE(
        "/api/chat",
        buildChatBody(text, pendingImages, {
          conversation_id: convId,
          model: (cloudModel ?? selectedModel) || "auto",
          temperature: 0.5,
          agent_id: agentId === "general" ? null : agentId,
          tools_enabled: toolsEnabled,
          rag_enabled: ragEnabled,
          web_enabled: webEnabled,
          regenerate: isRegenerate,
        }),
        (event) => {
          if (event.type === "meta") {
            const id = event.conversation_id as number;
            if (convId === null && id) {
              convId = id;
              creeeParFluxRef.current = id;
              onConversationCreated(id);
            }
            setPipeline({
              agent: String(event.agent ?? ""),
              model: String(event.model ?? ""),
              etaMs: typeof event.eta_ms === "number" ? (event.eta_ms as number) : null,
            });
            updateLast((m) => ({
              ...m,
              model: event.model as string,
              learned: Boolean(event.learned),
              routedFrom: (event.route_reason as string) || undefined,
              sources: ((event.rag_sources as { filename: string; url?: string }[]) ?? [])
                .filter(Boolean)
                .map((s) => ({ filename: s.filename, url: s.url })),
            }));
          } else if (event.type === "token") {
            const morceau = String(event.content ?? "");
            finalContent += morceau;
            updateLast((m) => ({ ...m, content: m.content + morceau }));
          } else if (event.type === "pensee") {
            const morceau = String(event.content ?? "");
            updateLast((m) => ({ ...m, pensee: (m.pensee ?? "") + morceau }));
          } else if (event.type === "replace") {
            // Garde-fou serveur : le contenu a ete corrige (chemins
            // d'images invalides remplaces/retires) -> remplacement
            // integral du message deja streamé.
            const contenu = String(event.content ?? "");
            finalContent = contenu;
            updateLast((m) => ({ ...m, content: contenu }));
          } else if (event.type === "tool_start") {
            setSteps((prev) => [
              ...prev,
              { name: String(event.name), at: Date.now(), done: false },
            ]);
          } else if (event.type === "tool_exec") {
            setSteps((prev) => {
              const copy = [...prev];
              const idx = copy.findIndex((s) => s.name === event.name && !s.done);
              const step: PipelineStep = {
                name: String(event.name),
                at: idx >= 0 ? copy[idx].at : Date.now(),
                denied: Boolean(event.denied),
                preview: String(event.preview ?? ""),
                args: (event.args as Record<string, unknown>) ?? undefined,
                done: true,
              };
              if (idx >= 0) {
                copy[idx] = step;
              } else {
                copy.push(step);
              }
              return copy;
            });
            const activity: ToolActivity = {
              name: event.name as string,
              denied: Boolean(event.denied),
              preview: String(event.preview ?? ""),
            };
            updateLast((m) => ({ ...m, tools: [...(m.tools ?? []), activity] }));
          } else if (event.type === "boost_start") {
            setBoostVisible(true);
            setPipeline((p) =>
              p ? { ...p, agent: "boost best-of-N", model: String(event.model ?? "") } : { agent: "boost best-of-N", model: String(event.model ?? ""), etaMs: null }
            );
          } else if (event.type === "boost_done") {
            setBoostVisible(false);
          } else if (event.type === "bascule") {
            // Bascule automatique (consigne tokens) : credit/quota cloud
            // epuise -> modele LOCAL. Jamais un autre fournisseur a cle.
            toast(
              `Bascule auto : ${event.from} vers ${event.to} (${event.raison})`,
              "info"
            );
            setPipeline((p) =>
              p ? { ...p, model: String(event.to ?? "") } : p
            );
          } else if (event.type === "done") {
            // Stats de la requete (encart tokens) : entree / sortie / total.
            const stIn =
              typeof event.prompt_tokens === "number" ? (event.prompt_tokens as number) : 0;
            const stOut =
              typeof event.completion_tokens === "number" ? (event.completion_tokens as number) : 0;
            const stTps =
              typeof event.tokens_per_second === "number" ? (event.tokens_per_second as number) : 0;
            if (stIn > 0 || stOut > 0) {
              window.dispatchEvent(
                new CustomEvent("neo-stats", {
                  detail: {
                    in: stIn,
                    out: stOut,
                    tps: stTps,
                    model: String(event.model ?? ""),
                    estime: Boolean(event.estime),
                  },
                })
              );
            }
            const stats: ChatStats = {
              prompt_tokens: event.prompt_tokens as number,
              completion_tokens: event.completion_tokens as number,
              total_duration_ms: event.duration_ms as number,
              tokens_per_second: event.tokens_per_second as number,
            };
            const learned = Boolean(event.learned);
            updateLast((m) => ({
              ...m,
              id: event.assistant_message_id as number,
              model: (event.model as string) || m.model,
              stats,
              learned,
            }));
            if ((autoTts || parler) && finalContent) void speak(finalContent.slice(0, 1200), true);
            onAfterResponse?.();
          } else if (event.type === "tts_followup") {
            const texte = String(event.text ?? "");
            if (texte) {
              window.setTimeout(() => void speak(texte, true), 900);
            }
          } else if (event.type === "score") {
            const grade = {
              value: Number(event.score ?? 0),
              avis: String(event.avis ?? ""),
            };
            updateLast((m) => ({ ...m, score: grade }));
          } else if (event.type === "error") {
            throw new Error(String(event.error));
          }
        },
        controller.signal
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        toast(`Erreur de génération : ${err}`, "error");
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === "assistant" && !last.content && !(last.tools?.length)) {
            return prev.slice(0, -1);
          }
          return prev;
        });
      }
    } finally {
      setStreaming(false);
      setImagesInFlight(0);
      setBoostVisible(false);
      if (abortRef.current === controller) abortRef.current = null;
      endActivity();
    }
  }, [input, pendingImages, streaming, selectedModel, cloudModel, agentId, toolsEnabled, ragEnabled, webEnabled, conversationId, onConversationCreated, onAfterResponse, autoTts, speak, toast, startActivity, endActivity, clear, updateLast]);

  // Generation d'image directe (contourne le modele : 100 % fiable).
  const genererPhoto = useCallback(async (format: string, qualite: string) => {
    const prompt = input.trim();
    if (!prompt) {
      toast("Saisissez une description d'image avant de cliquer", "error");
      return;
    }
    if (streaming) return;
    setFormatOpen(false);
    localStorage.setItem("neo.imageFormat", format);
    localStorage.setItem("neo.imageQuality", qualite);
    setStreaming(true);
    setInput("");
    const assistantMsg: DisplayMessage = { role: "assistant", content: "⏳ Génération de l'image en cours (≈25 s)…", tools: [] };
    setMessages((prev) => [...prev, { role: "user", content: `🎨 ${prompt} [${format.toUpperCase()}, ${qualite}]` }, assistantMsg]);
    startActivity("generating_image", "Génère une image…", prompt.slice(0, 60));
    try {
      const r = await sendJSON<{ ok: boolean; url?: string; path?: string; message?: string; error?: string; duree_s?: number }>(
        "/api/sd/generate",
        "POST",
        { prompt, resolution: "1024_upscale", qualite, format },
      );
      if (!r.ok || !r.url) {
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = { ...last, content: `✗ Échec de la génération : ${r.error ?? r.message ?? "erreur inconnue"}` };
          }
          return copy;
        });
        toast("Échec de la génération d'image", "error");
      } else {
        const url = r.url;
        const texte = `Image générée en ${r.duree_s ?? "?"} s. Elle est disponible dans vos Documents.`;
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = { ...last, content: `${texte}\n\n![Image générée](${url})` };
          }
          return copy;
        });
        toast("Image générée avec succès", "success");
      }
    } catch (err) {
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant") {
          copy[copy.length - 1] = { ...last, content: `✗ Erreur : ${err}` };
        }
        return copy;
      });
      toast("Erreur lors de la génération", "error");
    } finally {
      setStreaming(false);
      endActivity();
    }
  }, [input, streaming, toast, startActivity, endActivity]);

  const regenerate = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    void send(lastUser.content.replace(/ \[\d+ images?\]$/, ""), true);
  }, [messages, send]);

  // Version courante de `send` : l'enregistrement vocal se termine plusieurs
  // secondes apres son lancement, il ne doit pas utiliser une fermeture perimee.
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });

  // Vocal : transcription locale (/api/voice/transcribe) puis envoi du texte
  // comme un message normal ; la reponse est lue a voix haute.
  const sendVoice = useCallback(async (blob: Blob) => {
    startActivity("transcribing", "Transcription audio…");
    let texte = "";
    try {
      texte = (await transcribeAudio(blob)).text?.trim() ?? "";
    } catch (err) {
      toast(`Erreur vocale : ${err}`, "error");
      return;
    } finally {
      endActivity();
    }
    if (!texte) {
      toast("Aucune parole détectée", "info");
      return;
    }
    void sendRef.current(texte, false, true);
  }, [toast, startActivity, endActivity]);

  const toggleRecord = useCallback(async () => {
    if (recording) {
      const rec = mediaRef.current;
      if (rec && rec.state !== "inactive") rec.stop();
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      toast("Micro inaccessible", "error");
      return;
    }
    chunksRef.current = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    mediaRef.current = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    mediaRef.current.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    mediaRef.current.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      if (blob.size > 0) void sendVoice(blob);
    };
    mediaRef.current.start();
    setRecording(true);
    toast("🎤 Enregistrement… parlez", "info");
  }, [recording, sendVoice, toast]);

  const insertPrompt = useCallback(
    (id: string) => {
      const p = prompts.find((x) => String(x.id) === id);
      if (!p) return;
      setInput((prev) => (prev ? `${prev}\n${p.content}` : p.content));
    },
    [prompts]
  );

  // Modele pre-charge : remplace une saisie vide, sinon s'ajoute a la suite.
  const insererModele = useCallback((texte: string) => {
    setInput((prev) => (prev.trim() ? `${prev}\n${texte}` : texte));
    const zone = document.querySelector(".chat-input-zone textarea") as HTMLTextAreaElement | null;
    zone?.focus();
  }, []);

  // Telechargement direct depuis le plein ecran (le clic sur l'image, lui,
  // continue d'ouvrir le plein ecran).
  const telechargerImage = useCallback((src: string, alt: string) => {
    const a = document.createElement("a");
    const sep = src.includes("?") ? "&" : "?";
    a.href = src.includes("/api/") ? `${src}${sep}download=1` : src;
    // Le nom reel est dans le parametre `path` de l'URL API (le dernier
    // segment de l'URL n'est que "file").
    let nom = "";
    try {
      nom = new URLSearchParams(src.split("?")[1] || "").get("path") || "";
    } catch {
      nom = "";
    }
    nom = nom.split(/[/\\]/).pop() || "";
    a.download = /\.(png|jpe?g|webp|tiff?|exr)$/i.test(nom) ? nom : `${alt || "image"}.jpg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, []);

  // Detection de visages sur l'image affichee (route Rust -> YuNet).
  const detecterVisages = useCallback(async (src: string) => {
    const chemin = cheminDepuisSrc(src);
    if (!chemin) {
      toast("Impossible de retrouver le fichier de cette image", "error");
      return;
    }
    setFaceBusy(true);
    setFaceResult(null);
    try {
      const r = await sendJSON<{
        ok: boolean;
        count?: number;
        annotated_path?: string;
        error?: string;
      }>("/api/vision/faces", "POST", { path: chemin });
      if (!r.ok) {
        setFaceResult({ count: 0, message: r.error ?? "Detection impossible" });
        toast(r.error ?? "Detection impossible", "error");
      } else {
        const n = r.count ?? 0;
        const annote = r.annotated_path ? fichierUrl(r.annotated_path) : "";
        setFaceResult({
          count: n,
          annotated: annote || undefined,
          message: n === 0
            ? "Aucun visage detecte sur cette image."
            : `${n} visage${n > 1 ? "s" : ""} detecte${n > 1 ? "s" : ""}.`,
        });
        if (annote) setFullscreenImage((prev) => (prev ? { ...prev, src: annote } : prev));
        toast(n === 0 ? "Aucun visage detecte" : `${n} visage(s) detecte(s)`, "success");
      }
    } catch (err) {
      setFaceResult({ count: 0, message: `Erreur : ${err}` });
      toast("Detection de visages impossible", "error");
    } finally {
      setFaceBusy(false);
    }
  }, [toast]);

  // Ouvre un modele 3D dans le slicer (bouton des cartes 3D du chat).
  const ouvrirSlicer = useCallback(async (chemin: string) => {
    try {
      const d = await sendJSON<unknown>("/api/outil/execute", "POST", {
        tool: "slicer_open",
        args: { file_path: chemin },
      });
      const ok = typeof d === "string" ? !d.startsWith("Erreur") && !d.startsWith("Aucun slicer") : true;
      toast(ok ? "Slicer ouvert avec le modèle ✓" : String(d).slice(0, 160), ok ? "success" : "error");
    } catch (err) {
      toast(`Slicer : ${err}`, "error");
    }
  }, [toast]);

  // Delegation des clics du rendu Markdown (aucun JavaScript inline) :
  // image -> plein ecran, bouton de carte 3D -> slicer.
  const surClicMessages = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-full], [data-slicer]");
    if (!el) return;
    if (el.dataset.slicer) {
      void ouvrirSlicer(el.dataset.slicer);
      return;
    }
    setFaceResult(null);
    setFullscreenImage({ src: el.dataset.full ?? "", alt: el.getAttribute("alt") || "image" });
  }, [ouvrirSlicer]);

  // Vide la conversation affichee (bouton du chat).
  const viderChat = useCallback(() => {
    if (streaming) return;
    if (conversationId !== null && onClearConversation) {
      onClearConversation();
      return;
    }
    if (messages.length === 0) return;
    if (!window.confirm("Vider cette conversation ?")) return;
    setMessages([]);
    setFaceResult(null);
    setFullscreenImage(null);
    toast("Conversation videe", "success");
  }, [streaming, conversationId, onClearConversation, messages.length, toast]);

  return (
      <div
        className={`chat-container ${dragging ? "drag-active" : ""}`}
        {...dragProps}
      >
        {dragging && (
          <div className="drop-overlay">
            <span>📎 Déposez vos images ici</span>
          </div>
        )}
        {/* Le fond dynamique est monte globalement (SkinBackground). */}
      <div className="view-header">
        <h2>Conversation</h2>
        <Avatar3D ref={avatarRef} size={96} />
        <select
          aria-label="Modèle de conversation"
          value={cloudModel ?? selectedModel}
          onChange={(e) => {
            const v = e.target.value;
            if (v.startsWith("cloud:")) {
              setCloudModel(v);
            } else {
              setCloudModel(null);
              onSelectModel(v);
            }
          }}
          style={{ minWidth: 210 }}
        >
          <option value="auto">Auto (routage intelligent)</option>
          {models.filter((m) => m.installed).map((m) => (
            <option key={m.tag} value={m.tag}>
              {m.name} ({m.parameter_size || m.size_label}){m.loaded ? " ●" : ""}
              {m.capabilities.includes("vision") ? " 👁" : ""}
            </option>
          ))}
          {fournisseurs.filter((f) => f.configue).map((f) => (
            <option key={`cloud-${f.id}`} value={`cloud:${f.id}`}>
              ☁ {f.nom} ({f.modele || "défaut"})
            </option>
          ))}
        </select>
        <select
          aria-label="Agent"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          style={{ minWidth: 160 }}
        >
          <option value="auto">Agent : Auto (intelligent)</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              Agent : {a.name}
            </option>
          ))}
        </select>
        <label className="toggle">
          <input type="checkbox" checked={toolsEnabled} onChange={(e) => setToolsEnabled(e.target.checked)} />
          Outils
        </label>
        <label className="toggle">
          <input type="checkbox" checked={webEnabled} onChange={(e) => setWebEnabled(e.target.checked)} />
          Internet
        </label>
        <label className="toggle">
          <input type="checkbox" checked={ragEnabled} onChange={(e) => setRagEnabled(e.target.checked)} />
          Documents
        </label>
        {/* Le boost best-of-N est decide par le serveur : simple temoin. */}
        {boostVisible && <span className="toggle boost-active">⚡ Boost</span>}
        {prompts.length > 0 && (
          <select
            aria-label="Bibliothèque de prompts"
            title="Bibliothèque de prompts"
            value=""
            onChange={(e) => insertPrompt(e.target.value)}
            style={{ maxWidth: 170 }}
          >
            <option value="">📚 Prompts…</option>
            {prompts.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        <button title="Exporter en Markdown" disabled={!conversationId} onClick={() => void exportMd()}>
          ⬇ MD
        </button>
        <button
          title="Vider cette conversation"
          className="danger"
          disabled={streaming || messages.length === 0}
          onClick={viderChat}
        >
          🗑 Vider
        </button>
        {pendingImages.length > 0 && (
          <span className="chip-meta" title={`${pendingImages.length} image(s) jointe(s) — analyse vision activée`}>
            👁 {pendingImages.length} image(s)
          </span>
        )}
        {streaming && (
          <button onClick={() => abortRef.current?.abort()} className="danger">
            Arrêter
          </button>
        )}
      </div>

      {streaming && pipeline && (
        <PipelinePanel
          agent={pipeline.agent}
          model={pipeline.model}
          etaMs={pipeline.etaMs}
          steps={steps}
          streaming={streaming}
        />
      )}
      {streaming && imagesInFlight > 0 && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", padding: "8px 12px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", margin: "8px 0", fontSize: 13, color: "var(--text)" }}>
          <span>⏳</span>
          <span>Néo traite votre demande…</span>
          <span>👁 {imagesInFlight} image(s) en cours d'analyse</span>
        </div>
      )}

      <div className="messages" ref={messagesRef} onClick={surClicMessages}>
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="big">Bienvenue dans la Matrice</div>
            <div>100% local — aucun envoi sur internet.</div>
            <div>Mode Auto : le modèle est choisi selon votre demande.</div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={m.id ?? `live-${i}`} className={`msg ${m.role}`}>
            {m.role === "assistant" && m.pensee && (
              <details className="msg-pensee" open={streaming && i === messages.length - 1 && !m.content}>
                <summary>
                  {streaming && i === messages.length - 1 && !m.content ? "Néo réfléchit…" : "Réflexion de Néo"}
                </summary>
                <div className="msg-pensee-texte">{m.pensee}</div>
              </details>
            )}
            <span dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} />
            {m.role === "assistant" && i === messages.length - 1 && streaming && !m.content && (
              <span className="cursor-blink" />
            )}
            {m.role === "assistant" && (m.content || (m.tools?.length ?? 0) > 0) && (
              <>
                {(m.tools?.length ?? 0) > 0 && (
                  <div className="tool-trace">
                    {m.tools!.map((t, ti) => (
                      <div key={ti} className={`tool-line ${t.denied ? "denied" : ""}`}>
                        ⚙ {t.name} → {t.preview.split("\n")[0].slice(0, 120)}
                        {t.denied ? " (refusé)" : ""}
                      </div>
                    ))}
                  </div>
                )}
                {m.learned && (
                  <div className="msg-meta">
                    <span className="chip-memo">⚡ Mémoire interne — réponse instantanée</span>
                  </div>
                )}
                {m.score && (
                  <div className="msg-meta">
                    <span
                      className={`chip-score ${
                        m.score.value >= 75 ? "good" : m.score.value >= 50 ? "mid" : "bad"
                      }`}
                      title={m.score.avis}
                    >
                      ◎ Score {m.score.value}/100 — {m.score.avis}
                    </span>
                  </div>
                )}
                {m.routedFrom && (
                  <div className="msg-meta">↳ routé : {m.routedFrom}</div>
                )}
                {m.sources && m.sources.length > 0 && (
                  <div className="msg-meta">
                    📄 sources :{" "}
                    {m.sources.map((s, si) => (
                      <span key={si}>
                        {s.url ? (
                          <a
                            href={s.url}
                            target="_blank"
                            rel="noreferrer"
                            title="Ouvrir le document a l'extrait cite"
                            onClick={(e) => ouvrirSource(e, s.url!)}
                          >
                            {s.filename}
                          </a>
                        ) : (
                          s.filename
                        )}
                        {si < m.sources!.length - 1 ? ", " : ""}
                      </span>
                    ))}
                  </div>
                )}
                {m.role === "assistant" && m.content && !streaming && (
                  <div className="msg-meta">
                    <button className="tts-btn" title="Lire à voix haute" onClick={() => speak(m.content)}>
                      <SpeakerIcon />
                    </button>
                    <button
                      className="tts-btn"
                      title="Copier le message"
                      onClick={() => copyMessage(m.content)}
                    >
                      ⧉
                    </button>
                    <button
                      className="tts-btn"
                      title="Envoyer sur Telegram"
                      onClick={() => sendToTelegram(m.content)}
                    >
                      ✈
                    </button>
                    {i === messages.length - 1 && messages.some((x) => x.role === "user") && (
                      <button className="tts-btn" title="Régénérer la réponse" onClick={regenerate}>
                        ↻
                      </button>
                    )}
                    {m.model && <span>{m.model}</span>}
                    {m.stats?.completion_tokens ? (
                      <span>
                        {m.stats.completion_tokens} tok
                        {m.stats.tokens_per_second ? ` · ${m.stats.tokens_per_second} tok/s` : ""}
                        {m.stats.total_duration_ms ? ` · ⏱ ${m.stats.total_duration_ms < 1000 ? `${m.stats.total_duration_ms}ms` : `${(m.stats.total_duration_ms / 1000).toFixed(1)}s`}` : ""}
                      </span>
                    ) : null}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-zone">
        <div className="chat-presets" aria-label="Prompts pré-chargés par type de requête">
          <span className="presets-label">Prompts</span>
          {MODELES_REQUETE.map((p) => (
            <button
              key={p.id}
              type="button"
              className="preset-chip"
              disabled={streaming}
              title={p.texte}
              onClick={() => insererModele(p.texte)}
            >
              <span className="preset-ico" aria-hidden="true">{p.icone}</span>
              {p.label}
            </button>
          ))}
        </div>
        {pendingImages.length > 0 && (
          <div className="image-previews">
            {pendingImages.map((p) => (
              <div key={p.id} className="image-thumb">
                <img src={`data:image/jpeg;base64,${p.preview}`} alt={p.name} />
                <button title="Retirer" onClick={() => remove(p.id)}>×</button>
              </div>
            ))}
          </div>
        )}
        <input
          type="file"
          accept="image/*"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            attachFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          className="icon-btn"
          title="Joindre une image (vision)"
          disabled={streaming}
          onClick={() => {
            const el = document.querySelector('input[type="file"]') as HTMLInputElement | null;
            el?.click();
          }}
        >
          📎
        </button>
        <button
          className={`icon-btn ${recording ? "recording" : ""}`}
          title={recording ? "Arrêter l'enregistrement vocal" : "Agent vocal instantané (parlez)"}
          disabled={streaming && !recording}
          onClick={() => void toggleRecord()}
          style={recording ? { color: "var(--red)", transform: "scale(1.15)" } : undefined}
        >
          🎤
        </button>
        <textarea
          placeholder={
            streaming
              ? "Génération en cours…"
              : "Écrivez votre message… (Entrée pour envoyer)"
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          onPaste={dragProps.onPaste}
          rows={2}
        />
        <button
          className="icon-btn"
          title="Générer une photo : choisir le format d'abord"
          disabled={streaming}
          style={{ height: 46, width: 46, fontSize: 18 }}
          onClick={() => {
            if (!input.trim()) {
              toast("Saisissez une description d'image avant de cliquer", "error");
              return;
            }
            setFormatOpen(true);
          }}
        >
          🎨
        </button>
        <button className="primary" style={{ height: 46 }} disabled={streaming || (!input.trim() && pendingImages.length === 0)} onClick={() => void send()}>
          Envoyer
        </button>
      </div>
      {/* ---------- Choix du format AVANT generation d'image ---------- */}
      {formatOpen && (
        <div
          className="format-modal"
          onClick={() => setFormatOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Choisir le format de l'image"
        >
          <div className="format-card" onClick={(e) => e.stopPropagation()}>
            <div className="format-head">
              <h3>Format de l'image</h3>
              <button
                className="format-close"
                onClick={() => setFormatOpen(false)}
                aria-label="Fermer"
              >
                ✕
              </button>
            </div>
            <p className="format-sub">
              Choisissez le format et la qualité avant de lancer la génération.
            </p>
            <div className="format-grid">
              {[
                { id: "jpeg", ext: "jpg", nom: "JPEG", desc: "Photo, fichier léger, compatible partout" },
                { id: "png", ext: "png", nom: "PNG", desc: "Sans perte, qualité maximale" },
                { id: "webp", ext: "webp", nom: "WebP", desc: "Compact, idéal pour le web" },
                { id: "tiff", ext: "tiff", nom: "TIFF", desc: "Master non compressé, impression" },
              ].map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={`format-option ${imageFormat === f.id ? "selected" : ""}`}
                  onClick={() => setImageFormat(f.id)}
                >
                  <span className="format-ext">.{f.ext}</span>
                  <span className="format-name">{f.nom}</span>
                  <span className="format-desc">{f.desc}</span>
                </button>
              ))}
            </div>
            <div className="format-quality">
              <span className="format-q-label">Qualité</span>
              <label>
                <input
                  type="radio"
                  name="neo-image-qualite"
                  checked={imageQuality === "standard"}
                  onChange={() => setImageQuality("standard")}
                />
                Standard (rapide)
              </label>
              <label>
                <input
                  type="radio"
                  name="neo-image-qualite"
                  checked={imageQuality === "max"}
                  onChange={() => setImageQuality("max")}
                />
                Max 4K (~45 s)
              </label>
            </div>
            <div className="format-actions">
              <button onClick={() => setFormatOpen(false)}>Annuler</button>
              <button
                className="primary"
                onClick={() => void genererPhoto(imageFormat, imageQuality)}
              >
                Générer l'image
              </button>
            </div>
          </div>
        </div>
      )}
      {/* ---------- Fullscreen Image Modal ---------- */}
      {fullscreenImage && (
        <div
          className="fullscreen-modal"
          onClick={() => {
            setFullscreenImage(null);
            setFaceResult(null);
          }}
          role="dialog"
          aria-modal="true"
          aria-label="Image en plein écran"
        >
          <div
            className="fullscreen-content"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="fs-close"
              onClick={() => {
                setFullscreenImage(null);
                setFaceResult(null);
              }}
              aria-label="Fermer"
            >
              ✕
            </button>
            {filtresOuverts && cheminDepuisUrl(fullscreenImage.src) ? (
              <PhotoFiltres
                chemin={cheminDepuisUrl(fullscreenImage.src) ?? ""}
                toast={toast}
                onCopie={(chemin) => {
                  // La copie filtree remplace l'image affichee ; le panneau se referme.
                  setFiltresOuverts(false);
                  setFullscreenImage({ src: fichierUrl(chemin), alt: chemin.split(/[\\/]/).pop() || "image" });
                }}
              />
            ) : (
              <img
                src={fullscreenImage.src}
                alt={fullscreenImage.alt}
                className="fs-image"
              />
            )}
            <div className="fs-caption">{fullscreenImage.alt}</div>
            <div className="fs-actions">
              {cheminDepuisUrl(fullscreenImage.src) && (
                <button
                  type="button"
                  className="fs-btn"
                  onClick={() => setFiltresOuverts((o) => !o)}
                  title="Essayer des filtres et des effets, puis enregistrer une copie"
                >
                  {filtresOuverts ? "↩ Revenir à l'image" : "🎨 Filtres et effets"}
                </button>
              )}
              <button
                type="button"
                className="fs-btn"
                onClick={() => telechargerImage(fullscreenImage.src, fullscreenImage.alt)}
                title="Télécharger l'image sans quitter le plein écran"
              >
                ⬇ Télécharger
              </button>
              <button
                type="button"
                className="fs-btn"
                disabled={faceBusy}
                onClick={() => void detecterVisages(fullscreenImage.src)}
                title="Détecter les visages (OpenCV YuNet, en local)"
              >
                {faceBusy ? "⏳ Analyse…" : "👁 Détecter les visages"}
              </button>
            </div>
            {faceResult && (
              <div className={`fs-faces ${faceResult.count > 0 ? "ok" : ""}`}>
                {faceResult.message}
                {faceResult.annotated ? " Image annotée affichée." : ""}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}