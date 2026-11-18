"""Acceso directo de Visoria en el escritorio, con su icono.

Se crea con el objeto estándar de Windows ``WScript.Shell`` desde PowerShell. Las rutas
van por **variables de entorno**, no metidas en el texto del script: así una carpeta con
comillas o símbolos raros no puede inyectar órdenes. El escritorio lo resuelve Windows
(``GetFolderPath('Desktop')``), también si está redirigido a OneDrive.

Cuando exista el instalador (fase 6), será él quien cree los accesos directos.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from visoria.sistema.identidad import set_shortcut_app_id

ICON = Path(__file__).resolve().parent.parent / "recursos" / "visoria.ico"
SHORTCUT_NAME = "Visoria.lnk"

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$folder = [Environment]::GetFolderPath($env:VISORIA_FOLDER)
$path = Join-Path $folder $env:VISORIA_NAME
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $env:VISORIA_TARGET
$link.Arguments = $env:VISORIA_ARGS
$link.WorkingDirectory = $env:VISORIA_WORKDIR
$link.IconLocation = $env:VISORIA_ICON + ',0'
$link.Description = 'Visoria: tu pantalla USB del PC'
$link.Save()
Write-Output $path
"""


class ShortcutError(RuntimeError):
    """No se pudo crear el acceso directo (mensaje para el usuario)."""


@dataclass(frozen=True)
class LaunchCommand:
    target: str
    arguments: str


def launch_command() -> LaunchCommand:
    """Cómo se arranca Visoria sin consola: el .exe empaquetado, o pythonw -m visoria."""
    if getattr(sys, "frozen", False):
        return LaunchCommand(sys.executable, "")
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return LaunchCommand(str(windowless if windowless.exists() else exe), "-m visoria")


def create_shortcut(
    folder: str = "Desktop", *, run=subprocess.run, mark=set_shortcut_app_id
) -> Path:
    """Crea (o actualiza) ``Visoria.lnk`` en ``folder`` (``Desktop`` o ``Programs``).

    Devuelve la ruta del acceso directo. ``run`` y ``mark`` se inyectan para probar sin
    tocar el escritorio de verdad. ``mark`` le pone el identificador de Visoria (ver
    ``identidad.py``): sin él, al anclarlo saldría como «pythonw». Si falla, el acceso
    directo funciona igual.
    """
    if folder not in ("Desktop", "Programs"):
        raise ValueError(folder)
    cmd = launch_command()
    env = {
        **os.environ,
        "VISORIA_FOLDER": folder,
        "VISORIA_NAME": SHORTCUT_NAME,
        "VISORIA_TARGET": cmd.target,
        "VISORIA_ARGS": cmd.arguments,
        "VISORIA_WORKDIR": str(Path.home()),
        "VISORIA_ICON": str(ICON),
    }
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ShortcutError("No he podido crear el acceso directo.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ShortcutError("No he podido crear el acceso directo.")
    path = Path(result.stdout.strip().splitlines()[-1])
    mark(path)
    return path


def relaunch_command() -> str:
    """La línea completa para volver a abrir Visoria (para anclar la ventana)."""
    cmd = launch_command()
    return f'"{cmd.target}" {cmd.arguments}'.strip()
