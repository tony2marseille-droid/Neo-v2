/** Habillages (skins) disponibles : source unique (liste, apercu, application). */
export const SKINS = ["oracle", "cartoon", "retro", "cyberpunk", "aurore"] as const;

export type Skin = (typeof SKINS)[number];

/** Couleurs de l'apercu dans la galerie des reglages (fond, accent, accent 2). */
export const APERCU: Record<Skin, [string, string, string]> = {
  oracle: ["#020603", "#00ff41", "#7dffa8"],
  cartoon: ["#f0f7ff", "#ff6b6b", "#4dabf7"],
  retro: ["#1a1a2e", "#e0b354", "#f2d9a6"],
  cyberpunk: ["#050510", "#ff2bd6", "#00e5ff"],
  aurore: ["#f6f7fb", "#5b5bd6", "#ec78a0"],
};

const CLE = "neo.skin";

export function lireSkin(): Skin {
  let memo: string | null = null;
  try {
    memo = localStorage.getItem(CLE);
  } catch {
    // stockage indisponible (navigation privee) : skin par defaut
  }
  return SKINS.find((s) => s === memo) ?? "oracle";
}

/** Pose le skin sur <html> (jetons de theme.css) et le memorise. */
export function appliquerSkin(skin: Skin): void {
  const racine = document.documentElement;
  racine.dataset.skin = skin;
  for (const s of SKINS) racine.classList.toggle(`skin-${s}`, s === skin);
  try {
    localStorage.setItem(CLE, skin);
  } catch {
    // sans stockage, le skin vaut pour la session
  }
}
