# Architecture technique — LocalAI

> Source de vérité condensée. Détails d'exploitation : `docs/SECURITE.md`,
> `docs/SKILLS.md`, `docs/DEPLOIEMENT.md`.

## 1. Vue d'ensemble

- **Serveur Rust** `localai-server` (axum) sur `:8320` — API REST + SSE,
  frontend servi en no-store, ~8 Mo RAM, ~0,46 ms p50, ~3 680 req/s.
- **Pont Python** `pybridge.rs` : le venv `E:\ia\.venv` est appelé en
  sous-processus par appel (`python -c`, cwd racine, timeout 1800 s,
  `kill_on_drop`). Aucun listener Python permanent.
- **Ollama** `:11434` — `OLLAMA_MODELS`, `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0`. Champion : `localai-prog-latest`
  (Qwen3-8B LoRA R16, Q8, num_ctx 8192, ~30 t/s).
- **Frontend** React 19 + Vite (thème Matrix : pluie canvas, anneau 3D de
  dossiers, HUD live) → `dist/` servi par le Rust.

## 2. Crates Rust

| Crate | Rôle |
|---|---|
| `localai-core` | config (LOCALAI_ROOT), SQLite WAL (db.rs : conversations, messages, tool_traces, memories, feedback/auto_evals/ameliorations), client Ollama |
| `localai-server` | **le serveur** : routes (chat, outil, feedback, conversations, models, documents+RAG, memory, settings, sd, studio, style_collab, extras, system, benchmark, finetune, voice, tts, comms, security, health), tools.rs (boucle 14 rounds, précheck sandbox), pybridge.rs, telegram.rs |
| `localai-pipeline` | CLI cycles : status / bench / promote |
| `localai-watch` | watchdog SYSTEM : santé 8320/11434, SMART, GPU > 85 °C, alertes |

## 3. Boucle chat (`routes/chat.rs`)

`chat.rs` (≈750 lignes) ne garde que le routeur et l'orchestration ; les
responsabilités annexes vivent dans des sous-modules :

| Fichier | Rôle |
|---|---|
| `routes/chat/prompt.rs` | estimation de tokens, format des messages Ollama, modèles vision |
| `routes/chat/images.rs` | extraction et vérification des chemins d'images (garde-fou anti-hallucination) |
| `routes/chat/stream.rs` | réponses locales et cloud en SSE, bascule quota → local |
| `routes/chat/vision.rs` | sanitisation base64, limite de corps, prompt vision |

1. Persistance conversation + historique (40 derniers).
2. **Routage** : agent explicite, sinon `route_agent` (Python) décide, seul — voir
   « Architecture des agents » ci-dessous.
3. Images jointes → `input/` (base64 ou data URL, détection magic bytes) ;
   intention d'édition → agent image (`edit_image`).
4. Prompt système combiné : agent + RAG (`modele_plongement` + cosinus + tri bge) + mémoires
   utilisateur (20 dernières, tags techniques exclus) + **règles
   d'auto-amélioration (5)** + dossier sandbox + images.
5. Image : l'agent appelle `generate_image` avec la demande telle quelle ; la recherche de
   référence (`find_reference_image`) est réservée aux sujets réels nommés.
6. Boucle outils : exécution via pont, détection de refus, traces
   (`tool_traces` status ok/denied/compact), troncature 4 000 car, compactage
   au-delà de 80 % de num_ctx.
7. Fin : persistance, événement `done`, **auto-évaluation non bloquante**
   (juge qwen3:8b → `auto_evals` → alimente le coach).

### Architecture des agents (revue du 11/11/2026)

Trois questions, trois endroits, sans doublon :

| Question | Où | Fichier |
|---|---|---|
| **Qui répond ?** | le routeur, seul | `backend/core/router.py` (`route_agent`) |
| **Avec quelle consigne et quels outils ?** | la table des agents | `backend/core/agents.py` + `backend/core/consignes/<id>.md` |
| **Que fait un outil ?** | le registre | `backend/tools/registry.py` (`execute_tool`) |

Le serveur Rust ne décide rien de tout cela : il relit la table à chaque message
(`tools.rs` : `agent_system`, `agent_tools`, `route_agent_full`), conduit la boucle d'outils et
ajoute l'identité de Néo. Il ne garde que deux corrections locales : la suite d'une image
(« plus sombre », `persona::suite_d_image`) et « écris-moi du code » sans fichier, que Néo
traite lui-même.

| Agent | Rôle | Choisi quand |
|---|---|---|
| `general` | Néo lui-même, sans outil ; sa consigne est le persona `config/neo_persona.md` | par défaut |
| `image` | images à créer, retoucher, montrer ; vidéo | verbe de création + sujet visuel, « Une photo de… », « montre-moi… » |
| `web` | actualité et faits récents, avec sources | « cherche sur le web… », marqueur d'actualité (résultat d'un match, cours, dernière version…) |
| `bureau` | fichiers Word, PDF, Excel | verbe de rédaction + format de fichier |
| `systeme` | météo, change, adresses, état du PC | mots-clés dédiés |
| `cad3d` | conception et impression 3D | vocabulaire 3D (STL, OpenSCAD, « imprimer en 3D »…) |
| `docs` | réponse depuis les documents indexés | « dans mes documents… » |
| `dev`, `depot` | code avec fichiers et terminal ; dépôt Git | action de développement ; opérations Git |

Ce que la revue a corrigé :
- **Consignes sorties du code** : chaque agent a son fichier `consignes/<id>.md`, lisible et
  modifiable sans toucher au Python (texte strictement identique à l'ancien, vérifié agent par
  agent). `agents.py` n'est plus qu'une table.
- **Contrôle de cohérence** (`agents.incoherences()`, test `test_agents.py`) : un agent ne cite
  que des outils qu'il possède, et ne possède que des outils qui existent. Il a trouvé l'agent
  `depot`, dont la consigne citait `code_edit` sans l'avoir.
- **Un seul routeur** : le serveur avait son propre repli 3D par sous-chaîne, qui contredisait
  le routeur (« mon imprimante ne marche plus », « un film en 3D » partaient en conception 3D).
  Supprimé. Côté routeur, « une tranche de pain perdu » n'est plus de la 3D.
- **Agent `web`** : la recherche web était logée dans l'agent image. Elle a son agent, et les
  questions d'actualité y vont d'elles-mêmes (avant, Néo répondait de mémoire).
- **Agent `bureau` enfin choisi** : il existait mais aucune règle n'y menait (« fais-moi un CV
  au format Word » partait à l'agent image).
- **L'intention reconnue est transmise à l'agent** (`persona::consigne_intention`) : le routeur
  savait que « Une photo d'un boulanger devant son four » était une image à créer, mais
  l'agent image l'ignorait et cherchait d'abord une photo existante.
- **Agent `bureau` : chemin de fichier** : le modèle inventait un chemin (`/home/user/…`),
  l'outil refusait, six appels de suite puis un fichier annoncé qui n'existait pas. Un chemin
  absent ou inutilisable mène maintenant à `workspace\documents` (`t_docs._chemin_document`).
- **Rendu des documents** (`backend/tools/t_docs.py`) : le premier PDF produit en production
  montrait des astérisques à la place du gras, le bloc d'adresse sur une seule ligne et un nom
  de fichier en guise de titre. Le convertisseur rend maintenant le gras, l'italique, les
  retours à la ligne forcés et les tableaux (PDF, Word), n'écrit plus le titre deux fois, et
  met de vrais nombres dans les cellules d'un tableur (« 2 400,50 € » devient 2400,5).
  Tests : `tests_unit/test_documents_rendu.py`.
- **Code mort retiré** : `router.wants_web`, opérations `agent_info` et `route` du worker (le
  serveur ne les appelait plus), ancienne consigne de `general` jamais envoyée.

## 4. Pipeline images (`sdgen.py`)

- **Sélection modèle** : flux Q4/Q5 prioritaire (VRAM), kontext jamais par
  défaut (exige une référence), turbo ≤ 12 steps.
- **Sans référence** : FLUX schnell 4 steps, base 16:9 1344×768.
- **Avec référence (sujet réel)** : FLUX Kontext Q6_K en **2 passes**
  (1 : « the same subject as in the reference… » ; 2 : « keep the subject
  exactly identical… ») — euler, cfg 1.0, guidance 3.5, base 1280×720.
- **4K** : Real-ESRGAN x4plus → 3840×2160 (repli Lanczos).
- **QC vision** : note /10 + FIDELE/PAS FIDELE par variante ; régénération
  (graine aléatoire) si PAS FIDELE ; « MEILLEURE IMAGE » en tête.
- **Préchauffage cache** : lecture séquentielle des modèles (~175 Mo/s) avant
  le lancement de sd-cli — les mmap à froid sur SSD QLC chutent à ~20 Mo/s.
- **Verrou GPU global** (msvcrt/fcntl) : une génération à la fois.
- **`_liberer_vram()`** : décharge les modèles Ollama avant SD/vision.

## 5. API jobs (tâches longues)

- `routes/outil.rs` : **jobs asynchrones** — `POST /api/outil/execute`
  `{"tool","args"}` → `{"job"}` ; `GET /api/outil/job/:id` (+ `?attendre=1`
  long-poll 880 s, refais un GET après).
- Le pont Python (`pybridge`) exécute les outils dans des sous-processus avec
  timeout cohérent (1800 s pour les chaînes longues).

## 6. Base de données (SQLite WAL)

`conversations`, `messages`, `memories`, `documents`, `document_chunks`,
`learned_knowledge`, `prompts`, `tool_traces`, `telegram_sessions`,
`app_settings`, `feedback`, `auto_evals`, `ameliorations`.

### Mémoire : deux natures de lignes

La colonne `memories.tag` sépare deux usages qui étaient confondus :

| Tag | Injecté dans le prompt de chat ? | Usage |
|---|---|---|
| `user`, `fait`, `preference`, `projet` | **oui** (20 dernières) | Ce que l'utilisateur partage : préférences, faits, projets |
| `outil`, `agent`, `debug`, `trace` | **non** | Notes techniques (échecs d'implémentation, traces) |

Le filtre est appliqué à deux endroits qui doivent rester cohérents :
`routes/chat.rs` (lecture pour le prompt) et `routes/memory.rs` (API).
`POST /api/memory` normalise le tag, tronque à 500 caractères et refuse les
doublons exacts ; `GET /api/memory/stats` expose la répartition et lève
`alerte` quand la mémoire utilisateur est vide alors que des notes
techniques existent.

> **Pourquoi c'est structurant** : l'agent `dev` écrivait ses constats
> d'échec avec le tag `outil`, mais le chat lisait *toutes* les lignes.
> L'assistant présentait donc des erreurs d'outillage comme des faits sur
> l'utilisateur. L'agent `dev` n'a plus l'outil `memoire_ajouter` : seuls
> les agents conversationnels écrivent de la mémoire utilisateur.

## 7. Auto-amélioration

- **Feedback utilisateur** : `POST /api/feedback` (rating 1-5).
- **Auto-évaluation** : après chaque réponse à outils, un juge léger note /10
  (non bloquant).
- **Coach hebdomadaire** (`coach.py`, tâche planifiée dimanche 05:00 ou
  `POST /api/selfimprove/coach`) : analyse signaux faibles 7 jours → 1-5
  règles (qwen3:14b, dédoublonnage) → `ameliorations` → **injectées dans le
  prompt système** (LIMIT 5).
- **Cycles fine-tune** : corpus (SFT+RLVR, anti-fuite MinHash) → LoRA
  (unsloth) → merge GGUF → bench holdout figé (100 prompts, double juge,
  Wilson 95 %) → promotion conditionnelle (`promote.py`, 10 conditions).

## 8. Performance — principes

- Tout ce qui est chaud en Rust (HTTP, boucle, gardes), lourd en Python
  (sous-processus), inférence en Ollama.
- Timeouts cohérents de bout en bout : client 900 s ≥ long-poll serveur
  880 s ≥ pont 1800 s (jobs) — aucun maillon plus court que la chaîne.
- Préchauffage cache avant tout chargement SD ; réutilisation des modèles
  Ollama déjà chargés (traduction sur le champion si présent).
- Verrou GPU : évite les OOM concurrents ; file d'attente implicite.

## 9. Premiere installation (onboarding)

- **Route `GET /api/onboarding`** (Rust) : renvoie la langue choisie, l'etat
  d'installation et, via le pont Python (`backend.core.hardware`), le
  materiel detecte et le plan de modeles adapte.
- **Paliers** : `T1` (<= 8 Go VRAM / CPU), `T2` (10-20 Go), `T3` (>= 22 Go).
  Chaque palier definit un modele par role (agent_brain, coder, vision,
  teacher, student, embed) ; le premier modele installe gagne.
- **Assistant frontend** (`OnboardingWizard.tsx`) : langue -> detection
  materielle -> modeles recommandes (installation SSE via `/api/models/install`)
  -> fin. L'etat est persiste (`language`, `onboarded`).
- **Langue des reponses** : le prompt systeme recoit une directive
  (« Reponds toujours en francais », « Always answer in English », etc.)
  construite depuis le reglage `language` — sans toucher aux prompts des agents.

## 10. Passerelle OpenAI (`routes/openai.rs`)

Un module séparé expose le protocole OpenAI sans réutiliser le pipeline
`/api/chat` :

| Route | Traduction interne |
|---|---|
| `GET /v1/models` | `Ollama::tags()` → `{object: "list", data: [...]}` |
| `GET /v1/models/:id` | vérification d'existence, `404` sinon |
| `POST /v1/chat/completions` | corps OpenAI → corps Ollama `/api/chat` |
| `POST /v1/completions` | prompt texte → un seul message `user` |

**Choix assumés :**

- **Pas de RAG, pas d'outils, pas d'agents.** Un client OpenAI (Open WebUI,
  LibreChat, Cursor…) gère son propre contexte et attend une réponse de chat
  pure. Le pipeline complet reste sur `POST /api/chat`.
- **Streaming** : Ollama renvoie du NDJSON, le module le retraduit en SSE
  OpenAI (`data: {...}` puis `data: [DONE]`). Le premier delta porte
  `role: "assistant"`, les suivants seulement `content`.
- **Paramètres** : `max_tokens` et `max_completion_tokens` sont acceptés,
  `stop` peut être une chaîne ou un tableau, `temperature`/`top_p` sont
  bornés, `num_ctx` reste aligné sur le serveur (8192).
- **Authentification** : `/v1/*` est soumis au même middleware Bearer que
  `/api/*` ; les appels loopback passent toujours.
- **`/v1/completions`** répond en une fois même si `stream: true` (API
  legacy, conservée pour compatibilité).

## 11. Serveur MCP (`routes/mcp.rs`)

JSON-RPC 2.0 sur HTTP, sans session ni streaming :

| Méthode | Traitement |
|---|---|
| `initialize` | négociation de version (2025-06-18 / 2025-03-26 / 2024-11-05) |
| `tools/list` | `tools::specs_tous()` → pont Python `TOOL_DEFINITIONS` (58 outils) |
| `tools/call` | `tools::execute()` : précontrôle Rust, permissions, pont Python |
| `resources/list`, `prompts/list` | listes vides |
| notification (sans `id`) | `202 Accepted`, pas de corps |

La conversion Ollama → MCP est triviale : `{"type":"function","function":
{name, description, parameters}}` devient `{name, description, inputSchema}`.
`isError` est déduit des marqueurs de refus (`PERMISSION REFUSEE`,
`ACCES REFUSE`, `COMMANDE BLOQUEE`, `Erreur…`).

`/mcp` est protégé par le middleware Bearer et compté dans la limitation de
débit. **Limite assumée** : c'est un serveur, pas un client — Néo ne consomme
pas encore les outils d'un serveur MCP tiers.

## 12. Observabilité et limitation de débit

`src/metrics.rs` tient des compteurs atomiques globaux (`OnceLock`) :

| Compteur | Incrémenté par |
|---|---|
| `rag_requetes` / `rag_vides` / `rag_echecs` | `documents::rag_block_detaille` |
| `embed_echecs` | échec de `Ollama::embed` dans le RAG |
| `rerank_ok` / `rerank_repli` | `rag_boost::rerank` |
| `ollama_echecs` | échec de génération (chat, tools, boost) |
| `rate_limited` | middleware de débit |

`/api/health` garde `status: "ok"` (le watchdog ne teste que le HTTP) et
expose `degraded` + `metrics`. `degraded` devient vrai dès qu'un embedding,
un RAG ou une génération a échoué depuis le démarrage.

`src/rate_limit.rs` : fenêtre glissante de 60 s par IP, `Mutex<HashMap<...>>`,
purge opportuniste au-delà de 1024 IP. Seules les routes GPU sont comptées
(`/api/chat`, `/api/outil/execute`, `/v1/chat/completions`,
`/v1/completions`, `/mcp`). La limite vient de `rate_limit_par_minute`
(défaut 300, `0` = désactivé), lue au démarrage.

## 13. Tests

| Niveau | Commande | Portée |
|---|---|---|
| Unitaire | `cargo test --release -p localai-server` | `chunk_text`, `cosine`, `pack/unpack`, RRF, `rate_limit`, `mcp`, échappement HTML, raisonnement |
| Intégration | `NEO_LIVE_TESTS=1 cargo test --release -p localai-server documents::` | `embed()` réel + `rag_block()` bout en bout |
| Frontend | `npx tsc -b` / `npx oxlint` | typage et lint |

Les tests d'intégration sont **désactivés par défaut** pour que la CI et les
machines sans GPU restent vertes ; ils exigent Ollama et
`nomic-embed-text`. Le test `rag_block` pointe volontairement `rerank_url`
vers un port mort : il vérifie que le repli RRF conserve le document — la
panne exacte qui avait rendu le RAG muet en production.
