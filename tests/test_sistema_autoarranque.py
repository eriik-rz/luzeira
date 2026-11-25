"""Tests del autoarranque. Nunca tocan la clave Run real: cada test usa una subclave de
prueba ``HKCU\\Software\\LuzeiraTests\\<uuid>\\Run`` que se borra al terminar."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import uuid
from pathlib import Path

import pytest

from luzeira.sistema import autoarranque

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")

if sys.platform == "win32":
    import winreg

TEST_ROOT = r"Software\LuzeiraTests"


def _real_run_value() -> str | None:
    """Lee (solo lee) el valor real de Luzeira para comprobar que no cambia."""
    return autoarranque.registered_command()


@pytest.fixture(scope="module", autouse=True)
def real_run_key_untouched():
    before = _real_run_value()
    yield
    assert _real_run_value() == before


#: Los tests que sí pasan por ``check_app_folders`` (los demás la quitan).
REAL_FOLDER_CHECK = {
    "test_enable_refuses_with_a_translated_message_and_writes_nothing",
    "test_the_real_check_reads_the_real_folders",
}


@pytest.fixture(autouse=True)
def app_folders_are_safe(request, monkeypatch):
    """La comprobación de la carpeta de la app (R4-06) depende de dónde esté el repo (en
    la CI, la carpeta de trabajo puede ser de otros): fuera, salvo en sus propios tests."""
    if request.node.name not in REAL_FOLDER_CHECK:
        monkeypatch.setattr(autoarranque, "check_app_folders", lambda: None)


@pytest.fixture
def test_key():
    """Ruta de una clave de prueba propia; se borra entera al acabar."""
    base = rf"{TEST_ROOT}\{uuid.uuid4().hex}"
    path = rf"{base}\Run"
    yield path
    for sub in (path, base):
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
    # LuzeiraTests solo se borra si se ha quedado vacía (si no, DeleteKey falla).
    with contextlib.suppress(OSError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_ROOT)


def test_disabled_when_key_does_not_exist(test_key):
    assert autoarranque.is_enabled(test_key) is False
    assert autoarranque.registered_command(test_key) is None


def test_enable_then_disable(test_key):
    cmd = r'"C:\Program Files\Luzeira\Luzeira.exe"'
    autoarranque.enable(cmd, test_key)
    assert autoarranque.is_enabled(test_key) is True
    assert autoarranque.registered_command(test_key) == cmd
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key) as key:
        value, kind = winreg.QueryValueEx(key, "Luzeira")
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


# --- C-24: quitado en el Administrador de tareas --------------------------------------------


def _approved(path: str, value: object, kind: int) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, autoarranque.VALUE_NAME, 0, kind, value)


def test_not_blocked_without_a_mark(test_key):
    assert not autoarranque.blocked_by_user(test_key)


@pytest.mark.parametrize(
    "value,kind,blocked",
    [
        (b"\x03" + bytes(11), "REG_BINARY", True),  # desactivado (lo observado)
        (b"\x02" + bytes(11), "REG_BINARY", False),  # activado
        (b"\x06" + bytes(11), "REG_BINARY", False),  # otro valor: como hasta ahora
        (b"", "REG_BINARY", False),
        ("03", "REG_SZ", False),  # tipo raro: no se adivina
    ],
)
def test_blocked_only_with_the_observed_disabled_mark(test_key, value, kind, blocked):
    _approved(test_key, value, getattr(winreg, kind))
    assert autoarranque.blocked_by_user(test_key) is blocked
    winreg.DeleteValue(
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key, 0, winreg.KEY_SET_VALUE),
        autoarranque.VALUE_NAME,
    )


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
    assert _argv(cmd) == [str(fake_env / "pythonw.exe"), "-m", "luzeira"]
    assert cmd.startswith('"')


def test_default_command_falls_back_to_sys_executable(fake_env):
    cmd = autoarranque.default_command()
    assert _argv(cmd) == [str(fake_env / "python.exe"), "-m", "luzeira"]


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


# --- La carpeta de la app la pueden cambiar otros (R4-06) -------------------------------

from luzeira.sistema import permisos  # noqa: E402

ME = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"
AUTH_USERS = "S-1-5-11"
FULL = 0x1F01FF
READ_EXECUTE = 0x1200A9


def _sec(owner, *aces):
    return permisos.Security(
        owner, tuple(permisos.Ace(0, flags, mask, sid) for sid, mask, flags in aces)
    )


#: Lo que tiene una carpeta del perfil del usuario (Descargas, Documentos…).
PROFILE_FOLDER = _sec(
    ME,
    (permisos.SID_SYSTEM, FULL, 0x13),
    (permisos.SID_ADMINISTRATORS, FULL, 0x13),
    (ME, FULL, 0x13),
)
#: Lo que hereda una carpeta nueva en C:\ (leído de C:\ el 2026-11-24): Usuarios
#: autentificados con «Modificar» para lo que se cree dentro.
DRIVE_ROOT_CHILD = _sec(
    ME,
    (permisos.SID_ADMINISTRATORS, FULL, 0x13),
    (permisos.SID_SYSTEM, FULL, 0x13),
    ("S-1-5-32-545", READ_EXECUTE, 0x13),
    (AUTH_USERS, 0x1301BF, 0x13),
    (permisos.SID_CREATOR_OWNER, 0x10000000, 0x1B),
)


def _check(security):
    return autoarranque.unsafe_folders([Path("x")], read_security=lambda _p: security, user_sid=ME)


def test_a_folder_of_the_user_is_safe():
    assert _check(PROFILE_FOLDER) == []


def test_a_portable_unzipped_in_the_drive_root_is_not():
    assert _check(DRIVE_ROOT_CHILD) == [Path("x")]


@pytest.mark.parametrize(
    "security",
    [
        _sec(ME, (OTHER, permisos.FILE_ADD_FILE, 0)),  # otro usuario puede meter ficheros
        _sec(ME, (permisos.SID_EVERYONE, permisos.DELETE, 0)),  # renombrarla y poner otra
        _sec(ME, (AUTH_USERS, permisos.GENERIC_WRITE, 0x1B)),  # lo que se cree dentro
        _sec(ME, (OTHER, permisos.WRITE_DAC, 0)),  # cambiar los permisos
        _sec(OTHER, (ME, FULL, 0)),  # el propietario es otro: puede cambiar la DACL
        permisos.Security(ME, None),  # sin DACL: todo el mundo puede todo
    ],
)
def test_anyone_else_who_can_write_makes_it_unsafe(security):
    assert _check(security) == [Path("x")]


def test_system_admins_and_trustedinstaller_may_write():
    security = _sec(
        permisos.SID_TRUSTED_INSTALLER,
        (permisos.SID_TRUSTED_INSTALLER, FULL, 0),
        (permisos.SID_SYSTEM, FULL, 0),
        (permisos.SID_ADMINISTRATORS, FULL, 0),
        ("S-1-5-32-545", READ_EXECUTE, 0),  # Usuarios: solo leer y ejecutar
    )
    assert _check(security) == []


def test_a_folder_whose_permissions_cannot_be_read_is_unsafe():
    def fail(_path):
        raise PermissionError(5, "acceso denegado")

    assert autoarranque.unsafe_folders([Path("x")], read_security=fail, user_sid=ME) == [Path("x")]


def test_enable_refuses_with_a_translated_message_and_writes_nothing(test_key, monkeypatch):
    from luzeira import idioma

    monkeypatch.setattr(autoarranque, "app_folders", lambda: [Path("x")])
    monkeypatch.setattr(autoarranque, "unsafe_folders", lambda folders: list(folders))
    idioma.set_language("es")
    with pytest.raises(autoarranque.UnsafeAppFolder) as caught:
        autoarranque.enable('"C:\\a.exe"', test_key)
    assert "otros usuarios" in str(caught.value)
    # Moverla conserva los permisos heredados (medido): el consejo es descomprimir otra vez.
    assert "descomprimir" in str(caught.value)
    assert isinstance(caught.value, autoarranque.AutostartError)  # la bandeja ya lo captura
    assert autoarranque.registered_command(test_key) is None


def test_the_real_check_reads_the_real_folders():
    # Lee (solo lee) los permisos de las carpetas de este entorno: no debe fallar.
    result = autoarranque.unsafe_folders(autoarranque.app_folders())
    assert isinstance(result, list)


def test_the_message_exists_in_every_language():
    from luzeira import idioma

    try:
        for lang in idioma.available():
            idioma.set_language(lang)
            text = idioma.t(autoarranque.UNSAFE_FOLDER_MESSAGE)
            assert text != autoarranque.UNSAFE_FOLDER_MESSAGE, lang
    finally:
        idioma.set_language("es")
