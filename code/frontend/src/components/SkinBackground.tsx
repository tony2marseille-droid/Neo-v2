import type { Skin } from "../lib/skins";
import MatrixRain from "./MatrixRain";

/**
 * Fond dynamique propre a chaque skin.
 *
 * - oracle    : pluie Matrix (canvas, deja optimisee : 15 i/s, pause onglet).
 * - cartoon   : bulles pastel qui montent + deux gros blobs qui respirent.
 * - retro     : balayage CRT, vignette et grain (aucune image externe).
 * - cyberpunk : grille neon en perspective + faisceaux qui balaient.
 * - aurore    : nappes de lumiere floues qui derivent lentement.
 *
 * Tout est en CSS (sauf Matrix), `pointer-events: none`, et coupe les
 * animations si `prefers-reduced-motion`.
 */
export default function SkinBackground({ skin }: { skin: Skin }) {
  return (
    <div className={`skin-bg skin-bg-${skin}`} aria-hidden="true">
      {skin === "oracle" && <MatrixRain background />}

      {skin === "cartoon" && (
        <div className="bg-cartoon">
          <div className="blob blob-a" />
          <div className="blob blob-b" />
          {Array.from({ length: 12 }).map((_, i) => (
            <span key={i} className={`bulle bulle-${(i % 6) + 1}`} />
          ))}
        </div>
      )}

      {skin === "retro" && (
        <div className="bg-retro">
          <div className="retro-scan" />
          <div className="retro-vignette" />
          <div className="retro-lignes" />
        </div>
      )}

      {skin === "cyberpunk" && (
        <div className="bg-cyberpunk">
          <div className="cyber-grid" />
          <div className="cyber-horizon" />
          <span className="cyber-streak s1" />
          <span className="cyber-streak s2" />
          <span className="cyber-streak s3" />
        </div>
      )}

      {skin === "aurore" && (
        <div className="bg-aurore">
          <div className="aurore-nappe n1" />
          <div className="aurore-nappe n2" />
          <div className="aurore-nappe n3" />
        </div>
      )}
    </div>
  );
}
