import { useCallback, useEffect, useState, type ReactNode } from "react";
import { fichierUrl, servicePy } from "../lib/api";
import type { ToastFn } from "../lib/toast";

/* Fiches de connaissances : ce que Neo a appris et garde.
 *
 * Une fiche porte sur un sujet (titre, resume, points cles, sources). On en
 * cree une ici en donnant un sujet, dans la conversation (« fais-moi une fiche
 * sur… »), ou Neo en garde une de lui-meme apres une recherche sur un vrai
 * sujet. Tout passe par le service Python (/api/fiches). */

interface Fiche {
  id: number;
  titre: string;
  categorie: string;
  resume: string;
  contenu_md?: string;
  image_path: string;
  source: string;
  sources: string[];
  updated_at: string;
}

const CATEGORIES: Record<string, { nom: string; icone: string }> = {
  sciences: { nom: "Sciences", icone: "🔬" },
  technologie: { nom: "Technologie", icone: "💡" },
  histoire: { nom: "Histoire", icone: "🏛️" },
  geographie: { nom: "Géographie", icone: "🗺️" },
  nature: { nom: "Nature", icone: "🌿" },
  personnalites: { nom: "Personnalités", icone: "👤" },
  culture: { nom: "Culture", icone: "🎭" },
  cuisine: { nom: "Cuisine", icone: "🍳" },
  sante: { nom: "Santé", icone: "❤️" },
  sport: { nom: "Sport", icone: "⚽" },
  pratique: { nom: "Pratique", icone: "🧰" },
  autre: { nom: "Autre", icone: "📘" },
};

const categorie = (id: string) => CATEGORIES[id] ?? { nom: id || "Autre", icone: "📘" };

function dateCourte(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
}

/** Corps d'une fiche (Markdown simple : titres ##, puces, paragraphes). */
function Corps({ texte }: { texte: string }) {
  const sortie: ReactNode[] = [];
  let puces: string[] = [];
  const vider = () => {
    if (puces.length === 0) return;
    sortie.push(
      <ul key={`u${sortie.length}`}>
        {puces.map((p, i) => (
          <li key={i}>{p}</li>
        ))}
      </ul>
    );
    puces = [];
  };
  for (const brute of texte.split("\n")) {
    const ligne = brute.trim().replace(/\*\*/g, "");
    if (!ligne) continue;
    if (/^[-*]\s+/.test(ligne)) {
      puces.push(ligne.replace(/^[-*]\s+/, ""));
      continue;
    }
    vider();
    if (ligne.startsWith("#")) sortie.push(<h3 key={`h${sortie.length}`}>{ligne.replace(/^#+\s*/, "")}</h3>);
    else sortie.push(<p key={`p${sortie.length}`}>{ligne}</p>);
  }
  vider();
  return <>{sortie}</>;
}

export default function FichesPanel({ toast }: { toast: ToastFn }) {
  const [fiches, setFiches] = useState<Fiche[]>([]);
  const [chargee, setChargee] = useState(false);
  const [filtre, setFiltre] = useState("");
  const [recherche, setRecherche] = useState("");
  const [sujet, setSujet] = useState("");
  const [avecImage, setAvecImage] = useState(false);
  const [creation, setCreation] = useState(false);
  const [ouverte, setOuverte] = useState<Fiche | null>(null);
  const [action, setAction] = useState("");

  const charger = useCallback(() => {
    servicePy<{ fiches: Fiche[] }>("/api/fiches")
      .then((d) => setFiches(d.fiches ?? []))
      .catch(() => undefined)
      .finally(() => setChargee(true));
  }, []);

  useEffect(charger, [charger]);

  const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const creer = useCallback(async () => {
    const s = sujet.trim();
    if (!s || creation) return;
    setCreation(true);
    try {
      const f = await servicePy<Fiche>("/api/fiches", "POST", { sujet: s, illustrer: avecImage });
      toast(`Fiche « ${f.titre} » enregistree`, "success");
      setSujet("");
      setOuverte(f);
      charger();
    } catch (e) {
      toast(`Fiche impossible : ${message(e)}`, "error");
    } finally {
      setCreation(false);
    }
  }, [sujet, avecImage, creation, toast, charger]);

  const ouvrir = useCallback(
    async (id: number) => {
      try {
        setOuverte(await servicePy<Fiche>(`/api/fiches/${id}`));
      } catch (e) {
        toast(`Ouverture impossible : ${message(e)}`, "error");
      }
    },
    [toast]
  );

  // Actions du lecteur : actualiser, illustrer (renvoient la fiche), exporter, supprimer.
  const agir = useCallback(
    async (nom: "actualiser" | "illustrer" | "exporter" | "supprimer") => {
      if (!ouverte || action) return;
      if (nom === "supprimer" && !window.confirm(`Supprimer la fiche « ${ouverte.titre} » ?`)) return;
      setAction(nom);
      try {
        if (nom === "supprimer") {
          await servicePy(`/api/fiches/${ouverte.id}`, "DELETE");
          toast("Fiche supprimee", "success");
          setOuverte(null);
        } else if (nom === "exporter") {
          const d = await servicePy<{ path: string }>(`/api/fiches/${ouverte.id}/exporter`, "POST", { type_doc: "pdf" });
          window.open(fichierUrl(d.path), "_blank", "noopener");
          toast(`PDF enregistre : ${d.path}`, "success");
        } else {
          setOuverte(await servicePy<Fiche>(`/api/fiches/${ouverte.id}/${nom}`, "POST", {}));
          toast(nom === "illustrer" ? "Illustration ajoutee" : "Fiche actualisee", "success");
        }
        charger();
      } catch (e) {
        toast(`Action impossible : ${message(e)}`, "error");
      } finally {
        setAction("");
      }
    },
    [ouverte, action, toast, charger]
  );

  const presentes = Array.from(new Set(fiches.map((f) => f.categorie)));
  const q = recherche.trim().toLowerCase();
  const visibles = fiches.filter(
    (f) => (!filtre || f.categorie === filtre) && (!q || `${f.titre} ${f.resume}`.toLowerCase().includes(q))
  );

  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <h3>Fiches de connaissances ({fiches.length})</h3>
      <div className="toolbar" style={{ marginBottom: 10 }}>
        <input
          placeholder="Sujet d'une nouvelle fiche (ex. le pont du Gard)…"
          value={sujet}
          disabled={creation}
          onChange={(e) => setSujet(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void creer();
          }}
          style={{ flex: 1, minWidth: 240 }}
        />
        <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={avecImage} onChange={(e) => setAvecImage(e.target.checked)} />
          avec illustration
        </label>
        <button className="primary" disabled={creation || !sujet.trim()} onClick={() => void creer()}>
          {creation ? "Néo cherche et rédige…" : "Créer la fiche"}
        </button>
      </div>

      {fiches.length > 0 && (
        <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button type="button" className={`tier-chip ${filtre === "" ? "active" : ""}`} onClick={() => setFiltre("")}>
            Toutes ({fiches.length})
          </button>
          {presentes.map((c) => (
            <button
              key={c}
              type="button"
              className={`tier-chip ${filtre === c ? "active" : ""}`}
              onClick={() => setFiltre(filtre === c ? "" : c)}
            >
              {categorie(c).icone} {categorie(c).nom} ({fiches.filter((f) => f.categorie === c).length})
            </button>
          ))}
          <span style={{ flex: 1 }} />
          <input
            placeholder="Chercher dans les fiches…"
            value={recherche}
            onChange={(e) => setRecherche(e.target.value)}
            style={{ minWidth: 200 }}
          />
        </div>
      )}

      {visibles.length === 0 ? (
        <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
          {!chargee
            ? "Chargement…"
            : fiches.length === 0
              ? "Aucune fiche pour l'instant. Donne un sujet ci-dessus, ou dis à Néo : « fais-moi une fiche sur… »."
              : "Aucune fiche ne correspond."}
        </div>
      ) : (
        <div className="savoir-grid">
          {visibles.map((f) => (
            <button key={f.id} type="button" className="neo-file-card savoir-carte" onClick={() => void ouvrir(f.id)}>
              <div className="savoir-visuel">
                {f.image_path ? <img src={fichierUrl(f.image_path)} alt="" loading="lazy" /> : categorie(f.categorie).icone}
              </div>
              <div className="savoir-texte">
                <div className="savoir-titre">{f.titre}</div>
                {/* Anciennes fiches automatiques : leur « resume » est un titre de section. */}
                <div className="savoir-resume">
                  {f.resume && !f.resume.startsWith("#") ? f.resume : "Ancienne fiche : ouvre-la puis « Actualiser »."}
                </div>
                <div className="savoir-meta">
                  <span className="chip">{categorie(f.categorie).nom}</span>
                  <span>{dateCourte(f.updated_at)}</span>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {ouverte && (
        <div className="savoir-lecteur" role="dialog" aria-modal="true" aria-label={ouverte.titre} onClick={() => setOuverte(null)}>
          <div className="savoir-feuille" onClick={(e) => e.stopPropagation()}>
            {ouverte.image_path && <img src={fichierUrl(ouverte.image_path)} alt="" />}
            <div className="savoir-corps">
              <h2>{ouverte.titre}</h2>
              <div className="savoir-meta" style={{ marginBottom: 10 }}>
                <span className="chip">
                  {categorie(ouverte.categorie).icone} {categorie(ouverte.categorie).nom}
                </span>
                <span>mise à jour le {dateCourte(ouverte.updated_at)}</span>
              </div>
              {ouverte.resume && !ouverte.resume.startsWith("#") && <p className="savoir-chapeau">{ouverte.resume}</p>}
              <Corps texte={ouverte.contenu_md ?? ""} />
              {ouverte.sources.length > 0 && (
                <div className="savoir-sources">
                  <h3>Sources</h3>
                  <ul>
                    {ouverte.sources.map((s) => (
                      <li key={s}>
                        <a href={s} target="_blank" rel="noopener noreferrer">
                          {s}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="toolbar" style={{ marginTop: 18 }}>
                <button disabled={!!action} onClick={() => void agir("actualiser")} title="Nouvelle recherche et nouvelle rédaction">
                  {action === "actualiser" ? "Néo réécrit…" : "↻ Actualiser"}
                </button>
                <button disabled={!!action} onClick={() => void agir("illustrer")} title="Dessiner une illustration (20 s environ)">
                  {action === "illustrer" ? "Néo dessine…" : "🖼 Illustrer"}
                </button>
                <button disabled={!!action} onClick={() => void agir("exporter")}>
                  {action === "exporter" ? "Export…" : "⬇ PDF"}
                </button>
                <span style={{ flex: 1 }} />
                <button className="danger" disabled={!!action} onClick={() => void agir("supprimer")}>
                  🗑 Supprimer
                </button>
                <button onClick={() => setOuverte(null)}>Fermer</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
