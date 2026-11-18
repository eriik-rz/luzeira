"""Diálogo nativo de abrir fichero (sin abrirlo de verdad)."""

import ctypes

from luzeira.sistema import dialogos


def test_structure_has_the_size_windows_expects():
    # OPENFILENAMEW en x64 mide 152 bytes; si no cuadra, GetOpenFileNameW falla.
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(dialogos._OPENFILENAMEW) == 152


def test_filter_format():
    assert dialogos.build_filter("Temas (*.zip)", "*.zip") == "Temas (*.zip)\0*.zip\0\0"


def test_no_tkinter_anywhere_in_the_app():
    # C-30: el Python embebible de la distribución no trae tkinter.
    from pathlib import Path

    src = Path(dialogos.__file__).resolve().parent.parent
    offenders = [
        p.name
        for p in src.rglob("*.py")
        if "import tkinter" in p.read_text(encoding="utf-8")
        or "from tkinter" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
