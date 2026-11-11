# Déploiement — LocalAI sur Windows

> Cible : Windows 10/11, GPU NVIDIA ≥ 8 Go, 32 Go RAM conseillés, SSD.

## 1. Prérequis

| Composant | Version | Usage |
|---|---|---|
| [Ollama](https://ollama.com) | ≥ 0.4 | inférence LLM |
| Rust (rustup) | ≥ 1.89 | build serveur |
| Python | 3.12+ | kernels/outils |
| Node.js | ≥ 20 | build frontend |
| Git | — | sources |

Variables d'environnement (HKCU) :
```
LOCALAI_ROOT=E:\ia
OLLAMA_MODELS=E:\LocalAI-Modeles
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

## 2. Modèles (Ollama)

```
ollama pull qwen3:8b            # dispatch rapide
ollama pull qwen2.5vl:7b        # vision
ollama pull nomic-embed-text    # embeddings RAG
# fine-tune maison (optionnel) : importer le GGUF via un Modelfile
```

Modèles SD (stable-diffusion.cpp, dossier `E:\ia\tools\sd\models`) :
`flux1-schnell-Q4_K_S.gguf` (défaut), `t5xxl-q8_0.gguf`, `clip_l.safetensors`,
`ae.safetensors`, `flux1-kontext-dev-Q6_K.gguf` (fidélité sujet).

Outils externes : Real-ESRGAN (ncnn-vulkan), OpenSCAD, PrusaSlicer, Blender,
TripoSR — paths dans `backend/tools/common.py` (surcharges `LOCALAI_*`).

## 3. Build

```bat
cd frontend && npm install && npm run build
cd ..\localai-rs && cargo build --release
target\release\localai-server.exe   → http://localhost:8320
```

## 4. Superviseur unique (production)

Une seule tâche planifiée fait tourner Néo : `LocalAI_watch_rs` (compte SYSTEM,
au démarrage, relancée chaque minute si elle s'arrête). Son binaire,
`localai-watch.exe`, est le **superviseur** :

- il lance et surveille les services décrits dans `E:\ia\config\services.json`
  (versionné : `code/config/services.json`) : serveur :8320, Ollama :11434,
  toolshim :8330, rerank :8322, API vidéo :8321, ComfyUI :8188 (désactivé) ;
- toutes les 15 s, il contrôle la route de santé de chaque service :
  - port fermé → lancement immédiat (sans fenêtre, journal dans `E:\ia\logs\`) ;
  - port ouvert mais aucune réponse pendant 2 min → arrêt forcé de l'arbre de
    processus puis relance (un serveur occupé par une génération répond
    toujours à sa route de santé) ;
  - relances espacées 10 s, 20 s, 40 s… 5 min ; après 5 échecs, état
    `en_echec` et alerte ;
- un service déjà lancé par ailleurs est adopté, pas relancé ; redémarrer le
  superviseur ne coupe donc aucun service ;
- il surveille aussi la machine : disque E: < 30 Go, SMART, GPU > 85 °C, refus
  d'outils en rafale, entraînement figé. Il alerte une seule fois, quand le
  problème apparaît (journal `logs\watch_rs.log`, et Telegram si
  `telegram_alert_chat_id` est réglé).

L'état est écrit dans `logs\services_etat.json`, exposé par
`GET /api/system/services` et affiché dans le panneau Système de l'interface.
Ajouter un service, ou en désactiver un (`"actif": false`), se fait dans
`services.json` : le fichier est relu à chaud, sans redémarrage.

Les anciennes tâches de lancement et de surveillance (`LocalAI_server_boot`,
`LocalAI_ollama_boot`, `Neo_Toolshim`, `NeoVideoAPI`, `Neo_Rerank`,
`Neo_GardeProd`, `Neo_Toolshim_Watchdog`) sont désactivées par
`code/scripts/superviseur_taches.ps1`, que `deployer.ps1` appelle lui-même.
Leur XML est sauvegardé, et `restaurer_taches.ps1` les remet en place.

| Autres tâches | Déclencheur | Action |
|---|---|---|
| `Ollama_superviseur` | ouverture de session | application Ollama (adoptée par le superviseur) |
| `LocalAI_coach` | dimanche 05:00 | cycle d'auto-amélioration |

⚠️ Désactiver l'auto-update d'Ollama OU prévoir le repair (un update raté
peut laisser une install amputée — symptom : 500 « llama-server binary not
found » sur tout chat ; solution : recopier `lib\ollama\*` depuis le zip
officiel).

## 5. Client vocal LAN (mot d'éveil)

`voice/neo_local.py` : wake-word openWakeWord + VAD + STT serveur +
`/api/chat` + TTS SAPI. Modes wake/push/console.

```
pip install openwakeword sounddevice soundfile httpx numpy
python neo_local.py --url http://IP_SERVEUR:8320 --mode wake
```

Le micro et les haut-parleurs restent côté client ; STT/TTS côté serveur.

## 6. Vérification post-install

```bash
curl http://localhost:8320/api/health          # {"status":"ok"}
curl http://localhost:8320/api/agents          # 7 agents
curl http://localhost:11434/api/version        # ollama
# génération test :
curl -X POST http://localhost:8320/api/outil/execute \
  -H "Content-Type: application/json" \
  -d '{"tool":"generate_image","args":{"prompt":"un cube rouge","resolution":"hd"}}'
# puis GET /api/outil/job/<id> jusqu'à fini:true
```

## 7. Premier lancement (Néo)

Au premier demarrage, l'interface ouvre l'assistant de configuration :

1. **Langue** — FR / EN / ES / DE / IT (interface + réponses).
2. **Détection matérielle** — GPU, VRAM, RAM, disque, palier T1/T2/T3.
3. **Modèles recommandés** — installation en un clic (Ollama pull en streaming).
4. **Fin** — Néo est prêt ; tout reste modifiable dans Réglages.

Rien n'est impose : « Passer l'installation guidée » reste disponible.
