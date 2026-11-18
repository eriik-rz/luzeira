"""Acceso directo: se prueba sin tocar el escritorio real (PowerShell simulado)."""

import subprocess

import pytest

from luzeira.sistema import accesos


class FakeRun:
    def __init__(self, returncode=0, stdout="C:\\Users\\x\\Desktop\\Luzeira.lnk\n"):
        self.calls = []
        self.returncode, self.stdout = returncode, stdout

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def test_paths_go_through_environment_not_the_script():
    run = FakeRun()
    path = accesos.create_shortcut("Desktop", run=run, mark=lambda p: None)
    args, kwargs = run.calls[0]
    script = args[-1]
    env = kwargs["env"]
    # Nada del usuario dentro del texto del script: todo por variables de entorno.
    assert env["LUZEIRA_TARGET"] not in script
    assert env["LUZEIRA_ICON"].endswith("luzeira.ico") and env["LUZEIRA_ICON"] not in script
    assert env["LUZEIRA_FOLDER"] == "Desktop" and env["LUZEIRA_NAME"] == "Luzeira.lnk"
    assert path.name == "Luzeira.lnk"


def test_launch_command_is_windowless_module():
    cmd = accesos.launch_command()
    assert cmd.arguments == "-m luzeira"
    assert cmd.target.lower().endswith(("pythonw.exe", "python.exe"))


def test_icon_file_exists():
    assert accesos.ICON.is_file()


def test_failure_gives_user_message():
    with pytest.raises(accesos.ShortcutError, match="acceso directo"):
        accesos.create_shortcut("Desktop", run=FakeRun(returncode=1, stdout=""))


def test_only_known_folders():
    with pytest.raises(ValueError):
        accesos.create_shortcut("C:\\Windows", run=FakeRun())


def test_shortcut_gets_the_app_id():
    marked = []
    path = accesos.create_shortcut("Desktop", run=FakeRun(), mark=marked.append)
    # Sin el identificador, al anclarlo saldría «pythonw» (ver sistema/identidad.py).
    assert marked == [path]


def test_relaunch_command_quotes_the_executable():
    cmd = accesos.launch_command()
    assert accesos.relaunch_command() == f'"{cmd.target}" -m luzeira'


def test_command_line_passes_open_editor_to_the_tray(monkeypatch):
    from luzeira import __main__ as cli
    from luzeira import bandeja

    calls = []
    monkeypatch.setattr(bandeja, "main", lambda open_editor=False: calls.append(open_editor) or 0)
    assert cli.main(["--abrir-editor"]) == 0
    assert cli.main([]) == 0
    assert calls == [True, False]


def test_powershell_by_full_path():
    run = FakeRun()
    accesos.create_shortcut("Desktop", run=run, mark=lambda p: None)
    exe = run.calls[0][0][0]
    # Por nombre, Windows lo buscaría antes en la carpeta actual y en la de la app.
    assert exe.lower().endswith(r"system32\windowspowershell\v1.0\powershell.exe")
