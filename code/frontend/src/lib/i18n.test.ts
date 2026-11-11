import { describe, expect, it } from "vitest";
import { getLang, LANGS, normalizeLang, setLang, t } from "./i18n";

describe("normalizeLang", () => {
  it("reconnait les codes valides, avec casse et region", () => {
    expect(normalizeLang("FR")).toBe("fr");
    expect(normalizeLang("en-US")).toBe("en");
    expect(normalizeLang("de")).toBe("de");
    expect(normalizeLang("it")).toBe("it");
  });

  it("replie sur fr pour les codes inconnus ou vides", () => {
    expect(normalizeLang("xx")).toBe("fr");
    expect(normalizeLang("")).toBe("fr");
    expect(normalizeLang(null)).toBe("fr");
    expect(normalizeLang(undefined)).toBe("fr");
  });
});

describe("t", () => {
  it("rend la traduction exacte par langue", () => {
    expect(t("fr", "nav.home")).toBe("Accueil");
    expect(t("en", "nav.home")).toBe("Home");
    expect(t("es", "nav.home")).toBe("Inicio");
  });

  it("replie sur la cle quand aucune langue ne la connait", () => {
    expect(t("fr", "cle.inexistante.42")).toBe("cle.inexistante.42");
  });

  it("ne plante jamais, quelle que soit la langue", () => {
    for (const langue of LANGS.map((l) => l.code)) {
      for (const cle of ["nav.home", "nav.settings", "settings.saved"]) {
        expect(t(langue, cle).length).toBeGreaterThan(0);
      }
    }
  });
});

describe("getLang/setLang", () => {
  it("persiste et relit la langue, et met a jour <html lang>", () => {
    setLang("en");
    expect(getLang()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("retombe sur fr sans preference enregistree", () => {
    localStorage.removeItem("neo.lang");
    expect(getLang()).toBe("fr");
  });
});
