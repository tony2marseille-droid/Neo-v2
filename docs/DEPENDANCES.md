# Dépendances tierces & licences — Néo

> Ce document recense ce que le projet **utilise**. Le code de Néo lui-même
> est 100 % original et publié sous **licence MIT** (fichier `LICENSE` à la
> racine). Les éléments ci-dessous conservent leurs licences respectives :
> la licence MIT de Néo ne s'étend ni aux bibliothèques, ni aux outils, ni
> aux modèles, qu'il faut respecter chacun selon ses propres conditions.

## 1. Notre code : 100 % original

Vérifié par balayage automatique (2026-10-20) :
- **aucun** en-tête copyright tiers dans `backend/`, `frontend/`, `localai-rs/` ;
- **aucun** marqueur « proprietary / confidential / all rights reserved » ;
- aucun code vendored/copié d'un autre projet.

## 2. Bibliothèques Python (179 paquets, venv)

| Licence | Part | Exemples |
|---|---|---|
| MIT / BSD / Apache-2.0 / PSF | ~95 % | torch, transformers, diffusers, trimesh, rembg, faster-whisper, httpx, gradio, unsloth, reportlab, openpyxl, python-docx, python-pptx, pypdf |
| MPL-2.0 (copyleft par fichier) | 3 | pyttsx3, orjson, tqdm, certifi |
| LGPL (copyleft faible, bibliothèque isolée) | 2 | pycountry (LGPL-2.1), unsloth_zoo (LGPL-3) |
| GPL-3 (copyleft fort, bibliothèque) | 2 | pymeshlab (traitement de mesh 3D), piper-tts (voix neuronale de Néo, chargée seulement à la synthèse) |
| Runtime Microsoft | 1 | msvc_runtime (DLL VC++ redistribuables Microsoft — gratuit, standard Windows, pas du code du projet) |

**Ce que ça implique** : en usage privé, aucune contrainte. Si le projet est
un jour **distribué compilé**, il faudrait écarter les copyleft — pymeshlab
est remplaçable (trimesh + fast-simplification, déjà présents, font le même
travail pour nos usages) ; piper-tts aussi (la voix retombe sur SAPI si le
paquet ou le modèle manque) ; les LGPL se satisfont d'un avertissement.

## 3. Crates Rust (201)

Tout l'écosystème standard **MIT / Apache-2.0** (axum, tokio, serde, reqwest,
hyper, rusqlite, chrono, clap…). Aucun copyleft. Aucune obligation sur notre
code, même en distribution.

## 4. Outils externes (processus séparés)

Appelés comme des **exécutables indépendants** — aucune contamination de
licence, même en distribution binaire :

| Outil | Licence | Usage |
|---|---|---|
| Ollama | MIT | serveur LLM |
| stable-diffusion.cpp | MIT | génération d'images |
| llama.cpp | MIT | conversion GGUF |
| Real-ESRGAN (ncnn) | BSD-3 | upscale 4K |
| TripoSR | MIT | image → mesh 3D |
| faster-whisper | MIT | transcription (secours) et décodage audio |
| onnx-asr | MIT | moteur de Parakeet (ONNX Runtime) |
| openWakeWord | Apache-2.0 | mot d'éveil |
| ffmpeg | GPL/LGPL | montage vidéo (processus séparé) |
| Blender | GPL | rendus 3D (processus séparé) |
| OpenSCAD | GPL-3 | CAO paramétrique (processus séparé) |
| PrusaSlicer | AGPL-3 | tranchage G-code (processus séparé) |

## 5. Modèles (poids) — les vraies conditions à connaître

| Modèle | Licence | Usage personnel | Usage commercial |
|---|---|---|---|
| Qwen3-8B (base du fine-tune), qwen3:8b/14b | Apache-2.0 | ✓ | ✓ |
| Qwen2.5-VL (vision) | Apache-2.0 | ✓ | ✓ |
| FLUX.1-schnell (images rapides) | Apache-2.0 | ✓ | ✓ |
| Z-Image-Turbo (images, modèle principal) + Qwen3-4B (son encodeur) | Apache-2.0 | ✓ | ✓ |
| DeepSeek-R1 | MIT | ✓ | ✓ |
| nomic-embed-text (RAG, index d'origine) | Apache-2.0 | ✓ | ✓ |
| Qwen3-Embedding 4B (RAG) | Apache-2.0 | ✓ | ✓ |
| Parakeet-TDT-0.6B-v3 (dictée, NVIDIA) | CC-BY-4.0 (attribution) | ✓ | ✓ |
| bge-reranker-v2-m3 (tri RAG) | Apache-2.0 | ✓ | ✓ |
| Whisper small (STT) | MIT | ✓ | ✓ |
| **Voix Piper fr_FR-tom-medium (voix de Néo)** | **jeu de données AGPL-3** | ✓ | conditions à vérifier |
| FLUX.2-klein-4B (retouche d'image) | Apache-2.0 | ✓ | ✓ |
| Kyutai Pocket TTS (essai de voix, non adopté à ce jour) | CC-BY-4.0 | ✓ | ✓ |
| **FLUX.1 Kontext dev (repli de retouche)** | **non-commerciale (BFL)** | ✓ | ✗ licence requise |
| **Hunyuan3D-2.1 / hy3dgen (3D organique)** | Tencent Hunyuan Community | à vérifier selon le pays | conditions à vérifier |
| Wan 2.2 TI2V-5B (vidéo) + umT5-XXL (son encodeur) | annoncée Apache-2.0 — à confirmer sur la page du modèle | ✓ | à confirmer |
| **Gemma 4 12B (cerveau conversationnel de Néo)**, gpt-oss 20B, Qwen3.5 9B | Apache-2.0 | ✓ | ✓ |
| gemma3 | Gemma Terms of Use | ✓ | conditions à vérifier |
| llama3.1 | Llama Community License | ✓ | conditions à vérifier |

**En pratique** : en usage 100 % privé et local (le cas aujourd'hui), tout
est conforme. Si le projet devient un **produit commercial**, deux modèles
exigeront une licence payante ou un remplacement : FLUX.1 Kontext dev (le
cœur de la fidélité photo — remplaçable par schnell ou un modèle Apache) et
Hunyuan3D-2.1 (remplaçable par TripoSR, MIT).

**Hunyuan3D, point d'attention** : la licence Tencent Hunyuan Community
comporte des restrictions d'usage et de **territoire**. Ce dépôt ne contient
ni ses poids ni son code ; avant de les télécharger, lire le texte de la
licence sur la page du modèle, en particulier depuis l'Union européenne. Sans
Hunyuan3D, Néo se replie sur TripoSR (MIT) pour les formes organiques, et les
gabarits paramétriques n'en dépendent pas.

**Bibliothèques Python** : la liste figée de l'installation de référence est
dans [`code/requirements.txt`](../code/requirements.txt).

## 6. Services en ligne gratuits (sans clé, appelés seulement à la demande)

| Service | Usage dans Néo | Ce qui sort de la machine | Limites |
|---|---|---|---|
| Frankfurter (taux BCE) | outil `taux_change` | montant n'est pas envoyé : seulement les deux devises | aucune annoncée |
| Géoplateforme IGN (`data.geopf.fr/geocodage`) | outil `adresse` | le texte de l'adresse ou du lieu cherché | 50 requêtes/s |
| Jina Reader (`r.jina.ai`) | lecture de secours de `web_fetch` / `web_browse` | l'URL de la page (publique) | 20 requêtes/min ; `NEO_LECTEUR_EXTERNE=0` le coupe |
| Open-Meteo | outil `meteo` | le nom de la ville | usage non commercial |
| Wikipédia, DuckDuckGo | `wikipedia`, `web_search` | la requête | — |

Jina Reader n'intervient que si la page **s'est ouverte normalement** mais que son texte est
presque vide (page construite en JavaScript). Un site qui refuse les robots (403, 429, page de
vérification anti-robot) n'est jamais contourné : son refus est rapporté tel quel.
