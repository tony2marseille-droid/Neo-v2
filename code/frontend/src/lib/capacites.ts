/**
 * Modules que la machine peut faire tourner, d'apres /api/system/info.
 *
 * Seuils (memoire de la plus grande carte NVIDIA) :
 *  - image (studio 4K)     : 8 Go
 *  - video (Wan 2.2)       : 12 Go
 *  - finetune (LoRA)       : 16 Go et 32 Go de memoire vive
 * Sans information (serveur indisponible), rien n'est grise : on ne bloque
 * jamais l'utilisateur sur une supposition.
 */
import type { SystemInfo } from "./api";

export type ModuleLourd = "image" | "video" | "finetune";

export interface Capacite {
  ok: boolean;
  raison: string;
}

const SEUILS: Record<ModuleLourd, { vram: number; ram?: number; nom: string }> = {
  image: { vram: 8, nom: "Le studio image 4K" },
  video: { vram: 12, nom: "La vidéo" },
  finetune: { vram: 16, ram: 32, nom: "L'entraînement de modèles" },
};

export function calculerCapacites(info: SystemInfo | null | undefined): Record<ModuleLourd, Capacite> {
  const libre = { ok: true, raison: "" };
  if (!info) return { image: libre, video: libre, finetune: libre };
  // Une carte « 16 Go » annonce 15,9 Go au pilote : tolerance d'un demi-Go sur les seuils.
  const vramGo = Math.max(0, ...(info.gpus ?? []).map((g) => g.vram_total_mb / 1024)) + 0.5;
  const ramGo = info.ram?.total_gb ?? 0;
  const res = {} as Record<ModuleLourd, Capacite>;
  for (const m of Object.keys(SEUILS) as ModuleLourd[]) {
    const s = SEUILS[m];
    if (vramGo < s.vram) {
      const detecte = vramGo > 0.5 ? `${(vramGo - 0.5).toFixed(0)} Go détectés` : "aucune carte NVIDIA détectée";
      res[m] = { ok: false, raison: `${s.nom} demande une carte NVIDIA de ${s.vram} Go (${detecte}).` };
    } else if (s.ram && ramGo < s.ram) {
      res[m] = { ok: false, raison: `${s.nom} demande ${s.ram} Go de mémoire vive (${ramGo.toFixed(0)} Go détectés).` };
    } else {
      res[m] = { ok: true, raison: "" };
    }
  }
  return res;
}
