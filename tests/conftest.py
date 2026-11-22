"""Lo común a todos los tests."""

from __future__ import annotations

import pytest

from luzeira import idioma


@pytest.fixture(autouse=True)
def spanish_by_default(monkeypatch):
    """Los tests comprueban los textos en español: el «idioma de Windows» se fija a ``es``
    (el runner de GitHub está en inglés) y cada test empieza en español."""
    monkeypatch.setattr(idioma, "windows_language", lambda: "es")
    idioma.set_language("es")
    yield
    idioma.set_language("es")
