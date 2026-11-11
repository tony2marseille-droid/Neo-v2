# -*- coding: utf-8 -*-
"""Tests du routage d'agent (regles regex uniquement : le classifieur LLM
de niveau 2 est neutralise, aucun appel reseau)."""
import pytest

from backend.core import router


@pytest.fixture(autouse=True)
def sans_llm(monkeypatch):
    monkeypatch.setattr(router, "_llm_intention", lambda message: "")


@pytest.mark.parametrize("message", [
    "peux-tu me dire qui a gagné la coupe du monde 2018",
    "je veux savoir quelle heure il est à Tokyo",
    "je voudrais comprendre la photosynthèse",
    "pouvez-vous m'expliquer la relativité restreinte",
    "imagine que tu es un pirate et raconte ta journée",
])
def test_tournure_modale_seule_ne_route_pas_vers_image(message):
    agent, _raison = router.route_agent(message)
    assert agent != "image", message


@pytest.mark.parametrize("message", [
    "génère une image d'un chat",
    "dessine un dragon",
    "peux-tu générer une image d'un coucher de soleil",
    "je voudrais une illustration d'une forêt",
])
def test_demande_de_creation_route_vers_image(message):
    agent, raison = router.route_agent(message)
    assert agent == "image", (message, raison)
    assert "generer_image" in raison


@pytest.mark.parametrize("message", [
    "Si 5 machines fabriquent 5 pièces en 5 minutes, combien de minutes faut-il à 100 machines ?",
    "Pourquoi les abeilles construisent-elles des alvéoles hexagonales ?",
    "Comment on fabrique du verre ?",
])
def test_question_avec_verbe_de_creation_ne_genere_pas_d_image(message):
    agent, raison = router.route_agent(message)
    assert agent != "image", (message, raison)


# Demandes reelles du 09/11/2026 (Telegram) restees sans image : Neo avait
# repondu « voici l'image » sans jamais appeler l'outil.
@pytest.mark.parametrize("message", [
    "Fait moi un visage d’un jeune avec un dégradé au laser",
    "Un nain barbu sexy guerrier sanguinaire en image",
    "Imagine une ville futuriste sous la pluie",
    "mon chat roux en photo",
])
def test_demandes_reelles_d_image_routees(message):
    agent, raison = router.route_agent(message)
    assert agent == "image", (message, raison)


@pytest.mark.parametrize("message,attendu", [
    ("Quel temps mercredi sur Aix", "systeme"),
    ("va-t-il pleuvoir demain à Marseille", "systeme"),
    ("Quel temps faut-il pour cuire des pâtes ?", "general"),
    ("Combien de pixels dans une image en 4K ?", "general"),
])
def test_meteo_vers_agent_systeme(message, attendu):
    agent, raison = router.route_agent(message)
    assert agent == attendu, (message, raison)


@pytest.mark.parametrize("message", [
    "Je n'arrive pas à me motiver pour reprendre le sport. Tu m'aides à trouver un plan réaliste ?",
    "Aide-moi à trouver une solution pour mon budget",
    "Je cherche une idée de cadeau pour ma mère",
])
def test_trouver_ou_chercher_sans_image_reste_une_conversation(message):
    agent, _ = router.route_agent(message)
    assert agent != "image"


@pytest.mark.parametrize("message", [
    "Trouve-moi une photo de la tour Eiffel",
    "Montre-moi un panda roux",
    "Cherche des images de chats persans",
])
def test_voir_une_image_toujours_reconnu(message):
    agent, _ = router.route_agent(message)
    assert agent == "image"


@pytest.mark.parametrize("message", [
    "Combien font 250 euros en dollars ?",
    "convertis 1000 francs suisses en euros",
    "c'est quoi le taux de change euro livre aujourd'hui",
    "100 € en $ ça fait combien",
])
def test_change_vers_systeme(message):
    assert router.route_agent(message)[0] == "systeme"


@pytest.mark.parametrize("message", [
    "Quel est le code postal d'Annecy ?",
    "Où se trouve la mairie d'Annecy ?",
    "donne-moi l'adresse de la gare Saint-Charles",
])
def test_adresse_vers_systeme(message):
    assert router.route_agent(message)[0] == "systeme"


@pytest.mark.parametrize("message", [
    "Combien font 15 * 23 ?",
    "Quelle est ton adresse mail ?",
    "J'ai dépensé 40 euros au restaurant hier, c'était bon",
])
def test_pas_de_faux_positif_change_adresse(message):
    assert router.route_agent(message)[0] != "systeme"


@pytest.mark.parametrize("message", [
    "Combien de mémoire vive mon PC utilise-t-il en ce moment ?",
    "Ma carte graphique est à quelle température ?",
    "Il reste combien de place libre sur mon disque ?",
    "Le processeur est chargé à combien actuellement ?",
])
def test_etat_machine_vers_systeme(message):
    assert router.route_agent(message)[0] == "systeme"


@pytest.mark.parametrize("message", [
    "Combien de RAM faut-il pour faire tourner un LLM de 14 milliards de paramètres ?",
    "Quelle carte graphique me conseilles-tu pour jouer en 4K ?",
])
def test_conseil_materiel_pas_vers_systeme(message):
    assert router.route_agent(message)[0] != "systeme"


@pytest.mark.parametrize("message", [
    "Combien font 17 fois 23 ?",
    "Calcule 17,5 % de 2340",
    "Combien font 1234 + 5678 - 999 ?",
    "Calcule la racine carrée de 2025",
])
def test_calcul_simple_par_neo(message):
    assert router.route_agent(message)[0] == "general"


@pytest.mark.parametrize("message", [
    "Calcule l'écart-type de ces valeurs avec Python : 3, 7, 9, 12",
    "Calcule la factorielle de 1000",
    "Calcule tous les nombres premiers jusqu'à 10 000",
    "Calcule la moyenne de la colonne B du fichier ventes.csv",
])
def test_calcul_lourd_par_dev(message):
    assert router.route_agent(message)[0] == "dev"


_LLM_INTENTION_REEL = router._llm_intention


# Demandes SANS verbe : le nom du media ouvre la phrase (« Une photo d'un
# boulanger devant son four » etait prise pour une conversation, 11/11/2026).
@pytest.mark.parametrize("message", [
    "Une photo d'un boulanger devant son four",
    "Un portrait d'une femme rousse de 30 ans",
    "Portrait photo d'un vieux pêcheur breton, bonnet de laine",
    "une image de dragon au-dessus d'un château",
    "Photo réaliste d'un chat roux sur un toit de Paris",
    "Une belle illustration avec un renard dans la neige",
    "Deux photos d’un guerrier viking sous la neige",
    "Un fond d'écran de montagne au lever du soleil",
])
def test_demande_nominale_route_vers_image(message):
    agent, raison = router.route_agent(message)
    assert agent == "image", (message, raison)
    assert "demande nominale" in raison or "generer_image" in raison


@pytest.mark.parametrize("message", [
    "La photo de profil ne s'affiche pas sur mon site",
    "Une photo de vacances, c'est combien de Mo en moyenne ?",
    "Une image de marque se construit comment ?",
    "Portrait de Dorian Gray, c'est de qui ?",
    "J'ai une photo de mon chat, décris-la",
    "Le portrait de la situation est sombre",
])
def test_phrase_avec_un_media_qui_n_est_pas_une_demande(message, monkeypatch):
    monkeypatch.setattr(router, "_llm_intention", lambda m: "autre")
    agent, raison = router.route_agent(message)
    assert "demande nominale" not in raison, (message, raison)


def test_classifieur_prend_le_modele_deja_charge(monkeypatch):
    import io
    import json
    import urllib.request

    monkeypatch.setattr(router, "_llm_intention", _LLM_INTENTION_REEL)
    envois = []

    def ouvrir(req, timeout=0):
        if isinstance(req, str):  # /api/ps
            return io.BytesIO(json.dumps({"models": [{"name": "gemma4:12b"}]}).encode())
        envois.append(json.loads(req.data.decode()))
        return io.BytesIO(json.dumps({"message": {"content": "generer_image"}}).encode())

    class _Ctx(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Ctx(ouvrir(req, timeout).read()))
    monkeypatch.delenv("LOCALAI_ROUTER_MODEL", raising=False)
    assert router._llm_intention("un truc avec une image") == "generer_image"
    assert envois[0]["model"] == "gemma4:12b" and envois[0]["think"] is False
    assert envois[0]["options"]["num_ctx"] == 16384
    monkeypatch.setenv("LOCALAI_ROUTER_MODEL", "llama3.1:8b")
    router._llm_intention("un truc avec une image")
    assert envois[1]["model"] == "llama3.1:8b" and "think" not in envois[1]


# Documents a produire en fichier : agent bureau (jamais choisi avant le 11/11/2026).
@pytest.mark.parametrize("message", [
    "Rédige une lettre de résiliation en PDF",
    "Fais-moi un CV au format Word",
    "Crée un tableau Excel de mes dépenses du mois",
    "Génère un rapport PDF sur les ventes",
    "Fais-moi un devis en PDF pour 3 jours de travail",
    "Prépare un tableur de suivi de budget",
])
def test_document_a_produire_vers_bureau(message):
    agent, raison = router.route_agent(message)
    assert agent == "bureau", (message, raison)


@pytest.mark.parametrize("message", [
    "Écris-moi une lettre de motivation",            # texte dans le chat, pas de fichier
    "Résume ce PDF",                                 # document existant
    "Que dit mon contrat PDF sur la résiliation ?",
    "Corrige le rapport word joint",
    "Comment faire un tableau Excel ?",              # question
    "Écris un script Python qui crée un fichier Excel",  # code
    "génère une image d'un chat",
])
def test_pas_de_bureau_sans_fichier_a_produire(message):
    agent, raison = router.route_agent(message)
    assert agent != "bureau", (message, raison)


# « trancher » n'est de la 3D qu'avec son objet.
@pytest.mark.parametrize("message,attendu", [
    ("Donne-moi la recette pour une tranche de pain perdu", "general"),
    ("Mon imprimante ne marche plus, que faire ?", "general"),
    ("tranche le modèle pour ma Bambu", "cad3d"),
    ("Fais-moi un support de téléphone à imprimer en 3D", "cad3d"),
    ("convertis ce mesh en gcode", "cad3d"),
])
def test_3d_seulement_quand_c_en_est(message, attendu):
    assert router.route_agent(message)[0] == attendu, message


# Actualite et faits recents : agent web (Neo va chercher au lieu de repondre de memoire).
@pytest.mark.parametrize("message", [
    "Quelles sont les actualités tech cette semaine ?",
    "Qui a gagné le Grand Prix de F1 hier ?",
    "Quel est le cours du bitcoin ?",
    "Date de sortie de GTA 6 ?",
    "Résultat du match OM PSG",
    "Donne-moi les dernières nouvelles sur l'IA",
    "cherche sur internet le prix d'une RTX 5090",
])
def test_actualite_vers_agent_web(message):
    agent, raison = router.route_agent(message)
    assert agent == "web", (message, raison)


@pytest.mark.parametrize("message,attendu", [
    ("Quelle est la capitale de l'Australie ?", "general"),      # savoir stable
    ("Qui a gagné la coupe du monde 1998 ?", "general"),
    ("Mon fils a gagné son match hier", "general"),
    ("Quoi de neuf ?", "general"),
    ("météo à Marseille", "systeme"),
    ("cherche sur le web une photo de chat persan", "image"),   # une image a montrer
    ("Fais une image sur l'actualité du jour", "image"),
    ("écris un script qui récupère les news", "dev"),
])
def test_pas_de_recherche_web_a_tort(message, attendu):
    assert router.route_agent(message)[0] == attendu, message
