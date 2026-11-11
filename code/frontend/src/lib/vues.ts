/** Vues de l'application — source unique pour le shell a onglets. */
export const VUES = [
  "accueil",
  "chat",
  "collab",
  "documents",
  "models",
  "finetune",
  "settings",
  "video",
] as const;

export type Vue = (typeof VUES)[number];

export function estVue(v: string): v is Vue {
  return (VUES as readonly string[]).includes(v);
}
