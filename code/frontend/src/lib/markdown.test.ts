import { afterEach, describe, expect, it } from "vitest";
import { escapeHtml, renderMarkdown } from "./markdown";

afterEach(() => localStorage.clear());

/** Aucun attribut on* ni URL javascript: dans le DOM produit. */
function sansScript(html: string) {
  const zone = document.createElement("div");
  zone.innerHTML = html;
  for (const el of Array.from(zone.querySelectorAll("*"))) {
    for (const attr of Array.from(el.attributes)) {
      expect(attr.name.toLowerCase().startsWith("on"), `${el.tagName} ${attr.name}`).toBe(false);
      expect(/^\s*javascript:/i.test(attr.value), `${el.tagName} ${attr.name}`).toBe(false);
    }
  }
  // Aucun on*= a l'interieur d'une balise reelle, hors valeurs d'attributs
  // entre guillemets (le texte echappe peut contenir "onerror=" en clair).
  expect(html.replace(/"[^"]*"/g, '""')).not.toMatch(/<[^>]*\son\w+\s*=/i);
  return zone;
}

describe("escapeHtml", () => {
  it("echappe aussi l'apostrophe", () => {
    expect(escapeHtml(`<a href="x" title='y'>&</a>`)).toBe(
      "&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;"
    );
  });
});

describe("renderMarkdown", () => {
  it("neutralise le lien file:// piege (apostrophes + parentheses)", () => {
    localStorage.setItem("neo.token", "secret");
    const html = renderMarkdown("[x](file:///C:/a'-alert%281%29-'.tif)");
    const zone = sansScript(html);
    expect(html).not.toContain("'");
    const img = zone.querySelector("img")!;
    // Fil : version ecran ; plein ecran : le fichier complet (meme chemin).
    expect(img.getAttribute("src")).toBe(`${img.dataset.full}&taille=ecran`);
    expect(img.dataset.fallback).toContain("/api/sd/file?path=");
  });

  it("neutralise les balises et attributs injectes dans le texte", () => {
    const attaques = [
      `<img src=x onerror=alert(1)>`,
      `[a" onmouseover="alert(1)](https://exemple.fr/"onmouseover="alert(1))`,
      `[clic](javascript:alert(1))`,
      `C:\\img\\x'onerror='alert(1)'.png`,
      `[C:\\a.png onerror=alert(1) x](file:///C:/b.png)`,
      `data/x"><svg onload=alert(1)>.png`,
      "`C:\\m\\a' onclick='alert(1).stl`",
      `/api/sd/file?path=a.png' onerror='alert(1)`,
    ];
    for (const a of attaques) sansScript(renderMarkdown(a));
  });

  it("javascript: n'est jamais transforme en lien", () => {
    const zone = sansScript(renderMarkdown("[clic](javascript:alert(1))"));
    expect(zone.querySelector("a")).toBeNull();
  });

  it("rend liens, gras, code et images locales", () => {
    const zone = sansScript(
      renderMarkdown("**fort** `code` [doc](https://exemple.fr/a?b=1&c=2) C:\\imgs\\chat.png")
    );
    expect(zone.querySelector("strong")?.textContent).toBe("fort");
    expect(zone.querySelector("code")?.textContent).toBe("code");
    expect(zone.querySelector("a")?.getAttribute("href")).toBe("https://exemple.fr/a?b=1&c=2");
    const img = zone.querySelector("img")!;
    const fichier = `/api/sd/file?path=${encodeURIComponent("C:\\imgs\\chat.png")}`;
    expect(img.getAttribute("src")).toBe(`${fichier}&taille=ecran`);
    expect(img.dataset.full).toBe(fichier);
    // PNG : pas de repli (il serait identique a la source).
    expect(img.dataset.fallback).toBeUndefined();
  });

  it("master TIFF : apercu JPEG + repli sur le master", () => {
    const img = sansScript(renderMarkdown("E:/out/rendu.tiff")).querySelector("img")!;
    expect(img.getAttribute("src")).toContain(encodeURIComponent("E:/out/rendu_apercu.jpg"));
    expect(img.dataset.fallback).toContain(encodeURIComponent("E:/out/rendu.tiff"));
  });

  it("carte 3D : bouton slicer via data-slicer, sans onclick", () => {
    const zone = sansScript(renderMarkdown("Modele : E:\\3d\\piece.stl"));
    expect(zone.querySelector("button")?.dataset.slicer).toBe("E:\\3d\\piece.stl");
    expect(zone.querySelector("a[download]")).not.toBeNull();
  });

  it("une URL API deja rendue n'est pas reinjectee dans son propre attribut", () => {
    localStorage.setItem("neo.token", "t0k");
    const html = renderMarkdown("![Image](/api/sd/file?path=E%3A%2Fa.png)");
    const zone = sansScript(html);
    expect(zone.querySelectorAll("img")).toHaveLength(1);
    expect(zone.querySelector("img")!.getAttribute("src")).toBe(
      "/api/sd/file?path=E%3A%2Fa.png&token=t0k"
    );
  });
});
