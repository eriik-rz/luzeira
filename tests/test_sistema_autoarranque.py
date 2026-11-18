"""Tests del autoarranque. Nunca tocan la clave Run real: cada test usa una subclave de
prueba ``HKCU\\Software\\VisoriaTests\\<uuid>\\Run`` que se borra al terminar."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import uuid
from pathlib import Path

import pytest

from visoria.sistema import autoarranque

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")

if sys.platform == "win32":
    import winreg

TEST_ROOT = r"Software\VisoriaTests"


def _real_run_value() -> str | None:
    """Lee (solo lee) el valor real de Visoria para comprobar que no cambia."""
    return autoarranque.registered_command()


@pytest.fixture(scope="module", autouse=True)
def real_run_key_untouched():
    before = _real_run_value()
    yield
    assert _real_run_value() == before


@pytest.fixture
def test_key():
    """Ruta de una clave de prueba propia; se borra entera al acabar."""
    base = rf"{TEST_ROOT}\{uuid.uuid4().hex}"
    path = rf"{base}\Run"
    yield path
    for sub in (path, base):
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
    # VisoriaTests solo se borra si se ha quedado vacía (si no, DeleteKey falla).
    with contextlib.suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_ROOT)


def test_disabled_when_key_does_not_exist(test_key):
    assert autoarranque.is_enabled(test_key) is False
    assert autoarranque.registered_command(test_key) is None


def test_enable_then_disable(test_key):
    cmd = r'"C:\Program Files\Visoria\Visoria.exe"'
    autoarranque.enable(cmd, test_key)
    assert autoarranque.is_enabled(test_key) is True
    assert autoarranque.registered_command(test_key) == cmd
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key) as key:
        value, kind = winreg.QueryValueEx(key, "Visoria")
    assert (value, kind) == (cmd, winreg.REG_SZ)

    autoarranque.disable(test_key)
    assert autoarranque.is_enabled(test_key) is False


def test_enable_overwrites_previous_command(test_key):
    autoarranque.enable('"C:\\viejo.exe"', test_key)
    autoarranque.enable('"C:\\nuevo.exe"', test_key)
    assert autoarranque.registered_command(test_key) == '"C:\\nuevo.exe"'


def test_disable_without_value_is_noop(test_key):
    autoarranque.disable(test_key)  # ni clave
    autoarranque.enable('"C:\\a.exe"', test_key, name="Otro")
    autoarranque.disable(test_key)  # clave sí, valor no
    assert autoarranque.registered_command(test_key, name="Otro") == '"C:\\a.exe"'


@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "x" * 261])
def test_enable_rejects_invalid_commands(test_key, bad):
    with pytest.raises(ValueError):
        autoarranque.enable(bad, test_key)
    assert autoarranque.is_enabled(test_key) is False


def test_enable_accepts_exactly_260_chars(test_key):
    autoarranque.enable("x" * 260, test_key)
    assert autoarranque.is_enabled(test_key)


# --- default_command ---------------------------------------------------------------


def _argv(command_line: str) -> list[str]:
    """Trocea una línea como lo hace Windows (CommandLineToArgvW, solo lectura)."""
    shell32 = ctypes.WinDLL("shell32")
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    argc = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    folder = tmp_path / "con espacios" / "Scripts"
    folder.mkdir(parents=True)
    python = folder / "python.exe"
    python.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.delattr(sys, "frozen", raising=False)
    return folder


def test_default_command_prefers_pythonw(fake_env):
    (fake_env / "pythonw.exe").write_bytes(b"")
    cmd = autoarranque.default_command()
    assert _argv(cmd) == [str(fake_env / "pythonw.exe"), "-m", "visoria"]
    assert cmd.startswith('"')


def test_default_command_falls_back_to_sys_executable(fake_env):
    cmd = autoarranque.default_command()
    assert _argv(cmd) == [str(fake_env / "python.exe"), "-m", "visoria"]


def test_default_command_quotes_extra_args(fake_env):
    cmd = autoarranque.default_command(["ejecutar", "mi tema"])
    assert _argv(cmd)[-2:] == ["ejecutar", "mi tema"]


def test_default_command_frozen_uses_own_exe(fake_env, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = autoarranque.default_command()
    assert _argv(cmd) == [str(fake_env / "python.exe")]


def test_default_command_real_environment_fits():
    cmd = autoarranque.default_command()
    assert len(cmd) <= autoarranque.MAX_COMMAND_LENGTH
    assert Path(_argv(cmd)[0]).is_file()
