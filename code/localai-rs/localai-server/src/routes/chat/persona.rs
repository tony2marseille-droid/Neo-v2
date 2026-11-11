//! Personnage de Neo : fiche d'identite injectee en tete de CHAQUE conversation
//! (tous agents confondus) et politique de reflexion du chemin conversationnel.
//!
//! La fiche vit dans `config/neo_persona.md` (versionnee, deployee, modifiable
//! sans recompiler) ; `{nom}` et `{utilisateur}` viennent des reglages
//! `assistant_name` et `display_name`.

use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;

/// Fiche minimale si le fichier manque : Neo reste Neo.
const FICHE_SECOURS: &str = "Tu es {nom}, l'intelligence locale de {utilisateur}. Tu parles un français \
naturel et chaleureux, tu raisonnes avec rigueur avant de conclure et tu reponds d'abord a la question.";

/// Modeles conversationnels par ordre de preference (reglage
/// `modele_conversation` pour en imposer un) : le premier installe parle.
/// Duel du 09/11/2026 (banc de 42 questions, docs/CERVEAU.md) : gemma4:12b
/// raisonne aussi bien que qwen3:14b avec la reflexion (90,8 % vs 86,7 % pour
/// qwen3.5:9b sur les questions verifiables), tient a 100 % en VRAM (~11 Go)
/// et repond 2 a 4 fois plus vite, dans un francais plus naturel.
pub(super) const MODELES_CONVERSATION: &[&str] = &["gemma4:12b", "qwen3:14b", "qwen3:8b"];

/// Premier modele de `preferes` present dans la liste `/api/tags` d'Ollama ;
/// le premier de la liste si Ollama ne repond pas (l'erreur sera explicite).
pub(super) fn choisir_installe(preferes: &[&str], tags: Option<&serde_json::Value>) -> String {
    let installes: Vec<&str> = tags
        .and_then(|t| t.get("models"))
        .and_then(|m| m.as_array())
        .map(|v| v.iter().filter_map(|m| m.get("name").and_then(|n| n.as_str())).collect())
        .unwrap_or_default();
    if installes.is_empty() {
        return preferes[0].to_string();
    }
    preferes
        .iter()
        .find(|p| installes.iter().any(|i| i == *p || i.strip_suffix(":latest") == Some(**p)))
        .unwrap_or(&preferes[0])
        .to_string()
}

/// Fiche du personnage, prete a injecter.
pub(super) fn fiche(root: &Path, db: &localai_core::db::Db) -> String {
    // NEO_PERSONA : autre fiche (instance d'essai, variantes du personnage).
    let chemin = std::env::var("NEO_PERSONA")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| root.join("config").join("neo_persona.md"));
    let brut = std::fs::read_to_string(chemin)
        .ok()
        .filter(|t| !t.trim().is_empty())
        .unwrap_or_else(|| FICHE_SECOURS.to_string());
    let nom = db.get_setting("assistant_name", "Néo");
    let nom = if nom.trim().is_empty() { "Néo".to_string() } else { nom.trim().to_string() };
    let utilisateur = db.get_setting("display_name", "");
    let utilisateur = if utilisateur.trim().is_empty() {
        "ton utilisateur".to_string()
    } else {
        utilisateur.trim().to_string()
    };
    format!(
        "{}\n\n{}",
        brut.replace("{nom}", &nom).replace("{utilisateur}", &utilisateur),
        moment_present(&chrono::Local::now())
    )
}

/// Date et heure locales, en clair : sans elles, « quelle heure est-il ? » ou
/// « on est quel jour ? » obtenaient « je n'ai pas acces a l'horloge » (banc
/// des outils du 09/11/2026).
pub(super) fn moment_present<T: chrono::Datelike + chrono::Timelike>(t: &T) -> String {
    const JOURS: [&str; 7] = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
    const MOIS: [&str; 12] = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                              "septembre", "octobre", "novembre", "décembre"];
    format!(
        "Nous sommes le {} {} {} {}, il est {} h {:02} (heure locale).",
        JOURS[t.weekday().num_days_from_monday() as usize],
        t.day(),
        MOIS[t.month0() as usize],
        t.year(),
        t.hour(),
        t.minute()
    )
}

/// Identite COURTE pour les agents outilles : leurs consignes metier (outils,
/// sandbox, formats) doivent rester dominantes. La fiche complete, placee avant
/// elles, les noyait : l'agent dev ecrivait le code dans un fichier et ne
/// rendait qu'un resume (banc du 09/11/2026 : code 80 % -> 20 %).
pub(super) fn identite_agent(db: &localai_core::db::Db) -> String {
    let nom = db.get_setting("assistant_name", "Néo");
    let nom = if nom.trim().is_empty() { "Néo".to_string() } else { nom.trim().to_string() };
    format!(
        "Tu es {nom}, l'intelligence locale de l'utilisateur : français naturel et précis, \
         tu le tutoies (sauf s'il te vouvoie), \
         réponse d'abord. Quand on te demande du code, ta réponse finale contient TOUJOURS \
         le code complet dans un bloc ```langage```, même s'il est aussi écrit dans un fichier. {}",
        moment_present(&chrono::Local::now())
    )
}

/// Demande d'ECRIRE du code (fonction, classe, script...) sans action sur des
/// fichiers, sans execution ni projet : la conversation suffit, l'agent dev
/// (outils, sandbox) n'apporte rien et perd le code dans sa reponse.
pub(super) fn code_a_ecrire_seulement(message: &str) -> bool {
    static ECRIRE: OnceLock<Regex> = OnceLock::new();
    static ACTION: OnceLock<Regex> = OnceLock::new();
    let ecrire = ECRIRE.get_or_init(|| {
        Regex::new(r"(?i)\b(écris|ecris|écrire|ecrire|écrit|implémente|implemente|code|donne|propose|montre)\b.{0,80}\b(fonction|classe|script|algorithme|regex|expression régulière|requête sql|requete sql|programme|méthode|methode|snippet|exemple de code)\b").unwrap()
    });
    let action = ACTION.get_or_init(|| {
        Regex::new(r"(?i)\b(fichier|dossier|répertoire|repertoire|enregistre|sauvegarde|exécute|execute|lance|lancer|run|teste-le|projet|workspace|sandbox|disque|installe|déploie|deploie|commit|git|mon code|ce code|ce fichier|corrige)\b|[a-z]:\\").unwrap()
    });
    ecrire.is_match(message) && !action.is_match(message)
}

/// La reponse precedente de Neo montrait-elle une image (chemin ou balise) ?
pub(super) fn reponse_avec_image(contenu: &str) -> bool {
    static IMG: OnceLock<Regex> = OnceLock::new();
    IMG.get_or_init(|| {
        Regex::new(r"(?i)\.(?:jpe?g|png|tiff?|webp)\b|!\[[^\]]*\]\(|\bIMAGE:\s").unwrap()
    })
    .is_match(contenu)
}

/// Suite courte d'une demande d'image : « plus de variations », « refais-la
/// en plus sombre », « une autre », « de profil »... Seule, elle ne dit pas
/// « image » ; c'est la reponse precedente qui le dit (Telegram du
/// 09/11/2026 : la conversation avait invente cinq « variations » sans outil).
pub(super) fn suite_d_image(message: &str, reponse_precedente: &str) -> bool {
    static SUITE: OnceLock<Regex> = OnceLock::new();
    static AUTRE_SUJET: OnceLock<Regex> = OnceLock::new();
    let suite = SUITE.get_or_init(|| {
        Regex::new(r"(?i)\b(variations?|variantes?|versions?|une autre|un autre|d'autres|encore|refai[st]|recommence|reg[ée]n[èe]re|retente|essaie|plus (?:sombre|clair|lumineux|color[ée]|r[ée]aliste|proche|loin|grand|petit|d[ée]taill[ée]|net|dramatique|r[ée]aliste)|moins|zoom|cadr|de face|de profil|de dos|en portrait|en paysage|en couleur|en noir et blanc|style|ambiance|fond|arri[èe]re-plan|ajoute|enl[èe]ve|retire|mets|sans|avec|change|modifie|agrandi|format|4k|8k)\b").unwrap()
    });
    let autre_sujet = AUTRE_SUJET.get_or_init(|| {
        Regex::new(r"(?i)\b(code|script|fonction|m[ée]t[ée]o|quel temps|pleuvoir|explique|pourquoi|comment [cç]a|document|fichier|mail|telegram)\b").unwrap()
    });
    message.chars().count() <= 160
        && reponse_avec_image(reponse_precedente)
        && suite.is_match(message)
        && !autre_sujet.is_match(message)
}

/// Le modele sait-il reflechir (champ `thinking` d'Ollama) ?
pub(super) fn sait_reflechir(modele: &str) -> bool {
    let m = modele.to_lowercase();
    m.starts_with("qwen3") || m.starts_with("gemma4") || m.contains("deepseek-r1") || m.starts_with("gpt-oss")
        || m.contains("magistral")
}


/// Retire les marqueurs internes que Gemma laisse parfois dans son texte quand
/// la reflexion est coupee : une ligne « thought » suivie de `<channel|>`.
/// Vus tels quels dans une reponse (« thought <channel|><channel|>J'ai cree… »).
pub(super) fn sans_marqueurs(texte: &str) -> String {
    static LIGNE: OnceLock<Regex> = OnceLock::new();
    static BALISE: OnceLock<Regex> = OnceLock::new();
    let ligne = LIGNE.get_or_init(|| Regex::new(r"(?m)^[ \t]*thought[ \t]*\r?\n?(?:<\|?channel\|?>)+").unwrap());
    let balise = BALISE.get_or_init(|| Regex::new(r"<\|?channel\|?>").unwrap());
    if !texte.contains("channel") {
        return texte.to_string();
    }
    let t = ligne.replace_all(texte, "");
    balise.replace_all(&t, "").into_owned()
}

/// Phrase qui transmet a l'agent l'intention deja reconnue par le routeur.
///
/// Le routeur tranche (« demande nominale : media + sujet » = image a creer),
/// mais l'agent image ne le savait pas et redevinait : pour « Une photo d'un
/// boulanger devant son four », il cherchait d'abord une photo existante
/// (deux `image_search`) avant de generer (essai du 11/11/2026).
pub(super) fn consigne_intention(raison: &str) -> Option<String> {
    let reste = raison.strip_prefix("intention=")?;
    let nom = reste.split([' ', '(']).next()?.trim();
    let texte = match nom {
        "generer_image" => "INTENTION RECONNUE : l'utilisateur veut une image NOUVELLE (cas B). Appelle generate_image avec sa demande telle quelle ; ne cherche pas d'image existante avec image_search.",
        "voir_image" => "INTENTION RECONNUE : l'utilisateur veut VOIR une image qui existe deja (cas A : recherche puis affichage), pas en generer une.",
        "modifier_image" => "INTENTION RECONNUE : l'utilisateur veut MODIFIER une image existante (cas C : edit_image), pas en generer une nouvelle.",
        "document" => "INTENTION RECONNUE : l'utilisateur veut un FICHIER (Word, PDF ou Excel). Ta reponse commence par l'appel a document_create ; un contenu ecrit dans le message ou un chemin que tu inventes ne cree aucun fichier.",
        "fiche" => "INTENTION RECONNUE : l'utilisateur veut une FICHE de connaissance enregistree. Appelle fiche_creer avec le sujet (un seul appel : il cherche, redige et enregistre), puis reponds avec l'essentiel de la fiche.",
        "3d" => "INTENTION RECONNUE : l'utilisateur veut un OBJET 3D en fichier. Ta reponse commence par un appel d'outil (pipeline_3d pour une piece fonctionnelle, generate_mesh_from_text pour une forme organique) ; ne demande pas de dossier et n'ecris pas le script dans le message.",
        _ => return None,
    };
    Some(texte.to_string())
}

/// Mots de quatre lettres et plus, en minuscules.
fn mots_longs(texte: &str) -> std::collections::HashSet<String> {
    texte
        .to_lowercase()
        .split(|c: char| !c.is_alphabetic())
        .filter(|m| m.chars().count() >= 4)
        .map(str::to_string)
        .collect()
}

/// Indices des fiches dont le titre recoupe le message : tous les mots longs
/// du titre s'il n'en a qu'un, au moins deux sinon. Les plus proches d'abord.
/// Meme regle que `backend.core.fiches.pertinentes`.
pub(super) fn fiches_pertinentes(titres: &[String], message: &str, maximum: usize) -> Vec<usize> {
    let mots = mots_longs(message);
    let mut notees: Vec<(f32, usize)> = titres
        .iter()
        .enumerate()
        .filter_map(|(i, titre)| {
            let du_titre = mots_longs(titre);
            let communs = du_titre.intersection(&mots).count();
            (!du_titre.is_empty() && communs >= du_titre.len().min(2))
                .then(|| (communs as f32 / du_titre.len() as f32, i))
        })
        .collect();
    notees.sort_by(|a, b| b.0.total_cmp(&a.0));
    notees.into_iter().take(maximum).map(|(_, i)| i).collect()
}

/// Bloc de contexte : les fiches de connaissances de Neo qui portent sur le
/// sujet du message (deux au plus). None s'il n'y en a pas, ou pas de table.
pub(super) fn fiches_connues(db: &localai_core::db::Db, message: &str) -> Option<String> {
    let lignes = db.query_all_json("SELECT titre, contenu_md FROM fiches", &[]).ok()?;
    let texte = |l: &serde_json::Value, c: &str| l.get(c).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let titres: Vec<String> = lignes.iter().map(|l| texte(l, "titre")).collect();
    let choisies = fiches_pertinentes(&titres, message, 2);
    if choisies.is_empty() {
        return None;
    }
    let mut bloc = String::from(
        "TES FICHES DE CONNAISSANCES sur ce sujet (tu les as redigees et gardees ; appuie-toi dessus si elles repondent a la question) :",
    );
    for i in choisies {
        let corps: String = texte(&lignes[i], "contenu_md").chars().take(1500).collect();
        bloc.push_str(&format!("\n\n### {}\n{}", titres[i], corps));
    }
    Some(bloc)
}

/// Rappel a adresser, une seule fois par tour, a un agent dont le metier est de
/// PRODUIRE un fichier et qui a repondu par du texte sans appeler d'outil.
///
/// Essais du 11/11/2026 : l'agent bureau a « livre » une facture PDF en
/// inventant son chemin, l'agent 3D a demande dans quel dossier enregistrer.
/// `outils_lances` : nombre d'outils deja executes dans ce tour (un agent qui a
/// travaille puis conclut par du texte n'est pas relance).
pub(super) fn relance_sans_outil(agent: &str, outils_lances: usize) -> Option<&'static str> {
    if outils_lances > 0 {
        return None;
    }
    match agent {
        "bureau" => Some("Aucun fichier n'a ete cree : tu n'as appele aucun outil, et un chemin que tu ecris toi-meme n'existe pas. Appelle maintenant document_create avec le contenu complet dans contenu_md, puis reponds avec le chemin qu'il renvoie."),
        "cad3d" => Some("Aucun fichier n'a ete produit : tu n'as appele aucun outil. Ne demande pas de dossier (les outils rangent les fichiers eux-memes) et n'ecris pas de script dans le message. Appelle maintenant pipeline_3d (piece fonctionnelle) ou generate_mesh_from_text (forme organique) avec la demande complete."),
        _ => None,
    }
}

/// Faut-il reflechir avant de repondre ? Oui des que la demande n'est pas une
/// simple formule sociale : raisonnement, explication, conseil, code,
/// comparaison, ou message un peu long. Une salutation reste instantanee.
pub(super) fn doit_reflechir(message: &str) -> bool {
    static BAVARDAGE: OnceLock<Regex> = OnceLock::new();
    static EXIGEANT: OnceLock<Regex> = OnceLock::new();
    let bavardage = BAVARDAGE.get_or_init(|| {
        Regex::new(r"(?i)^\s*(salut|bonjour|bonsoir|coucou|hello|hey|yo|merci|ok|d'accord|super|génial|top|à plus|bonne nuit|ça va)\b").unwrap()
    });
    let exigeant = EXIGEANT.get_or_init(|| {
        Regex::new(r"(?i)(pourquoi|comment|explique|calcul|combien|code|fonction|script|bug|erreur|compar|différence|diff[ée]rence|conseil|aide-moi|analyse|résous|démontr|prouv|optimis|stratégie|plan|quel(le)?s? (est|sont)|que penses|ton avis|vaut-il|faut-il|\d+\s*[-+*/x×÷]\s*\d+)").unwrap()
    });
    let n = message.chars().count();
    if exigeant.is_match(message) {
        return true;
    }
    if bavardage.is_match(message) && n < 120 {
        return false;
    }
    n >= 160
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fiches_reprises_quand_le_titre_recoupe_le_message() {
        let titres: Vec<String> = ["Le pont du Gard", "La photosynthèse", "Le RAG", "Les volcans d'Auvergne"]
            .iter()
            .map(|t| t.to_string())
            .collect();
        assert_eq!(fiches_pertinentes(&titres, "Parle-moi du pont du Gard", 2), vec![0]);
        assert_eq!(fiches_pertinentes(&titres, "explique la PHOTOSYNTHÈSE simplement", 2), vec![1]);
        // Un seul mot commun sur deux : pas assez (« un pont » n'est pas « le pont du Gard »).
        assert!(fiches_pertinentes(&titres, "construis-moi un pont en bois", 2).is_empty());
        assert!(fiches_pertinentes(&titres, "bonjour, ça va ?", 2).is_empty());
        assert!(fiches_pertinentes(&titres, "le RAG", 2).is_empty());      // titre sans mot long
    }

    #[test]
    fn agent_qui_n_agit_pas_est_relance_une_fois() {
        assert!(consigne_intention("intention=fiche (fiche de connaissance a rediger et enregistrer)").unwrap().contains("fiche_creer"));
        assert!(relance_sans_outil("bureau", 0).unwrap().contains("document_create"));
        assert!(relance_sans_outil("cad3d", 0).unwrap().contains("pipeline_3d"));
        // Il a deja travaille, ou ce n'est pas un agent qui produit des fichiers.
        assert!(relance_sans_outil("bureau", 1).is_none());
        assert!(relance_sans_outil("web", 0).is_none());
        assert!(relance_sans_outil("", 0).is_none());
        assert!(consigne_intention("intention=document (fichier Word/PDF/Excel a produire)").unwrap().contains("document_create"));
        assert!(consigne_intention("intention=3d (objet a concevoir ou imprimer en 3D)").unwrap().contains("pipeline_3d"));
    }

    #[test]
    fn marqueurs_de_gemma_retires() {
        let sale = "Je relance.\n\nthought\n<channel|><channel|>J'ai cree la photo.";
        assert_eq!(sans_marqueurs(sale), "Je relance.\n\nJ'ai cree la photo.");
        assert_eq!(sans_marqueurs("<|channel|>Bonjour"), "Bonjour");
        // Un texte normal, meme s'il parle de « channel » ou de « thought », est intact.
        let normal = "Le mot thought veut dire pensee ; un channel Slack est un canal.";
        assert_eq!(sans_marqueurs(normal), normal);
        assert_eq!(sans_marqueurs(""), "");
    }

    #[test]
    fn intention_du_routeur_transmise_a_l_agent() {
        let g = consigne_intention("intention=generer_image (demande nominale : media + sujet)").unwrap();
        assert!(g.contains("generate_image") && g.contains("image_search"));
        assert!(consigne_intention("intention=voir_image (photo existante)").unwrap().contains("VOIR"));
        assert!(consigne_intention("intention=modifier_image (edition)").unwrap().contains("edit_image"));
        assert!(consigne_intention("intention=meteo (outil meteo)").is_none());
        assert!(consigne_intention("agent auto: conception/impression 3D").is_none());
        assert!(consigne_intention("").is_none());
    }

    #[test]
    fn reflexion_selon_la_demande() {
        assert!(!doit_reflechir("Salut, ça va ?"));
        assert!(!doit_reflechir("Merci beaucoup !"));
        assert!(doit_reflechir("Pourquoi le ciel est bleu ?"));
        assert!(doit_reflechir("Combien font 17 x 23 ?"));
        assert!(doit_reflechir("Salut, tu peux m'expliquer comment marche un thread ?"));
        assert!(doit_reflechir("Écris une fonction Python qui fusionne deux listes"));
    }

    #[test]
    fn code_seulement_ou_action_sur_fichiers() {
        assert!(code_a_ecrire_seulement("Écris une fonction Python `romain(n)` qui convertit un entier"));
        assert!(code_a_ecrire_seulement("Donne-moi un script bash qui compte les lignes"));
        assert!(!code_a_ecrire_seulement("Écris une fonction dans le fichier utils.py"));
        assert!(!code_a_ecrire_seulement("Écris un script et exécute-le"));
        assert!(!code_a_ecrire_seulement("Corrige la fonction de mon projet"));
        assert!(!code_a_ecrire_seulement("Liste le dossier E:\\ia\\workspace"));
    }

    #[test]
    fn suites_de_demande_d_image() {
        let img = "Voici l'image : ![x](/api/sd/file?path=E%3A%5Cia%5Cworkspace%5Cimages%5Cx.jpg)";
        assert!(suite_d_image("Plus de variations", img));
        assert!(suite_d_image("Refais-la en plus sombre", img));
        assert!(suite_d_image("une autre de profil", img));
        assert!(!suite_d_image("Plus de variations", "Voici la recette des crêpes."));
        assert!(!suite_d_image("Explique-moi comment ça marche", img));
        assert!(!suite_d_image("Quel temps demain avec ce vent ?", img));
    }

    #[test]
    fn moment_present_en_francais() {
        let t = chrono::NaiveDate::from_ymd_opt(2026, 11, 9).unwrap().and_hms_opt(20, 5, 0).unwrap();
        assert_eq!(moment_present(&t), "Nous sommes le lundi 9 novembre 2026, il est 20 h 05 (heure locale).");
    }

    #[test]
    fn modele_conversation_selon_les_installes() {
        let tags = serde_json::json!({"models": [{"name": "qwen3:14b"}, {"name": "qwen3:8b"}]});
        assert_eq!(choisir_installe(MODELES_CONVERSATION, Some(&tags)), "qwen3:14b");
        let tags = serde_json::json!({"models": [{"name": "gemma4:12b"}, {"name": "qwen3:14b"}]});
        assert_eq!(choisir_installe(MODELES_CONVERSATION, Some(&tags)), "gemma4:12b");
        assert_eq!(choisir_installe(MODELES_CONVERSATION, None), "gemma4:12b");
    }

    #[test]
    fn modeles_capables_de_reflexion() {
        assert!(sait_reflechir("qwen3:14b"));
        assert!(!sait_reflechir("gemma3:12b"));
        assert!(sait_reflechir("gemma4:26b"));
        assert!(sait_reflechir("qwen3.6:35b-a3b"));
        assert!(!sait_reflechir("localai-prog-v10-q5km:latest"));
    }

    #[test]
    fn fiche_du_depot_complete() {
        let fiche = std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/neo_persona.md"),
        )
        .expect("config/neo_persona.md");
        assert!(fiche.contains("{nom}") && fiche.contains("{utilisateur}"));
        assert!(fiche.len() < 6000, "la fiche doit rester compacte (contexte)");
    }
}
