//! Chemins d'images : extraction depuis un texte et verification sur disque.
//! Garde-fou anti-hallucination : le modele cite parfois des fichiers qui
//! n'existent pas ; ces fonctions les detectent et les remplacent.

/// Extraction des chemins d'images cites dans un texte, sans crate regex.
/// Reconnait les absolus [A-Za-z]:\...\x.(png|jpe?g|webp) et les relatifs
/// data\...|data/...|workspace\...|workspace/.... Une frontiere est exigee
/// avant le debut (evite les faux positifs "https://...", "metadata\...").
pub(super) fn chemins_images(texte: &str) -> Vec<String> {
    let chars: Vec<char> = texte.chars().collect();
    let n = chars.len();
    // "jpeg" avant "jpg" : sinon ".jpeg" serait tronque en ".jpg".
    let exts = ["tiff", "png", "jpeg", "jpg", "webp", "tif"];
    let stop = |c: char| {
        c.is_whitespace()
            || matches!(
                c,
                '<' | '>' | '"' | '\'' | '`' | ')' | ']' | '}' | '(' | '[' | '*' | '!'
                    | ',' | ';' | '|'
            )
    };
    let mut res: Vec<String> = Vec::new();
    let mut i = 0usize;
    while i < n {
        let prev_ok = i == 0
            || !(chars[i - 1].is_alphanumeric()
                || chars[i - 1] == '_'
                || chars[i - 1] == '\\'
                || chars[i - 1] == '/'
                || chars[i - 1] == '.');
        if !prev_ok {
            i += 1;
            continue;
        }
        let debut_abs = chars[i].is_ascii_alphabetic()
            && i + 2 < n
            && chars[i + 1] == ':'
            && (chars[i + 2] == '\\' || chars[i + 2] == '/');
        let fenetre: String = chars[i..n.min(i + 12)]
            .iter()
            .collect::<String>()
            .to_lowercase();
        let debut_rel = fenetre.starts_with("data\\")
            || fenetre.starts_with("data/")
            || fenetre.starts_with("workspace\\")
            || fenetre.starts_with("workspace/");
        if !(debut_abs || debut_rel) {
            i += 1;
            continue;
        }
        let mut j = i;
        let mut trouve = false;
        while j < n {
            let c = chars[j];
            if stop(c) {
                break;
            }
            if c == '.' && j + 1 < n {
                let suite: String = chars[(j + 1)..n.min(j + 6)]
                    .iter()
                    .collect::<String>()
                    .to_lowercase();
                for ext in exts {
                    if suite.starts_with(ext) {
                        let k = j + 1 + ext.len();
                        if k >= n || !chars[k].is_alphanumeric() {
                            j = k;
                            trouve = true;
                            break;
                        }
                    }
                }
                if trouve {
                    break;
                }
            }
            j += 1;
        }
        if trouve && j > i {
            let chemin: String = chars[i..j].iter().collect();
            if !res.contains(&chemin) {
                res.push(chemin);
            }
            i = j;
        } else {
            i += 1;
        }
    }
    res
}

pub(super) fn chemin_existe(root: &std::path::Path, brut: &str) -> bool {
    let p = std::path::Path::new(brut);
    if p.is_absolute() {
        p.is_file()
    } else {
        root.join(p).is_file()
    }
}

/// Vrais fichiers images produits par les outils de CE tour, extraits des
/// traces (MEILLEURE IMAGE: x, Image generee: x, Image telechargee: x...).
pub(super) fn vivants_depuis_rows(rows: &[serde_json::Value], root: &std::path::Path) -> Vec<String> {
    let mut vivants: Vec<String> = Vec::new();
    for r in rows {
        let Some(preview) = r.get("result_preview").and_then(|v| v.as_str()) else {
            continue;
        };
        for p in chemins_images(preview) {
            if !vivants.contains(&p) && chemin_existe(root, &p) {
                vivants.push(p);
            }
        }
    }
    vivants
}

/// Garde-fou anti-hallucination : chaque chemin d'image cite dans la
/// reponse doit exister sur disque. Les chemins morts sont remplaces par
/// les vrais fichiers produits par les outils de CE tour, sinon retires
/// avec un avertissement visible. Le modele fine-tune invente parfois des
/// reponses au format "image generee" sans appeler les outils : ceci
/// empeche l'affichage de liens morts dans le chat.
pub(super) fn corrige_chemins_image(full: &str, root: &std::path::Path, vivants: &[String]) -> String {
    let cites = chemins_images(full);
    let morts: Vec<&String> = cites.iter().filter(|c| !chemin_existe(root, c)).collect();
    if morts.is_empty() {
        return full.to_string();
    }
    let mut out = full.to_string();
    let mut vi = 0usize;
    for m in &morts {
        if vi < vivants.len() {
            out = out.replace(m.as_str(), vivants[vi].as_str());
            vi += 1;
        } else {
            out = out.replace(m.as_str(), "");
        }
    }
    if vi == 0 {
        out.push_str(
            "\n\n(Attention : aucune image n'a reellement ete generee ni telechargee durant ce tour ; les chemins cites etaient invalides et ont ete retires. Reformule ta demande pour que les outils soient appeles.)",
        );
    } else {
        out.push_str(
            "\n\n(Correction automatique : des chemins d'images invalides ont ete remplaces par les fichiers reellement produits.)",
        );
    }
    out
}
