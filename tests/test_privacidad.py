"""Guardián: nada de lo que se publica lleva el nombre de usuario de quien desarrolla.

El nombre se lee en el momento (variable USERNAME), así que este test no contiene ningún
nombre y vale en cualquier equipo. Se revisa solo lo que va al repo público (git ls-files,
sin docs/, que es un repo privado aparte).
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def public_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [REPO / f for f in out.splitlines() if f and not f.startswith("docs/")]


def test_no_developer_username_in_published_files():
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(user) < 3:
        pytest.skip("sin nombre de usuario útil en este entorno")
    needle = user.lower()
    offenders = []
    for path in public_files():
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue  # binarios (imágenes, fuentes, .ico)
        if needle in text:
            offenders.append(path.relative_to(REPO).as_posix())
    assert offenders == [], f"aparece el nombre de usuario en: {offenders}"
