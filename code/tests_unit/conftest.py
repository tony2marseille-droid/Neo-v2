# -*- coding: utf-8 -*-
"""Rend la racine de Neo importable pour pytest (package `backend`).

Les tests de `<racine>/tests` sont des scripts autonomes executes a l'import :
ils ne sont pas collectes ici. Ce dossier ne contient que des tests pytest
classiques (fixtures, aucune connexion a la base de production).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
