import { useCallback, useEffect, useMemo, useState } from "react";
import { servicePy } from "../lib/api";
import type { ToastFn } from "../lib/toast";

/* Filtres et effets d'une photo, avec apercu immediat.
 *
 * A l'ouverture, le service Python rend TOUS les filtres sur une copie reduite
 * de l'image (une seconde environ) ; ils restent en memoire, si bien que passer
 * de l'un a l'autre ne demande plus aucun appel. L'intensite se regle en
 * superposant l'apercu filtre a l'original (meme calcul que le rendu final).
 * « Enregistrer une copie » applique le filtre a l'image pleine resolution ;
 * l'original n'est jamais modifie. */

interface Filtre {
  id: string;
  nom: string;
  famille: string;
  apercu: string;
}

interface Apercus {
  source: string;
  original: string;
  filtres: Filtre[];
  largeur: number;
  hauteur: number;
}

interface Props {
  /** Chemin local de l'image (parametre `path` de son URL). */
  chemin: string;
  toast: ToastFn;
  /** Appele avec le chemin de la copie enregistree. */
  onCopie: (chemin: string) => void;
}

const appel = <T,>(route: string, corps: unknown) => servicePy<T>(route, "POST", corps);

export default function PhotoFiltres({ chemin, toast, onCopie }: Props) {
  const [donnees, setDonnees] = useState<Apercus | null>(null);
  const [erreur, setErreur] = useState("");
  const [choisi, setChoisi] = useState("");
  const [force, setForce] = useState(100);
  const [enregistrement, setEnregistrement] = useState(false);

  useEffect(() => {
    let actif = true;
    setDonnees(null);
    setErreur("");
    setChoisi("");
    appel<Apercus>("/api/photo/apercus", { path: chemin })
      .then((d) => actif && setDonnees(d))
      .catch((e: unknown) => actif && setErreur(e instanceof Error ? e.message : String(e)));
    return () => {
      actif = false;
    };
  }, [chemin]);

  const ordre = useMemo(() => ["", ...(donnees?.filtres.map((f) => f.id) ?? [])], [donnees]);
  const filtre = donnees?.filtres.find((f) => f.id === choisi) ?? null;

  // Fleches gauche / droite : filtre precedent / suivant.
  useEffect(() => {
    const surTouche = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if ((e.target as HTMLElement | null)?.tagName === "INPUT") return;
      e.preventDefault();
      setChoisi((actuel) => {
        const i = ordre.indexOf(actuel);
        return ordre[(i + (e.key === "ArrowRight" ? 1 : ordre.length - 1)) % ordre.length];
      });
    };
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [ordre]);

  // Le filtre choisi au clavier reste visible dans la bande.
  useEffect(() => {
    document
      .querySelector(`.pf-vignette[data-filtre="${choisi || "original"}"]`)
      ?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [choisi]);

  const enregistrer = useCallback(async () => {
    if (!choisi) return;
    setEnregistrement(true);
    try {
      const d = await appel<{ path: string; nom: string }>("/api/photo/appliquer", {
        path: chemin,
        filtre: choisi,
        force: force / 100,
      });
      toast(`Copie enregistree : ${d.nom}`, "success");
      onCopie(d.path);
    } catch (e) {
      toast(`Enregistrement impossible : ${e instanceof Error ? e.message : e}`, "error");
    } finally {
      setEnregistrement(false);
    }
  }, [chemin, choisi, force, toast, onCopie]);

  if (erreur) return <div className="pf-etat">Filtres indisponibles : {erreur}</div>;
  if (!donnees) return <div className="pf-etat">Préparation des filtres…</div>;

  return (
    <div className="pf">
      <div className="pf-scene">
        <img src={donnees.original} alt="Original" className="pf-img" />
        {filtre && (
          <img src={filtre.apercu} alt={filtre.nom} className="pf-img pf-dessus" style={{ opacity: force / 100 }} />
        )}
      </div>
      <div className="pf-barre">
        <strong>{filtre ? filtre.nom : "Original"}</strong>
        <label className="pf-force">
          Intensité
          <input
            type="range"
            min={0}
            max={100}
            value={force}
            disabled={!filtre}
            onChange={(e) => setForce(Number(e.target.value))}
          />
          <span>{force} %</span>
        </label>
        <button type="button" className="fs-btn" disabled={!filtre || enregistrement} onClick={() => void enregistrer()}>
          {enregistrement ? "⏳ Enregistrement…" : "💾 Enregistrer une copie"}
        </button>
        <span className="pf-aide">← → pour changer de filtre · l'original n'est pas modifié</span>
      </div>
      <div className="pf-bande" role="listbox" aria-label="Filtres">
        <button
          type="button"
          role="option"
          aria-selected={choisi === ""}
          data-filtre="original"
          className={`pf-vignette ${choisi === "" ? "active" : ""}`}
          onClick={() => setChoisi("")}
        >
          <img src={donnees.original} alt="" />
          <span>Original</span>
        </button>
        {donnees.filtres.map((f, i) => {
          const entete = i === 0 || donnees.filtres[i - 1].famille !== f.famille;
          return (
            <span key={f.id} className="pf-groupe">
              {entete && <span className="pf-famille">{f.famille}</span>}
              <button
                type="button"
                role="option"
                aria-selected={choisi === f.id}
                data-filtre={f.id}
                className={`pf-vignette ${choisi === f.id ? "active" : ""}`}
                onClick={() => setChoisi(f.id)}
              >
                <img src={f.apercu} alt="" />
                <span>{f.nom}</span>
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}
