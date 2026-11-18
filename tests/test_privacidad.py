"""Guardián: nada de lo que se publica lleva datos de quien desarrolla.

Usuario, nombre del equipo, carpeta personal y emails de git se leen en el momento
(``personal_needles`` de ``scripts/empaquetar.py``, el mismo criterio que usa la build),
así que este test no contiene ningún dato y vale en cualquier equipo. Se revisa solo lo
que va al repo público (git ls-files, sin docs/, que es un repo privado aparte), y en
bytes: también imágenes, fuentes y ficheros que no son UTF-8.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_packager():
    spec = importlib.util.spec_from_file_location("empaquetar", REPO / "scripts" / "empaquetar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def public_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [REPO / f for f in out.splitlines() if f and not f.startswith("docs/")]


def test_no_developer_data_in_published_files():
    needles = _load_packager().personal_needles()
    if not needles:
        pytest.skip("sin datos personales que buscar en este entorno")
    encoded = [n.encode() for n in needles] + [n.encode("utf-16-le") for n in needles]
    offenders = []
    for path in public_files():
        try:
            data = path.read_bytes().lower()
        except OSError:
            continue
        if any(raw in data for raw in encoded):
            offenders.append(path.relative_to(REPO).as_posix())
    # Sin decir qué dato: el mensaje acabaría en un log público de CI.
    assert offenders == [], f"aparecen datos de quien desarrolla en: {offenders}"


def test_needles_include_the_git_email(monkeypatch):
    emp = _load_packager()
    monkeypatch.setattr(emp, "_git_emails", lambda: {"alguien@ejemplo.org"})
    monkeypatch.setenv("COMPUTERNAME", "EQUIPO-PRUEBA")
    needles = emp.personal_needles()
    assert "alguien@ejemplo.org" in needles and "equipo-prueba" in needles
