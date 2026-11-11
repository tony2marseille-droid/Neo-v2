import { describe, expect, it } from "vitest";
import type { SystemInfo } from "./api";
import { calculerCapacites } from "./capacites";

function machine(vramGo: number[], ramGo: number): SystemInfo {
  return {
    app: { name: "Neo", version: "test" },
    uptime_seconds: 0,
    cpu: { percent: 0, cores_physical: 4, cores_logical: 8 },
    ram: { used_gb: 1, total_gb: ramGo, percent: 1 },
    disks: [],
    gpus: vramGo.map((v) => ({
      name: "NVIDIA",
      vram_used_mb: 0,
      vram_total_mb: v * 1024,
      utilization_percent: 0,
      temperature_c: 40,
    })),
    ollama: { available: true, version: "x", host: "h" },
    models_dir: "",
  };
}

describe("calculerCapacites", () => {
  it("ne grise rien sans information", () => {
    const c = calculerCapacites(null);
    expect(c.image.ok && c.video.ok && c.finetune.ok).toBe(true);
  });

  it("portable 6 Go : tout ce qui est lourd est grise, avec la raison", () => {
    const c = calculerCapacites(machine([6], 16));
    expect(c.image.ok).toBe(false);
    expect(c.video.ok).toBe(false);
    expect(c.finetune.ok).toBe(false);
    expect(c.image.raison).toContain("8 Go");
    expect(c.image.raison).toContain("6 Go détectés");
  });

  it("sans carte NVIDIA", () => {
    expect(calculerCapacites(machine([], 32)).video.raison).toContain("aucune carte NVIDIA");
  });

  it("carte 12 Go : image et video, pas d'entrainement", () => {
    const c = calculerCapacites(machine([12], 32));
    expect([c.image.ok, c.video.ok, c.finetune.ok]).toEqual([true, true, false]);
  });

  it("carte 16 Go mais 16 Go de RAM : entrainement grise pour la memoire vive", () => {
    const c = calculerCapacites(machine([16], 16));
    expect(c.finetune.ok).toBe(false);
    expect(c.finetune.raison).toContain("mémoire vive");
  });

  it("carte 16 Go annoncee 15,9 Go par le pilote : rien n'est grise", () => {
    const c = calculerCapacites(machine([15.9], 48));
    expect([c.image.ok, c.video.ok, c.finetune.ok]).toEqual([true, true, true]);
  });

  it("machine de reference (16 Go, 48 Go) : tout est disponible", () => {
    const c = calculerCapacites(machine([16], 48));
    expect([c.image.ok, c.video.ok, c.finetune.ok]).toEqual([true, true, true]);
  });
});
