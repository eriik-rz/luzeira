"""Identificador de la app en Windows (AUMID): que al anclar salga «Luzeira»."""

import os
import subprocess
import sys

import pytest

from luzeira.sistema import identidad


class FakeShell32:
    def __init__(self, result=0):
        self.result, self.calls = result, []

    def SetCurrentProcessExplicitAppUserModelID(self, app_id):
        self.calls.append(app_id.value)
        return self.result


def test_process_gets_the_luzeira_id():
    shell32 = FakeShell32()
    assert identidad.set_process_app_id(shell32) is True
    assert shell32.calls == ["Luzeira.Luzeira"]


def test_process_id_failure_is_not_fatal():
    assert identidad.set_process_app_id(FakeShell32(result=-2147024809)) is False


def test_app_id_follows_windows_rules():
    # Microsoft: máx. 128 caracteres, sin espacios, formato «Empresa.Producto».
    assert len(identidad.APP_ID) <= 128 and " " not in identidad.APP_ID
    assert identidad.APP_ID.count(".") == 1


@pytest.mark.skipif(sys.platform != "win32", reason="accesos directos de Windows")
def test_shortcut_is_marked_and_read_back(tmp_path):
    # Un .lnk de prueba en una carpeta temporal: nunca en el escritorio ni en Inicio.
    link = tmp_path / "prueba.lnk"
    script = (
        "$l = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$l.TargetPath = $env:WINDIR + '\\notepad.exe'; $l.Save()"
    )
    env = {**os.environ, "LNK": str(link)}
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert identidad.read_shortcut_app_id(link) is None
    assert identidad.set_shortcut_app_id(link) is True
    assert identidad.read_shortcut_app_id(link) == "Luzeira.Luzeira"


@pytest.mark.skipif(sys.platform != "win32", reason="accesos directos de Windows")
def test_missing_shortcut_fails_softly(tmp_path):
    assert identidad.set_shortcut_app_id(tmp_path / "no-existe.lnk") is False
