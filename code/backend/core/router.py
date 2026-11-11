"""Routage automatique des questions vers le modele le plus adapte.

Logique : detecter l'intention de la question puis choisir le meilleur
modele installe selon la capacite requise, en privilegiant le plus gros
modele pour les intentions exigeantes (code, fait precis, raisonnement)
et le plus rapide pour la conversation simple.
"""

import re

CODING_HINTS = re.compile(
    r"\b(code|coder|codage|programme|programmation|developper|développer|python|"
    r"javascript|typescript|typescript|java\b|c\+\+|c#|rust|golang|sql|html|css|"
    r"react|vue|angular|node|django|flask|fastapi|docker|git\b|github|"
    r"fonction|classe|methode|méthode|algorithme|bug|debug|deboguer|déboguer|"
    r"exception|traceback|compil|script|api|rest|json|regex|refactor|"
    r"ide\b|vs ?code|terminal|commande shell|powershell|bash)\b",
    re.IGNORECASE,
)
# « code » au sens courant (code d'acces, code postal, mot de passe...) n'est
# PAS une demande de programmation : ces tournures ne doivent pas router vers
# l'agent dev (sinon « quel est le code d'acces... » part en lecture de
# fichiers au lieu du RAG).
NON_CODE_CONTEXT = re.compile(
    r"\bcode\s*(?:d['’]?\s*acc[eè]s|secret|postal|postaux|pin|promo|"
    r"de\s+(?:la\s+)?route|de\s+s[eé]curit[eé]|confidentiel|"
    r"d['’]?\s*activation)\b|\bmot\s+de\s+passe\b|\bidentifiant\b",
    re.IGNORECASE,
)
FACTUAL_HINTS = re.compile(
    r"^\s*(c(?:\s*')?\s*est\s*quoi|qu(?:\s*')?\s*est[\s'-]*ce que|qui (?:est|etait|était)|"
    r"que (?:signifie|veut dire)|quelle(?:s)? (?:sont|est)|o[uù] se trouve|"
    r"comment fonctionne|pourquoi\b|definis?|definition|définition|explique[- ]moi|"
    r"parle[- ]moi (?:de|du|des|d')|donne[- ]moi des informations?|histoire du|liste des)\b",
    re.IGNORECASE,
)
REASONING_HINTS = re.compile(
    r"\b(demontre|démontre|prouve|raisonnement|logique|enigme|énigme|resolve|résous|"
    r"calcul complexe|analyse approfondie|strategie|stratégie|compare\s+\w+\s+et\b|"
    r"avantages et inconvénients|avantages et inconvenients|maths?\b|equation|équation)\b",
    re.IGNORECASE,
)
MATH_HINTS = re.compile(
    r"\b(combien font|calcule|résultat de|resultat de|\d+\s*[\+\-\*x×\/÷]\s*\d+)\b",
    re.IGNORECASE,
)
CALCUL_LOURD_HINTS = re.compile(
    r"\b(?:python|script|programme|code|ex[ée]cute|simul\w*|fichier|csv|excel|tableur|"
    r"statistiques?|[ée]cart[- ]type|variance|r[ée]gression|matrices?|int[ée]grale|d[ée]riv[ée]e|"
    r"factorielle\s+de\s+\d{3,}|nombres?\s+premiers?\s+(?:jusqu|inf[ée]rieurs?|entre)|"
    r"tous\s+les|liste\s+des|pour\s+chaque|boucle)\b|\d{9,}",
    re.IGNORECASE,
)
SUMMARY_HINTS = re.compile(
    r"\b(resume|résume|résumé|synth[eé]tise|tldr|en bref|points cles|points clés)\b",
    re.IGNORECASE,
)
CREATIVE_HINTS = re.compile(
    r"\b(poeme|poème|poesie|poésie|chanson|histoire|récit|recit|conte|blague|"
    r"haiku|sonnet|nouvelle courte|invente[- ]moi)\b",
    re.IGNORECASE,
)
SMALLTALK_RE = re.compile(
    r"^\s*(salut|bonjour|bonsoir|coucou|hello|hi\b|merci|au revoir|ça va|ca va|"
    r"hey\b|yo\b|super|ok\b|d'accord)\b[\s!.?]*$",
    re.IGNORECASE,
)
CAD3D_HINTS = re.compile(
    r"\b(imprim\w*[^.?!]{0,20}3\s*d|impression\s+3\s*d|imprime[-\s]?moi|\.stl\b|\bstl\b|"
    # « trancher » seulement avec son objet 3D : « une tranche de pain perdu »
    # partait en conception 3D (11/11/2026).
    r"g-?code\b|\bscad\b|slicer\b|tranchage|trancheur|"
    r"tranch(?:e[rsz]?|ons)\b[^.?!]{0,30}\b(?:mod[eè]le|stl|3mf|pi[eè]ce|fichier|mesh|maillage|objet)|"
    r"(?:objet|mod[eè]le|pi[eè]ce|design)\s+(?:en\s+)?3\s*d(?:\b|$)|"
    r"3\s*d\s*(?:objet|mod[eè]le|pi[eè]ce)|con[cç]ois[-\s]?moi\s+un|"
    r"mesh\b|maillage|cad\b|openscad|blender)\b",
    re.IGNORECASE,
)
# Objet a FABRIQUER en 3D, dit simplement : « fais-moi un support de telephone
# en 3D a imprimer » ne contenait aucun des mots ci-dessus et partait chez
# l'assistant general, qui repondait par du code sans creer de fichier (essai
# du 11/11/2026). Un media 2D (« une image en 3D », « un logo style 3D ») reste
# une image.
OBJET_3D_HINTS = re.compile(
    r"\ben\s+3\s*d\b|\b3\s*d\b[^.?!]{0,40}\bimprim|\bimprimante\s+3\s*d\b|\b3mf\b",
    re.IGNORECASE,
)
MEDIA_2D_HINTS = re.compile(
    r"\b(?:images?|dessins?|illustrations?|logos?|rendus?|photos?|affiches?|animations?|"
    r"vid[ée]os?|films?|fond\s+d['’][eé]cran|wallpaper|style|pixar|cartoon)\b",
    re.IGNORECASE,
)
# VOIR une photo/image EXISTANTE (recherche web + telechargement) :
# verbe de consultation + photo/portrait/image de quelque chose.
# L'ancienne variante « une photo de X » (sans verbe) est retiree : elle
# prenait « j'ai une photo de mon chat » pour une demande de recherche web.
# « trouver / chercher » n'en font plus partie : « trouver un plan réaliste »,
# « chercher une solution » partaient vers l'agent image (banc du 09/11/2026).
# Avec un mot visuel (« trouve-moi une photo de… »), VOIR_IMAGE_STRONG suffit.
VOIR_IMAGE_HINTS = re.compile(
    r"\b(?:montre|montrer|affiche|afficher|"
    r"fais(?:[- ]moi)?\s+voir|montre[- ]moi|fais[- ]moi\s+(?:une\s+)?(?:photo|image)|"
    r"j(?:e\s+)?(?:voudrais|aimerais)\s+voir|[aà]\s+quoi\s+ressemble)\b"
    r"\s+(?:de|du|des|d')?\s*\w",
    re.IGNORECASE,
)
VOIR_IMAGE_STRONG = re.compile(
    r"\b(montre|montrer|affiche|afficher|trouve|trouver|cherche|chercher|"
    r"voir|regarde|ressemble)\b[^.?!]{0,60}\b(photo|photos|portrait|image|"
    r"images|visage|figure)\b",
    re.IGNORECASE,
)
# MODIFIER une image EXISTANTE (edition img2img via edit_image) :
# verbe de modification + cible visuelle proche (image, photo, deco,
# livree, fond, paquet...). Doit passer AVANT generer_image : la base
# existe deja, on ne doit pas regenerer de zero.
EDIT_IMAGE_HINTS = re.compile(
    r"\b(?:applique|appliquer|superpose|superposer|mets\b|mettre|"
    r"repeins?|repeindre|recolore|recolorer|retouche[rz]?|incruste[rz]?|"
    r"modifie[rz]?|change[rz]?|remplace[rz]?)\b[^.?!]{0,80}\b"
    r"(?:image|photo|deco|d[ée]co|livree|livr[ée]e|couleurs?|fond|style|"
    r"motif|paquet|design|logo|texture)\b",
    re.IGNORECASE,
)
# ENVOYER sur Telegram (texte ou image) :
# verbe d'envoi + mention de Telegram/Telegram.
TELEGRAM_SEND_HINTS = re.compile(
    r"\b(?:envoie|envoi|transmet|transmets|partage|partager)\b[^.?!]{0,80}\b"
    r"(?:telegram|telegramme|tg)\b",
    re.IGNORECASE,
)

# TELECHARGEMENT / FETCH URL : "télécharge https://...", "download https://..."
FETCH_URL_HINTS = re.compile(
    r"\b(t[eé]l[eé]charge|download|r[eé]cup[eé]re|fetch)\b.*https?://",
    re.IGNORECASE
)

# RECHERCHE WEB : "cherche sur le web", "cherche sur internet", "recherche web",
# "search web", "google", "duckduckgo" -> route vers image (qui a web_search)
WEB_SEARCH_HINTS = re.compile(
    r"\b(cherche|recherche|search)\b.*\b(web|internet|google|duckduckgo|bing|recherche\s+web|sur\s+le\s+web|sur\s+internet)\b",
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# DETECTION D'INTENTION IMAGE (revue complete)
#
# Ancienne regle : un verbe de creation ET un nom de media devaient apparaitre
# dans la meme phrase. Resultat : « genere un dragon », « dessine un chat » ou
# « fais-moi un logo » n'etaient JAMAIS reconnus (aucun mot « image »), alors
# que « j'ai une image de mon chat » etait pris pour une generation.
#
# Nouvelle logique, en trois etages :
#   1. verbe de dessin explicite (dessine/peins/illustre) -> image, toujours ;
#   2. verbe de creation + media explicite (image/photo/logo/...) -> image ;
#   3. verbe de creation + objet, SANS objet non visuel (code, mot de passe,
#      resume, calcul...) -> image. C'est ce qui couvre « genere un dragon ».
# Tout le reste part au classifieur LLM leger, puis a « general ».
# ---------------------------------------------------------------------------
MEDIA_NOUNS = (
    r"image|images|photo|photos|portrait|portraits|dessin|dessins|"
    r"illustration|illustrations|logo|logos|affiche|affiches|avatar|avatars|"
    r"banni[eè]re|banni[eè]res|wallpaper|fond\s+d['’][eé]cran|"
    r"sticker|stickers|visuel|visuels|poster|posters|ic[oô]ne|ic[oô]nes|"
    r"vignette|vignettes|d[ée]co|pixel\s*art|artwork|render|peinture|fresque|"
    r"mural|sch[ée]ma|planche|caricature|manga|aquarelle"
)
MEDIA_NOUN_RE = re.compile(rf"\b(?:{MEDIA_NOUNS})\b", re.IGNORECASE)

# Assets graphiques nommes (« un logo pour ma chaine », « une affiche de
# concert ») : demande de creation meme sans verbe explicite.
DESIGN_ASSET_HINTS = re.compile(
    r"\b(logo|logos|affiche|affiches|banni[eè]re|banni[eè]res|avatar|avatars|"
    r"illustration|illustrations|visuel|visuels|sticker|stickers|poster|posters|"
    r"vignette|vignettes|ic[oô]ne|ic[oô]nes|enseigne|flyer|d[ée]pliant)\b"
    r"[^.?!]{0,60}\b(pour|de|du|des|sur|avec)\b",
    re.IGNORECASE,
)
# Styles graphiques : « un dragon en pixel art », « un portrait a l'aquarelle ».
# Le style doit etre introduit par « en / style / facon / a la » pour eviter
# de declencher sur « j'aime le pixel art ».
STYLE_IMAGE_HINTS = re.compile(
    r"\b(?:en|style|fa[çc]on|[àa]\s+la|[àa]\s+l['’])\s+"
    r"(?:pixel\s*art|artwork|aquarelle|manga|caricature|peinture|fresque|"
    r"mural|render)\b",
    re.IGNORECASE,
)

# Vrais verbes de creation (imperatif ou infinitif) : seuls, ils suffisent
# a l'etage 3 (« genere un dragon »).
_VERBES_CREATION = (
    r"g[ée]n[eè]re[rsz]?|cr[ée]e[rsz]?|dessine\w*|peins|peindre|illustre\w*|"
    r"esquisse\w*|fabrique[rsz]?|produis|produisez|"
    # « fait moi » : faute courante (Telegram du 09/11/2026, jamais reconnue).
    r"fai[st][- ]moi|fai[st][- ]nous"
)
# Tournures modales ou vagues : elles ne valent demande d'image qu'AVEC un
# nom de media (« peux-tu me dire qui a gagne... » n'est pas une image).
_VERBES_MODAUX = (
    r"imagine\w*|je\s+(?:veux|voudrais|aimerais)|peux[- ]tu|pourrais[- ]tu|"
    r"pouvez[- ]vous|fais[- ](?:une?|un)|fait[- ](?:une?|un)"
)
GEN_VERB_HINTS = re.compile(rf"\b(?:{_VERBES_CREATION})\b", re.IGNORECASE)
# Question (et non commande) : point d'interrogation final ou mot interrogatif.
QUESTION_HINTS = re.compile(
    r"\?\s*$|\b(combien|pourquoi|comment|quel(?:le)?s?|est-ce que|faut-il|peut-on)\b",
    re.IGNORECASE,
)
# Verbe de creation OU modal : sert a reperer le debut de la demande quand un
# media est cite (voir _media_is_direct_object).
_GEN_OU_MODAL_RE = re.compile(
    rf"\b(?:{_VERBES_CREATION}|{_VERBES_MODAUX})\b", re.IGNORECASE
)

# Objets qui ne sont JAMAIS des images : si le verbe de creation porte sur
# l'un d'eux et qu'aucun media n'est cite, aucune image n'est generee.
NON_VISUAL_HINTS = re.compile(
    r"\b(code|script|programme|fonction|classe|m[ée]thode|application|app|"
    r"site|page\s+web|api|test|tests|fichier|fichiers|dossier|rapport|"
    r"r[ée]sum[ée]|synth[èe]se|liste|tableau|texte|pdf|csv|json|sql|"
    r"requ[êe]te|commande|mot\s+de\s+passe|token|cl[ée]\s+api|uuid|nombre|"
    r"calcul|r[ée]sultat|id[ée]e|plan\s+de\s+travail|musique|m[ée]lodie|son|"
    r"voix|vid[ée]o|animation|3d|stl|g-?code|mod[èe]le\s+3d|compte|"
    r"utilisateur|base\s+de\s+donn[ée]es|email|mail|message|titre|nom|"
    r"prompt|traduction|r[ée]ponse|question|po[èe]me|chanson|histoire|"
    r"point|bilan|analyse|revue|compte\s+rendu|pr[ée]sentation|"
    r"comparaison|diagnostic|inventaire|devis|facture)\b",
    re.IGNORECASE,
)

# Verbe de dessin explicite : une image est demandee, sans ambiguite.
DRAW_VERB_HINTS = re.compile(
    r"\b(dessine\w*|peins|peindre|illustre\w*|esquisse\w*|croquis)\b",
    re.IGNORECASE,
)

# GENERER une image nouvelle (IA locale) : verbe de creation + media explicite.
GEN_IMAGE_HINTS = re.compile(
    rf"\b(?:{_VERBES_CREATION}|{_VERBES_MODAUX})\b[^.?!]{{0,100}}"
    rf"\b(?:{MEDIA_NOUNS})\b",
    re.IGNORECASE,
)
# « Un nain barbu guerrier en image », « mon chat en photo » : le media en fin
# de demande vaut commande, sans verbe (Telegram du 09/11/2026).
EN_IMAGE_HINTS = re.compile(
    r"\ben\s+(?:image|images|photo|photos|dessin|illustration|peinture|"
    r"wallpaper|fond\s+d['’][eé]cran|4k|8k)\b\s*[.!]*\s*$",
    re.IGNORECASE,
)
# « Une photo d'un boulanger devant son four », « Portrait d'une femme rousse » :
# la demande COMMENCE par un nom de media suivi de son sujet, sans aucun verbe.
# Aucune regle ne la reconnaissait : elle partait au classifieur LLM (12 s
# d'attente quand il expirait), puis en conversation, et Neo repondait « je ne
# peux pas generer d'images » (essai du 11/11/2026). Article indefini ou
# absent seulement : « la photo de profil ne s'affiche pas » parle d'une image
# existante.
NOMINAL_IMAGE_HINTS = re.compile(
    r"^\s*(?:(?:une?|des|deux|trois|quatre|quelques|plusieurs)\s+)?"
    r"(?:(?:belles?|jolies?|grandes?|petites?|superbes?|magnifiques?|vraies?)\s+)?"
    r"(?:photos?|images?|portraits?|dessins?|illustrations?|peintures?|aquarelles?|"
    r"croquis|caricatures?|fonds?\s+d['’][eé]cran|wallpapers?|affiches?|posters?)"
    r"(?:\s+(?:photos?|r[ée]alistes?|photor[ée]alistes?|hd|4k|artistiques?|"
    r"(?:de|en)\s+studio|en\s+pied|en\s+gros\s+plan|couleurs?|noir\s+et\s+blanc))*"
    r"\s+(?:de|d['’]|du|des|avec|repr[ée]sentant|montrant)\s*\S",
    re.IGNORECASE,
)
# « Imagine une ville futuriste sous la pluie » : scene a visualiser. « Imagine
# que tu es... » (jeu de role) reste une conversation.
IMAGINE_HINTS = re.compile(
    r"^\s*(?:imagine[sz]?|visualise[sz]?)(?:[- ]moi)?\s+(?:un|une|des|le|la|les|l['’])\s",
    re.IGNORECASE,
)
# Meteo : l'agent systeme a l'outil meteo(ville). Sans lui, la conversation
# inventait des temperatures.
_DEVISE = (r"(?:euros?|dollars?|livres?(?:\s+sterling)?|francs?\s+suisses?|yens?|yuans?|"
           r"usd|eur|gbp|chf|jpy|cad|cny|€|\$|£)")
CHANGE_HINTS = re.compile(
    rf"\btaux\s+de\s+change\b|\bcours\s+du\s+(?:dollar|yen|franc\s+suisse|yuan)\b|"
    rf"\d(?:[\d\s.,]*\d)?\s*{_DEVISE}\s+en\s+{_DEVISE}(?!\w)|"
    rf"\b(?:convert\w*|combien\s+(?:fait|font|vaut|valent))\b[^?.!]{{0,40}}{_DEVISE}[^?.!]{{0,25}}\ben\s+{_DEVISE}",
    re.IGNORECASE,
)
ADRESSE_HINTS = re.compile(
    r"\bcode\s+postal\b|\bcoordonn[ée]es\s+gps\b|"
    r"\b(?:o[uù]\s+se\s+(?:trouve|situe)|adresse\s+(?:exacte\s+)?(?:de|du|des))\s+(?:la\s+|le\s+|l')?"
    r"(?:mairie|gare|pr[ée]fecture|poste|h[oô]pital|commissariat|rue|avenue|boulevard|place)\b",
    re.IGNORECASE,
)
# Etat de la machine (outil system_stats) : la ressource ET « ma machine /
# maintenant ». « Combien de RAM pour faire tourner un LLM ? » reste un conseil.
_RESSOURCE = r"(?:m[ée]moire(?:\s+vive)?|ram|processeur|cpu|carte\s+graphique|gpu|vram|disques?(?:\s+durs?)?|ssd)"
_MA_MACHINE = (r"(?:mon\s+(?:pc|ordi\w*)|ma\s+(?:machine|carte)|en\s+ce\s+moment|actuellement|maintenant|"
               r"utilis[ée]e?s?\b|utilise|libres?|occup[ée]e?s?|charg[ée]e?|temp[ée]rature)")
STATS_HINTS = re.compile(
    rf"\b{_RESSOURCE}\b[^?.!]{{0,50}}\b{_MA_MACHINE}|\b{_MA_MACHINE}[^?.!]{{0,50}}\b{_RESSOURCE}\b",
    re.IGNORECASE,
)
METEO_HINTS = re.compile(
    r"\bm[ée]t[ée]o\b|\bquel\s+temps\b(?!\s+(?:faut|pour|de\s+cuisson|met|mettre|prend))|"
    r"\b(?:va|vat?-t-il|est-ce\s+qu'il\s+va)\s+(?:pleuvoir|neiger)\b|\bpr[ée]visions?\b",
    re.IGNORECASE,
)
IMAGE_TOPIC_HINTS = re.compile(
    rf"\b(?:photo|photos|portrait|image|images|dessin|affiche|illustration|"
    rf"visage|acteur|actrice|c[eé]l[ée]brit|{MEDIA_NOUNS})\b",
    re.IGNORECASE,
)


def _media_is_direct_object(msg: str) -> bool:
    """Vrai si le premier media cite est l'objet direct du verbe de creation.

    « genere une image de code » -> True (le media suit directement le verbe).
    « cree un site web avec une image » -> False (le media est secondaire :
    entre le verbe et « image » on trouve « site », objet non visuel).
    """
    verbe = _GEN_OU_MODAL_RE.search(msg)
    if not verbe:
        return False
    media = MEDIA_NOUN_RE.search(msg, verbe.end())
    if not media:
        return False
    entre = msg[verbe.end():media.start()]
    return not NON_VISUAL_HINTS.search(entre)


DOCS_HINTS = re.compile(
    r"\b(mes\s+documents?|le\s+(?:document|pdf|rapport|contrat|facture)|"
    r"dans\s+(?:le|mon)\s+(?:document|pdf|fichier\s+(?:pdf|word|excel)))\b",
    re.IGNORECASE,
)
# DOCUMENT A PRODUIRE EN FICHIER (agent bureau : Word, PDF, Excel). L'agent
# existait mais n'etait jamais choisi : « fais-moi un CV au format Word » partait
# a l'agent image, « genere un rapport PDF » restait un texte dans le chat.
# Il faut un verbe de redaction ET un format de fichier : « ecris-moi une lettre
# de motivation » reste une reponse dans le chat, et « resume ce PDF » (document
# existant) n'est pas concerne.
_VERBES_REDACTION = (
    r"r[ée]dige[rsz]?|[ée]cri[st]|[ée]crire|[ée]crivez|fai[st]|faire|cr[ée]e[rsz]?|g[ée]n[eè]re[rsz]?|"
    r"pr[ée]pare[rsz]?|produis|produire|[ée]tabli[st]|monte[rsz]?|mets?\s+en\s+forme"
)
_FORMATS_BUREAU = r"pdf|docx?|word|xlsx?|excel|odt|ods|pptx?|power\s?point"
BUREAU_HINTS = re.compile(
    rf"\b(?:{_VERBES_REDACTION})\b[^.?!]{{0,120}}"
    rf"(?:\b(?:en|au\s+format|format|fichier|document|sous)\s+(?:un\s+)?(?:{_FORMATS_BUREAU})\b|"
    rf"\b(?:tableau|tableur|classeur|feuille|document|fichier|rapport|lettre|cv|devis|facture|"
    rf"compte[- ]rendu|note|fiche|pr[ée]sentation)\s+(?:{_FORMATS_BUREAU})\b|\btableur\b|"
    # Une presentation a projeter, meme sans le mot PowerPoint.
    rf"\b(?:power\s?point|diaporama|diapositives?|slides?)\b)",
    re.IGNORECASE,
)
# Fiche de connaissance a creer : un verbe de creation ou de memoire, puis
# « fiche » et son sujet (« sur », « de », « a propos »).
FICHE_HINTS = re.compile(
    r"\b(?:fai[st]|faire|cr[ée]e[rsz]?|r[ée]dige[rsz]?|[ée]cri[st]|pr[ée]pare[rsz]?|g[ée]n[eè]re[rsz]?|"
    r"garde[rsz]?|ajoute[rsz]?|enregistre[rsz]?)\b[^.?!]{0,30}\bfiches?\b[^.?!]{0,25}"
    r"\b(?:sur|de|du|des|d['’]|[àa]\s+propos|concernant)\b",
    re.IGNORECASE,
)
# Document deja la (a lire, resumer, corriger) : jamais l'agent bureau.
DOCUMENT_EXISTANT_HINTS = re.compile(
    r"\b(?:ce|cet|cette|mon|ma|mes|le|la|les|du|dans)\s+(?:\w+\s+)?"
    r"(?:pdf|docx?|document|fichier|rapport|contrat|facture|tableur|classeur)\b[^.?!]{0,40}\b"
    r"(?:dit|contient|parle|indique|mentionne|joint|ci-joint)|"
    r"\b(?:r[ée]sume|lis|analyse|explique|traduis|corrige|relis)\b[^.?!]{0,40}\b"
    r"(?:ce|cet|cette|mon|ma|le|la)\s+(?:\w+\s+)?(?:pdf|docx?|document|fichier|rapport|contrat)\b",
    re.IGNORECASE,
)
# Description d'une image DEJA fournie/possedee (piece jointe) : ni recherche
# web ni generation. « j'ai une image de mon chat, decris-la » ne doit pas
# partir en web_search d'une photo de chat.
IMAGE_POSSESSION_HINTS = re.compile(
    r"\b(?:j'ai|j’ai|je\s+poss[eè]de|voici|voil[àa]|regarde|ci[- ]joint)\b"
    r"[^.?!]{0,60}\b(?:image|photo|dessin|capture|screenshot|scan)\b",
    re.IGNORECASE,
)
DESCRIBE_IMAGE_HINTS = re.compile(
    r"\b(?:d[ée]cris|decris|analyse|explique|identifie|reconnais|regarde|"
    r"qu'est[- ]ce que c'est)\b",
    re.IGNORECASE,
)
DEV_ACTION_HINTS = re.compile(
    r"\b(?:cr[ée]e[rz]?|g[ée]n[eè]re[rz]?|écris|ecris|ajoute|modifie|corrige|refactore|"
    r"compile|exécute|execute|lance|déploie|deploie|installe)\b[^.?!]*\b"
    r"(?:script|programme|fonction|classe|code|application|app\b|site|page|api|test|"
    r"fichier|commande)\b|\bdebug\b|\bdéboguer\b",
    re.IGNORECASE,
)
# Technologies/langages : signe fort de developpement, meme sur un message
# court (« Code un site web en React »).
DEV_STRONG_HINTS = re.compile(
    r"\b(python|javascript|typescript|java|react|vue|angular|node|django|flask|"
    r"fastapi|docker|git|github|sql|html|css|rust|golang|c\+\+|c#|powershell|"
    r"bash|algorithme|fonction|classe|api|json|regex|refactor|bug|debug|"
    r"compil\w*|script)\b",
    re.IGNORECASE,
)
NEWS_HINTS = re.compile(
    r"\b(actualit\w*|nouvelles?\b|news\b|derni[eè]res?\s+(?:nouvelle|version|info)|"
    r"r[eé]cemment|aujourd'hui|en\s+ce\s+moment|cette\s+semaine|m[eé]t[eé]o\b|"
    r"prix\s+(?:de|du|des|d')|cours\s+(?:de|du)|sorti|publi[eé]|date\s+de\s+sortie)\b",
    re.IGNORECASE,
)

# ACTUALITE / FAIT RECENT : la reponse depend de ce qui s'est passe apres
# l'entrainement du modele. Regle volontairement etroite (une question generale
# reste une conversation) : il faut un marqueur d'actualite explicite.
ACTU_HINTS = re.compile(
    r"\b(?:actualit[ée]s?|actus?\b|news\b|derni[eè]res?\s+(?:nouvelles|infos?)|"
    r"quoi\s+de\s+neuf\s+(?:dans|en|sur|chez|c[oô]t[ée])|"
    r"qui\s+a\s+(?:gagn[ée]|remport[ée]|[ée]t[ée]\s+[ée]lue?)\b[^.?!]{0,60}\b"
    r"(?:hier|ce\s+soir|ce\s+week-?end|cette\s+(?:semaine|ann[ée]e|nuit)|derni[eè]re?s?|20[2-9]\d)|"
    r"(?:r[ée]sultats?|score)\s+(?:du|de\s+la|des|de)\s+(?:match|course|grand\s+prix|[ée]lections?|tirage|loto)|"
    r"(?:cours|prix)\s+(?:actuel\s+)?(?:du|de\s+l['’]|de\s+la|des)\s*"
    r"(?:bitcoin|ethereum|btc|eth|p[ée]trole|or\b|actions?|baril|gaz|essence|gazole)|"
    r"derni[eè]re\s+version\s+(?:de|d['’]|du)|date\s+de\s+sortie\s+(?:de|d['’]|du|des)|"
    r"que\s+se\s+passe-t-il\s+(?:en|au|aux|à|a|dans))",
    re.IGNORECASE,
)

# AGENT DEPOT : operations git, tests, exploration de depot/repertoire.
# Doit passer AVANT la regle dev car "git" declenche CODING_HINTS et
# "lance les tests" declenche DEV_ACTION_HINTS.
DEPOT_HINTS = re.compile(
    r"\b(git\s+(?:diff|status|log|commit|branch|add|stage|stash|checkout|"
    r"merge|revert|remote|pull|push|clone)|diff\s+git|commit\b|rollback\b|"
    r"annule\s+les?\s+modifications?|d[ée]p[oô]t\s*(?:git)?\b|"
    r"\brepo\b|repository\b|"
    r"lance?\s+les\s+tests?|ex[ée]cute?\s+les\s+tests?|run\s+tests?|pytest|"
    r"cherche(?:r)?\s+dans\s+(?:le|les)\s+(?:code|fichiers|r[ée]pertoire)|"
    r"liste\s+les\s+fichiers\s+(?:du|de|dans)\s+(?:r[ée]pertoire|d[ée]p[oô]t|"
    r"[a-z]:[/\\]))\b",
    re.IGNORECASE,
)

CAPABILITY_PRIORITY = {
    "coding": ["coding", "general"],
    "reasoning": ["raisonnement", "general"],
    "factual": ["connaissance", "general"],
    "summary": ["rapide", "general"],
    "math": ["general", "coding"],
    "creative": ["general"],
    "chat": ["rapide", "general"],
}


def _size_value(model: dict) -> float:
    size = model.get("parameter_size", "") or model.get("size_label", "")
    digits = re.sub(r"[^\d.]", "", str(size))
    try:
        return float(digits) if digits else 999.0
    except ValueError:
        return 999.0


def classify(message: str) -> str:
    msg = message.strip()
    if len(msg) < 40 and SMALLTALK_RE.match(msg):
        return "chat"
    if CREATIVE_HINTS.search(msg):
        return "creative"
    if MATH_HINTS.search(msg):
        return "math"
    if not NON_CODE_CONTEXT.search(msg) and not re.search(
        r"\bcodes?\s+posta(?:l|ux)\b", msg, re.IGNORECASE
    ):
        if CODING_HINTS.search(msg):
            return "coding"
    if SUMMARY_HINTS.search(msg):
        return "summary"
    if REASONING_HINTS.search(msg):
        return "reasoning"
    if FACTUAL_HINTS.search(msg):
        return "factual"
    return "general"


_GENERALISTES = ("gemma4", "qwen3", "gemma3", "llama3")


def _modele_classifieur() -> str:
    """Modele du classifieur d'intention : LOCALAI_ROUTER_MODEL, sinon le
    generaliste DEJA sur la carte (celui de la conversation), sinon qwen3:8b.

    Avec qwen3:8b impose, Ollama devait le charger a cote du modele de
    conversation : le classifieur expirait (12 s d'attente avant chaque
    reponse) et pouvait chasser le modele de conversation de la carte."""
    import json as _json
    import os as _os
    import urllib.request as _urllib

    force = _os.environ.get("LOCALAI_ROUTER_MODEL", "").strip()
    if force:
        return force
    try:
        from backend.core.config import ollama_url

        with _urllib.urlopen(ollama_url() + "/api/ps", timeout=3) as rep:
            charges = [m.get("name", "") for m in _json.loads(rep.read().decode("utf-8")).get("models", [])]
        for nom in charges:
            if nom.lower().startswith(_GENERALISTES):
                return nom
    except Exception:
        pass
    return "qwen3:8b"


def _llm_intention(message: str) -> str:
    """Niveau 2 : classifieur LLM leger (modele deja charge, temp 0) pour les
    demandes ambigues que les regex n'ont pas tranchees. Retourne une intention
    parmi un ensemble ferme, ou "" si indisponible/erreur. ~1-2 s max."""
    import json as _json
    import os as _os
    import re as _re
    import unicodedata as _unicodedata
    import urllib.request as _urllib

    prompt = (
        "Classifie cette demande utilisateur en UNE SEULE intention, sans "
        "aucun autre texte. Intentions possibles :\n"
        "- voir_image : l'utilisateur veut VOIR une photo/image EXISTANTE "
        "(recherche d'une photo reelle d'une personne, d'un objet, d'un lieu)\n"
        "- modifier_image : il veut MODIFIER une image existante (appliquer "
        "une livree ou une deco, recolorer, changer le fond, ajouter ou "
        "enlever un element sur une image deja la)\n"
        "- generer_image : il veut faire CREER/DESSINER une nouvelle image, "
        "un logo, une illustration, un dessin (meme si le mot « image » "
        "n'apparait pas : « fais-moi un dragon »)\n"
        "- envoyer_telegram : il veut ENVOYER un message ou une image sur Telegram\n"
        "- 3d : conception 3D, piece, STL, impression 3D, modelisation\n"
        "- autre : tout le reste (question, code, discussion, recette, "
        "musique, texte a ecrire...)\n"
        f"Demande : {message[:300]}\n"
        "Reponds UNIQUEMENT par un mot : voir_image, modifier_image, "
        "generer_image, envoyer_telegram, 3d ou autre."
    )
    try:
        from backend.core.config import contexte_pour, ollama_url

        modele = _modele_classifieur()
        corps = {
            "model": modele,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": "60m",
            # Meme contexte que le chat : sinon Ollama rechargerait le modele.
            "options": {"temperature": 0.0, "num_predict": 8,
                        "num_ctx": contexte_pour(modele, 4096)},
        }
        if modele.lower().startswith(("qwen3", "gemma4", "gpt-oss")):
            corps["think"] = False
        body = _json.dumps(corps).encode("utf-8")

        req = _urllib.Request(
            ollama_url() + "/api/chat",
            data=body, headers={"Content-Type": "application/json"},
        )
        # 12 s : assez pour un modele 8B a froid, assez court pour ne pas
        # bloquer le routage si Ollama est occupe par une generation d'image.
        delai = float(_os.environ.get("LOCALAI_ROUTER_TIMEOUT", "12"))
        with _urllib.urlopen(req, timeout=delai) as rep:
            texte = _json.loads(rep.read().decode("utf-8")).get(
                "message", {}).get("content", "").strip().lower()
        # Normalisation : accents retires, espaces/tirets -> underscore, pour
        # accepter « générer image », « generer-image », « GENERER_IMAGE ».
        norm = _unicodedata.normalize("NFKD", texte)
        norm = norm.encode("ascii", "ignore").decode("ascii")
        norm = _re.sub(r"[\s\-]+", "_", norm)
        for intention in ("voir_image", "modifier_image", "generer_image",
                          "envoyer_telegram"):
            if intention in norm:
                return intention
        if _re.search(r"\b3d\b|stl|impression|modelisation", norm):
            return "3d"
        # Dernier recours : mots significatifs, quel que soit l'ordre.
        if "image" in norm or "dessin" in norm or "logo" in norm:
            if "modifier" in norm or "editer" in norm:
                return "modifier_image"
            if "voir" in norm or "montrer" in norm or "trouver" in norm:
                return "voir_image"
            if "gener" in norm or "creer" in norm or "dessin" in norm:
                return "generer_image"
        if "telegram" in norm:
            return "envoyer_telegram"
    except Exception:
        pass
    return ""


def route_agent(message: str) -> tuple[str, str]:
    """Choisit l'agent automatiquement selon le type de demande.

    Ordre de priorite des regles rapides (niveau 1) :
    1. voir_image : montrer/trouver une photo EXISTANTE (avant 3d : une
       demande de photo d'une personne ne doit jamais finir en mesh)
    2. generer_image : dessin explicite, media explicite, ou verbe de
       creation portant sur un objet visuel (« genere un dragon »)
    3. cad3d / docs / dev (regles historiques)
    Niveau 2 : si le message parle d'image/3d sans qu'aucune regle ne
    tranche, un classifieur LLM leger arbitre (~1-2 s).
    """
    msg = message.strip()

    # 0. AGENT DEPOT : git/tests/exploration — PRIORITAIRE, car "affiche le
    #    diff git" doit aller au depot et pas a l'agent image (mot "affiche").
    if DEPOT_HINTS.search(msg):
        return "depot", "agent auto: operations depot (git/tests/exploration)"

    # 0bis. TELECHARGEMENT / FETCH URL : "télécharge https://...", "download https://..."
    # Doit passer AVANT voir_image pour ne pas confondre "télécharge une photo" avec "cherche une photo".
    if FETCH_URL_HINTS.search(msg):
        return "image", "intention=fetch_url (téléchargement d'une URL, web_fetch)"

    # 0bis-b. FICHE DE CONNAISSANCE (« fais-moi une fiche sur le pont du Gard »,
    # « garde une fiche de… ») : l'agent web la redige et l'enregistre. Une
    # fiche a produire en fichier (« une fiche PDF ») reste a l'agent bureau.
    if FICHE_HINTS.search(msg) and not BUREAU_HINTS.search(msg):
        return "web", "intention=fiche (fiche de connaissance a rediger et enregistrer)"

    # 0ter. RECHERCHE WEB demandee en toutes lettres (« cherche sur le web… ») :
    # agent web. Avant voir_image : « cherche une photo » (une image a montrer)
    # n'est pas « cherche sur le web » (une information a trouver). Une image
    # a chercher sur le web reste a l'agent image.
    if WEB_SEARCH_HINTS.search(msg):
        if re.search(r"\b(?:photos?|images?|logo|fond\s+d['’][eé]cran|wallpaper)\b", msg, re.I):
            return "image", "intention=voir_image (image a chercher sur le web)"
        return "web", "intention=recherche_web (demande explicite)"

    # 1. VOIR une photo existante (doit passer AVANT generer_image et 3d :
    #    "montre-moi une photo de X" ne doit jamais devenir un mesh).
    if VOIR_IMAGE_STRONG.search(msg):
        return "image", (
            "intention=voir_image (photo existante : web_search + fetch_image, "
            "pas de generation IA)"
        )

    # 1bis. Audit §7 : « montre-moi / affiche / fais-moi voir <objet> » sans
    # le mot "photo" = VOIR quand meme : on affiche une image web de l'objet,
    # jamais de generation 3D ni 2D (meme si le sujet ressemble a du 3D :
    # "montre-moi un modele 3D de dragon" = image web + proposition de
    # generation ensuite). "montre-moi COMMENT" reste une explication.
    if (VOIR_IMAGE_HINTS.search(msg)
            and not re.search(r"\bcomment\b", msg, re.I)
            and not re.search(
                r"\b(g[eé]n[eè]r\w*|cr[eé]\w+|dessin\w*)\b|"
                r"fais[\s-]+(?:moi[\s]+)?(?:une[\s]+)?"
                r"(?:image|dessin|illustration|affiche|d[eé]co)",
                msg, re.I)):
        return "image", (
            "intention=voir_image (montre-moi : afficher une image web, "
            "aucune generation)"
        )

    # Envoi Telegram (doit passer AVANT generer_image et modifier_image)
    if TELEGRAM_SEND_HINTS.search(msg):
        return "image", "intention=envoyer_telegram (envoi Telegram)"

    # 2. MODIFIER une image existante (edition : la base existe deja,
    #    edit_image doit etre utilise, pas une regeneration complete).
    if EDIT_IMAGE_HINTS.search(msg):
        return "image", "intention=modifier_image (edition d'une image existante)"

    # 2bis. DECRIRE une image fournie/possedee : ni web_search ni generation.
    #    Exclu si un verbe de creation explicite est present (« a partir de
    #    cette photo, genere une aquarelle » reste une generation).
    if (IMAGE_POSSESSION_HINTS.search(msg)
            and DESCRIBE_IMAGE_HINTS.search(msg)
            and not DRAW_VERB_HINTS.search(msg)
            and not GEN_IMAGE_HINTS.search(msg)):
        return "general", "intention=decrire_image (image fournie, sans recherche web)"

    # 2ter. METEO : outil meteo(ville) de l'agent systeme.
    if METEO_HINTS.search(msg) and not GEN_IMAGE_HINTS.search(msg):
        return "systeme", "intention=meteo (outil meteo)"

    # 2quater. CHANGE et ADRESSES : outils taux_change / adresse (donnees
    # officielles BCE et IGN, sans cle) ; avant les calculs (« 250 euros en
    # dollars » n'est pas un calcul a deviner).
    if CHANGE_HINTS.search(msg):
        return "systeme", "intention=change (outil taux_change, taux BCE)"
    if ADRESSE_HINTS.search(msg):
        return "systeme", "intention=adresse (outil adresse, IGN)"
    if STATS_HINTS.search(msg) and not CODING_HINTS.search(msg):
        return "systeme", "intention=etat_machine (outil system_stats)"

    # 2quinquies. ACTUALITE et faits recents : Neo va chercher au lieu de
    # repondre de memoire (son savoir s'arrete a l'entrainement du modele).
    if (ACTU_HINTS.search(msg) and not CODING_HINTS.search(msg)
            and not GEN_IMAGE_HINTS.search(msg) and not DRAW_VERB_HINTS.search(msg)):
        return "web", "intention=actualite (information recente : recherche web)"

    # 3bis. CALCULS : un calcul du quotidien (« combien font 17 x 23 »,
    # « 17,5 % de 2340 ») est resolu par Neo lui-meme, avec reflexion : 14/14
    # justes en 3 s (banc du 09/11/2026), contre ~36 s via l'agent dev, dont le
    # modele remplace Gemma en VRAM (puis ~22 s pour revenir au message suivant).
    # Ce qui demande d'EXECUTER du code (script, fichier, statistiques, tres
    # grands nombres) reste a l'agent dev.
    if MATH_HINTS.search(msg):
        if CALCUL_LOURD_HINTS.search(msg):
            return "dev", "intention=calcul_lourd (execution de code via agent dev)"
        return "general", "intention=calcul_simple (Neo, reflexion)"

    # 3bis-b. OBJET A FABRIQUER EN 3D (« … en 3D », « … 3D a imprimer ») :
    # agent de conception, qui produit le fichier. Jamais pour un media 2D,
    # un dessin ou une question (« c'est quoi l'impression en 3D ? »).
    if (OBJET_3D_HINTS.search(msg) and not MEDIA_2D_HINTS.search(msg)
            and not DRAW_VERB_HINTS.search(msg) and not QUESTION_HINTS.search(msg)):
        return "cad3d", "intention=3d (objet a concevoir ou imprimer en 3D)"

    # 3ter. DOCUMENT A PRODUIRE EN FICHIER (Word, PDF, Excel) : agent bureau.
    # Avant les regles d'image (« fais-moi un CV au format Word » n'en est pas
    # une) ; jamais pour du code ni pour un document deja la.
    if (BUREAU_HINTS.search(msg) and not DOCUMENT_EXISTANT_HINTS.search(msg)
            and not CAD3D_HINTS.search(msg) and not DEV_STRONG_HINTS.search(msg)
            and not QUESTION_HINTS.search(msg)):
        return "bureau", "intention=document (fichier Word/PDF/Excel a produire)"

    # 3. GENERER une image IA (trois etages, voir le bloc de regles plus haut).
    if not CAD3D_HINTS.search(msg):
        if DRAW_VERB_HINTS.search(msg):
            return "image", "intention=generer_image (verbe de dessin)"
        if EN_IMAGE_HINTS.search(msg) and not QUESTION_HINTS.search(msg):
            return "image", "intention=generer_image (« en image » en fin de demande)"
        if IMAGINE_HINTS.search(msg):
            return "image", "intention=generer_image (imagine une scene)"
        if (NOMINAL_IMAGE_HINTS.search(msg) and not QUESTION_HINTS.search(msg)
                and not DEV_ACTION_HINTS.search(msg)):
            return "image", "intention=generer_image (demande nominale : media + sujet)"
        if GEN_IMAGE_HINTS.search(msg) and _media_is_direct_object(msg):
            return "image", "intention=generer_image (media explicite)"
        # Regle faible (un verbe de creation suffit) : jamais sur une QUESTION.
        # « Si 5 machines fabriquent 5 pieces... combien ? » partait en image.
        if (GEN_VERB_HINTS.search(msg) and not NON_VISUAL_HINTS.search(msg)
                and not QUESTION_HINTS.search(msg)):
            return "image", "intention=generer_image (verbe de creation + objet)"

    # 4. Regles historiques.
    if CAD3D_HINTS.search(msg):
        return "cad3d", "agent auto: conception/impression 3D"
    # 4bis. Asset graphique nomme (« un logo pour ma chaine ») ou style
    # visuel (« un dragon en pixel art ») : creation d'image meme sans verbe.
    # On exclut les demandes de dev (« ajoute un logo a mon site ») et les
    # messages qui decrivent une image existante (« j'ai un logo... »).
    if (not DEV_ACTION_HINTS.search(msg)
            and not re.search(
                r"\b(j'ai|j’ai|j'aime|j’aime|j'adore|j’adore|voici|voil[àa]|"
                r"c'est|c’est)\b", msg, re.I)
            and (DESIGN_ASSET_HINTS.search(msg) or STYLE_IMAGE_HINTS.search(msg))):
        return "image", "intention=generer_image (asset graphique / style visuel)"
    if DOCS_HINTS.search(msg):
        return "docs", "agent auto: recherche documentaire"
    if DEV_ACTION_HINTS.search(msg) or (
        CODING_HINTS.search(msg)
        and not NON_CODE_CONTEXT.search(msg)
        and (len(msg) > 30 or DEV_STRONG_HINTS.search(msg))
    ):
        return "dev", "agent auto: developpement/action code"

    # 5. Niveau 2 (LLM) uniquement si le sujet image/3d est present mais
    #    qu'aucune regle n'a tranche (cas ambigus sans verbe clair).
    if IMAGE_TOPIC_HINTS.search(msg) or CAD3D_HINTS.search(msg):
        intention = _llm_intention(msg)
        if intention == "voir_image":
            return "image", "intention=voir_image (classifieur LLM)"
        if intention == "modifier_image":
            return "image", "intention=modifier_image (classifieur LLM)"
        if intention == "generer_image":
            return "image", "intention=generer_image (classifieur LLM)"
        if intention == "envoyer_telegram":
            return "image", "intention=envoyer_telegram (classifieur LLM)"
        if intention == "3d":
            return "cad3d", "intention=3d (classifieur LLM)"

    return "general", ""


def route_model(message: str, installed_models: list[dict]) -> tuple[str, str]:
    intent = classify(message)
    priorities = CAPABILITY_PRIORITY.get(intent, ["general"])
    eligible = [m for m in installed_models if m.get("tag")]

    for capability in priorities:
        matches = [
            m
            for m in eligible
            if capability in (m.get("capabilities") or [])
            and "embeddings" not in (m.get("capabilities") or [])
            and _size_value(m) <= 15
        ]
        if not matches:
            continue
        if intent == "creative":
            gemmas = [m for m in matches if m["tag"].startswith("gemma")]
            best = max(gemmas if gemmas else matches, key=_size_value)
        elif intent in ("chat", "summary"):
            best = min(matches, key=_size_value)
        else:
            best = max(matches, key=_size_value)
        return best["tag"], (
            f"intention={intent}, capacite={capability}, taille={_size_value(best):g}B"
        )

    fallback_pool = [m for m in eligible if _size_value(m) <= 15] or eligible
    if intent == "chat" and fallback_pool:
        fallback = min(fallback_pool, key=_size_value)
    else:
        fallback = max(fallback_pool, key=_size_value) if fallback_pool else {"tag": ""}
    return fallback["tag"], f"intention={intent}, repli"
