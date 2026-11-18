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


# --- confirmar Sí/No (C-44) -----------------------------------------------------------


def fake_box(monkeypatch, answer):
    calls = []

    def box(owner, text, title, flags):
        calls.append((owner, text, title, flags))
        return answer

    monkeypatch.setattr(dialogos, "_message_box", lambda: box)
    return calls


def test_confirm_is_true_only_with_yes(monkeypatch):
    calls = fake_box(monkeypatch, dialogos.IDYES)
    assert dialogos.confirm("Título", "¿Seguro?") is True
    assert calls == [(None, "¿Seguro?", "Título", dialogos.confirm_flags(warning=False))]
    fake_box(monkeypatch, 7)  # IDNO
    assert dialogos.confirm("t", "x") is False
    fake_box(monkeypatch, 0)  # el diálogo falló
    assert dialogos.confirm("t", "x") is False


def test_warning_defaults_to_no():
    flags = dialogos.confirm_flags(warning=True)
    assert flags & 0xF == 0x4  # MB_YESNO
    assert flags & 0xF0 == 0x30  # MB_ICONWARNING
    assert flags & 0xF00 == 0x100  # MB_DEFBUTTON2: Intro = «No»
    plain = dialogos.confirm_flags(warning=False)
    assert plain & 0xF0 == 0x20 and plain & 0xF00 == 0  # pregunta, «Sí» por defecto


def test_confirm_without_windows_is_no(monkeypatch):
    monkeypatch.setattr(dialogos, "_message_box", lambda: None)
    assert dialogos.confirm("t", "x") is False


def test_default_no_keeps_the_question_icon_and_passes_through(monkeypatch):
    flags = dialogos.confirm_flags(warning=False, default_no=True)
    assert flags & 0xF0 == 0x20  # MB_ICONQUESTION: no es una advertencia
    assert flags & 0xF00 == 0x100  # MB_DEFBUTTON2: Intro = «No»
    calls = fake_box(monkeypatch, 7)
    assert dialogos.confirm("t", "¿Se ve bien?", default_no=True) is False
    assert calls[0][3] == flags
