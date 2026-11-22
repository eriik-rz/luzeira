"""Una sola Luzeira a la vez por sesión de Windows.

Concepto: un *mutex con nombre* es un objeto del núcleo de Windows que vive mientras
algún proceso tenga un «handle» abierto a él. La primera Luzeira lo crea; la segunda,
al pedir el mismo nombre, recibe el existente y ``ERROR_ALREADY_EXISTS``: así sabe que
ya hay otra y debe salir (dos procesos peleándose por el puerto serie fallan los dos).
No hace falta «poseer» el mutex: basta con tener el handle abierto. Si la app muere,
Windows cierra el handle solo y el nombre queda libre.

Fuente: «CreateMutexW», Microsoft Learn (consultada 2026-11-17),
https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw

- Prefijo ``Local\\``: espacio de nombres de la **sesión** (cada usuario con sesión
  abierta puede tener su Luzeira). Tras el prefijo el nombre no admite ``\\`` y el total
  no pasa de MAX_PATH (260). Distingue mayúsculas.
- Si el nombre ya existe devuelve el handle existente y GetLastError = 183.
- Aviso de la propia documentación: otro programa de la misma sesión podría crear el
  mutex antes y **impedir que Luzeira arranque**. Se acepta: el atacante ya tendría que
  estar ejecutando código como el usuario.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Self

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
MAX_PATH = 260
_PREFIX = "Local\\"
#: Nombre del mutex de la app. El instalador lo usa como AppMutex (MutexName del .iss).
APP_MUTEX = "Luzeira"


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
    """Candado de instancia única. ``acquire()`` → ``False`` si ya hay otra Luzeira.

    Con ``with``, el resultado se mira en ``acquired``::

        with SingleInstance() as lock:
            if not lock.acquired:
                return  # ya hay otra
    """

    def __init__(self, name: str = APP_MUTEX) -> None:
        if not name or "\\" in name:
            raise ValueError("El nombre del mutex no puede estar vacío ni llevar '\\'.")  # i18n: no
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
            raise OSError(error, f"CreateMutexW falló (código {error})")  # i18n: no
        if error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            log.info("Ya hay otra Luzeira en marcha en esta sesión")
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


#: Nombre del evento con el que otra copia de Luzeira pide «abre el editor».
EDITOR_EVENT = "Luzeira-AbrirEditor"
_WAIT_OBJECT_0 = 0
_EVENT_MODIFY_STATE = 0x0002


def _load_events() -> ctypes.WinDLL | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    kernel32.SetEvent.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


class EditorRequests:
    """Avisos de «abre el editor» desde otra copia de Luzeira.

    Concepto: un *evento con nombre* es otro objeto del núcleo de Windows, como el mutex
    de arriba, pero con dos estados: señalado o no. La Luzeira abierta lo crea y espera a
    que alguien lo señale; el acceso directo del escritorio arranca otra copia que, al ver
    que ya hay una, lo señala (``request``) y se cierra. Es de reinicio automático: tras
    despertar a quien espera, vuelve solo a «no señalado».

    Fuentes: «CreateEventW», «OpenEventW», «SetEvent», «WaitForSingleObject», Microsoft
    Learn (consultadas 2026-11-18).
    """

    def __init__(self, name: str = EDITOR_EVENT, kernel32: object | None = None) -> None:
        self.event_name = _PREFIX + name
        self._kernel32 = kernel32 if kernel32 is not None else _load_events()
        self._handle: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def request(self) -> bool:
        """Pide a la Luzeira abierta que abra el editor. ``False`` si no hay ninguna."""
        k = self._kernel32
        if k is None:
            return False
        handle = k.OpenEventW(_EVENT_MODIFY_STATE, False, self.event_name)
        if not handle:
            return False
        try:
            return bool(k.SetEvent(handle))
        finally:
            k.CloseHandle(handle)

    def listen(self, on_request: Callable[[], None], poll_ms: int = 500) -> bool:
        """Empieza a escuchar en un hilo. ``on_request`` se llama en ese hilo."""
        k = self._kernel32
        if k is None:
            return False
        # Reinicio automático (bManualReset = FALSE), empieza sin señalar.
        handle = k.CreateEventW(None, False, False, self.event_name)
        if not handle:
            log.warning("No se pudo crear el aviso para abrir el editor desde fuera")
            return False
        self._handle = handle

        def loop() -> None:
            # Espera a trozos para poder parar sin cerrar el handle con alguien esperando.
            while not self._stop.is_set():
                if k.WaitForSingleObject(handle, poll_ms) == _WAIT_OBJECT_0:
                    try:
                        on_request()
                    except Exception:
                        log.exception("Falló abrir el editor a petición de otra copia")

        self._thread = threading.Thread(target=loop, name="luzeira-abrir-editor", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
