import { avecToken, fichierUrl } from "./api";

/**
 * Rendu des messages du chat (Markdown minimal + apercus d'images/3D).
 *
 * Fonction pure qui renvoie du HTML SANS aucun JavaScript inline : pas
 * d'attribut on*, pas d'URL javascript:. Les interactions passent par des
 * attributs data-* lus par delegation d'evenements dans ChatView :
 *   - data-full     : image a ouvrir en plein ecran au clic ;
 *   - data-fallback : source de repli si l'image ne charge pas ;
 *   - data-slicer   : chemin du modele 3D a ouvrir dans le slicer.
 *
 * Le texte est echappe en premier ; chaque fragment HTML genere est mis de
 * cote (jeton n) pour que les regles suivantes ne le reecrivent
 * jamais (plus d'injection d'une balise dans l'attribut d'une autre).
 */

const IMG_EXT = "png|jpe?g|webp|tiff?|exr";
const RE_LIEN =
  /\[([^\]]{1,120})\]\(((?:file:\/\/|https?:\/\/|\/api\/(?:sd|video)\/file\?)[^\s)]+)\)/g;
const RE_URL_IMAGE = new RegExp(`\\.(${IMG_EXT})(\\?.*)?$`, "i");
// Chemins d'images Windows ou relatifs entre backticks : on retire les
// backticks (le modele en met parfois, ce qui empechait la detection).
const RE_BACKTICK_CHEMIN = new RegExp(`\`([A-Za-z]:[/\\\\][^\\s<>\`]*?\\.(?:${IMG_EXT}))\``, "gi");
const RE_BACKTICK_API = new RegExp(`\`(\\/api\\/(?:sd|video)\\/file\\?[^\\s<>\`]*?\\.(?:${IMG_EXT}))\``, "gi");
// Caracteres admis dans un chemin nu (les jetons / en sont exclus).
const CAR_CHEMIN = "[^\\s<>\"'`*\\uE000\\uE001]";
const DEBUT_CHEMIN = "(?:[A-Za-z]:[/\\\\]|data[/\\\\]|workspace[/\\\\])";
const RE_CHEMIN_IMAGE = new RegExp(`\\b(${DEBUT_CHEMIN}${CAR_CHEMIN}+?\\.(?:${IMG_EXT}))\\b`, "gi");
const RE_URL_API = new RegExp(`(\\/api\\/(?:sd|video)\\/file\\?[^\\s<>"'\`\\uE000\\uE001]*?\\.(?:${IMG_EXT}))`, "gi");
const RE_CHEMIN_3D = new RegExp(`\\b(${DEBUT_CHEMIN}${CAR_CHEMIN}+?\\.(?:3mf|stl))\\b`, "gi");

/** Echappe pour un contenu ou un attribut HTML (apostrophe comprise). */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Inverse de escapeHtml : retrouve le texte brut d'un fragment capture. */
function brut(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

function decoder(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s; // sequence % invalide : on garde le chemin tel quel
  }
}

/** Masters TIFF/EXR : non affichables -> apercu JPEG genere a cote. */
export function srcImage(p: string): string {
  return p.replace(/\.(tiff?|exr)$/i, "_apercu.jpg");
}

/** Repli si l'apercu manque : le master d'origine (sinon l'image elle-meme). */
function srcFallback(p: string): string {
  return /\.(tiff?|exr)$/i.test(p) ? p : srcImage(p);
}

function balisImage(src: string, alt: string, repli?: string, pleine?: string): string {
  const attrRepli = repli && repli !== src ? ` data-fallback="${escapeHtml(repli)}"` : "";
  return `<img class="msg-image" alt="${escapeHtml(alt)}" src="${escapeHtml(src)}" data-full="${escapeHtml(pleine ?? src)}"${attrRepli} decoding="async" loading="lazy" />`;
}

/** Image generee : version ecran (1920 px, reduite proprement) dans le fil,
 *  fichier complet (4K) en plein ecran. Le serveur sert l'original si la
 *  version ecran n'existe pas. */
function imageLocale(chemin: string, alt: string): string {
  const affichee = srcImage(chemin);
  const repli = srcFallback(chemin);
  const pleine = fichierUrl(affichee);
  return balisImage(`${pleine}&taille=ecran`, alt, repli !== affichee ? fichierUrl(repli) : undefined, pleine);
}

/** Modele 3D (3MF/STL) : carte avec apercu PNG + telechargement + slicer. */
function carte3d(p: string): string {
  const stem = p.replace(/\.(3mf|stl)$/i, "");
  const format = /\.3mf$/i.test(p) ? "3MF" : "STL";
  return (
    `<div class="model-3d-card">` +
    balisImage(fichierUrl(`${stem}_apercu.png`), "modele 3D", fichierUrl(`${stem}_iso.png`)) +
    `<div class="model-3d-actions">` +
    `<a class="m3d-btn" href="${escapeHtml(fichierUrl(p))}" download>⬇ Télécharger ${format}</a>` +
    `<button type="button" class="m3d-btn" data-slicer="${escapeHtml(p)}">🖨 Ouvrir dans le slicer</button>` +
    `</div></div>`
  );
}

/** Convertit le texte d'un message en HTML sur (sans JavaScript inline). */
export function renderMarkdown(text: string): string {
  const blocs: string[] = [];
  const garder = (html: string) => `${blocs.push(html) - 1}`;

  const html = escapeHtml(text.replace(/[]/g, ""))
    .replace(RE_LIEN, (_m, label: string, url: string) => {
      const libelle = brut(label);
      const u = brut(url);
      if (RE_URL_IMAGE.test(u)) {
        if (u.startsWith("/api/")) return garder(balisImage(avecToken(u), libelle));
        return garder(imageLocale(decoder(u.replace(/^file:\/\/\/?/i, "")), libelle));
      }
      return garder(
        `<a href="${escapeHtml(u)}" target="_blank" rel="noreferrer">${escapeHtml(libelle)}</a>`
      );
    })
    .replace(RE_BACKTICK_CHEMIN, "$1")
    .replace(RE_BACKTICK_API, "$1")
    // Chemins d'images locales cites par l'assistant -> apercu inline (avant
    // la regle `code` pour que les chemins entre backticks soient vus).
    .replace(RE_CHEMIN_IMAGE, (_m, p: string) => garder(imageLocale(brut(p), "image")))
    // URLs d'images API nues : corrige aussi les anciens messages stockes.
    .replace(RE_URL_API, (_m, u: string) => garder(balisImage(avecToken(brut(u)), "image")))
    .replace(/\*\*([^*\n]{1,200})\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]{1,200})`/g, "<code>$1</code>")
    .replace(RE_CHEMIN_3D, (_m, p: string) => garder(carte3d(brut(p))));

  return html.replace(/(\d+)/g, (_m, i: string) => blocs[Number(i)] ?? "");
}
