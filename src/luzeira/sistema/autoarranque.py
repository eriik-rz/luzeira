"""Autoarranque con la sesión del usuario, sin admin (RQ-06).

Se usa la clave ``Run`` **del usuario** (HKCU), que no pide privilegios. Nunca la de la
máquina (HKLM): esa exige admin y afectaría a todos los usuarios.

Fuente: «Run and RunOnce Registry Keys», Microsoft Learn,
https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys
(consultada 2026-11-17). De ahí salen tres reglas que este módulo respeta:

1. La línea de órdenes no puede pasar de **260 caracteres** (``MAX_COMMAND_LENGTH``).
2. Un programa lanzado desde ``Run`` **no debe escribir en esa clave mientras se
   ejecuta**: ``enable()``/``disable()`` solo se llaman cuando el usuario cambia la
   opción en la bandeja, nunca automáticamente en cada arranque.
3. Windows **no garantiza cuándo** se lanzan los programas de ``Run``: puede
   retrasarlos. Por eso la app debe tolerar arrancar tarde (y reintentar la pantalla).

La versión instalada y la portable no tienen ``.exe`` propio (fase 6, D-12): son el
Python embebible, así que ``default_command()`` da ``"<carpeta>\\python\\pythonw.exe" -m
luzeira``, la misma línea que escribe el instalador. ``sys.frozen`` queda por si algún
día se empaqueta con PyInstaller.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

try:  # winreg solo existe en Windows; fuera de él el módulo se importa igual.
    import winreg
except ImportError:  # pragma: no cover - solo fuera de Windows
    winreg = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

#: Ruta de la clave Run del usuario (bajo HKEY_CURRENT_USER).
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: Nombre del valor que crea Luzeira dentro de la clave.
VALUE_NAME = "Luzeira"
#: Límite documentado por Microsoft para la línea de órdenes de un valor de Run.
MAX_COMMAND_LENGTH = 260


class AutostartError(RuntimeError):
    """No se pudo leer o cambiar el autoarranque (con un mensaje para el usuario)."""


def _require_winreg() -> None:
    if winreg is None:
        raise AutostartError("El arranque automático solo está disponible en Windows.")


def registered_command(key_path: str = RUN_KEY_PATH, name: str = VALUE_NAME) -> str | None:
    """Línea de órdenes registrada ahora mismo, o ``None`` si no hay ninguna.

    Sirve para detectar un autoarranque «viejo» (p. ej. el entorno se movió de sitio).
    """
    _require_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except FileNotFoundError:  # no existe la clave o no existe el valor
        return None
    except OSError as exc:
        raise AutostartError(f"No se pudo leer el arranque automático: {exc}") from exc
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
        log.warning("El valor de autoarranque tiene un tipo inesperado (%s); se ignora", kind)
        return None
    return value


def is_enabled(key_path: str = RUN_KEY_PATH, name: str = VALUE_NAME) -> bool:
    """``True`` si existe el valor de Luzeira en la clave Run.

    Ojo: si el usuario lo desactiva desde el Administrador de tareas, Windows **no borra**
    el valor, sino que lo marca en otra clave (``Explorer\\StartupApproved\\Run``), cuyo
    formato no está documentado por Microsoft. Esta función no la mira: para eso está
    :func:`blocked_by_user`.
    """
    return registered_command(key_path, name) is not None


#: Donde marca Windows lo que el usuario desactiva en el Administrador de tareas (C-24).
APPROVED_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
#: Primer byte del valor cuando está desactivado. El formato **no lo documenta
#: Microsoft**: es lo que se observa (``02`` activado, ``03`` desactivado). Por eso solo se
#: lee, y ante cualquier otra cosa se da por activado, como hasta ahora.
_DISABLED_BY_USER = 0x03


def blocked_by_user(key_path: str = APPROVED_KEY_PATH, name: str = VALUE_NAME) -> bool:
    """``True`` si el usuario desactivó Luzeira en el Administrador de tareas (C-24).

    El valor de ``Run`` sigue ahí, pero Windows no lo lanza, y escribir de nuevo en
    ``Run`` no lo arregla: se reactiva en el mismo sitio donde se quitó.
    """
    _require_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise AutostartError(f"No se pudo leer el arranque automático: {exc}") from exc
    return kind == winreg.REG_BINARY and isinstance(value, bytes) and value[:1] == b"\x03"


def enable(command: str, key_path: str = RUN_KEY_PATH, name: str = VALUE_NAME) -> None:
    """Crea o actualiza el valor de autoarranque con ``command``.

    Lanza ``ValueError`` si la orden está vacía, tiene saltos de línea o pasa de 260
    caracteres, y ``AutostartError`` si el registro rechaza la escritura.
    """
    _require_winreg()
    if not command.strip():
        raise ValueError("La orden de autoarranque está vacía.")
    if "\n" in command or "\r" in command or "\0" in command:
        raise ValueError("La orden de autoarranque no puede tener saltos de línea.")
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError(
            f"La orden de autoarranque tiene {len(command)} caracteres y Windows admite "
            f"{MAX_COMMAND_LENGTH} como máximo."
        )
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
    except OSError as exc:
        raise AutostartError(f"No se pudo activar el arranque automático: {exc}") from exc
    log.info("Arranque automático activado")


def disable(key_path: str = RUN_KEY_PATH, name: str = VALUE_NAME) -> None:
    """Borra el valor de autoarranque. Si no existía, no hace nada."""
    _require_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AutostartError(f"No se pudo desactivar el arranque automático: {exc}") from exc
    log.info("Arranque automático desactivado")


def default_command(args: Sequence[str] = ()) -> str:
    """Línea para arrancar la app actual **sin ventana de consola**.

    - Congelada (``sys.frozen``, PyInstaller; hoy no se usa): su propio ``.exe``.
    - Si no: ``pythonw.exe -m luzeira`` del entorno actual (``pythonw`` no abre consola);
      si no hay ``pythonw.exe`` al lado del intérprete, ``sys.executable``.

    El ejecutable va **siempre entre comillas** (las rutas pueden tener espacios y una
    ruta sin comillas con espacios es ambigua para ``CreateProcess``). El resto de
    argumentos se entrecomilla con las reglas de Windows (``subprocess.list2cmdline``).
    """
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        parts: list[str] = list(args)
    else:
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            exe = pythonw
        parts = ["-m", "luzeira", *args]
    line = f'"{exe}"'
    if parts:
        line += " " + subprocess.list2cmdline(parts)
    return line
