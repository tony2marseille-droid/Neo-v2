import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { appliquerSkin, lireSkin } from "./lib/skins";
import "./theme.css";
import "./index.css";
import "./shell.css";

// Skin pose AVANT le premier rendu : aucun flash du theme par defaut.
appliquerSkin(lireSkin());

// Acces web (pseudo + mot de passe) : le navigateur presente le token
// obtenu au login sur tous les appels /api/*. Une reponse 401 declenche
// l'evenement "neo-401" -> App affiche la porte de login. Le jeton part
// aussi vers le service video :8321 (meme jeton exige depuis le LAN), mais
// seul un 401 du serveur principal (meme origine) deconnecte.
const fetchOriginel = window.fetch.bind(window);
window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const memeOrigine = new URL(url, window.location.href).origin === window.location.origin;
  const token = localStorage.getItem("neo.token");
  let init2 = init;
  if (url.includes("/api/") && token) {
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined)
    );
    if (!headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    init2 = { ...(init ?? {}), headers };
  }
  return fetchOriginel(input, init2).then((rep) => {
    if (rep.status === 401 && memeOrigine && url.includes("/api/")) {
      window.dispatchEvent(new CustomEvent("neo-401"));
    }
    return rep;
  });
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
