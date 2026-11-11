# -*- coding: utf-8 -*-
"""Tests du decoupage de texte et de l'extraction de documents (fonctions
pures uniquement : aucun fichier de production n'est touche)."""
from pathlib import Path

from backend.core import documents


def test_chunk_text_vide():
    assert documents.chunk_text("") == []


def test_chunk_text_court_reste_entier():
    assert documents.chunk_text("Une seule phrase.") == ["Une seule phrase."]


def test_chunk_text_preserve_les_paragraphes_courts():
    texte = "alpha\nbeta\ngamma"
    assert documents.chunk_text(texte, target=900, overlap=120) == [
        "alpha\nbeta\ngamma"
    ]


def test_chunk_text_decoupe_et_couvre_tout():
    texte = "\n".join(f"paragraphe {i} " + "mot " * 30 for i in range(10))
    morceaux = documents.chunk_text(texte, target=500, overlap=100)
    assert len(morceaux) > 1
    assert all(len(m) <= 500 for m in morceaux)
    contenu = " ".join(morceaux)
    for i in range(10):
        assert f"paragraphe {i}" in contenu, f"paragraphe {i} perdu"


def test_chunk_text_long_paragraphe_decoupe_avec_chevauchement():
    long_paragraphe = "abcdefgh" * 400  # 3200 caracteres, un seul paragraphe
    morceaux = documents.chunk_text(long_paragraphe, target=900, overlap=120)
    assert len(morceaux) >= 4
    assert all(len(m) <= 900 for m in morceaux)


def test_extract_plain(tmp_path: Path):
    fichier = tmp_path / "note.txt"
    fichier.write_text("contenu simple", encoding="utf-8")
    assert documents.extract_text(fichier) == "contenu simple"


def test_extract_csv(tmp_path: Path):
    fichier = tmp_path / "t.csv"
    fichier.write_text("a,b\n1,2\n", encoding="utf-8")
    resultat = documents.extract_text(fichier)
    assert "a | b" in resultat
    assert "1 | 2" in resultat


def test_extract_texte_tronque_a_max_chars(tmp_path: Path):
    fichier = tmp_path / "long.txt"
    fichier.write_text("x" * (documents.MAX_CHARS + 5000), encoding="utf-8")
    assert len(documents.extract_text(fichier)) == documents.MAX_CHARS


def test_extract_encodage_invalide_replie(tmp_path: Path):
    fichier = tmp_path / "bizarre.txt"
    fichier.write_bytes(b"caf\xe9 \xff coucou")
    resultat = documents.extract_text(fichier)
    assert "coucou" in resultat
