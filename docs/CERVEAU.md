# Cerveau de Néo : choix du modèle conversationnel

Duel du 09/11/2026 sur la machine (RTX 5080 16 Go, 48 Go de RAM, Ollama 0.40.15).
Banc : `code/eval/bench_agents.py --niveau tout` (42 questions : raisonnement, code exécuté,
identité, conversation, explication, et 15 questions « expert » ajoutées ce jour-là car le
banc de base était saturé à 98,5/100). Juge : qwen3:14b avec réflexion.

## Résultat : `gemma4:12b` (Apache-2.0)

| Modèle | Banc complet | Temps moyen | Réflexion, questions vérifiables | Taille en VRAM |
|---|---|---|---|---|
| **gemma4:12b** | 88,8 * | **2,7 s** | **90,8 %** en 5,7 s | ~11 Go |
| qwen3.5:9b | 95,2 | 14,0 s | 86,7 % en 20,5 s (jusqu'à 69 s) | ~9 Go |
| gpt-oss:20b | 93,6 | 5,1 s | — | 12,7 Go |
| qwen3:14b (avant) | 93,1 (90,7 au passage précédent) | 10,5 s | — | 10,4 Go |

\* Sans réflexion : le serveur déployé ne l'activait pas encore pour Gemma 4
(`persona::sait_reflechir`). Avec la réflexion, il est le seul à trouver le jour du
14 juillet 1789 (mardi), et ne rate que le piège « +20 % puis −20 % ».

Écarts de ±2 points d'un passage à l'autre : les scores globaux ne départagent pas à eux
seuls. Lecture des réponses de conversation : Gemma 4 et qwen3.5 sont les plus naturels ;
gpt-oss écrit un français plus plat (« a‑il »), qwen3:14b fait des coquilles (« Pardoner »).

Choix : **gemma4:12b**, avec la réflexion pour les demandes exigeantes. Repli automatique,
dans l'ordre, sur `qwen3:14b` puis `qwen3:8b` s'il n'est pas installé
(`persona::MODELES_CONVERSATION`). Le réglage `modele_conversation` en impose un autre.

### Vérification après déploiement (mode normal, modèle « auto »)

| Banc complet, 42 questions | Score | Raisonnement / expert | Conversation | Explication |
|---|---|---|---|---|
| qwen3:14b (avant) | 93,1 | 100 / 87,5 | 94 | 90 |
| **gemma4:12b avec réflexion** | **96,7** | 100 / 87,5 | 96 | **100** |

Code expert 100 (qwen3:14b : 66,7). Seul échec : le jour du 14 juillet 1789 (« lundi »).
Le banc a révélé un aiguillage erroné : « tu m'aides à **trouver** un plan réaliste ? »
partait vers l'agent image (85 s, vouvoiement) ; « trouver / chercher » n'envoient plus
vers l'image sans mot visuel (photo, image…).

## Vision : `gemma4:12b` aussi

Comparatif du 09/11/2026 (`vision.py` de la session) : 4 images du studio avec 29 éléments
vérifiés à l'œil (dont 13 pièges absents : chapeau, cheval, astronaute…) et 14 questions de
lecture et de comptage (ticket, facture de 8 lignes en petits caractères, avis en corps 15,
graphique en barres, 26 formes colorées).

| | Éléments justes | Pièges acceptés | Lecture / comptage | Par image |
|---|---|---|---|---|
| qwen2.5vl:7b (avant) | 28/29 | 0/13 | 12/14 | 1,5 s |
| gemma4:12b | 29/29 | 0/13 | 13/14 | 1,0 s |
| gemma4:12b avec réflexion | 29/29 | 0/13 | **14/14** | 2,5 s |

Le même modèle parle et voit : plus de déchargement du cerveau quand une image arrive.
Chat avec image : réflexion si la question est exigeante (« combien… », « quel est… »).
Vérification du studio (`direction.verifier`) : sans réflexion, ~2 s au lieu de ~15 s.
Le contexte est partout de 8192 jetons : un autre `num_ctx` ferait recharger le modèle.
Repli sur `qwen2.5vl:7b` si Gemma 4 manque ; `NEO_IMAGE_VISION` impose un modèle.

## Modèles écartés : trop gros pour la carte

`qwen3.6:35b-a3b` (22,5 Go) et `gemma4:26b` (18,7 Go) dépassent 16 Go : Ollama les répartit
entre la carte et la mémoire vive. Ils tournent vite (73 à 96 jetons/s), mais **qwen3.6 a
provoqué deux écrans bleus (0xD1) le 09/11/2026**, à 16 h 15 et 17 h 53, en plein banc ;
qwen3:14b, entièrement en VRAM, a tourné des heures sans incident. Règle : un modèle de
conversation doit tenir **entièrement** dans la carte. Le banc le vérifie avant de commencer
(`_tient_dans_la_carte`, recoupé avec `nvidia-smi` car Ollama sous-estime la taille des
Gemma 4 dans `/api/ps`) et refuse sinon (`--accepter-debordement` pour passer outre).

Windows n'a pas pu écrire le rapport de plantage : le fichier d'échange de `C:` (3 Go) est
trop petit. Le passer en « taille gérée par le système » permettrait d'identifier le pilote.

## Ollama : mise à jour automatique dangereuse

Le même jour, l'application Ollama de la barre des tâches a lancé seule une mise à jour
(0.40.12 → 0.40.15) sans droits administrateur ; ne pouvant remplacer `ollama.exe`, tenu par
le `ollama serve` du superviseur, l'installeur a annulé en effaçant `lib\ollama` : plus aucun
modèle ne se chargeait. Réparation : `code/scripts/reparer_ollama.ps1` (administrateur ;
`-SansMiseAJourAuto` désactive les tâches qui lancent l'application).

**Récidive le 11/11/2026**, au redémarrage du PC : l'application, relancée par son raccourci du
dossier Démarrage, a retenté la mise à jour (0.40.16), échoué de la même façon et vidé
`lib\ollama` — plus aucun modèle ne se chargeait (« llama-server binary not found »), alors que
l'API répondait encore. Les deux tâches planifiées n'étaient donc pas la seule source :
`-SansMiseAJourAuto` met maintenant aussi ce raccourci de côté. Le superviseur de Néo lance
`ollama serve` sans l'application ; une mise à jour d'Ollama devient un geste volontaire
(installeur lancé en administrateur, superviseur en pause : ce que fait le script).

## Refaire le duel

```
python -I code/eval/bench_agents.py duel_<nom> --modele <modele> --niveau tout --sortie <dossier>
```

Le banc reprend là où il s'est arrêté (`*.partiel.jsonl`), décharge le candidat avant de
juger, et `--juge` change de juge (garder le même pour comparer).

## Agents à outils : `localai-prog-v10` reste en place

Banc `code/eval/bench_outils.py` (11 cas sans effet de bord : change, adresse, météo, heure,
calcul, état de la machine, recherche web ; valeurs attendues calculées au moment du banc),
09/11/2026 :

| Modèle des agents | Bon outil | Réponse juste | Temps médian |
|---|---|---|---|
| **localai-prog-v10-q5km** | 9/11 | 9/11 | 2,7 s |
| gemma4:12b | 9/11 | **0/11** | 13,6 s |

Gemma 4 appelle le bon outil puis le rappelle en boucle (13 tours) sans répondre : les
outils passent par le « toolshim » (port 8330), qui les décrit en texte pour le modèle
entraîné ; Gemma, qui appelle les outils nativement, n'y reconnaît pas le résultat. L'y
adapter demanderait de contourner le toolshim pour les modèles à outils natifs : chantier à
part. Coût de l'échange de modèle (Gemma ↔ localai-prog) : ~25 s au premier appel d'outil.

Les deux échecs communs, corrigés : « quelle heure est-il ? » (la date et l'heure locales
sont désormais dans la fiche de Néo et l'identité des agents) et « combien de mémoire vive
mon PC utilise ? » (aiguillé vers l'agent Système, sans capter « combien de RAM pour un
LLM ? »).

Après déploiement (09/11/2026, 21 h 13) : **11/11 réponses justes**, temps moyen 3,6 s (9,7 s
avant) ; l'heure vient de la fiche, sans outil. Les agents tutoient désormais l'utilisateur
(« Votre PC utilise… » relevé sur l'agent Système).

## Recherche dans les documents : Qwen3-Embedding + bge sur la carte

Banc `code/eval/bench_rag.py` (09/11/2026) : 30 questions reformulées sur la documentation du
projet (100 passages), noyée parmi les 1 753 passages déjà indexés.

| Configuration | Bon passage dans les 4 retenus | MRR@10 | Temps par question |
|---|---|---|---|
| nomic-embed-text seul | 63 % | 0,50 | 0,3 s |
| qwen3-embedding:4b seul | 73 % | 0,62 | 0,1 s |
| nomic + bge-reranker-v2-m3 (avant, processeur) | 83 % | 0,71 | **13 s** |
| nomic + Qwen3-Reranker-0.6B | 80 % | 0,69 | 0,8 s |
| **qwen3-embedding:4b + bge-reranker-v2-m3** | **97 %** | **0,82** | 0,06 s sur la carte |
| qwen3-embedding:4b + Qwen3-Reranker-0.6B | 90 % | 0,78 | 0,2 s |

- Le modèle de l'index est le réglage `modele_plongement` (défaut : nomic, l'index d'origine) ;
  `POST /api/documents/reindexer {"modele": "qwen3-embedding:4b"}` recalcule tous les vecteurs
  en mémoire, les écrit en une transaction, puis bascule le réglage (échec = index intact).
  Les écritures Python (`vector_store.py`, `apprentissage.py`) lisent le même réglage.
- Requêtes Qwen3 précédées d'une consigne (`documents::texte_requete`), documents tels quels.
- Deux vecteurs de tailles différentes ne se comparent plus (score 0) : avant, le cosinus
  tronquait silencieusement au plus court.
- Trieur bge : 12,3 s sur le processeur, 0,06 s sur la carte (fp16), scores identiques. Il
  vit en mémoire vive, monte sur la carte (1,1 Go, 0,15 s) pour trier, redescend après 60 s
  d'inactivité (`NEO_RERANK_REPOS_S`) ou sur `POST /liberer`, appelé par le studio image.
- Qwen3-Reranker-0.6B, moins bon ici, n'est pas adopté (téléchargé dans
  `I:\Neo-Archive-2026\models`).

## Dictée : Parakeet-TDT-0.6B-v3

Banc `code/eval/bench_dictee.py` : 15 phrases du quotidien, deux voix de synthèse (Piper,
Hortense), propres et avec bruit de fond à 10 dB (60 enregistrements).

| Moteur | Erreurs sur les mots | Temps par phrase |
|---|---|---|
| Whisper small (avant, carte) | 7,1 % | 0,40 s |
| **Parakeet v3 fp32 (processeur)** | **4,7 %** | 0,24 s |
| Parakeet v3 int8 | 5,6 % | 0,23 s |

Réglage `stt_model` : `parakeet` (défaut) ou une taille Whisper. Le webm du navigateur est
décodé par `faster_whisper.decode_audio` ; Whisper reste le secours (erreur, audio de plus
de 20 min). La dictée passe par le processus Python résident : Parakeet (8 s de chargement)
reste en mémoire. « Bonjour Léo » est corrigé en « Néo » juste après une salutation.
Limite : audio de synthèse ; à confirmer sur la voix réelle.

Mise en service (09/11/2026, 22 h 20) : base sauvegardée (`E:\ia\backups\20261109_avant_reindex`),
réindexation de 1 753 passages en 82 s (2 560 dimensions), dictée basculée sur Parakeet
(0,4 à 0,7 s par dictée de bout en bout). Recherche témoin « comment cuisiner un plat » :
le document de cuisine passe premier (avant : un jeu d'entraînement).

Deux défauts trouvés en conditions réelles, corrigés :
- « D'après mes documents, quelles recettes ai-je enregistrées ? » : la recherche classait
  les 5 passages de cuisine en tête, mais le trieur les notait 0,008 et le seuil de 0,20
  vidait le contexte. Quand le trieur doute, un groupe de passages nettement détaché
  (meilleur moins médiane des 20 premiers d'au moins 0,12) est gardé (`groupe_detache`).
- L'agent Documents passait par la boucle d'outils (calculatrice) avec localai-prog :
  58 s et trois échanges de modèles. Il répond désormais sans outil, avec le modèle de
  conversation (Gemma 4) et ses consignes (citer les fichiers sources).

### Une seule taille de contexte pour Gemma 4

Ollama recharge entièrement un modèle dès que `num_ctx` change d'une requête à l'autre :
**5,3 s** pour gemma4:12b (mesuré : 8 192 → 12 288 → 8 192 → 4 096, chaque bascule recharge).
Le chat alternait 8 192 (réponse directe) et 12 288 (réflexion), la reformulation du RAG
demandait 4 096 : des secondes perdues en silence. Gemma 4 ne coûte que 200 Mo de plus à
16 384 jetons (9,26 Go → 9,46 Go) : tous les appels à Gemma 4 utilisent désormais 16 384
(`localai_core::ollama::contexte_pour`, `backend.core.config.contexte_pour`). Les autres
modèles gardent leur taille.

Le groupe de passages gardé quand le trieur doute se calcule sur la question d'origine
seule : avec les reformulations, l'écart s'écrasait une fois sur deux (« recettes » de
nouveau sans contexte au second essai).

### Calculs simples : Néo répond lui-même

« Combien font 17 fois 23 ? » partait vers l'agent Dev : 36 s (localai-prog remplace Gemma
sur la carte), puis 22 s au message suivant pour revenir à Gemma. Gemma 4 avec réflexion :
14 calculs du quotidien sur 14 justes (pourcentages, soldes, TVA, puissances, racines,
fractions, conversions d'unités), 3 s en moyenne, 6 s au plus. Les calculs simples restent
donc à Néo ; l'agent Dev garde ce qui demande d'exécuter du code (script, fichier,
statistiques, factorielles géantes, listes de nombres premiers) : `CALCUL_LOURD_HINTS`.

## Agents à outils : le dialogue d'outils complet, puis Gemma 4

Cause de la boucle de Gemma 4 (13 appels, aucune réponse) : le serveur réinjectait le
résultat d'un outil, mais pas l'appel lui-même (message de l'assistant sans `tool_calls`,
résultat sans `tool_name`). Un modèle à appel natif voyait un résultat qu'il ne s'attribuait
pas et rappelait l'outil. Le toolshim, qui traduit ces champs pour les modèles émulés, masquait
le défaut avec localai-prog. Boucle rejouée à l'identique via le toolshim (10 cas) :

| Modèle | Ancien format | Dialogue complet |
|---|---|---|
| gemma4:12b | 0/10, 6 appels par question | **10/10, 1 appel, 1,4 s** |
| localai-prog-v10 | 10/10, 5,3 s | 10/10, 2,0 s (plus d'appel en double) |

Les agents outillés (sauf Dev : code, fichiers, exécution) utilisent désormais le modèle de
conversation, déjà en VRAM : plus d'échange de modèles de 25 à 35 s entre une conversation et
un outil. Réglage `agents_modele_conversation` = 0 pour revenir à `default_model` ;
`modele_conversation`, `agents_modele_conversation` et `model_rapide` sont modifiables par
`PUT /api/settings`.
