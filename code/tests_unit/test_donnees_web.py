"""Outils web sans cle : lecture de secours, change, adresses (reseau simule)."""
import pytest

from backend.tools import t_donnees, t_web


@pytest.fixture
def secours_appele(monkeypatch):
    appels = []

    def faux_secours(url, max_chars):
        appels.append(url)
        return "Titre JS", "Contenu rendu par JavaScript " * 30, []

    monkeypatch.setattr(t_web, "_lecture_externe", faux_secours)
    return appels


def test_page_javascript_vide_lue_par_le_secours(monkeypatch, secours_appele):
    monkeypatch.setattr(t_web, "_fetch_page", lambda u, max_chars: ("App", "Chargement...", []))
    titre, texte, _ = t_web._page("https://app.exemple.fr", 4000)
    assert secours_appele and texte.startswith("Contenu rendu")


@pytest.mark.parametrize("erreur", [
    "Erreur HTTP 403 pour https://site.fr",
    "Erreur HTTP 429 pour https://site.fr",
    "refusé: adresse non publique (intranet -> 10.0.0.2)",
])
def test_un_refus_n_est_jamais_contourne(monkeypatch, secours_appele, erreur):
    monkeypatch.setattr(t_web, "_fetch_page", lambda u, max_chars: ("", erreur, []))
    _, texte, _ = t_web._page("https://site.fr", 4000)
    assert texte == erreur and not secours_appele


def test_page_normale_sans_secours(monkeypatch, secours_appele):
    monkeypatch.setattr(t_web, "_fetch_page", lambda u, max_chars: ("Article", "Texte riche. " * 100, []))
    t_web._page("https://site.fr", 4000)
    assert not secours_appele


def test_page_anti_robot_du_secours_ignoree(monkeypatch):
    class R:
        status_code = 200
        text = "Title: Just a moment...\n\nMarkdown Content:\n## Performing security verification"

    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return R()

    monkeypatch.setattr(t_web, "client_http", lambda timeout: C())
    monkeypatch.setattr(t_web, "refus_url", lambda u: None)
    assert t_web._lecture_externe("https://site.fr", 4000) == ("", "", [])


def test_secours_desactivable(monkeypatch):
    monkeypatch.setenv("NEO_LECTEUR_EXTERNE", "0")
    assert t_web._lecture_externe("https://site.fr", 4000) == ("", "", [])


@pytest.mark.parametrize("texte,code", [
    ("euros", "EUR"), ("Dollars", "USD"), ("$", "USD"), ("livre sterling", "GBP"),
    ("francs suisses", "CHF"), ("jpy", "JPY"), ("couronne suédoise", "SEK"), ("bitcoin", ""),
])
def test_codes_devises(texte, code):
    assert t_donnees.code_devise(texte) == code


def test_change_formate_a_la_francaise(monkeypatch):
    import httpx

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"date": "2026-11-09", "rates": {"USD": 1.1193}}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    s = t_donnees.tool_taux_change("1234,5", "euros", "dollars")
    assert s.startswith("1 234,50 EUR = 1 381,78 USD") and "BCE du 2026-11-09" in s


def test_devise_inconnue_explicite():
    assert t_donnees.tool_taux_change(5, "bitcoin", "eur").startswith("Erreur: devise inconnue")


def test_plongement_dimensions_differentes_incomparables():
    from backend.core import vector_store

    assert vector_store.cosine([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0
    assert vector_store.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


@pytest.mark.parametrize("entendu,attendu", [
    ("Bonjour Léo, peux-tu m'aider ?", "Bonjour Néo, peux-tu m'aider ?"),
    ("salut leo tu vas bien", "salut Néo tu vas bien"),
    ("Mon ami Léo arrive demain", "Mon ami Léo arrive demain"),
    ("Bonjour Léonard, ravi de te voir", "Bonjour Léonard, ravi de te voir"),
])
def test_dictee_nom_de_l_assistant(entendu, attendu):
    from backend.core import stt

    assert stt.corriger_nom(entendu) == attendu


def test_dictee_parakeet_par_defaut(monkeypatch):
    from backend.core import stt

    class Db:
        def query_one(self, *a):
            return None

    import backend.core.database as database
    monkeypatch.setattr(database, "get_db", lambda: Db())
    assert stt._size() == "parakeet"


@pytest.mark.parametrize("expression,attendu", [
    ("17 * 23", "391"),
    ("17.5/100*2340", "409.5"),
    ("stdev([3, 7, 9, 12])", "3.7749172176"),
    ("mean([3, 7, 9, 12])", "7.75"),
    ("sqrt(2025)", "45"),
    ("12,5".replace(",", "."), "12.5"),
    ("2^10", "1024"),
    ("factorial(10)", "3628800"),
])
def test_calculatrice(expression, attendu):
    from backend.tools.t_util import tool_calculator

    assert tool_calculator(expression).split(" = ")[-1] == attendu


@pytest.mark.parametrize("expression", [
    "2**10**10", "9**9**9", "factorial(100000)", "__import__('os')", "open('x')", "(1).__class__",
])
def test_calculatrice_refuse_le_dangereux(expression):
    from backend.tools.t_util import tool_calculator

    assert tool_calculator(expression).startswith("Erreur de calcul")


@pytest.mark.parametrize("code,attendu", [
    ("import statistics\nvaleurs = [3, 7, 9, 12]\necart_type = statistics.stdev(valeurs)\necart_type", "3.774917"),
    ("import statistics\nstatistics.mean([12, 15, 9, 22, 18])", "15.2"),
    ("x = 2\nprint(x * 21)", "42"),
    ("sum(\n    [1, 2, 3]\n)", "6"),
])
def test_execution_affiche_le_resultat(code, attendu):
    from backend.tools.t_files import tool_check_code

    assert attendu in tool_check_code(code, "python", executer=True)


def test_execution_sans_sortie_le_dit():
    from backend.tools.t_files import tool_check_code

    r = tool_check_code("x = 41 + 1", "python", executer=True)
    assert "AUCUNE" in r and "invente" in r.lower()


def test_execution_expression_sur_meme_ligne_intacte():
    from backend.tools.t_files import _afficher_derniere_expression

    assert _afficher_derniere_expression("a = 1; a") == "a = 1; a"


def test_calculatrice_rappelle_l_expression():
    from backend.tools.t_util import tool_calculator

    assert tool_calculator("stdev([4, 8, 15, 16, 23, 42])") == "stdev([4, 8, 15, 16, 23, 42]) = 13.4907375632"
    assert tool_calculator("variance([4, 8, 15, 16, 23, 42])").endswith("= 182")
