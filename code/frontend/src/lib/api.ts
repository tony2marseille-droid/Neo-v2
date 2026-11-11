export interface ModelRuntime {
  total_bytes: number;
  vram_bytes: number;
  cpu_bytes_estimated: number;
  gpu_memory_share_pct: number | null;
  context_length: number | null;
  runner: string | null;
}

export interface ModelEntry {
  id: string;
  name: string;
  tag: string;
  family: string;
  capabilities: string[];
  size_label: string;
  quantization: string;
  vram_gb: number;
  context: number;
  description: string;
  source: string;
  installed: boolean;
  loaded: boolean;
  runtime?: ModelRuntime | null;
  size_bytes: number;
  parameter_size: string;
  fit?: string;
  hors_config?: boolean;
  /** Tier de taille calcule par le serveur : T1..T5, Embed ou Cloud. */
  tier?: string;
  /** Nombre de milliards de parametres (0 si inconnu). */
  params_b?: number;
  /** Poids du modele sur le disque, en Go (0 si non installe). */
  size_gb?: number;
}

export interface Conversation {
  id: number;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  /** 1 si la conversation est pilotee par le poller Telegram. */
  telegram?: number;
}

export interface ChatStats {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_duration_ms?: number;
  tokens_per_second?: number;
}

export interface SystemInfo {
  app: { name: string; version: string };
  uptime_seconds: number;
  cpu: { percent: number; cores_physical: number; cores_logical: number };
  ram: { used_gb: number; total_gb: number; percent: number };
  disks: { mount: string; total_gb: number; free_gb: number; used_percent: number }[];
  gpus: {
    name: string;
    vram_used_mb: number;
    vram_total_mb: number;
    utilization_percent: number;
    temperature_c: number;
  }[];
  ollama: { available: boolean; version: string; host: string };
  models_dir: string;
}

export interface Voice {
  id: string;
  name: string;
  languages: string[];
  gender?: "F" | "M";
}

export interface MemoryItem {
  id: number;
  content: string;
  tag: string;
  created_at: string;
}

export interface AgentDef {
  id: string;
  name: string;
  description: string;
  tools: string[];
  rag: boolean;
}

export interface BenchmarkSummary {
  tag: string;
  score_percent: number;
  avg_tokens_per_second: number;
  avg_latency_ms: number;
}

export interface ToolActivity {
  name: string;
  denied: boolean;
  preview: string;
}

export type SSEEvent = Record<string, unknown> & { type: string };

/**
 * Lit un flux Server-Sent Events et appelle `onEvent` pour chaque evenement
 * JSON. Les lignes coupees entre deux blocs reseau sont reassemblees ; seul
 * un JSON invalide est ignore : une exception levee par `onEvent` (ex.
 * evenement "error") remonte a l'appelant et interrompt la lecture.
 */
export async function readSSE(
  resp: Response,
  onEvent: (event: SSEEvent) => void
): Promise<void> {
  if (!resp.body) throw new Error("Flux SSE vide");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let data: string[] = [];
  const emettre = () => {
    if (data.length === 0) return;
    const brut = data.join("\n");
    data = [];
    let event: SSEEvent;
    try {
      event = JSON.parse(brut);
    } catch {
      return; // JSON invalide : ignore
    }
    onEvent(event);
  };
  let fini = false;
  try {
    while (!fini) {
      const { done, value } = await reader.read();
      fini = done;
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lignes = buffer.split("\n");
      buffer = fini ? "" : (lignes.pop() ?? "");
      for (const l of lignes) {
        const ligne = l.endsWith("\r") ? l.slice(0, -1) : l;
        if (ligne === "") emettre();
        else if (ligne.startsWith("data:")) data.push(ligne.slice(ligne.startsWith("data: ") ? 6 : 5));
      }
    }
    emettre();
  } finally {
    if (!fini) reader.cancel().catch(() => undefined);
  }
}

export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status} ${text.slice(0, 200)}`);
  }
  await readSSE(resp, onEvent);
}

export async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function sendJSON<T>(path: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

/** Service Python de Neo (port 8321) : video, fichiers, filtres photo, fiches. */
export const SERVICE_PY: string =
  // VITE_SERVICE_PY : autre adresse pendant le developpement (service lance a la main).
  (import.meta.env.VITE_SERVICE_PY as string | undefined) ?? `http://${window.location.hostname}:8321`;
/** Appel JSON au service Python ; l'erreur porte l'explication du serveur (`detail`). */
export async function servicePy<T>(route: string, method = "GET", corps?: unknown): Promise<T> {
  const r = await fetch(`${SERVICE_PY}${route}`, {
    method,
    headers: corps === undefined ? undefined : { "Content-Type": "application/json" },
    body: corps === undefined ? undefined : JSON.stringify(corps),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((d as { detail?: string }).detail ?? `HTTP ${r.status}`);
  return d as T;
}

/** API video/fichiers de Neo. */
export const VIDEO_API = `${SERVICE_PY}/api/video`;

export interface ProviderCredit {
  id: string;
  nom: string;
  configue: boolean;
  modele: string;
  tokens_in: number;
  tokens_out: number;
  requests: number;
  updated_at: string;
  credit: { total: number; utilise: number; restant: number } | null;
  quota_err: string | null;
}

export interface DiscoveredModel {
  name: string;
  tag: string;
}

/**
 * Joint le jeton API a une URL de fichier en lecture seule.
 *
 * Les balises `<img>`, `<video>` et `<a download>` ne peuvent pas envoyer
 * l'en-tete `Authorization`. Depuis un poste du LAN, le serveur Rust
 * (`GET /api/sd/file`) et le service video :8321 (fichiers et vignettes)
 * exigent le jeton : on le joint donc en parametre de requete. A reserver
 * a ces URLs-la ; les appels `fetch` passent l'en-tete (wrapper de main.tsx).
 */
export function avecToken(url: string): string {
  try {
    const token = localStorage.getItem("neo.token");
    if (!token) return url;
    return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
  } catch {
    // localStorage indisponible (mode prive strict) : URL sans jeton.
    return url;
  }
}

/** URL (avec jeton) d'un fichier local servi par `/api/sd/file`. */
export function fichierUrl(chemin: string): string {
  return avecToken(`/api/sd/file?path=${encodeURIComponent(chemin)}`);
}

/** Chemin local d'une image servie par `/api/sd/file?path=…`, sinon null. */
export function cheminDepuisUrl(src: string): string | null {
  try {
    const u = new URL(src, window.location.href);
    return u.pathname.endsWith("/api/sd/file") ? u.searchParams.get("path") : null;
  } catch {
    return null;
  }
}

export async function transcribeAudio(audioBlob: Blob): Promise<{
  text: string;
  language: string;
  duration: number;
}> {
  const form = new FormData();
  form.append("file", audioBlob, "voice.webm");
  const resp = await fetch("/api/voice/transcribe", { method: "POST", body: form });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status} ${text.slice(0, 200)}`);
  }
  return resp.json();
}
