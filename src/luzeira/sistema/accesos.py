"""Acceso directo de Luzeira en el escritorio, con su icono.

Se crea con el objeto estándar de Windows ``WScript.Shell`` desde PowerShell. Las rutas
van por **variables de entorno**, no metidas en el texto del script: así una carpeta con
comillas o símbolos raros no puede inyectar órdenes. El escritorio lo resuelve Windows
(``GetFolderPath('Desktop')``), también si está redirigido a OneDrive.

El instalador crea los del menú Inicio y, si se marca, el del escritorio; este módulo es
el de la opción de la bandeja (también para la versión portable, que no tiene instalador).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from luzeira.idioma import t
from luzeira.sistema.identidad import set_shortcut_app_id

ICON = Path(__file__).resolve().parent.parent / "recursos" / "luzeira.ico"
SHORTCUT_NAME = "Luzeira.lnk"
#: Lo que añade el acceso del escritorio: abrir el editor (ver bandeja.main).
OPEN_EDITOR_FLAG = "--abrir-editor"

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$folder = [Environment]::GetFolderPath($env:LUZEIRA_FOLDER)
$path = Join-Path $folder $env:LUZEIRA_NAME
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $env:LUZEIRA_TARGET
$link.Arguments = $env:LUZEIRA_ARGS
$link.WorkingDirectory = $env:LUZEIRA_WORKDIR
$link.IconLocation = $env:LUZEIRA_ICON + ',0'
$link.Description = 'Luzeira: tu pantalla USB del PC'
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
    """Cómo se arranca Luzeira sin consola: el .exe empaquetado, o pythonw -m luzeira."""
    if getattr(sys, "frozen", False):
        return LaunchCommand(sys.executable, "")
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return LaunchCommand(str(windowless if windowless.exists() else exe), "-m luzeira")


def _powershell() -> str:
    """PowerShell de Windows por su ruta completa: por nombre, Windows lo buscaría antes en
    la carpeta actual y en la de la app."""
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def create_shortcut(
    folder: str = "Desktop", *, run=subprocess.run, mark=set_shortcut_app_id
) -> Path:
    """Crea (o actualiza) ``Luzeira.lnk`` en ``folder`` (``Desktop`` o ``Programs``).

    Devuelve la ruta del acceso directo. ``run`` y ``mark`` se inyectan para probar sin
    tocar el escritorio de verdad. ``mark`` le pone el identificador de Luzeira (ver
    ``identidad.py``): sin él, al anclarlo saldría como «pythonw». Si falla, el acceso
    directo funciona igual.
    """
    if folder not in ("Desktop", "Programs"):
        raise ValueError(folder)
    cmd = launch_command()
    env = {
        **os.environ,
        "LUZEIRA_FOLDER": folder,
        "LUZEIRA_NAME": SHORTCUT_NAME,
        "LUZEIRA_TARGET": cmd.target,
        # El del escritorio abre también el editor (es lo que espera quien
        # hace doble clic en un icono: ver la app). El de Inicio, solo la bandeja.
        "LUZEIRA_ARGS": f"{cmd.arguments} {OPEN_EDITOR_FLAG}".strip()
        if folder == "Desktop"
        else cmd.arguments,
        "LUZEIRA_WORKDIR": str(Path.home()),
        "LUZEIRA_ICON": str(ICON),
    }
    try:
        result = run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ShortcutError(t("sistema.acceso_fallo")) from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ShortcutError(t("sistema.acceso_fallo"))
    path = Path(result.stdout.strip().splitlines()[-1])
    mark(path)
    return path


def relaunch_command() -> str:
    """La línea completa para volver a abrir Luzeira (para anclar la ventana)."""
    cmd = launch_command()
    return f'"{cmd.target}" {cmd.arguments}'.strip()
