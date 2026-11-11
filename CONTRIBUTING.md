# Contribuer à Néo

Merci de l'intérêt. Ce dépôt est la **source unique** du projet : la machine
de référence est alimentée exclusivement par `deployer.ps1`, d'après
`correspondances.psd1`.

## Signaler un bug

Ouvrir une *issue* avec :

- la version (`GET /api/health` → `version`) ;
- l'OS et la version de Rust / Node / Ollama ;
- la commande ou la requête exacte ;
- la sortie observée et la sortie attendue.

Les journaux utiles sont `E:\ia\logs\server_rs.log` (serveur),
`watch_rs.log` (superviseur) et `services_etat.json` (état des services).
**Ne jamais joindre `data/localai.db`** : il contient des
jetons en clair.

## Proposer une modification

1. Créer une branche : `git switch -c fix/description-courte`.
2. Modifier les sources **dans ce dépôt** (`code/`), jamais dans
   `E:\localai-rs` / `E:\ia`. Un nouveau fichier destiné à la production
   s'ajoute aussi dans `correspondances.psd1`.
3. Vérifier localement :

   ```powershell
   cd code/localai-rs
   cargo clippy --release --workspace --all-targets
   cargo test --release --workspace

   cd ../frontend
   npm ci
   npx tsc -b
   npx oxlint
   ```

4. Commiter en [Conventional Commits](https://www.conventionalcommits.org/) :
   `fix(memoire): ...`, `feat(v1): ...`, `docs: ...`.
5. Ouvrir une *pull request* en décrivant le **pourquoi**, pas seulement le
   **quoi**.

## Règles du projet

- **Pas de cloud par défaut.** Toute dépendance réseau doit rester
  optionnelle et documentée ; le mode local doit fonctionner hors ligne.
- **Pas de télémétrie.** Aucune donnée ne quitte la machine sans action
  explicite de l'utilisateur.
- **Repli explicite.** Un maillon externe absent (Ollama, rerank, modèle) doit
  produire un log et un comportement dégradé clair, jamais un échec muet.
- **Tests d'intégration** : gardés par `NEO_LIVE_TESTS=1` pour que la CI reste
  verte sans GPU.
- **Code original.** Les contributions doivent être écrites pour ce projet ;
  l'inspiration conceptuelle est acceptée, la copie structurelle non.

## Licence

En contribuant, vous acceptez que votre contribution soit publiée sous
licence MIT (voir `LICENSE`).
