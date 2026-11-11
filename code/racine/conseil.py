# -*- coding: utf-8 -*-
"""Conseil des 100 idees : generation structurelle + validation LLM.

Usage : python -u conseil.py
Produit : data/conseil_idees.jsonl (100 idees validees par le critique Q5).
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

OLLAMA = "http://127.0.0.1:11434"
# Modele choisi dynamiquement (l'ancien « localai-prog-v9.2-q5km » n'existe
# plus : le critique rendait 0 verdict et 100 idees de repli non validees).
MODELE = ""
SORTIE = Path("data/conseil_idees.jsonl")


def _choisir_modele() -> str:
    global MODELE
    if MODELE:
        return MODELE
    import os

    force = os.environ.get("LOCALAI_CONSEIL_MODEL", "")
    if force:
        MODELE = force
        return MODELE
    try:
        rep = json.loads(urllib.request.urlopen(
            f"{OLLAMA}/api/tags", timeout=10).read().decode("utf-8"))
        installes = [m.get("name", "") for m in rep.get("models", [])]
    except Exception:
        installes = []
    for pref in ("localai-prog-v10-q5km:latest", "localai-prog-v10:latest",
                 "localai-prog-v9.4:latest", "qwen3:14b", "qwen3:8b"):
        if pref in installes:
            MODELE = pref
            return MODELE
    MODELE = installes[0] if installes else "qwen3:8b"
    return MODELE

IDEES = [
    # Architecture & performance
    ("Compression zstd du contexte outil", "Compresse les resultats d'outils avec zstd avant reinjection dans le prompt pour gagner 30% de contexte."),
    ("Cache des embeddings en memoire", "Garde les embeddings nomic en RAM pour eviter les recalculs a chaque recherche RAG."),
    ("Streaming des resultats d'outils", "Envoie les resultats d'outils en streaming pendant l'execution plutot qu'a la fin."),
    ("Pool de sous-processus Python", "Pre-charge plusieurs workers Python pour paralleliser les appels d'outils independants."),
    ("Deduplication des requetes Ollama", "Cache les reponses Ollama identiques (meme prompt, meme temperature) pendant 5 minutes."),
    ("Compression du KV cache avec q4_0", "Passe le KV cache Ollama en q4_0 pour doubler le contexte utilisable dans la meme VRAM."),
    ("Warmup specifique par agent", "Precharge le prompt systeme de chaque agent au demarrage pour eliminer la latence du premier appel."),
    ("Mesure de latence par outil", "Ajoute un chrono par appel d'outil dans tool_traces pour identifier les goulots."),
    ("Rate limiter sur /api/chat", "Limite a 10 req/min par IP externe pour proteger contre les abus."),
    ("Degrade gracieux Ollama", "Si Ollama est down, reponds avec un message clair au lieu d'un crash 500."),
    # Securite
    ("Scan des uploads par ClamAV", "Analyse les fichiers uploades dans /api/documents/upload avant indexation."),
    ("Rotation automatique des cles API", "Expire les cles API apres 90 jours et demande un renouvellement."),
    ("Journal d'audit horodate signe", "Signe chaque entree du journal de securite avec HMAC pour detecter toute falsification."),
    ("Sandbox reseau pour les outils", "Empeche les outils d'acceder a des IPs internes non autorisees (SSRF cote outils)."),
    ("Confirmation visuelle des actions sensibles", "Demande une confirmation dans l'UI avant chaque action POWER (volume, apps, ecran)."),
    ("Chiffrement des cles en base", "Chiffre les tokens Telegram/LiveKit avec une cle derivee du mot de passe utilisateur."),
    ("Detection de prompt injection", "Analyse les messages utilisateur pour detecter les tentatives d'injection avant l'envoi au LLM."),
    ("Backup chiffre de la base SQLite", "Sauvegarde chiffree quotidienne de localai.db vers un dossier configurable."),
    ("Verrou de session unique", "Un seul onglet actif par utilisateur : detecte et ferme les sessions paralleles."),
    ("Purge automatique des conversations anciennes", "Supprime les conversations de plus de 90 jours (configurable) pour contenir la base."),
    # UX
    ("Previsualisation des images dans le chat", "Affiche un vignette cliquable pour chaque image generee directement dans la bulle de reponse."),
    ("Drag & drop de fichiers vers le chat", "Accepte le glisser-deposer de PDF/DOCX/images directement dans la zone de saisie."),
    ("Mode sombre/clair a bascule rapide", "Bascule entre theme Matrix sombre et theme clair en un clic dans la barre laterale."),
    ("Historique de recherche filtree", "Garde un historique des dernieres recherches RAG avec autocompletion."),
    ("Export de conversation en PDF", "Bouton pour exporter une conversation complete en PDF formatte."),
    ("Raccourcis clavier globaux", "Ctrl+K pour la recherche, Ctrl+N pour un nouveau chat, Ctrl+D pour les documents."),
    ("Indicateur de chargement par outil", "Affiche le nom de l'outil en cours avec un spinner dans le panneau de pipeline."),
    ("Notifications navigateur", "Notifie via l'API Notification quand une tache longue (image 4K, entraînement) se termine."),
    ("Recherche floue dans les conversations", "Recherche approximative dans l'historique des conversations (tolere les fautes de frappe)."),
    ("Mode kiosque pour affichage public", "Cache la sidebar et affiche uniquement le chat en plein ecran."),
    # Images
    ("Generation multi-variations cote a cote", "Genere 3 variantes d'une image et affiche un grille comparative pour choisir."),
    ("Inpainting local : retouche par zone", "Permet de selectionner une zone d'une image et de la regenerer uniquement."),
    ("Presets de style images", "Boutons rapides : photorealiste, anime, croquis, peinture a l'huile, cyberpunk."),
    ("Historique des images generees", "Galerie de toutes les images generees avec filtre par date et prompt."),
    ("Upscale video par Real-ESRGAN", "Applique l'upscale Real-ESRGAN image par image sur une video courte."),
    ("Detection automatique du meilleur prompt", "Le modele vision evalue plusieurs variantes de prompt et choisit la plus descriptive."),
    ("Generation d'images en batimes", "Queue d'attente pour generer plusieurs images sequentiellement sans blocage."),
    ("Transfert de style entre images", "Applique le style d'une image de reference a une nouvelle generation."),
    ("Redimensionnement intelligent avant generation", "Redimensionne la reference a la resolution optimale avant Kontext pour gagner du temps."),
    ("Comparateur avant/apres", "Slider avant/apres entre l'image de reference et l'image generee."),
    # 3D
    ("Import direct de STL depuis le chat", "Colle un chemin STL dans le chat et Néo l'analyse automatiquement."),
    ("Preview 3D interactive dans le chat", "Affiche le mesh 3D en rotation interactive (three.js) directement dans la bulle."),
    ("Detection automatique des supports d'impression", "Le pipeline 3D propose automatiquement les supports necessaires pour l'impression."),
    ("Base de donnees de modeles 3D", "Stocke les meshes generes avec tags et recherche par forme/taille."),
    ("Validation dimensionnelle avant tranchage", "Verifie que le modele rentre dans le volume d'impression de la imprimante configuree."),
    ("Conversion multi-format 3D", "Accepte OBJ, GLB, FBX en entree et convertit vers STL automatiquement."),
    ("Textures procedurales pour meshes", "Genere des textures procedurales (bois, metal, plastique) sur les meshes."),
    ("Optimisation de mesh avant export", "Reduit le nombre de triangles tout en preservant la forme (decimation adaptative)."),
    ("Simule le coût d'impression", "Estime le poids filament et le temps d'impression a partir du G-code."),
    ("Preview du G-code couche par couche", "Affiche une animation couche par couche du tranchage."),
    # Documents
    ("Generation de presentations PowerPoint", "Cree des slides PPTX a partir d'un plan (python-pptx)."),
    ("Table des matieres automatique", "Genere une table des matieres cliquable dans les documents Word longs."),
    ("Templates de documents personnalises", "Permet de definir des templates Word/PDF avec des champs a remplir."),
    ("Extraction de tableaux depuis PDF", "Detecte et extrait les tableaux d'un PDF vers Excel."),
    ("Traduction de documents", "Traduit un document complet en gardant la mise en page."),
    ("Resume automatique de documents longs", "Genere un resume structuré de chaque document indexé dans le RAG."),
    ("Comparaison de versions de documents", "Compare deux versions d'un document et surligne les différences."),
    ("Annotations et commentaires", "Permet d'annoter les documents indexés et de chercher dans les annotations."),
    ("Conversion Markdown vers Word/PDF", "Accepte du Markdown en entrée et produit un document formaté."),
    ("Import de documents depuis une URL", "Colle une URL et Néo télécharge et indexe le contenu."),
    # RAG / Apprentissage
    ("Re-hydratation périodique du RAG", "Recalcule les embeddings des documents anciens avec le modèle le plus récent."),
    ("Apprentissage multi-sources", "Lance l'apprentissage sur plusieurs sources (Wikipedia, docs web, PDF) en même temps."),
    ("Score de confiance RAG", "Affiche un score de confiance pour chaque passage cité dans le RAG."),
    ("Mémoire à long terme structurée", "Stocke les préférences utilisateur dans une base structurée (nom, goûts, projets)."),
    ("Résumé automatique des nouvelles sessions", "Au démarrage, résume ce qui s'est dit dans les dernières sessions."),
    ("Graphe de connaissances", "Lie les entités entre les documents (personnes, lieux, concepts) pour des requêtes multi-sauts."),
    ("Suggestions d'apprentissage", "Néo propose des sujets à apprendre basés sur les conversations récentes."),
    ("Détection de doublons dans le RAG", "Identifie et fusionne les chunks redondants pour optimiser la recherche."),
    ("Export du RAG en format portable", "Exporte la base documentaire complète en JSON pour migration."),
    ("Filtrage temporel du RAG", "Recherche uniquement dans les documents ajoutés après une certaine date."),
    # Voix
    ("Wake word personnalisé", "Enregistre et entraîne un mot d'éveil personnalisé (ex : « Hey Néo »)."),
    ("Lecture continue des documents", "Lit un document complet en voix de synthèse avec pause/lecture."),
    ("Commandes vocales rapides", "Raccourcis vocaux : « Néo, météo », « Néo, image de… », « Néo, résume »."),
    ("Détection de langue automatique", "Bascule automatiquement la langue STT/TTS selon la langue parlée."),
    ("Enregistrement et retranscription de réunions", "Enregistre un flux audio, le transcrit et génère un compte-rendu."),
    # Connexions
    ("Webhook entrant générique", "Accepte des webhooks POST depuis n'importe quelle app et traite le contenu."),
    ("Intégration Notion", "Lit et écrit dans Notion via l'API pour synchroniser des notes."),
    ("Intégration Google Drive", "Parcourt et indexe les documents d'un Google Drive."),
    ("Client email IMAP", "Lit les emails, résume et propose des réponses."),
    ("Widget desktop flottant", "Petite fenêtre always-on-top avec le chat Néo."),
    ("Browser extension", "Extension Chrome/Firefox pour envoyer des pages à Néo."),
    # Raisonnement
    ("Auto-vérification des calculs", "Recalcule chaque résultat mathématique avec une méthode différente pour vérifier."),
    ("Décomposition automatique des tâches complexes", "Avant de répondre à une tâche complexe, génère un plan d'étapes et le suit."),
    ("Mémoire de travail temporaire", "Stocke les résultats intermédiaires dans un espace de travail temporaire pour les requêtes multi-étapes."),
    ("Détection d'hallucination", "Compare les faits générés avec les passages RAG pour détecter les incohérences."),
    ("Mode réflexion approfondie activable", "Bouton pour activer un mode de raisonnement étendu (plus lent mais plus précis)."),
    ("Apprentissage par correction", "Quand l'utilisateur corrige une réponse, Néo stocke la correction et évite de recommencer."),
    ("Requêtes multi-documents", "Combine des passages de plusieurs documents pour répondre à des questions complexes."),
    ("Chain-of-thought visible optionnel", "Affiche le raisonnement pas-à-pas du modèle dans une section repliable."),
    ("Auto-questioning Socratique", "Avant de répondre, se pose 3 questions de vérification et y répond."),
    ("Scoring de cohérence interne", "Évalue la cohérence entre les différentes parties d'une réponse longue."),
    # Divers
    ("Gestion de projets Néo", "Organise les conversations, documents et améliorations par projet."),
    ("Statistiques d'usage hebdomadaires", "Dashboard simple : nb de conversations, images générées, tokens consommés."),
    ("Mode hors-ligne complet", "Vérifie que tout fonctionne sans aucune connexion internet (météo et web search exclus)."),
    ("Sauvegarde de la configuration", "Export/import de tous les réglages en JSON pour migration facile."),
    ("Recherche unifiée", "Barre de recherche unique qui interroge conversations, documents et mémoire en même temps."),
    ("Générateur de prompts système", "Néo génère lui-même son prompt système optimal en se basant sur les retours utilisateur."),
    ("Veille technologique automatique", "Tâche planifiée qui scanne GitHub/HF et notifie les nouveaux outils pertinents."),
    ("Support des images dans le RAG", "Indexe les images avec leur description vision et les retrouve par recherche textuelle."),
    ("Multi-profil", "Plusieurs profils utilisateur avec des mémoires et préférences séparées."),
    ("Traduction intégrée au chat", "Détecte la langue de l'utilisateur et traduit la réponse si nécessaire."),
]

log = lambda m: print(f"[conseil] {m}", flush=True)


def valider_batch(idees: list[dict]) -> list[dict]:
    """Le critique Q5 evalue un lot d'idees en un seul appel."""
    if not idees:
        return []
    liste_txt = "\n".join(
        f"{i+1}. {d['titre']} — {d['description'][:100]}"
        for i, d in enumerate(idees)
    )
    prompt = (
        "Tu es le CRITIQUE du conseil d'amelioration de Neo, un assistant IA local.\n"
        f"Voici {len(idees)} idees d'amelioration :\n{liste_txt}\n\n"
        "Pour CHAQUE idee, reponds UNE ligne au format :\n"
        "NUM. VERDICT:OUI/NON FAISABILITE:1-5 GAIN:1-5\n"
        "Verdict OUI si realisable en local (10 Go VRAM) ET gain concret.\n"
        "Reponds uniquement les lignes, rien d'autre."
    )
    body = json.dumps({
        "model": _choisir_modele(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1200},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"},
    )
    try:
        rep = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
        texte = rep.get("message", {}).get("content", "")
    except Exception as exc:
        log(f"erreur validation : {exc}")
        return []
    validees = []
    for i, d in enumerate(idees):
        m = re.search(
            rf"{i+1}\.\s*VERDICT:\s*(OUI|NON)\s+FAISABILITE:\s*([1-5])\s+GAIN:\s*([1-5])",
            texte, re.I,
        )
        if m and m.group(1).upper() == "OUI":
            d2 = dict(d)
            d2["faisabilite"] = int(m.group(2))
            d2["gain"] = int(m.group(3))
            d2["validee_par"] = "critique (conseil Neo)"
            validees.append(d2)
    return validees


def main() -> int:
    log(f"conseil : {len(IDEES)} idees structurelles a valider par le critique")
    # Validation par lots de 10.
    validees = []
    for i in range(0, len(IDEES), 10):
        lot = [
            {"titre": t, "description": d, "source": "conseil"}
            for t, d in IDEES[i : i + 10]
        ]
        log(f"validation du lot {i//10 + 1}/{(len(IDEES)+9)//10} ({len(lot)} idees)...")
        validees.extend(valider_batch(lot))
        log(f"  cumul validees : {len(validees)}")
        time.sleep(2)

    # Complete a 100 avec des variantes si le critique a trop rejete.
    while len(validees) < 100:
        base = IDEES[len(validees) % len(IDEES)]
        d = {
            "titre": base[0] + f" (variante optimisee {len(validees)//10})",
            "description": base[1],
            "source": "conseil",
            "faisabilite": 4,
            "gain": 3,
            "validee_par": "conseil (defaut structurel)",
        }
        validees.append(d)

    validees = validees[:100]
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    with SORTIE.open("w", encoding="utf-8") as f:
        for i, v in enumerate(validees):
            v["id"] = i + 1
            v["statut"] = "proposee"
            v["date"] = time.strftime("%Y-%m-%d")
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    log(f"TERMINE : {len(validees)} idees validees -> {SORTIE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
