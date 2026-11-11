# Images et vidéo : pipelines, réglages, stockage

Document de référence des choix faits le 08/11/2026, tous mesurés sur la machine
(RTX 5080 16 Go, 51 Go de RAM). Code : `backend/core/sdgen.py`, `backend/tools/t_video.py`.

## Image : le studio (4K, ~45-70 s de bout en bout)

Code : `backend/tools/t_image.py` (`tool_generate_image`), `backend/core/direction.py`,
`backend/core/sdgen.py`. Refonte du 09/11/2026, après des demandes mal comprises
(« lazar », « science diction », « effet 3D plongeant ») et des générations bloquées.

1. **Compréhension** (`direction.brief`, 12-18 s avec réflexion, `NEO_IMAGE_REFLEXION=0` : 6-7 s).
   Méthode imposée au modèle : thème de la demande, puis pour chaque mot douteux 2 à 4
   candidats (orthographe ET son), choix selon le thème, vérification mot à mot ; les doutes
   restants sont dits à l'utilisateur (`ambiguites`). Un **lexique thématique**
   (astronomie, science-fiction, fantasy, coiffure, photo) rapproché par orthographe et par
   une forme sonore française fournit les candidats rares : « lazar » -> blazar, laser,
   quasar ; « kazar » -> quasar ; « pulsart » -> pulsar. Sans lui, même en réflexion,
   qwen3:14b ne proposait que lazare/lasser et répondait « laser » (cas piège : 5/7 -> 7/7 ;
   « lazar » reste un laser pour un chat ou un fusil du futur).
   La
   demande, transmise TELLE QUELLE par l'agent, est confiée à `gemma4:12b` (le modèle de
   conversation, déjà chargé ; `qwen3:14b` jusqu'au 11/11/2026, voir « Temps d'attente ») qui
   rend un brief JSON contraint par schéma :
   - `comprehension` : ce qui a été compris, en français, repris par Néo dans sa réponse ;
   - `corrections` : fautes corrigées (« lazar -> laser »), seulement si présentes dans la demande ;
   - `style` et `cadrage` (`paysage` 16:9, `portrait` 9:16, `carre`) ;
   - `elements` : ce qui doit être visible (vérifié ensuite) ;
   - `prompt` : anglais (refusé s'il est en français), sujet d'abord, plans avant/milieu/fond,
     optique, lumière, matières ; les références (« type Interstellar ») deviennent des visuels,
     « effet 3D » devient une perspective grand-angle ; jamais de bandes noires.
   Le brief est enregistré à côté de l'image (`<nom>.brief.json`) : une suite (« plus sombre »,
   « des variations ») passe `precedent=<chemin>` et part de ce brief.
2. **Génération** : **Z-Image-Turbo Q8** (`z_image_turbo-Q8_0.gguf`, encodeur
   `Qwen3-4B-Instruct-2507-Q8_0.gguf`, VAE `ae.safetensors`), 8 étapes, ~29 s. Comparatif du
   09/11/2026 sur 5 demandes réelles : rendu photographique nettement supérieur à FLUX schnell
   (peau, cheveux, métal), 29 s au lieu de ~45 s, tous les éléments présents sur 4 demandes sur 5.
   Sa faiblesse : il a dessiné une planète à anneaux pour « trou noir » ; d'où la seconde prise
   avec **l'autre modèle** (FLUX schnell Q8, 6 étapes) quand un élément essentiel manque.
   Sans l'encodeur Qwen3-4B, FLUX schnell redevient le modèle principal.
   Commun aux deux : base selon le cadrage (1536×864, 864×1536
   ou 1152×1152), passe haute résolution dans le même processus (2560×1440, 1440×2560 ou
   1920×1920) aux **sigmas imposés** `0,30 → 0,15 → 0`, décodage **par tuiles** (`--vae-tiling` :
   37 s au lieu de 46 s, moins de VRAM, pas de couture visible).
   - FLUX décale son calendrier de bruit avec la résolution : une « force 0,32 » valait un
     bruit quasi total en 2560×1440 et la scène était réinventée.
   - Raffinage par tuiles écarté : chaque tuile inventait son propre sujet.
   - Passe 4K native écartée : plus douce, +110 s.
3. **Vérification** (`direction.verifier`, ~2 s) : `gemma4:12b` (sinon `qwen2.5vl:7b`) dit si chaque élément est
   visible et note l'image. S'il manque l'un des trois éléments principaux, une seconde prise
   (autre modèle, autre graine, éléments manquants en tête du prompt) ; la meilleure prend le nom principal,
   l'autre reste en variante. L'outil rend `COMPRIS`, `CORRECTIONS`, `IMAGE`, `VERIFICATION`.
4. **Master** : Lanczos ×1,5 + accentuation légère (0,7 s) vers 3840 px de grand côté
   (3840×2160, 2160×3840 ou 2880×2880). `LOCALAI_SD_AGRANDISSEMENT=esrgan` rétablit Real-ESRGAN.
5. **Finition** légère ; palette neutre sauf mots-clés (coucher de soleil, néons…).
6. **Version écran** `<nom>_ecran.jpg` (1920 px) affichée dans le fil (`taille=ecran`).

### Fiabilité (plus jamais de génération bloquée)

Le 09/11/2026, une génération a dépassé les 600 s du pont Rust : le Python a été tué mais
**sd-cli a survécu**, gardant 15 Go de VRAM ; la tentative suivante a tourné à 1 % du GPU.
- Chaque sd-cli tourne dans un **job Windows « tuer à la fermeture »** : si le Python meurt,
  sd-cli meurt avec lui (vérifié).
- Le pont Rust tue l'**arbre** de processus (`taskkill /T`) au délai dépassé.
- Sous le verrou GPU, tout sd-cli dont le parent est mort est arrêté avant de commencer.
- Délai par passe : 300 s (`NEO_SD_DELAI_S`), puis un **essai de secours** de 200 s (FLUX Q4,
  encodeur de texte sur le processeur, décodage par tuiles) : le total reste sous 600 s.
- Pic mesuré : ~15 Go sur 16 avec le Q8 ; un autre programme gourmand en VRAM pendant la
  génération la fait déborder en mémoire partagée (lenteur), d'où le secours.

Chaque génération journalise ses étapes (`etapes` : chargement, échantillonnage, décodage,
agrandissement, finition) dans le journal `sd`.
Mesure du 09/11/2026 : chargement 23 s, échantillonnage ~27 s, décodage 4 s, reste ~4 s.

Modèles essayés et écartés : FLUX Kontext dev en texte→image (bloqué), **FLUX Krea dev Q8**
(motif de « toile tissée » sur la peau et les dégradés quels que soient guidance et
attention ; rangé dans `tools/sd/modeles_essai`, utilisable via `LOCALAI_SD_MODEL`).

| Variable | Effet |
|---|---|
| `LOCALAI_SD_MODEL` | impose un fichier de modèle |
| `LOCALAI_SD_HIRES=0` | désactive la passe 2560×1440 |
| `LOCALAI_SD_TRAD_MODELE` | modèle LLM de réécriture du prompt |
| `LOCALAI_SD_GUIDANCE` | guidance des modèles FLUX dev (3,5) |
| `LOCALAI_SD_HIRES_SIGMAS` | sigmas de la passe 2560 (`0.30,0.15,0.0`) |
| `LOCALAI_SD_AGRANDISSEMENT` | `lanczos` (défaut) ou `esrgan` |
| `qualite="rapide"` (API) | force schnell 6 étapes |
| `NEO_IMAGE_DIRECTION=0` | saute le directeur artistique |
| `NEO_IMAGE_BRIEF` | modèle du brief (défaut : `gemma4:12b`, sinon `qwen3:14b`) |
| `NEO_IMAGE_REFLEXION` | `1` / `0` : réflexion séparée pour le brief (défaut : non pour Gemma 4, oui sinon) |
| `NEO_IMAGE_PRECHAUFFE=0` | ne relit pas les fichiers du modèle de conversation pendant la génération |
| `NEO_IMAGE_PRECHARGE=0` | ne recharge pas le modèle de conversation pendant la finition |
| `NEO_SD_SAGE=0` | passes klein (visage, yeux) sans SageAttention |
| `NEO_IMAGE_VERIF=0` | saute la vérification par la vision |
| `NEO_IMAGE_VISION` | modèle de vérification (défaut : `gemma4:12b`, sinon `qwen2.5vl:7b`) |
| `NEO_SD_DELAI_S` | délai d'une passe sd-cli (300 s) |
| `NEO_IMAGE_DETAIL_VISAGES=0` | saute les passes de détail des visages (peau, puis yeux) |
| `NEO_IMAGE_DETAIL_YEUX=0` | saute la seule passe des yeux |
| `NEO_SD_RETOUCHE` | dossier des fichiers FLUX.2-klein (défaut : SSD, puis `tools/sd/models_edit`) |

## Visages photoréalistes : passe « détail du visage » (10/11/2026)

À 100 %, la peau sortait lisse et cireuse (image de 2 560 px agrandie en 4K, générateur rapide).
Après l'agrandissement et avant la finition, chaque visage détecté (YuNet, au moins 160 px,
4 au plus) est recadré (2 fois sa taille), retravaillé à 1 536 px par FLUX.2-klein (pores, cils,
mèches), recalé, puis réintégré par **séparation de fréquences** : couleurs, volumes et teinte
des yeux viennent de l'original, seul le grain fin vient de klein (fondu elliptique). Sans cette
séparation, klein changeait la couleur des yeux et ajoutait des taches. ~13 s par visage.
Jamais pour les styles dessinés ni pour une retouche ; `NEO_IMAGE_DETAIL_VISAGES=0` la coupe.
Comparaison : `code/eval/resultats/visage_detail_avant_apres.jpg`.

### Passe « yeux » et fusion révisée (10/11/2026, soir)

Contrôle à 100 % du premier portrait obtenu par le chat : peau convaincante, mais **yeux
baveux** (iris sans structure, pupille indécise). La passe visage ne garde de klein que le
grain fin : un iris flou au départ le restait. Trois corrections (`backend/core/sdgen.py`) :

1. **Passe « yeux »** (`_detail_yeux`, ~11 s, deux plus grands visages, de face) : la bande des
   deux yeux, lue dans l'image d'avant la passe visage, est agrandie jusqu'à 1 536 px de large
   (×3 au plus), redessinée par klein, puis fusionnée dans deux ellipses centrées sur les yeux.
   La **luminance** prend le dessin de klein jusqu'à l'échelle de l'iris (écart des yeux / 30) ;
   la **couleur** reste celle de l'original (mesurée : identique avant et après). Essais écartés :
   consigne « macro » (regard et maquillage modifiés), Z-Image en img2img (reste flou jusqu'à
   un bruit de 0,6).
2. **Fusion en rapport** (`_fusion_structure`, espace Lab) : le détail est le rapport
   clair / moyenne locale, multiplié par les volumes de l'original. En addition, klein, qui
   éclaircit tout, faisait ressortir un œil à travers un verre de lunettes noires.
3. **Recalage local** (`_recaler_dense`) : flot optique lissé en ne se fiant qu'aux contours
   francs. Le décalage global laissait un double bord le long d'une monture de lunettes ; un
   flot lissé uniformément étirait le grain de peau en ondulations.

Garde-fous : pas de passe yeux si les yeux ne sont pas visibles (lunettes noires, ombre
profonde : aucun point de la zone n'atteint la moitié de la luminosité de la peau), ni si klein
a changé les grandes masses de la zone (corrélation < 0,80, journalisée). Validé sur cinq
visages (gros plan, homme en plan moyen, scène à deux dont un profil à lunettes, lunettes de
soleil, guerrier aux yeux rouges). `NEO_IMAGE_DETAIL_YEUX=0` coupe la passe. Comparaison :
`code/eval/resultats/yeux_detail_avant_apres.jpg`. Tests : `tests_unit/test_detail_visages.py`.

Les fichiers de klein sont copiés sur le SSD (`tools/sd_ssd/models_edit`, 4,5 Go), lus en
priorité : klein sert à chaque portrait, et son premier chargement depuis le disque d'archives
coûtait ~35 s.

Deux défauts du chemin par le chat, corrigés le même jour :
- sans `save_path` (Gemma n'en passe pas), l'image partait dans le « Documents » du compte
  système (`C:\Windows\System32\config\systemprofile`), invisible, et l'outil échouait
  (WinError 2) : désormais `workspace\images`, et `neo_documents_dir` ne pointe plus jamais
  vers le profil système (vidéos et voix comprises) ;
- le lexique des mots rares lisait « cheveux roux » comme « trou (noir) », « yeux verts » comme
  « ver » : couleurs et descriptions de personnes ajoutées aux mots courants.

### Temps d'attente d'un portrait (11/11/2026)

Un portrait demandé par le chat prenait 163 s. Chronologie de l'outil (126 s), mesurée appel
par appel : brief 35 s, studio 62 s, vérification 28 s. Plus de la moitié du temps hors studio
était du **chargement de modèles**, pas du travail.

| Étape | Avant | Après | Ce qui a changé |
|---|---|---|---|
| Brief | 35 s | 4 s | `gemma4:12b` (déjà chargé par la conversation) au lieu de `qwen3:14b` (24 s de chargement + 10 s) |
| Studio | 62 s | 64 s | inchangé (Z-Image 34 s, détail visage + yeux 26 s, finition 3 s) |
| Vérification | 28 à 33 s | 12 s | fichiers de Gemma relus pendant la génération |
| **Outil** | **126 s** | **~80 s** | |

- **Brief par Gemma 4, sans réflexion séparée.** Banc `code/eval/bench_brief.py` (12 demandes
  dont 7 pièges : « lazar », « kazar », références de films, cadrage) : Gemma direct 12/12,
  médiane 3,8 s ; Gemma avec réflexion 12/12, 8,7 s ; qwen3:14b avec réflexion 12/12, 10,8 s.
  Les briefs de Gemma sont au moins aussi riches. Sans Gemma 4 installé, qwen3:14b reprend le
  rôle, avec sa réflexion. `NEO_IMAGE_BRIEF` et `NEO_IMAGE_REFLEXION` imposent un choix.
- **Préchauffage du modèle de conversation** (`direction.prechauffer_modele`). Le studio
  décharge Gemma pour libérer la carte ; Ollama le recharge ensuite pour vérifier l'image, puis
  pour répondre. Sous Windows, Ollama charge sans lecture mappée (journal : `disabling mmap …
  reason=windows_cuda`) et ce chargement ne laisse pas le fichier dans le cache du système :
  17 à 26 s à chaque fois, même juste après un déchargement. Une simple lecture du fichier
  (7,6 Go, 2 à 4 s sur le SSD) le met en cache, et le rechargement tombe à 6 à 9 s. La lecture
  est lancée dans un fil au début de la génération : elle ne coûte rien à l'attente. Elle est
  abandonnée s'il reste moins de 6 Go de mémoire vive au-delà de la taille du fichier.
  Essai écarté : l'option `use_mmap` d'Ollama (chargement toujours à 25 à 28 s).

Vérifié en production par le chat : 163 s -> 80 s (outil 76 s : brief 3,9 s, studio 64 s,
vérification 7,3 s).

### Le studio lui-même (11/11/2026, suite)

Relevé de chaque lancement de sd-cli (portrait, 62 s) : Z-Image 29 s (base 8 étapes 9,4 s,
passe 2 560 px 12 s, décodage 4,2 s), klein visage 12,5 s, klein yeux 10,3 s, calcul Python
3 s, finition 2,4 s, lecture préalable des fichiers 3,3 s. **Les chargements ne pèsent
presque rien** (1 à 2 s par modèle, fichiers en cache) : un serveur `sd-server` gardant les
modèles en mémoire ne rapporterait que ~3 s, piste abandonnée. Trois gains retenus :

1. **Fichiers du studio lus pendant le brief** (`sdgen.prechauffer_studio`, lancé par l'outil
   avant le brief) : `generer` ne relit pas un fichier lu dans les 3 dernières minutes et
   n'attend que les siens (klein finit d'être lu pendant que la base se calcule). -3 s.
2. **SageAttention sur les passes klein** (`--sage-attn`, `sdgen._attention`) : visage 12,6 s
   -> 9,9 s, yeux 10,3 s -> 8 s, image finale quasi identique (écart moyen 0,8/255, netteté
   du visage -3 à -5 %, rien de visible à 100 %). **Refusée pour Z-Image** : 5,6 s gagnées,
   mais visage nettement plus lisse sur 2 portraits sur 2 (variance du laplacien -30 à -45 %,
   grain de peau et texture des lèvres perdus), alors qu'un premier essai sur une seule graine
   la donnait équivalente. Non appliquée à la retouche demandée (non mesurée).
   `NEO_SD_SAGE=0` coupe ; un binaire sans l'option garde l'attention « flash » seule.
3. **Modèle de conversation rechargé pendant la finition** (`direction.precharger_modele`,
   appelé par `generer(apres_gpu=...)` dès que la carte est libre, dernière prise seulement) :
   la vérification passe de 7 s à 4 s. `NEO_IMAGE_PRECHARGE=0` coupe.

Mesure de bout en bout (outil, portrait, deux essais) : 76 s -> 64 s (studio 64 s -> 56 s,
vérification 7,3 s -> 3,8 s). Ce qui reste est du calcul : Z-Image 29 s, klein 18 s.

Vérifié en production par le chat : 74 s pour un portrait, dont 6 s de rechargement de Gemma
après le redémarrage du déploiement (outil 65,7 s, vérification 4,5 s, note 9/10).

**Recherche de référence inutile.** Pour « un vieux pêcheur breton… », Gemma appelait d'abord
`find_reference_image` (3 fois sur 3) : 65 s de recherche web et de tri par la vision, sans
résultat, puis la génération normale — 136 s au lieu de 70. Deux corrections :
- consigne de l'agent image et description de `generate_image` : la référence est réservée
  à un sujet réel **nommé** (SR-71, Peugeot 205 GTI), jamais pour une personne, un animal ou
  un décor décrits en général. Premier outil appelé sur 10 demandes × 3 (hors production,
  `gemma4:12b`, mêmes outils) : 21/30 -> 27/30 ; les 7 demandes génériques vont toutes droit
  à `generate_image`. Reste « une photo de la Tour Eiffel sous la neige », lue comme une
  recherche de photo existante (`image_search`), avant comme après ;
- `find_reference_image` ne décharge plus le modèle de vision quand c'est lui qui est déjà sur
  la carte (il le déchargeait pour le recharger aussitôt, 17 à 26 s).

### Cause des lenteurs en production : la priorité des tâches planifiées (11/11/2026)

Le premier plan vidéo passé par le service a mis 700 s, le second 410 s, contre 114 s pour la
même commande lancée depuis une console. Le suivi a montré 250 s avant tout calcul, carte
graphique presque inactive. **Cause mesurée** : Windows donne à une tâche planifiée la
priorité 7 par défaut ; le programme et tout ce qu'il lance tournent alors en classe
« inférieure à la normale », avec une priorité de lecture disque basse et une priorité mémoire
de 2 sur 5 (relevé sur Ollama, lancé ainsi). Le serveur (`LocalAI_watch_rs`) et Ollama
(`Ollama_superviseur`) sont tous deux dans ce cas.

| Même clip Wan (3 s, même graine) | Durée | Chargement des poids |
|---|---|---|
| priorité normale | 92 s | 1,8 s |
| priorité d'une tâche planifiée | 350 s | 262 s |
| idem, avec le correctif du code | 90 s | 2,1 s |

Deux corrections :
- **dans le code** (`backend/core/priorite.py`) : le studio et le worker se remettent en priorité
  normale au démarrage, et les programmes qu'ils lancent (sd-cli, ffmpeg) en héritent.
  `NEO_PRIORITE_NORMALE=0` coupe. Test : `tests_unit/test_priorite.py` ;
- **à la source** (`code/scripts/priorite_taches.ps1`, console administrateur) : priorité 4 pour
  les tâches qui font tourner un service. C'est le seul moyen de corriger Ollama (chargement de
  Gemma en 17 à 39 s au lieu de 5 à 9 s) et le serveur. Simulation par défaut, sauvegarde du XML
  des tâches, effet au prochain démarrage de la tâche.

C'est très probablement aussi ce qui a transformé l'incident ci-dessous (cache froid après un
déploiement) en attente de 27 minutes.

### Incident du 11/11/2026, 08 h 16 : une image en 27 minutes

Première demande après un déploiement, par le chat : trois appels de `generate_image`, deux
expirés (passe de 300 s, puis essai de secours), le troisième abouti en 440 s (chargement des
poids 218 s au lieu de 4, échantillonnage 207 s au lieu de 18, lecture de Gemma 55 s au lieu
de 4). Cinq minutes plus tard, même demande : studio 60 s, normal. **La cause n'est pas
établie** : tout était froid (cache disque vidé par la compilation et la copie du déploiement,
Gemma en cours de premier chargement, 39 s) et sd-cli a manifestement tourné poids hors de la
carte. Une piste a été écartée par la mesure : la mémoire graphique est bien rendue 0,2 s après
qu'Ollama a retiré le modèle de `/api/ps`. Une autre a été vue une fois : 6 Go encore occupés
4 s après le déchargement de deux modèles.

Ce qui a été durci, faute de cause certaine :
- `_liberer_vram` attend que l'occupation de la carte ait baissé et soit stable
  (`_attendre_vram_rendue`, ~0,6 s en temps normal, 25 s au plus ; `NEO_SD_ATTENTE_VRAM=0` coupe) ;
- l'erreur renvoyée au modèle disait « Réessaie dans un instant » : il relançait aussitôt, d'où
  trois essais. Elle dit maintenant de ne pas relancer et de prévenir l'utilisateur ;
- le texte qu'un agent écrit en même temps qu'un appel d'outil n'entre plus dans la réponse
  finale, et les marqueurs internes de Gemma (« thought », `<channel|>`) en sont retirés : la
  réponse de cet essai collait « Désolé, erreur technique… » devant « J'ai créé l'image ».

Pour la prochaine fois : une passe expirée journalise maintenant ce que sd-cli avait écrit (ligne « auto-fit plan » :
mémoire graphique libre vue au lancement), message « sd-cli arrete au delai » dans `logs/neo.log`.

Stries sur la peau : sur un portrait, à contraste ×5, de fines stries verticales apparaissaient
au menton après la passe visage. Rejouée étape par étape sur un second visage (sortie de klein,
recalage, fusion), la passe n'en crée aucune : structure verticale 4,38 après fusion contre
4,35 dans l'original. Non reproduit ; probablement le duvet dessiné par klein sur ce visage-là.

**Yeux naturels** : pour « yeux verts », le brief écrivait « vibrant green eyes » et le
générateur peignait des iris de lentilles colorées. La consigne du brief demande des couleurs
naturelles, et `direction._nettoyer` retire ces qualificatifs devant « eyes » (photo et cinéma
seulement, sauf si la demande veut un regard irréel : elfe, cyborg, « yeux qui brillent »).
Banc du brief inchangé : 12/12. Tests : `tests_unit/test_brief_yeux.py`.

Options de sd-cli essayées et écartées (même graine) : `--vae-conv-direct` (+6 s), décodage
sans tuiles (aucun gain, passe 2 560 px plus lente), tuiles de 512 px (-1 s, image différente).

Tests : `tests_unit/test_brief_modele.py`, `tests_unit/test_detail_visages.py`.

## Retouche d'image : FLUX.2-klein-4B (09/11/2026)

`edit_image` (et `generer(..., mode="edit")`) passe par **FLUX.2-klein-4B Q8** (Apache-2.0) :
une passe de 4 étapes, au format de l'image d'origine (1 536 px de grand côté), consigne en
français acceptée, encodeur Qwen3-4B partagé avec Z-Image. Fichiers à part dans
`tools/sd/models_edit` (`flux-2-klein-4b-Q8_0.gguf`, `flux2-vae.safetensors`) pour ne jamais être
pris pour un modèle de génération (`NEO_SD_RETOUCHE` pour un autre dossier). Repli sur Kontext
si klein manque.

Banc `code/eval/bench_retouche.py` (casquette, décor enneigé, vaisseau rouge ;
planche `code/eval/resultats/retouche_planche.jpg`) :

| | Temps | Ce qui est fait |
|---|---|---|
| FLUX Kontext, 2 passes (avant) | 140 à 218 s | retouche faite mais **scène recréée** : autre visage, autre cadrage, galaxie disparue |
| **FLUX.2-klein-4B** | **8 à 16 s** | retouche faite, visage, pose et cadrage gardés |

Photo jointe dans le chat (« retouche cette photo : … ») : le serveur l'enregistre dans
`E:\ia\input\joint_<date>_<n>.<ext>` et donne ce chemin exact au modèle
(`vision::enregistrer_jointes`). Avant le 10/11/2026, l'image n'était jamais écrite sur le disque
et le modèle inventait un nom de fichier (« input_file_0.png ») : la retouche échouait.

Le juge par la vision (Gemma 4) notait Kontext 9-10/10 sur la préservation : il ne voit pas
qu'une scène a été recréée. Jugement retenu : la planche, à l'œil.

## Vidéo (24 i/s, 1080p par défaut, `NEO_VIDEO_RES=4k`)

1. **Storyboard** : `gemma4:12b` (déjà chargé ; 5 s, et aucune règle oubliée sur trois histoires,
   contre 11 s et deux mouvements de caméra manquants pour `qwen3:14b`), JSON forcé, plans de 3 à 5 s, prompts
   FLUX détaillés, personnage et style recopiés dans chaque plan, graine commune.
2. **Image du plan** : la base du studio (1536×864) quand Wan anime le plan, l'image 4K
   complète pour un Ken Burns.
3. **Mouvement réel** : Wan 2.2 TI2V 5B (image→vidéo, 1280×704, 121 images = 5 s),
   `cfg 5`, `flow-shift 5`, **SageAttention** (échantillonnage 402 s → ~160 s) et décodeur
   léger **TAEHV** `taew2_2` (décodage 720 s → 3 s). ~3 à 5 min par plan.
   Repli automatique : Ken Burns fluide (sur-échantillonnage ×2, courbe en S).
4. **Montage** : fondus enchaînés, ouverture/fermeture au noir, narration (ElevenLabs si clé,
   sinon voix locale SAPI) normalisée EBU R128, encodage NVENC.

| Variable | Effet |
|---|---|
| `NEO_VIDEO_WAN=0` | force le Ken Burns |
| `NEO_WAN_STEPS` | étapes Wan (20) |
| `NEO_WAN_CACHE` | seuil EasyCache de Wan (0,05 ; `0` = sans cache ; `0.2` = le plus rapide, plus mou) |
| `NEO_WAN_DELAI_S` | délai d'un clip Wan (1 500 s) |
| `NEO_VIDEO_IMAGE_COMPLETE=1` | image 4K complète pour chaque plan, même animé par Wan |
| `NEO_FFMPEG` | chemin de ffmpeg |

### Revue du 11/11/2026 : un plan plus rapide et plus fiable

Un plan de 3 s (73 images), mesuré étape par étape : image 51 s, Wan 114 s (échantillonnage
96 s), encodage 3 s. Quatre changements :

| | Avant | Après | Comment |
|---|---|---|---|
| Image du plan | 51 s | 19 s | Wan ne lit que 1280×704 : la base du studio (1536×864) suffit. Plus de passe 2 560 px, d'agrandissement 4K, de passes visage ni de finition. Une fois ramenée à 1280×704, l'image est quasi la même (netteté -11 %, rien de visible à 100 %). Si Wan échoue, l'image 4K est refaite (même graine) pour le zoom du Ken Burns. |
| Mouvement (Wan) | 114 s | 86 s | **EasyCache**, seuil 0,05 : 6 étapes sur 20 reprises de la précédente, image quasi identique (écart 2/255, netteté -3 à -5 %). Le seuil par défaut de sd-cli (0,2) gagne 46 % mais ramollit visiblement les textures (maille d'un bonnet, barbe) : écarté. Un seul plan testé. |
| Fichiers de Wan (12,8 Go, disque d'archives) | lus par sd-cli | lus pendant l'image | Lecture à froid mesurée : 85 s. Elle démarre en même temps que l'image du plan. |
| Total, fichiers en cache | ~170 s | ~110 s | |

`--offload-to-cpu` retiré : aucun gain (113 s contre 114 s, image identique), donc gardé.

Fiabilité :
- Wan passe par le lanceur du studio (`_executer_sd`) : il est tué avec le processus qui l'a
  lancé. Avant, son délai (3 600 s) dépassait celui du pont Rust (1 800 s), qui tuait Python
  en laissant Wan tourner, carte graphique occupée. Délai ramené à 1 500 s.
- Si la carte est prise par une autre génération, Wan ne démarre plus à côté.
- **Montage** : sans horodatage (appel par le chat), seuls les clips postérieurs au dernier
  storyboard sont montés ; avant, des clips d'un projet précédent, de même numéro, pouvaient
  s'y mêler. Un plan en échec ou écarté (au-delà du 12e) est maintenant signalé dans le
  résultat au lieu de disparaître.
- Le `_ecran.jpg` de chaque plan n'est plus laissé derrière.

Non fait : enchaîner toutes les images puis tous les clips (Wan resterait chargé) demande une
route « tous les plans » côté interface ; chaque plan est encore un processus séparé.

Tests : `tests_unit/test_video.py`.

## Voix de Néo (synthèse locale)

- Moteur : `backend/tts/engine.py`. Les voix **Piper** (neuronales, 44,1 kHz) passent avant
  les voix SAPI de Windows ; sans voix choisie, la première voix Piper est utilisée.
  Le Markdown et les blocs de code sont retirés avant la lecture.
- Voix installée : `fr_FR-tom-medium` (`rhasspy/piper-voices`, ~61 Mo, SHA-256
  `bf65074c…a234fd41`), paquet `piper-tts` 1.8.0 dans `E:\ia\.venv`.
  Environ 2,8 s de calcul pour 10 s de parole (chargement du modèle compris).
- Dossier : `E:\ia\tools\voix` est une jonction vers `I:\Neo-Archive-2026\tools\voix`
  (`NEO_VOIX_DIR` pour un autre dossier). Toute paire `nom.onnx` + `nom.onnx.json`
  déposée là apparaît dans Réglages › Voix sous « Néo (neuronale) — nom ».
- Sans le paquet `piper-tts` ou sans modèle, les voix Piper disparaissent de la liste et
  la synthèse retombe sur SAPI.
- Le réglage `tts_voice` l'emporte : s'il désigne une voix SAPI, c'est elle qui parle.
- La narration vidéo garde volontairement une voix féminine SAPI (`voix_feminine_fr`).

## Stockage hybride des modèles

- `E:\ia\tools\sd` est une jonction vers `I:\Neo-Archive-2026\tools\sd` (disque d'archives,
  118 Mo/s à froid) : il garde **tous** les modèles et ménage le SSD.
- `E:\ia\tools\sd_ssd\models` (SSD) contient le **jeu chaud** relu à chaque image :
  `z_image_turbo-Q8_0`, `Qwen3-4B-Instruct-2507-Q8_0`, `flux1-schnell-q8_0`, `t5xxl-q8_0`,
  `clip_l`, `ae` (~29 Go). À nom égal, la copie SSD l'emporte
  (`LOCALAI_SD_RAPIDE` pour un autre dossier). Gain : ~25 s au lieu de ~108 s par chargement
  à froid.
- `E:\ia\tools\sd_ssd\models_edit` (SSD, 4,5 Go) : copie de FLUX.2-klein et de son VAE, lue
  avant `tools/sd/models_edit` (retouche, passes visage et yeux de chaque portrait).
- `tools/sd/models_video` : Wan 2.2 et ses encodeurs, **séparés** des modèles FLUX
  (le VAE de Wan contient « ae. » dans son nom et était pris pour le VAE de FLUX).

## Documents et 3D : revue du 11/11/2026

Essais en production par le chat, demandes telles qu'un utilisateur les écrit.

### Documents produits (agent bureau)

| Constat | Correction |
| --- | --- |
| « Fais-moi une facture en PDF » : texte dans le chat, chemin inventé, aucun fichier | le routeur transmet l'intention (« document ») ; un agent qui produit des fichiers et répond sans outil est relancé une fois (`persona::relance_sans_outil`) |
| Tableur de budget : `[Montant]` partout, totaux figés à 0 | formules retrouvées et vérifiées sur les nombres du modèle (`t_docs._formules_table`) : colonne calculée (Total = Quantité × Prix, Écart = Réel − Prévu), ligne Total (`SUM`), TVA (pourcentage du total), TTC (HT + TVA) ; champ à remplir = cellule vide ; format monétaire |
| En-tête d'un compte rendu (« Date : … » / « Lieu : … ») sur une seule ligne | un retour à la ligne du modèle est gardé, sauf si la ligne suivante commence par une minuscule |
| Fichier nommé `compte_rendu_pdf.pdf`, `cv__tonnom.docx` | nom tiré du titre sans extension ni champ entre crochets |
| Montants bruts (« 1350 ») dans les PDF et Word | « 1 350,00 € », alignés à droite, en-tête grisé |

Une formule n'est posée que si une seule opération explique toutes les lignes
chiffrées (au moins deux, ou une seule quand l'en-tête annonce un résultat) ;
un total qui ne colle à rien reste le nombre écrit par le modèle.

PowerPoint : pris en charge depuis la suite de cette revue (voir plus bas).

### Bibliothèque de documents (page Documents)

- Import de plusieurs fichiers : seul le premier était indexé, la page
  annonçait le nombre envoyé. Le serveur indexe tout et renvoie `count`,
  `documents` et `refuses`.
- Ouvrir ou télécharger un document importé répondait « fichier introuvable » :
  la page demandait le nom d'origine, le fichier est rangé sous
  `data/documents/doc_<id>.<ext>`.
- Le motif d'un refus (`detail`) n'était pas affiché.

### 3D

| Constat | Correction |
| --- | --- |
| « un support de téléphone en 3D à imprimer » : assistant général, script dans le chat, aucun fichier | règle `OBJET_3D_HINTS` du routeur (« en 3D », « 3D … imprimer », « imprimante 3D ») |
| « une tête de lion en 3D » : « Quel dossier ? » | l'agent ne demande plus de dossier ; les outils rangent dans `workspace/pipeline_3d` et `workspace/modeles_3d` |
| Pièce paramétrique : 95 s pour une plaque entaillée, notée 10/10 par le contrôle visuel | gabarits (`backend/tools/gabarits_3d.py`) : la géométrie est écrite une fois, le modèle choisit le gabarit et les cotes. Support de téléphone : 3 s, pièce juste |
| Contrôle visuel complaisant, verdict illisible quand la réponse est en gras | décrire d'abord, pointer chaque fonction (PRÉSENTE / ABSENTE), puis conclure ; plaque : NON 2/10, vrai support : OUI 10/10 |
| Forme organique : la recherche d'une photo échoue (53 s, 4 candidates refusées pour « tête de lion ») | `generate_mesh_from_text` : Néo dessine sa référence (maquette mate, fond blanc) puis Hunyuan3D. Tête de lion : 183 s, étanche, 80 mm |
| STL de 1,1 million de triangles (54 Mo) | allégé à 300 000 (`fast_simplification`, 1,3 s, toujours étanche) |
| Image de référence « non produite » quand le sujet a un accent | noms de fichiers sans accent (`_nom_ascii`) : le générateur écrivait `tÃªte_…` |

Gabarits : support de téléphone, boîte, pot, crochet mural, équerre,
entretoise, plaque, dessous de verre (rond ou polygone, avec rebord), cale,
porte-clés. Tous rendus par OpenSCAD : étanches, un
seul corps, posés sur Z = 0. Une demande hors gabarit garde le chemin libre
(script écrit par Gemma, boucle de correction). `NEO_3D_GABARITS=0` les coupe.

Limite connue du chemin libre (essai « dessous de verre hexagonal avec
rebord », avant son gabarit) : la pièce se construit et s'exporte, mais Gemma
a posé le rebord à plat (aucun relief) et 93,5 mm au lieu de 90 ; la mesure l'a
signalé (« aucun essai conforme »), le contrôle visuel non. Un objet demandé
souvent mérite donc son gabarit.

Non vérifié : Meshy (aucune clé enregistrée), la texture, le pipeline hybride
(sujet réel + pièce fonctionnelle).

## Fiches de connaissances, filtres photo, PowerPoint (11/11/2026, suite)

### Fiches de connaissances

Constat : la page Documents n'affichait aucune fiche. Il y en avait 8 en base,
écrites par un script hors dépôt (`fiche_auto.py`) après chaque réponse avec
recherche web : titre = six premiers mots de la question (« Une photo d'un
boulanger devant son »), résumé = « ## En bref », et la réponse attendait la
fiche (jusqu'à 150 s) avant d'être enregistrée.

Désormais un seul module, `backend/core/fiches.py`, servi par le service
Python (`/api/fiches`, port 8321, `backend/api/routes_neo.py`) :

| Geste | Chemin |
| --- | --- |
| Créer depuis la page Documents (sujet, illustration au choix) | `POST /api/fiches` |
| Créer dans la conversation (« fais-moi une fiche sur… ») | routeur → agent web → outil `fiche_creer` |
| Fiche gardée d'elle-même après une recherche | `depuis_reponse`, en tâche de fond ; le modèle juge si le sujet le mérite (pas pour un cours de bourse) |
| Lire, actualiser, illustrer, exporter en PDF, supprimer | panneau « Fiches de connaissances » (`FichesPanel.tsx`) |
| Réutiliser | une fiche dont le titre recoupe le message est jointe au contexte de la conversation (`persona::fiches_connues`) |

Une fiche : titre de sujet (jamais une question), catégorie, résumé de deux
phrases, « En bref », « Points clés », « À retenir », sources. Mesuré : « le
pont du Gard » en 6 s (recherche, deux pages lues, rédaction par Gemma), 6 faits
datés et chiffrés, 2 sources. Les anciennes fiches restent en base : les ouvrir
puis « Actualiser » les réécrit à partir de la question d'origine.
`E:\ia\fiche_auto.py` n'est plus appelé.

### Filtres et effets photo

Dans l'image en plein écran, « Filtres et effets » : 22 filtres (couleur,
lumière, ambiance, noir et blanc, effets) dans `backend/core/filtres_photo.py`.
À l'ouverture, tous sont rendus d'un coup sur une copie de 960 px (1,1 s pour un
portrait 4K) et gardés par la page : passer de l'un à l'autre, au clic ou aux
flèches, ne demande plus aucun appel. L'intensité superpose l'aperçu à
l'original. « Enregistrer une copie » applique le filtre à la pleine résolution
(1,2 s en 4K) ; l'original n'est jamais modifié. Le même code rend l'aperçu et
l'image finale ; flou, grain et vignette sont proportionnels à l'image.

### PowerPoint

`document_create(type_doc="pptx")` (bibliothèque `python-pptx`, à installer dans
le venv : `pip install python-pptx`) : page de titre, une diapositive par
titre `##`, puces, tableau, notes de l'orateur (« Note : … »), suite automatique
au-delà de 8 lignes. « Une présentation PowerPoint de 5 diapositives sur
l'énergie solaire » : titre + 5 diapositives.

Vérifié dans le navigateur avant déploiement avec le service lancé depuis le
dépôt sur une copie de la base. Non vérifié en production : le tout après
déploiement, l'illustration d'une fiche, l'enregistrement d'une copie filtrée
depuis la page.

### Vérification en production du 11/11/2026 (après déploiement) et deux corrections

Vérifié par le vrai chat et les vraies routes : facture PDF (4,5 s, fichier
créé), devis Excel (formules, TVA, TTC), PowerPoint (titre + 5 diapositives),
support de téléphone (10 s, pièce juste), fiche par la conversation (10 s),
fiches par le service (création 7 s, lecture, export, suppression), filtres
(22 aperçus en 1,2 s, copie 4K en 1,4 s), import de plusieurs documents.

Deux défauts trouvés :

- **Tête de lion par le chat : 442 s et un échec.** `generate_mesh_from_text`
  n'avait aucun des mots qui donnent dix minutes à un outil
  (`tools.rs::delai_outil`) : tué à 120 s, relancé trois fois par le modèle.
  « mesh » rejoint la liste des outils longs.
- **Vidéo : 120 s sur 210 perdues à relire le modèle sur le disque dur.** La
  trace `clip wan` a montré « read: 121 s » à chaque plan, même juste après
  une lecture des fichiers à 3 Go/s. Suivi des disques : sd-cli lisait 5 Go à
  42 Mo/s sur le disque d'archives. Les fichiers de Wan, pré-lus au début du
  plan, étaient les plus anciens du cache ; l'image du plan, puis les 11 Go que
  sd-cli réserve d'un coup, faisaient jeter le modèle (48 Go de mémoire vive :
  Wan 12,8 Go + fichiers de l'image 11,5 Go + Gemma 8 Go ne tiennent pas avec
  11 Go réservés). Ils sont maintenant relus juste avant le lancement
  (`_prechauffer_cache(…, force=True)`, 4 s) : chargement en 5 s, aucune
  lecture sur le disque dur. Plan de 3 s : **235 s → 115 s** (mesuré depuis la
  console avec le code du dépôt ; même temps en console et en production avant
  la correction : 233 et 235 s).

Après le second déploiement (11/11/2026, 16 h 22), en production :

- tête de lion par le chat : `generate_mesh_from_text` aboutit en 184 s
  (257 s avec le contrôle visuel), pièce étanche de 80 mm ;
- plan vidéo de 3 s : chargement du modèle 125 s → 3,7 s ; **119 s** à chaud
  (235 s avant), 189 s juste après une génération 3D qui avait vidé le cache
  (la relecture forcée prend alors 42 s sur le disque d'archives, au lieu de
  120 s de lecture désordonnée).

Reste trouvé à cette occasion, corrigé dans le dépôt mais pas encore déployé :
le lion de production gardait 1,5 million de triangles (71 Mo), parce que
`fast_simplification` le rendait non étanche à toutes les cibles et que
l'allègement était donc refusé. Second recours ajouté : `pymeshlab`,
topologie préservée — 300 000 triangles, étanche, même volume, en 15 à 21 s
(vérifié sur les deux lions).

### « Fichiers de Néo » : les productions du chat n'y étaient pas (11/11/2026)

Le panneau de la page Documents ne listait que les anciens dossiers
`Documents\Neo`. Tout ce que Néo range aujourd'hui dans l'espace de travail
(`workspace\images`, `documents`, `modeles_3d`, `pipeline_3d`) n'apparaissait
nulle part : ni les images du chat, ni les PDF, Word, Excel et PowerPoint, ni
les modèles 3D.

`/api/video/neo-files` liste maintenant ces quatre dossiers en plus des
anciens (`routes_video._racines_listees`), sans les fichiers de travail (essais
`piece_t1`, vues de contrôle, images de référence, copies d'écran, notes
internes), avec une vignette pour les images (`_ecran.jpg`) et les modèles 3D
(`_iso.png`, `_apercu.png`). Les mêmes dossiers, et eux seuls, sont servis et
supprimables ; le reste de `workspace` (essais des agents, auto-amélioration)
ne l'est pas. Dans la page, « ouvrir » un Word, Excel, PowerPoint ou un modèle
3D le télécharge. Sur la machine de référence : 201 fichiers listés, dont
6 documents et 37 modèles 3D, contre aucun de ces dossiers auparavant.
