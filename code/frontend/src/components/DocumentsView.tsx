import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { avecToken, fichierUrl, getJSON, sendJSON, VIDEO_API } from "../lib/api";
import type { ToastFn } from "../lib/toast";
import { usePolling } from "../lib/usePolling";
import FichesPanel from "./FichesPanel";
import PhotoFiltres from "./PhotoFiltres";

interface Props {
  toast: ToastFn;
}

/* ------------------------------------------------------------------ *
 * Libelles des categories (alignes sur les fiches creees par le backend)
 * ------------------------------------------------------------------ */
const CAT_LABELS: Record<string, string> = {
  programmation: "Programmation",
  ia: "Intelligence artificielle",
  impression3d: "Impression 3D",
  bricolage: "Bricolage",
  cuisine: "Cuisine",
  sante: "Sante",
  finances: "Finances",
  loisirs: "Loisirs",
  science: "Science",
  autre: "Autre",
  neo: "Fichiers de Neo",
};

const CAT_DESCS: Record<string, string> = {
  programmation: "Code, scripts, documentation technique",
  ia: "Modeles, articles et notes sur l'IA",
  impression3d: "STL, 3MF, G-code et fiches d'impression",
  bricolage: "Plans, mesures et notes atelier",
  cuisine: "Recettes, menus et notes de cuisine",
  sante: "Sante, bien-etre et suivi personnel",
  finances: "Budgets, comptes et documents financiers",
  loisirs: "Voyages, jeux, lectures et centres d'interet",
  science: "Sciences, recherches et vulgarisation",
  autre: "Documents non classes ailleurs",
  neo: "Images et fichiers produits par Neo",
};

/* Icones de categorie : SVG modernes dessines a la main (aucun emoji, aucun
 * fichier externe). Trait fin + forme d'accent pour un rendu net a 54 px. */
function CatIcon({ cat }: { cat: string }) {
  const p = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  let dessin: ReactNode;
  switch (cat) {
    case "programmation":
      dessin = (
        <>
          <path {...p} d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4" />
          <path {...p} d="m13.4 5.5-2.8 13" />
        </>
      );
      break;
    case "ia":
      dessin = (
        <>
          <rect {...p} x="6.5" y="6.5" width="11" height="11" rx="3" />
          <path {...p} d="M10 10h4M10 14h4M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" />
        </>
      );
      break;
    case "impression3d":
      dessin = (
        <>
          <path {...p} d="M12 2.8 20 7v10l-8 4.2L4 17V7Z" />
          <path {...p} d="m4 7 8 4.2L20 7M12 11.2v10" />
          <path {...p} d="m8 4.9 8 4.1v4.6" opacity="0.55" />
        </>
      );
      break;
    case "bricolage":
      dessin = (
        <>
          <path {...p} d="M14.8 4.2a4.6 4.6 0 0 0-5.6 6L4 15.4 6.6 18l5.2-5.2a4.6 4.6 0 0 0 6-5.6l-3 3-2.6-.6-.6-2.6Z" />
          <path {...p} d="m17.5 15.5 3 3M15.5 17.5l3 3" />
        </>
      );
      break;
    case "cuisine":
      dessin = (
        <>
          <path {...p} d="M5 10.5a4 4 0 0 1 1.2-7.8 4.6 4.6 0 0 1 8.4-1 4 4 0 0 1 4.4 8.8Z" />
          <path {...p} d="M5 10.5h14v4a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4Z" />
          <path {...p} d="M8 21h8" />
        </>
      );
      break;
    case "sante":
      dessin = (
        <>
          <path {...p} d="M12 20.5S3.5 15.7 3.5 9.7a4.7 4.7 0 0 1 8.5-2.8 4.7 4.7 0 0 1 8.5 2.8c0 6-8.5 10.8-8.5 10.8Z" />
          <path {...p} d="M5.5 12h3l1.6-3 2.6 6 1.7-3h3.1" />
        </>
      );
      break;
    case "finances":
      dessin = (
        <>
          <rect {...p} x="3" y="6.5" width="18" height="12.5" rx="2.6" />
          <path {...p} d="M3 10.5h18" />
          <circle cx="16.5" cy="15" r="1.5" fill="currentColor" />
          <path {...p} d="M6.5 15h4" />
        </>
      );
      break;
    case "loisirs":
      dessin = (
        <>
          <path {...p} d="M7.6 8h8.8a4.4 4.4 0 0 1 4.2 5.6l-.9 3.3a2.2 2.2 0 0 1-3.8.9L14.6 16H9.4l-1.3 1.8a2.2 2.2 0 0 1-3.8-.9l-.9-3.3A4.4 4.4 0 0 1 7.6 8Z" />
          <path {...p} d="M7 11v3M5.5 12.5h3M16.2 11.6h.01M18 13.4h.01" />
        </>
      );
      break;
    case "science":
      dessin = (
        <>
          <path {...p} d="M9.5 3h5M10.5 3v6.2L5.2 18a2.2 2.2 0 0 0 1.9 3.3h9.8a2.2 2.2 0 0 0 1.9-3.3l-5.3-8.8V3" />
          <path {...p} d="M7.6 15h8.8" />
          <circle cx="12" cy="18" r="1.1" fill="currentColor" />
        </>
      );
      break;
    case "neo":
      dessin = (
        <>
          <path {...p} d="M12 3.2 14 8l4.8 2-4.8 2-2 4.8-2-4.8L5.2 10 10 8Z" />
          <path {...p} d="m18 15.5.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9Z" />
        </>
      );
      break;
    default:
      dessin = (
        <>
          <path {...p} d="M3.5 7.2a1.7 1.7 0 0 1 1.7-1.7h4l2 2.3h7.6a1.7 1.7 0 0 1 1.7 1.7v8.8a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7Z" />
          <path {...p} d="M3.5 10.2h17" />
        </>
      );
  }
  return (
    <svg className="cat-svg" viewBox="0 0 24 24" aria-hidden="true">
      {dessin}
    </svg>
  );
}

/* ------------------------------------------------------------------ *
 * Sous-categories du dossier « fichiers de Neo »
 * ------------------------------------------------------------------ */
type SousCatId = "toutes" | "images" | "video" | "mesh" | "documents" | "code" | "autres";

interface SousCategorie {
  id: SousCatId;
  label: string;
  exts?: string[];
}

const SOUS_CATEGORIES: SousCategorie[] = [
  { id: "toutes", label: "📁 Toutes" },
  {
    id: "images",
    label: "🖼️ Images",
    exts: ["png", "jpg", "jpeg", "webp", "tiff", "tif", "exr", "bmp", "gif"],
  },
  {
    id: "video",
    label: "🎬 Vidéo",
    exts: ["mp4", "mov", "avi", "mkv", "webm"],
  },
  {
    id: "mesh",
    label: "📐 Mesh",
    exts: ["stl", "3mf", "obj", "scad", "gcode", "step", "stp"],
  },
  {
    id: "documents",
    label: "📄 Documents",
    exts: ["pdf", "docx", "doc", "txt", "md", "csv", "xlsx", "xls", "pptx", "odt"],
  },
  {
    id: "code",
    label: "💻 Code",
    exts: ["py", "js", "ts", "tsx", "rs", "go", "java", "cpp", "c", "h", "json", "yaml", "yml", "toml", "sh", "ps1", "bat"],
  },
  { id: "autres", label: "📦 Autres" },
];

/* ------------------------------------------------------------------ *
 * Formes exactes renvoyees par l'API Rust
 * ------------------------------------------------------------------ */
interface Fiche {
  category: string;
  count: number;
  illustration: boolean;
}

interface NeoFile {
  name: string;
  path: string;
  size_bytes: number;
  /** Timestamp Unix en secondes. */
  modified: number;
  /** Categorie serveur : images | video | mesh | documents | code | autres. */
  type?: string;
  ext?: string;
  /** Vignette reelle (_apercu.jpg/png) si elle existe. */
  preview?: string | null;
  /** URL absolue servie par l'API video (8321). */
  url?: string;
  preview_url?: string | null;
  source?: string;
}

interface DocumentIndexe {
  id: number;
  filename: string;
  category: string;
  size_bytes: number;
  chunk_count: number;
  description?: string;
  created_at: string;
}

interface SearchHit {
  document_id: number;
  filename: string;
  position: number;
  content: string;
}

/* ------------------------------------------------------------------ *
 * Helpers d'affichage
 * ------------------------------------------------------------------ */

/** Apercu affichable : TIFF/EXR -> JPEG, fichiers 3D -> PNG. */
function cheminAffiche(p: string): string {
  if (/\.(tiff?|exr)$/i.test(p)) return p.replace(/\.(tiff?|exr)$/i, "_apercu.jpg");
  if (/\.(3mf|stl|scad|obj|gcode)$/i.test(p)) return p.replace(/\.[^.]+$/, "_apercu.png");
  return p;
}

/** Repli si l'apercu n'existe pas : on tente le fichier d'origine. */
function srcFallback(p: string): string {
  return /\.(tiff?|exr)$/i.test(p) ? p : cheminAffiche(p);
}

/** Fichier d'un document importe : le serveur le range sous son numero
 * (data/documents/doc_<id>.<ext>), pas sous son nom d'origine. Ouvrir ou
 * telecharger « rapport.pdf » repondait « fichier introuvable ». */
function cheminDocument(id: number, filename: string): string {
  const i = filename.lastIndexOf(".");
  return `data/documents/doc_${id}${i >= 0 ? filename.slice(i).toLowerCase() : ""}`;
}

/** Extension normalisee (minuscules, sans point). */
function extension(p: string): string {
  const base = p.split(/[\\/]/).pop() || "";
  const i = base.lastIndexOf(".");
  return i >= 0 ? base.slice(i + 1).toLowerCase() : "";
}

/** Recupere le type de fichier depuis l'API (fallback sur l'extension). */
function getFileType(nf: NeoFile): string {
  // Le serveur classe deja par extension (json -> documents, py -> code...).
  if (nf.type) return nf.type;
  const ext = extension(nf.path);
  if (["png", "jpg", "jpeg", "webp", "tiff", "tif", "exr", "bmp", "gif"].includes(ext)) return "images";
  if (["mp4", "mov", "avi", "mkv", "webm"].includes(ext)) return "video";
  if (["stl", "3mf", "obj", "scad", "gcode", "step", "stp"].includes(ext)) return "mesh";
  if (["pdf", "docx", "doc", "txt", "md", "csv", "xlsx", "xls", "pptx", "odt", "json"].includes(ext)) return "documents";
  if (["py", "js", "jsx", "ts", "tsx", "rs", "go", "java", "cpp", "c", "h", "yaml", "yml", "toml", "sh", "ps1", "bat", "sql"].includes(ext)) return "code";
  return "autres";
}

/* Les URLs du service video (:8321) servent des <img>/<video>/liens : elles
 * portent le jeton en parametre, comme /api/sd/file (exige depuis le LAN). */

/** URL d'affichage d'un fichier Neo : vignette reelle, sinon fichier servi. */
function urlNeo(nf: NeoFile): string {
  if (nf.preview_url) return avecToken(nf.preview_url);
  if (nf.url) return avecToken(nf.url);
  return fichierUrl(cheminAffiche(nf.path));
}

/** URL de telechargement/original (jamais la vignette). */
function urlOriginale(nf: NeoFile): string {
  if (nf.url) return avecToken(nf.url);
  return fichierUrl(nf.path);
}



/** Taille lisible en Ko ou Mo. */
function tailleLisible(octets: number): string {
  if (!octets) return "—";
  if (octets < 1024 * 1024) return `${Math.max(1, Math.round(octets / 1024))} Ko`;
  return `${(octets / 1024 / 1024).toFixed(1)} Mo`;
}

/** Date courte : accepte un timestamp Unix (s) ou une chaine ISO. */
function dateCourte(v: number | string): string {
  if (!v) return "";
  const d = typeof v === "number" ? new Date(v * 1000) : new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ------------------------------------------------------------------ *
 * Barre de sous-categories
 * ------------------------------------------------------------------ */
function SousCategorieBar({
  actif,
  onSelect,
  comptes,
}: {
  actif: SousCatId;
  onSelect: (id: SousCatId) => void;
  comptes: Record<string, number>;
}) {
  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap", alignItems: "center" }}>
      {SOUS_CATEGORIES.map((c) => (
        <button
          key={c.id}
          type="button"
          className={`tier-chip ${actif === c.id ? "active" : ""} ${
            c.id !== "toutes" && (comptes[c.id] ?? 0) === 0 ? "vide" : ""
          }`}
          onClick={() => onSelect(c.id)}
          title={`${comptes[c.id] ?? 0} fichier(s)`}
          style={{ fontSize: 11.5, padding: "3px 10px" }}
        >
          {c.label} ({comptes[c.id] ?? 0})
        </button>
      ))}
    </div>
  );
}

export default function DocumentsView({ toast }: Props) {
  const [fiches, setFiches] = useState<Fiche[]>([]);
  const [nonClasses, setNonClasses] = useState(0);
  const [docs, setDocs] = useState<DocumentIndexe[]>([]);
  const [neoFiles, setNeoFiles] = useState<NeoFile[]>([]);
  const [selectionNeo, setSelectionNeo] = useState<string[]>([]);
  const [envoiTelegram, setEnvoiTelegram] = useState(false);
  const [suppressionNeo, setSuppressionNeo] = useState(false);
  const [photoFiltres, setPhotoFiltres] = useState<NeoFile | null>(null);
  const [filtre, setFiltre] = useState("");
  const [sousCategorie, setSousCategorie] = useState<SousCatId>("toutes");
  const [appr, setAppr] = useState<{ running: boolean; category: string; etape: string } | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [recherche, setRecherche] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  /* ---------------- Chargements ---------------- */
  const refreshFiches = useCallback(() => {
    getJSON<{ fiches: Fiche[]; non_classes: number }>("/api/documents/fiches")
      .then((d) => {
        setFiches(d.fiches ?? []);
        setNonClasses(d.non_classes ?? 0);
      })
      .catch(() => undefined);
  }, []);

  const refreshDocs = useCallback(() => {
    getJSON<{ documents: DocumentIndexe[] }>("/api/documents")
      .then((d) => setDocs(d.documents ?? []))
      .catch(() => undefined);
  }, []);

  const refreshNeoFiles = useCallback(() => {
    getJSON<{ files: NeoFile[] }>(VIDEO_API + "/neo-files")
      .then((d) => {
        // Deduplication de securite (meme chemin ou meme nom+taille), puis
        // tri du plus recent au plus ancien.
        const vus = new Set<string>();
        const uniques = (d.files ?? []).filter((f) => {
          const cle = `${f.path.toLowerCase()}|${f.size_bytes}`;
          if (vus.has(cle)) return false;
          vus.add(cle);
          return true;
        });
        uniques.sort((a, b) => b.modified - a.modified);
        setNeoFiles(uniques);
      })
      .catch(() => undefined);
  }, []);

  const refreshAppr = useCallback(() => {
    getJSON<{ running: boolean; category: string; etape: string }>("/api/documents/apprentissage/status")
      .then((d) => setAppr(d))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshFiches();
    refreshDocs();
    refreshNeoFiles();
    refreshAppr();
  }, [refreshFiches, refreshDocs, refreshNeoFiles, refreshAppr]);

  // Suivi de l'apprentissage uniquement pendant qu'il tourne (y compris s'il
  // etait deja lance a l'ouverture de la vue) ; s'arrete tout seul ensuite.
  usePolling(
    () => {
      refreshAppr();
      refreshFiches();
      refreshDocs();
    },
    5000,
    appr?.running ?? false
  );

  /* ---------------- Actions ---------------- */
  const lancerApprentissage = useCallback(
    async (category: string) => {
      try {
        await sendJSON("/api/documents/apprentissage", "POST", { category });
        toast(`Neo commence a apprendre « ${CAT_LABELS[category] ?? category} »`, "info");
        setAppr({ running: true, category, etape: "recherche en cours…" });
      } catch {
        toast("Lancement de l'apprentissage impossible", "error");
      }
    },
    [toast]
  );

  const uploader = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const form = new FormData();
      Array.from(files).forEach((f) => form.append("files", f));
      try {
        const resp = await fetch("/api/documents/upload", { method: "POST", body: form });
        const data: { error?: string; detail?: string; count?: number; refuses?: { filename: string; raison: string }[] } =
          await resp.json().catch(() => ({}));
        if (!resp.ok || data.error) {
          // Le serveur explique le refus dans « detail ».
          toast(`Import refuse : ${data.detail ?? data.error ?? `HTTP ${resp.status}`}`, "error");
        } else {
          // Le nombre vient du serveur : annoncer files.length faisait croire
          // que tout etait indexe alors qu'un seul fichier l'etait.
          toast(`${data.count ?? 1} document(s) indexe(s)`, "success");
          for (const r of data.refuses ?? []) toast(`${r.filename} : ${r.raison}`, "error");
          refreshFiches();
          refreshDocs();
        }
      } catch (err) {
        toast(`Import impossible : ${err}`, "error");
      }
    },
    [toast, refreshFiches, refreshDocs]
  );

  const doSearch = useCallback(async () => {
    if (!query.trim()) return;
    setRecherche(true);
    try {
      const d = await sendJSON<{ results: SearchHit[] }>("/api/documents/search", "POST", {
        query: query.trim(),
      });
      setResults(d.results ?? []);
      if ((d.results ?? []).length === 0) toast("Aucun extrait correspondant", "info");
    } catch (err) {
      toast(`Recherche impossible : ${err}`, "error");
    } finally {
      setRecherche(false);
    }
  }, [query, toast]);

  // Ouverture dans un onglet : le fichier est recupere avec l'en-tete
  // Authorization (wrapper fetch) puis ouvert en blob, pour ne jamais placer
  // le jeton dans une URL d'onglet (historique, barre d'adresse).
  const ouvrirUrl = useCallback(
    async (adresse: string) => {
      try {
        const r = await fetch(adresse);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const url = URL.createObjectURL(await r.blob());
        window.open(url, "_blank", "noopener");
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } catch (err) {
        toast(`Ouverture impossible : ${err}`, "error");
      }
    },
    [toast]
  );

  const ouvrirFichier = useCallback(
    (chemin: string) => ouvrirUrl(`/api/sd/file?path=${encodeURIComponent(chemin)}`),
    [ouvrirUrl]
  );

  const telechargerNeo = useCallback((nf: NeoFile) => {
    const a = document.createElement("a");
    a.href = urlOriginale(nf);
    a.download = nf.name;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, []);

  // Un navigateur n'affiche ni Word, ni Excel, ni PowerPoint, ni un modele 3D :
  // « ouvrir » ces fichiers les telecharge, pour qu'ils s'ouvrent dans leur logiciel.
  const ouvrirNeo = useCallback(
    (nf: NeoFile) => {
      if (/\.(docx?|xlsx?|pptx?|odt|stl|3mf|obj|scad|gcode|step|stp)$/i.test(nf.path)) telechargerNeo(nf);
      else void (nf.url ? ouvrirUrl(nf.url) : ouvrirFichier(nf.path));
    },
    [ouvrirUrl, ouvrirFichier, telechargerNeo]
  );

  const telecharger = useCallback((chemin: string, nom?: string) => {
    const a = document.createElement("a");
    a.href = `${fichierUrl(chemin)}&download=1`;
    a.download = nom || chemin.split(/[\\/]/).pop() || "fichier";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, []);

  const envoyerTelegram = useCallback(async () => {
    if (selectionNeo.length === 0) return;
    setEnvoiTelegram(true);
    try {
      const d = await sendJSON<{ ok: boolean; sent?: number; images?: number; error?: string }>(
        "/api/telegram/send",
        "POST",
        { text: `${selectionNeo.length} fichier(s) depuis Neo`, images: selectionNeo }
      );
      if (d.ok) toast(`${d.images ?? selectionNeo.length} fichier(s) envoye(s)`, "success");
      else toast(`Envoi Telegram refuse : ${d.error ?? "inconnu"}`, "error");
    } catch (err) {
      toast(`Envoi Telegram impossible : ${err}`, "error");
    } finally {
      setEnvoiTelegram(false);
    }
  }, [selectionNeo, toast]);

  const basculerSelection = useCallback((chemin: string) => {
    setSelectionNeo((s) => (s.includes(chemin) ? s.filter((p) => p !== chemin) : [...s, chemin]));
  }, []);

  const supprimerSelection = useCallback(async () => {
    if (selectionNeo.length === 0) return;
    const n = selectionNeo.length;
    if (!window.confirm(`Supprimer definitivement ${n} fichier(s) ?\n\nCette action est irreversible.`)) return;
    setSuppressionNeo(true);
    try {
      const d = await sendJSON<{ ok: boolean; supprimes?: string[]; erreurs?: string[] }>(
        VIDEO_API + "/neo-delete",
        "POST",
        { paths: selectionNeo }
      );
      const sup = d.supprimes?.length ?? 0;
      const err = d.erreurs ?? [];
      if (sup > 0) toast(`${sup} fichier(s) supprime(s)`, "success");
      if (err.length > 0) toast(`${err.length} echec(s) : ${err[0]}`, "error");
      setSelectionNeo([]);
      refreshNeoFiles();
    } catch (err) {
      toast(`Suppression impossible : ${err}`, "error");
    } finally {
      setSuppressionNeo(false);
    }
  }, [selectionNeo, toast, refreshNeoFiles]);

  /* ---------------- Filtrage ---------------- */
  const docsFiltres = filtre ? docs.filter((d) => d.category === filtre) : [];
  const ficheActive = fiches.find((f) => f.category === filtre);

  const neoFiltres = neoFiles.filter((nf) => {
    if (sousCategorie === "toutes") return true;
    const type = getFileType(nf);
    if (sousCategorie === "autres") {
      const knownTypes = SOUS_CATEGORIES.filter(c => c.id !== "toutes" && c.id !== "autres").map(c => c.id);
      return !knownTypes.includes(type as typeof knownTypes[number]);
    }
    const validTypes = ["images", "video", "mesh", "documents", "code", "autres"];
    if (!validTypes.includes(type)) return false;
    return type === sousCategorie;
  });

  const comptesSousCat: Record<string, number> = { toutes: neoFiles.length };
  for (const c of SOUS_CATEGORIES) {
    if (c.id === "toutes") continue;
    comptesSousCat[c.id] = neoFiles.filter((nf) => {
      const type = getFileType(nf);
      if (c.id === "autres") {
        const knownTypes = SOUS_CATEGORIES.filter(cc => cc.id !== "toutes" && cc.id !== "autres").map(cc => cc.id);
        return !knownTypes.includes(type as typeof knownTypes[number]);
      }
      return type === c.id;
    }).length;
  }

  return (
    <>
      <div className="view-header">
        <h2>Documents</h2>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
          {neoFiles.length} fichier(s) · {docs.length} document(s)
          {nonClasses > 0 ? ` · ${nonClasses} a classer` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <button
          onClick={() => {
            refreshFiches();
            refreshDocs();
            refreshNeoFiles();
          }}
        >
          Rafraichir
        </button>
      </div>

      <div className="content-scroll">
        {/* ---------------- Recherche + import ---------------- */}
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <input
            placeholder="Rechercher dans les documents indexes…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void doSearch();
            }}
            style={{ flex: 1, minWidth: 240 }}
          />
          <button className="primary" disabled={recherche || !query.trim()} onClick={() => void doSearch()}>
            {recherche ? "Recherche…" : "Rechercher"}
          </button>
          <button onClick={() => fileRef.current?.click()}>Ajouter document(s)</button>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".pdf,.docx,.xlsx,.xlsm,.txt,.md,.csv,.json,.py,.log"
            style={{ display: "none" }}
            onChange={(e) => {
              void uploader(e.target.files);
              if (fileRef.current) fileRef.current.value = "";
            }}
          />
        </div>

        {results.length > 0 && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <h3>Extraits trouves ({results.length})</h3>
            {results.map((r, i) => (
              <div key={`${r.document_id}-${r.position}-${i}`} className="search-hit" style={{ marginBottom: 8 }}>
                <div className="search-hit-head">
                  <span className="chip">{r.filename}</span>
                  <span className="chip">extrait {r.position + 1}</span>
                  <span style={{ flex: 1 }} />
                  <button
                    className="tts-btn"
                    title="Telecharger le document source"
                    onClick={() => telecharger(cheminDocument(r.document_id, r.filename), r.filename)}
                  >
                    ⬇
                  </button>
                </div>
                <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 4 }}>
                  {r.content.slice(0, 320)}
                  {r.content.length > 320 ? "…" : ""}
                </div>
              </div>
            ))}
            <button onClick={() => setResults([])}>Effacer les resultats</button>
          </div>
        )}

        {/* ---------------- Filtres et effets d'une image de Neo ---------------- */}
        {photoFiltres && (
          <div
            className="fullscreen-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Filtres et effets"
            onClick={() => setPhotoFiltres(null)}
          >
            <div className="fullscreen-content" onClick={(e) => e.stopPropagation()}>
              <button className="fs-close" onClick={() => setPhotoFiltres(null)} aria-label="Fermer">
                ✕
              </button>
              <PhotoFiltres
                chemin={photoFiltres.path}
                toast={toast}
                onCopie={() => {
                  // La copie filtree apparait dans la liste, a cote de l'original.
                  setPhotoFiltres(null);
                  refreshNeoFiles();
                }}
              />
              <div className="fs-caption">{photoFiltres.name}</div>
            </div>
          </div>
        )}

        {/* ---------------- Fiches de connaissances ---------------- */}
        <FichesPanel toast={toast} />

        {/* ---------------- Vignettes par categorie ---------------- */}
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Documents importés, par catégorie</h3>
          {fiches.length === 0 ? (
            <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
              Aucun document indexe pour l'instant. Utilisez « Ajouter document(s) ».
            </div>
          ) : (
            <div className="fiches-grid">
              {fiches.map((f) => {
                const actif = filtre === f.category;
                return (
                  <button
                    key={f.category}
                    type="button"
                    className={`neo-file-card fiche-card ${actif ? "selected" : ""}`}
                    onClick={() => setFiltre(actif ? "" : f.category)}
                    title={CAT_DESCS[f.category] ?? f.category}
                  >
                    <div className="fiche-visual">
                      <span className="fiche-icon" aria-hidden="true">
                        <CatIcon cat={f.category} />
                      </span>
                      {f.illustration && (
                        <img
                          src={fichierUrl(`data/fiches/fiche_${f.category}.jpg`)}
                          alt={f.category}
                          loading="lazy"
                          onError={(e) => {
                            (e.currentTarget as HTMLImageElement).style.display = "none";
                          }}
                        />
                      )}
                    </div>
                    <div className="fiche-label">{CAT_LABELS[f.category] ?? f.category}</div>
                    <div className="fiche-count">{f.count} doc.</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* ---------------- Detail d'une categorie ---------------- */}
        {filtre && ficheActive && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
              <h3 style={{ margin: 0 }}>{CAT_LABELS[filtre] ?? filtre}</h3>
              <span className="chip">{docsFiltres.length} document(s)</span>
              <span style={{ flex: 1 }} />
              <button
                className="primary"
                disabled={appr?.running ?? false}
                onClick={() => void lancerApprentissage(filtre)}
              >
                {appr?.running && appr.category === filtre ? "Neo apprend…" : "Lancer un apprentissage"}
              </button>
              <button onClick={() => setFiltre("")}>Fermer</button>
            </div>
            <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 10 }}>
              {CAT_DESCS[filtre] ?? ""}
            </div>
            {appr?.running && (
              <div style={{ fontSize: 12, color: "var(--orange)", marginBottom: 10 }}>
                {appr.category === filtre
                  ? `en cours : ${appr.etape || "traitement…"}`
                  : `en attente : Neo apprend « ${CAT_LABELS[appr.category] ?? appr.category} »`}
              </div>
            )}

            {docsFiltres.length === 0 ? (
              <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
                Aucun document dans cette categorie.
              </div>
            ) : (
              <div className="fiches-grid">
                {docsFiltres.map((d) => (
                  <div key={d.id} className="neo-file-card doc-card">
                    <div className="fiche-label" title={d.filename}>
                      {d.filename}
                    </div>
                    <div className="fiche-count">
                      {tailleLisible(d.size_bytes)} · {d.chunk_count} extrait(s)
                    </div>
                    <div className="neo-file-actions">
                      <button
                        type="button"
                        className="tts-btn"
                        title="Ouvrir dans un nouvel onglet"
                        onClick={() => void ouvrirFichier(cheminDocument(d.id, d.filename))}
                      >
                        ⧉
                      </button>
                      <button
                        type="button"
                        className="tts-btn"
                        title="Telecharger dans le format d'origine"
                        onClick={() => telecharger(cheminDocument(d.id, d.filename), d.filename)}
                      >
                        ⬇
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ---------------- Fichiers generes par Neo ---------------- */}
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>
            Fichiers de Neo ({neoFiltres.length}
            {sousCategorie !== "toutes" ? ` / ${neoFiles.length}` : ""})
          </h3>

          <SousCategorieBar actif={sousCategorie} onSelect={setSousCategorie} comptes={comptesSousCat} />

          <div className="toolbar" style={{ marginBottom: 10 }}>
            <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
              {selectionNeo.length > 0 ? `${selectionNeo.length} selectionne(s)` : `${neoFiltres.length} fichier(s)`}
            </span>
            <button onClick={() => setSelectionNeo(neoFiltres.map((n) => n.path))} disabled={neoFiltres.length === 0}>
              Tout selectionner
            </button>
            {selectionNeo.length > 0 && (
              <>
                <button className="primary" disabled={envoiTelegram} onClick={() => void envoyerTelegram()}>
                  {envoiTelegram ? "Envoi…" : "✈ Envoyer sur Telegram"}
                </button>
                <button className="danger" disabled={suppressionNeo} onClick={() => void supprimerSelection()}>
                  {suppressionNeo ? "Suppression…" : `🗑 Supprimer (${selectionNeo.length})`}
                </button>
                <button onClick={() => setSelectionNeo([])}>Tout deselectionner</button>
              </>
            )}
          </div>

          {neoFiltres.length === 0 ? (
            <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
              Aucun fichier dans cette sous-categorie. Demandez une image dans le
              chat, elle apparaitra ici.
            </div>
          ) : (
            <div className="neo-file-grid">
              {neoFiltres.map((nf) => {
                const choisi = selectionNeo.includes(nf.path);
                const ext = extension(nf.path);
                const type = getFileType(nf);
                const estImage = type === "images";
                const estVideo = type === "video";
                return (
                  <div
                    key={nf.path}
                    className={`neo-file-card ${choisi ? "selected" : ""}`}
                    onClick={() => basculerSelection(nf.path)}
                    onKeyDown={(e) => {
                      if (e.target !== e.currentTarget) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        basculerSelection(nf.path);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-pressed={choisi}
                    title={nf.path}
                  >
                    <div className={`neo-thumb type-${type}`}>
                      {estImage || (nf.preview_url && !estVideo) ? (
                        <img
                          src={urlNeo(nf)}
                          alt={nf.name}
                          loading="lazy"
                          onError={(e) => {
                            // Un seul repli, et seulement s'il differe de la
                            // source actuelle (sinon boucle de rechargement).
                            const img = e.currentTarget;
                            if (img.dataset.repliTente) return;
                            img.dataset.repliTente = "1";
                            // Vignette d'un modele 3D illisible : pas de repli
                            // sur le fichier lui-meme, qui n'est pas une image.
                            if (!estImage) {
                              img.style.display = "none";
                              return;
                            }
                            const repli = nf.url ? avecToken(nf.url) : fichierUrl(srcFallback(nf.path));
                            if (repli !== img.getAttribute("src")) img.src = repli;
                          }}
                        />
                      ) : estVideo ? (
                        <video src={urlOriginale(nf)} preload="metadata" muted playsInline />
                      ) : (
                        <span className="neo-ico">{ext.toUpperCase() || "?"}</span>
                      )}
                      <span className={`neo-badge cat-${type}`}>{type}</span>
                    </div>
                    <div className="neo-file-name" title={nf.name}>
                      {nf.name}
                    </div>
                    <div className="neo-file-meta">
                      {tailleLisible(nf.size_bytes)} · {dateCourte(nf.modified)}
                    </div>
                    <div className="neo-file-actions">
                      {estImage && (
                        <button
                          type="button"
                          className="tts-btn"
                          title="Filtres et effets : essayer, puis enregistrer une copie"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPhotoFiltres(nf);
                          }}
                        >
                          🎨
                        </button>
                      )}
                      <button
                        type="button"
                        className="tts-btn"
                        title="Telecharger dans le format d'origine"
                        onClick={(e) => {
                          e.stopPropagation();
                          telechargerNeo(nf);
                        }}
                      >
                        ⬇
                      </button>
                      <button
                        type="button"
                        className="tts-btn"
                        title="Ouvrir dans un nouvel onglet"
                        onClick={(e) => {
                          e.stopPropagation();
                          ouvrirNeo(nf);
                        }}
                      >
                        ⧉
                      </button>
                      <span className={`neo-check ${choisi ? "checked" : ""}`}>{choisi ? "✓" : ""}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
