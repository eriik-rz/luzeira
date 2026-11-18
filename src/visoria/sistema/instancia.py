"""Una sola Visoria a la vez por sesión de Windows.

Concepto: un *mutex con nombre* es un objeto del núcleo de Windows que vive mientras
algún proceso tenga un «handle» abierto a él. La primera Visoria lo crea; la segunda,
al pedir el mismo nombre, recibe el existente y ``ERROR_ALREADY_EXISTS``: así sabe que
ya hay otra y debe salir (dos procesos peleándose por el puerto serie fallan los dos).
No hace falta «poseer» el mutex: basta con tener el handle abierto. Si la app muere,
Windows cierra el handle solo y el nombre queda libre.

Fuente: «CreateMutexW», Microsoft Learn (consultada 2026-11-17),
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw

- Prefijo ``Local\\``: espacio de nombres de la **sesión** (cada usuario con sesión
  abierta puede tener su Visoria). Tras el prefijo el nombre no admite ``\\`` y el total
  no pasa de MAX_PATH (260). Distingue mayúsculas.
- Si el nombre ya existe devuelve el handle existente y GetLastError = 183.
- Aviso de la propia documentación: otro programa de la misma sesión podría crear el
  mutex antes y **impedir que Visoria arranque**. Se acepta: el atacante ya tendría que
  estar ejecutando código como el usuario.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from types import TracebackType
from typing import Self

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
MAX_PATH = 260
_PREFIX = "Local\\"


def _load_kernel32() -> ctypes.WinDLL | None:
    if sys.platform != "win32":
        return None
    # WinDLL propio (no ctypes.windll compartido) para que los argtypes de aquí no
    # choquen con los de otros módulos.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


class SingleInstance:
    """Candado de instancia única. ``acquire()`` → ``False`` si ya hay otra Visoria.

    Con ``with``, el resultado se mira en ``acquired``::

        with SingleInstance() as lock:
            if not lock.acquired:
                return  # ya hay otra
    """

    def __init__(self, name: str = "Visoria") -> None:
        if not name or "\\" in name:
            raise ValueError("El nombre del mutex no puede estar vacío ni llevar '\\'.")
        self.mutex_name = _PREFIX + name
        if len(self.mutex_name) > MAX_PATH:
            raise ValueError("El nombre del mutex es demasiado largo.")
        self._handle: int | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """``True`` si esta es la única instancia (o ya lo había conseguido antes)."""
        if self._handle is not None:
            return True
        kernel32 = _load_kernel32()
        if kernel32 is None:
            log.warning("Sin control de instancia única en este sistema")
            return True
        # bInitialOwner = FALSE: no se pide la propiedad, solo el handle (lo recomienda
        # la documentación cuando varios procesos abren el mismo mutex).
        # La documentación solo promete fijar el error cuando el mutex ya existía: se
        # pone a 0 antes para no leer un 183 viejo de otra llamada.
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self.mutex_name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, f"CreateMutexW falló (código {error})")
        if error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            log.info("Ya hay otra Visoria en marcha en esta sesión")
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Suelta el candado. Seguro de llamar varias veces."""
        if self._handle is None:
            return
        kernel32 = _load_kernel32()
        if kernel32 is not None and not kernel32.CloseHandle(self._handle):
            log.error(
                "No se pudo cerrar el mutex de instancia (código %d)", ctypes.get_last_error()
            )
        self._handle = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
