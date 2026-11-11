# Installation sur un PC plus puissant

LocalAI detecte automatiquement le materiel (VRAM GPU) et choisit ses modeles
par role. Aucune configuration manuelle n'est necessaire.

## 1. Prerequis

- Ollama installe et lance (`ollama --version`)
- Drivers NVIDIA a jour
- Python 3.11+ si mode developpeur, sinon utiliser l'exe fourni

## 2. Installer les modeles recommandes

```powershell
cd scripts
.\install_models.ps1          # palier auto-detecte depuis nvidia-smi
.\install_models.ps1 -Tier T3 # forcer le palier (24 Go+ recommande)
```

## 3. Verifier la detection

Demarrer LocalAI puis :

```
GET http://127.0.0.1:8320/api/system/hardware
```

Reponse attendue : tier T1/T2/T3 + plan resolu par role :

| Role | T1 (≤8 Go) | T2 (10-20 Go) | T3 (≥22 Go) |
|---|---|---|---|
| agent_brain | qwen3:8b | qwen3:14b | qwen3:30b |
| coder | qwen2.5-coder:7b | qwen2.5-coder:14b | qwen2.5-coder:32b |
| vision | qwen2.5vl:7b | qwen2.5vl:7b | qwen2.5vl:14b |
| teacher (distillation) | qwen3:14b | qwen3:14b | qwen3:30b |
| student | gemma3:4b | qwen3:8b | qwen3:8b |

## 4. Generation organique 3D (optionnel)

Copier le dossier `D:\tools3d` (TripoSR + venv + cache HF ~4 Go) depuis
l'ancienne machine, meme emplacement. Sinon l'agent retombe proprement sur
la modelisation parametrique.

## 5. Activer les fonctions avancées

Mode reflexion (auto-relecture des reponses, questions longues uniquement) :

```
PUT http://127.0.0.1:8320/api/settings
{"key": "reflection_mode", "value": "on"}
```

## 6. Boucle de distillation

1. Utiliser l'assistant normalement (les traces d'outils sont enregistrees)
2. `POST /api/distill/upgrade` : le modele TEACHER re-travaille les reponses faibles
3. Exporter les datasets pour fine-tuning LoRA/DPO offline :
   - `/api/distill/export` (SFT conversations)
   - `/api/distill/export-traces` (comportement agent)
   - `/api/distill/export-dpo` (paires choisi/rejete)

Sur une machine T3 avec 24 Go+, le fine-tuning LoRA de qwen3:8b devient
realisable directement (unsloth ou axolotl, QLoRA 4-bit).
