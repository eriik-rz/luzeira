"""Tests del aviso de apagado. No apagan nada: los mensajes de fin de sesión se envían
a mano con SendMessageW **solo a la ventana oculta del propio test**."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

import pytest

from luzeira.sistema import sesion
from luzeira.sistema.sesion import WM_ENDSESSION, WM_QUERYENDSESSION, SessionWatcher

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")

WM_GETTEXTLENGTH = 0x000E

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetClassInfoExW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_void_p]
    user32.GetClassInfoExW.restype = wintypes.BOOL
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _class_exists(name: str) -> bool:
    info = sesion.WNDCLASSEXW()
    info.cbSize = ctypes.sizeof(info)
    return bool(user32.GetClassInfoExW(kernel32.GetModuleHandleW(None), name, ctypes.byref(info)))


@pytest.fixture
def calls():
    return []


@pytest.fixture
def watcher(calls):
    w = SessionWatcher(lambda: calls.append("end"))
    assert w.start() is True
    yield w
    w.stop()


@windows_only
def test_query_end_session_returns_true(watcher, calls):
    assert user32.SendMessageW(watcher.hwnd, WM_QUERYENDSESSION, 0, 0) == 1
    assert calls == []


@windows_only
def test_end_session_true_calls_callback_once(watcher, calls):
    assert user32.SendMessageW(watcher.hwnd, WM_ENDSESSION, 1, 0) == 0
    assert calls == ["end"]


@windows_only
def test_end_session_false_does_not_call(watcher, calls):
    assert user32.SendMessageW(watcher.hwnd, WM_ENDSESSION, 0, 0) == 0
    assert calls == []


@windows_only
def test_other_messages_go_to_defwindowproc(watcher):
    # DefWindowProc responde a WM_GETTEXTLENGTH con la longitud del título «Luzeira».
    assert user32.SendMessageW(watcher.hwnd, WM_GETTEXTLENGTH, 0, 0) == len("Luzeira")


@windows_only
def test_callback_errors_are_logged_not_raised(caplog):
    def boom() -> None:
        raise RuntimeError("fallo del usuario")

    with SessionWatcher(boom) as w, caplog.at_level(logging.ERROR, logger="luzeira.sistema.sesion"):
        assert user32.SendMessageW(w.hwnd, WM_ENDSESSION, 1, 0) == 0
        assert w.active
    assert any("on_end_session" in r.getMessage() and r.exc_info for r in caplog.records)


@windows_only
def test_start_stop_many_times_without_leaks(calls):
    baseline = threading.active_count()
    w = SessionWatcher(lambda: calls.append("end"))
    for _ in range(5):
        assert w.start() is True
        assert w.start() is True  # idempotente
        hwnd = w.hwnd
        assert user32.IsWindow(hwnd)
        assert _class_exists(w.class_name)
        w.stop()
        assert not w.active
        assert w.hwnd is None
        assert not user32.IsWindow(hwnd)
        assert not _class_exists(w.class_name)
    w.stop()  # sin arrancar: no falla
    assert threading.active_count() == baseline
    assert calls == []


@windows_only
def test_window_is_top_level_and_hidden(watcher):
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    assert user32.GetParent(watcher.hwnd) is None
    assert not user32.IsWindowVisible(watcher.hwnd)


@windows_only
def test_start_fails_cleanly_when_class_cannot_register(caplog):
    w = SessionWatcher(lambda: None)
    w.class_name = "x" * 300  # más de 256 caracteres: RegisterClassExW lo rechaza
    with caplog.at_level(logging.ERROR, logger="luzeira.sistema.sesion"):
        assert w.start() is False
    assert not w.active
    assert any("registrar la clase" in r.getMessage() for r in caplog.records)
    w.stop()


def test_start_without_api_warns(monkeypatch, caplog):
    monkeypatch.setattr(sesion, "_load_user32", lambda: None)
    w = SessionWatcher(lambda: None)
    with caplog.at_level(logging.WARNING, logger="luzeira.sistema.sesion"):
        assert w.start() is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)
