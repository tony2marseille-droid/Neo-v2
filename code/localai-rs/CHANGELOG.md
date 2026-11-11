# Journal des versions — Néo

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
versionné selon le format SemVer. Le numéro du binaire est exposé par
`GET /api/version` (`app`, `version`, `commit`, `build_date`).

## [Non publié]

### Ajouté
- **tool calling local (toolshim)** : proxy d'émulation `127.0.0.1:8330` pour
  les modèles sans capacité `tools` (qwen3, gemma3, localai-prog, qwen2.5vl) —
  prompt JSON contraint, validation des appels, retries guidés, sélection des
  8 outils les plus pertinents. Intégré par la variable `OLLAMA_URL` de
  `run_server_boot.ps1`, sans modification du serveur ni du frontend.
- **agent `depot`** : 5 outils (`file_search`, `git_diff`, `git_rollback`,
  `repo_index`, `run_tests`) et routage automatique `DEPOT_HINTS`, prioritaire
  sur la règle image pour que « affiche le diff git » ne parte pas vers
  l'agent image.
- **mémoire** : `POST /api/memory/extract` (extraction de faits par LLM après
  la réponse) et `PUT /api/memory/:id` avec édition inline dans les réglages.
- **RAG** : recherche hybride BM25 (FTS5) + cosine (alpha 0,6) et seuillage du
  score de rerank (< 0,20 → contexte vide, pas de citation fantôme).
- **providers cloud** : 6 fournisseurs, routage `cloud:<provider>`, sélecteur
  ☁ dans le chat et badge ⚡ Boost ; clés masquées côté API.

### Corrigé
- **outils avec image jointe** : `chat.rs` désactivait tous les outils dès
  qu'une image était jointe (contournement de l'erreur 500 « does not support
  tools » de `qwen2.5vl:7b`). toolshim émulant désormais le tool-calling, le
  bypass est retiré et `toolshim/prompting.py` préserve les images au format
  Ollama. Preuve : image jointe + « génère une image 3d cartoon de cette
  fourmi » → `generate_image` exécuté, `ant_in_south_park.tiff` (13 065 Ko) et
  `ant_in_south_park_v1.tiff` (15 261 Ko) produits.
- **agent depot** : les clés d'outils préfixées `tool_` dans `registry.py`
  produisaient des specs vides → clés sans préfixe.

## [0.4.1] — 2026-10-29

### Sécurité
- **Masquage des secrets** dans `/api/settings` : `api_token`, `webui_pass`,
  `telegram_token`, `livekit_api_secret`, `livekit_api_key`, clés
  `connecteur_*_token`, `provider_*_key` et toute clé dont le nom contient
  `token`/`secret`/`password`/`pass` ne sortent plus jamais du serveur.
  L'écriture ignore la valeur masquée renvoyée par l'UI (pas d'écrasement
  d'un secret par `********` à la sauvegarde du formulaire).
- `api_token` n'est plus modifiable via `PUT /api/settings` (rotation
  uniquement par `/api/auth/setup`, protégée par l'ancien mot de passe).
- **En-têtes de sécurité** sur toutes les réponses : `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`,
  `Permissions-Policy`.

### Ajouté
- `GET /api/version` : identité exacte du binaire (version, commit, date de
  build), pour vérifier qu'un déploiement a bien pris.
- `build.rs` dans `localai-server` : grave le commit (`NEO_GIT_COMMIT`,
  fichier `COMMIT` du workspace, ou `git rev-parse`) et la date de compilation
  (UTC RFC 3339).

### Tests
- Suite serveur portée de 19 à 50 tests : masquage des secrets (9), cycle
  d'authentification (8), mémoire (7), en-têtes de sécurité et routage HTTP
  complet en mémoire (5), compteurs de santé (2), `/api/version` (1).
- Routeur montable en test (`construire_app`) avec état sur base temporaire.

## [0.4.0] — 2026-10-27

- Industrialisation : métriques de dégradation, limitation de débit par IP,
  passerelle MCP, citations RAG, mémoire séparée utilisateur/technique.

## [0.3.0] — 2026-10-20

- RAG boosté : expansion de requêtes, fusion RRF, rerank cross-encoder avec
  repli automatique.

## [0.2.0] — 2026-10-10

- Pont Python chaud, outils, documents (pypdf/docx/openpyxl).

## [0.1.0] — 2026-09-28

- Premier serveur Rust (axum) : santé, chat, conversations, modèles.
