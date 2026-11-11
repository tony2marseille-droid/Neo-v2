# Sécurité — LocalAI

## 1. Sandbox double couche (fichiers & commandes)

1. **Précontrôle Rust** (`tools.rs::precheck`) AVANT le pont :
   - refus de `..` (traversée), chemins absolus hors racines autorisées ;
   - `run_command` : liste blanche d'exécutables (python, node, git,
     openscad, dir, type, findstr), refus des caractères `| & ; > < \``
     et de la substitution `$()` ;
   - racines : écriture = `E:\ia\workspace` ; lecture = workspace +
     `E:\ia\data\documents` (configurables dans `data/security_rules.json`).
2. **Sandbox Python** (`t_files.py`, `common.py`) : résolution anti-junction,
   extensions interdites, exécution sans shell, timeout 60 s, sortie tronquée
   à 20 000 caractères, journal des refus.
3. Détection des refus côté chat (`refusé…`, `ACCES REFUSE`,
   `COMMANDE BLOQUEE`) → `tool_traces.status='denied'` + alerte watchdog
   si ≥ 5 refus/10 min (Telegram).
4. Tests : `tests/test_sandbox.py` (17 cas), `test_safe_code_exec.py` (6).

## 2. Permissions d'outils

Chaque outil déclare un niveau : `SAFE` < `USER` < `POWER` < `ADMIN`.
Le niveau courant vient du réglage `tool_level` (défaut `USER`). Un appel
sous le niveau requis est refusé AVANT exécution (message
`PERMISSION REFUSEE`).

## 3. Verrou GPU global

Fichier lock inter-processus (`msvcrt`/`fcntl`) : **une seule génération SD
à la fois** — évite les OOM (les modèles FLUX chargent ~10 Go). File
d'attente implicite, timeout d'attente 1 h.

## 4. Réseau et authentification

- Serveur Rust : écoute locale/LAN (`0.0.0.0:8320`) — pare-feu Windows avec
  règle dédiée ; aucun service n'expose le système de fichiers au-delà des
  racines sandbox.
- `/api/outil/execute` exécute des outils au niveau configuré — même surface
  que `/api/chat`.

### Jeton Bearer (`api_token`)

Le middleware `main.rs::auth_middleware` protège **`/api/*`, `/v1/*` et
`/mcp`**. Trois routes restent publiques : `/api/health`, `/api/auth/status`,
`/api/auth/login`. Les requêtes **loopback** (`127.0.0.1`, `::1`) passent
toujours : l'UI servie sur le poste n'a pas à se connecter.

Le jeton est relu **en base à chaque requête** : l'activer ou le changer ne
demande aucun redémarrage.

**Mise en service** (une fois) :

```powershell
curl.exe -s -X POST http://127.0.0.1:8320/api/auth/setup `
  -H "Content-Type: application/json" `
  -d '{\"user\":\"Camille\",\"pass\":\"...\"}'
```

La réponse contient `{"ok":true,"token":"<64 hex>"}`. Le mot de passe est
stocké en `sha256$sel$hash` ; le jeton en clair dans `app_settings`
(`api_token`) — c'est un secret de machine, pas un mot de passe.

**Changement** : renvoyer `setup` avec `old_pass` (l'ancien mot de passe).
Sans `old_pass` valide, la réponse est `{"ok":false}`.

**Côté client** : `POST /api/auth/login` avec `{"user","pass"}` renvoie le
jeton ; l'UI le place en `Authorization: Bearer`. Pour un client externe
(Open WebUI, Cursor…) : même en-tête sur chaque appel.

**Vérification** :

```powershell
# Doit répondre 401 depuis une autre machine
curl.exe -s -o NUL -w "%{http_code}" http://<adresse-de-neo>:8320/api/memory/stats
# Doit répondre 200 avec le jeton
curl.exe -s -o NUL -w "%{http_code}" -H "Authorization: Bearer <jeton>" `
  http://<adresse-de-neo>:8320/api/memory/stats
```

> **Limite connue** : si un reverse-proxy local (ex. `tailscale serve`)
> publiait le port, la requête arriverait depuis `127.0.0.1` et le bypass
> loopback s'appliquerait. Ne pas exposer Néo derrière un proxy local sans
> traiter `X-Forwarded-For`.

### Limitation de débit

Fenêtre glissante de 60 s par IP (`src/rate_limit.rs`), réglable via
`rate_limit_par_minute` (défaut 300, `0` = désactivé). Routes concernées :
`/api/chat`, `/api/outil/execute`, `/v1/chat/completions`, `/v1/completions`
et `/mcp`. Réponse au-delà du plafond :

```
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

Les lectures ne sont pas limitées. Le réglage est lu au démarrage.

> **Attention `/mcp`** : le serveur MCP expose les 58 outils, y compris
> `run_command` et `write_file`, filtrés par le niveau `tool_level` et la
> sandbox. Ne l'ouvrez pas au-delà du tailnet sans jeton.

## 5. Secrets

- Aucune clé cloud requise (météo Open-Meteo sans clé, DuckDuckGo public).
- Les tokens (Telegram/LiveKit) vivent en base (`app_settings`), jamais dans
  le code. Ne jamais versionner `data/localai.db`.
- Elévation : `LocalAccountTokenFilterPolicy=1` est requis pour
  l'administration SSH — à réserver à un LAN de confiance.

### Clés d'API : ajout et suppression (11/11/2026)

Toutes les clés sont chiffrées au repos (ChaCha20, `.neo_keystore`) et ne sortent jamais du
serveur (lecture masquée). Quatre familles, toutes gérées dans Réglages :

| Famille | Clés | Ajouter | Supprimer |
|---|---|---|---|
| Réglages | `api_key_elevenlabs`, `api_key_deepl`, `api_key_unsplash`, LiveKit | `PUT /api/settings` | `DELETE /api/settings/:key` |
| Connexions | Meshy (3D), Telegram, Discord | `PUT /api/connecteurs` | `DELETE /api/connecteurs/:service` |
| Fournisseurs cloud | Anthropic, OpenAI, Gemini, Mistral, Groq, OpenRouter | `POST /api/fournisseurs` | `DELETE /api/fournisseurs/:id` |
| API publiques | `pubapi_key.<service>` | `PUT /api/public-apis/keys/:slug` | `DELETE /api/public-apis/keys/:slug` |

- **Supprimer** retire la ligne de la base (`Db::delete_setting`) : il ne reste aucune trace de la
  valeur, même chiffrée. Avant, seules les API publiques avaient un bouton ; ailleurs il fallait
  vider un champ, et les connexions comme les fournisseurs refusaient un champ vide : leurs clés
  ne pouvaient pas être retirées. Chaque suppression est confirmée dans l'interface.
- `DELETE /api/settings/:key` n'accepte que les secrets de la liste blanche : ni un réglage
  ordinaire, ni `api_token` / `webui_pass` (l'accès à Néo lui-même).
- Supprimer la clé Telegram coupe aussi le bot (`telegram_enabled` = 0).
- **Clé Meshy (3D en ligne)** : elle se saisissait déjà, mais l'outil la lisait directement dans la
  table, donc chiffrée : Meshy la refusait, le moteur n'a jamais pu servir. Elle est maintenant
  lue par `get_setting`. Le moteur se choisit dans le chat (« avec Meshy », paramètre `moteur` de
  `generate_mesh_from_image`) ; sans cette demande la 3D reste locale, et une clé absente est
  signalée avant tout calcul.

## 6. Défender / AV

- Exclusions recommandées (les compilateurs et loaders ML génèrent des faux
  positifs massifs) : `E:\ia`, `E:\LocalAI-Modeles`,
  `E:\localai-rs`, `E:\LocalAI-tmp` + processus `sd-cli.exe`,
  `llama-server.exe`, `ollama.exe`.
- Sans exclusions, les chargements de modèles peuvent chuter à ~20 Mo/s
  (scanning temps réel) — le préchauffage cache n'y remédie pas seul.

## 7. Journalisation

- `data/security_log.jsonl` : accès refusés, outils refusés, événements.
- `tool_traces` (SQLite) : chaque appel d'outil (args, aperçu, statut,
  raison) — base de l'auto-amélioration et de l'audit.
