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

Objetos de otros (R4-08, medido 2026-11-24 en Windows 10 19045): un programa de
**integridad baja** (p. ej. algo que se escapa de un navegador en modo protegido) puede
crear antes ``Local\\Luzeira`` y dejar a Luzeira sin arrancar, y abrir con
``SYNCHRONIZE`` los objetos que crea Luzeira: con el mutex abierto mantiene vivo el
nombre después de cerrar Luzeira, y con el evento puede «robar» la señal de abrir el
editor. No puede señalar ni modificar los de Luzeira (Windows no deja escribir «hacia
arriba»). Por eso:

1. Luzeira crea sus objetos con una **etiqueta de integridad media** que prohíbe
   escribir, leer y ejecutar desde abajo (``S:(ML;;NWNRNX;;;ME)``; ``SYNCHRONIZE`` cuenta
   como ejecutar). Medido: el proceso de integridad baja ya no los puede abrir.
2. Si el objeto **ya existía** (183) o no se puede abrir (5), se mira quién lo creó: si
   su etiqueta es de integridad baja, o su propietario no es este usuario, SYSTEM ni
   Administradores, **no es de Luzeira**. Con el mutex, se arranca igualmente y se avisa
   en el log (mejor dos copias que ninguna: el puerto serie lo resuelve la segunda al no
   poder abrirlo). Con el evento, no se escucha ese ni se señala: crear otro con otro
   nombre no sirve, porque la segunda copia no sabría cuál.
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

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
MAX_PATH = 260
_PREFIX = "Local\\"
#: Nombre del mutex de la app. El instalador lo usa como AppMutex (MutexName del .iss).
APP_MUTEX = "Luzeira"
#: Etiqueta de integridad media que no deja escribir, leer ni ejecutar (``SYNCHRONIZE``)
#: a un proceso de integridad más baja. Sin DACL en el texto: se queda la de siempre.
LABEL_SDDL = "S:(ML;;NWNRNX;;;ME)"

#: ``(handle) -> True`` si el objeto existente es de fiar (lo creó Luzeira u otro proceso
#: del mismo usuario con integridad media o más). Se inyecta en los tests.
OwnerCheck = Callable[[int], bool]


def object_is_ours(handle: int) -> bool:
    """¿Lo creó un proceso de este usuario (o SYSTEM o un administrador) sin integridad
    baja? El handle necesita ``READ_CONTROL`` (lo traen CreateMutexW/CreateEventW)."""
    from luzeira.sistema import permisos

    security = permisos.handle_security(handle, kernel_object=True)
    if security.integrity is not None and security.integrity < permisos.INTEGRITY_MEDIUM:
        return False
    trusted = {permisos.current_user_sid(), *permisos.ADMIN_OWNERS}
    return security.owner in trusted


def _check_or_distrust(check: OwnerCheck, handle: int) -> bool:
    """``check`` sin que un fallo al leer los permisos tumbe nada: si no se puede saber,
    no se fía."""
    try:
        return check(handle)
    except Exception as exc:
        log.warning("No se pudo comprobar de quién es un objeto (%s).", type(exc).__name__)
        return False


def _label() -> object | None:
    """El descriptor con la etiqueta, o ``None`` si no se pudo crear (se sigue sin él)."""
    from luzeira.sistema.permisos import SecurityDescriptor

    try:
        return SecurityDescriptor(LABEL_SDDL)
    except (OSError, AttributeError) as exc:
        log.warning("Sin etiqueta de integridad para los objetos (%s).", type(exc).__name__)
        return None


def _attributes(descriptor: object | None) -> object | None:
    return None if descriptor is None else ctypes.byref(descriptor.attributes)  # type: ignore[attr-defined]


def _close_label(descriptor: object | None) -> None:
    if descriptor is not None:
        descriptor.close()  # type: ignore[attr-defined]


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

    ``kernel32``, ``owner_check`` y ``label`` se inyectan en los tests (con un kernel32
    falso se prueba qué pasa cuando el nombre ya lo tiene otro).
    """

    def __init__(
        self,
        name: str = APP_MUTEX,
        *,
        kernel32: object | None = None,
        owner_check: OwnerCheck = object_is_ours,
        label: Callable[[], object | None] = _label,
    ) -> None:
        if not name or "\\" in name:
            raise ValueError("El nombre del mutex no puede estar vacío ni llevar '\\'.")  # i18n: no
        self.mutex_name = _PREFIX + name
        if len(self.mutex_name) > MAX_PATH:
            raise ValueError("El nombre del mutex es demasiado largo.")
        self._kernel32 = kernel32
        self._owner_check = owner_check
        self._label = label
        self._handle: int | None = None
        #: El nombre lo tenía otro programa y se arrancó igualmente (R4-08).
        self.foreign = False

    @property
    def acquired(self) -> bool:
        return self._handle is not None or self.foreign

    def _k32(self):  # type: ignore[no-untyped-def]
        return self._kernel32 if self._kernel32 is not None else _load_kernel32()

    def acquire(self) -> bool:
        """``True`` si esta es la única instancia (o ya lo había conseguido antes)."""
        if self.acquired:
            return True
        kernel32 = self._k32()
        if kernel32 is None:
            log.warning("Sin control de instancia única en este sistema")
            return True
        # bInitialOwner = FALSE: no se pide la propiedad, solo el handle (lo recomienda
        # la documentación cuando varios procesos abren el mismo mutex).
        # La documentación solo promete fijar el error cuando el mutex ya existía: se
        # pone a 0 antes para no leer un 183 viejo de otra llamada.
        descriptor = self._label()
        try:
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(_attributes(descriptor), False, self.mutex_name)
            error = ctypes.get_last_error()
        finally:
            _close_label(descriptor)
        if not handle:
            if error == ERROR_ACCESS_DENIED:
                # Existe y no nos deja abrirlo: no lo creó Luzeira (las nuestras se abren
                # entre sí sin problema).
                return self._start_anyway()
            raise OSError(error, f"CreateMutexW falló (código {error})")  # i18n: no
        if error == ERROR_ALREADY_EXISTS:
            # Si no se puede saber de quién es, se hace lo de siempre (no arrancar dos
            # copias): un programa de integridad baja no puede provocar ese fallo, porque
            # si nos niega leer su objeto CreateMutexW ya falla con 5 (arriba).
            try:
                ours = self._owner_check(handle)
            except Exception as exc:
                log.warning("No se pudo comprobar de quién es el candado (%s).", type(exc).__name__)
                ours = True
            kernel32.CloseHandle(handle)
            if not ours:
                return self._start_anyway()
            log.info("Ya hay otra Luzeira en marcha en esta sesión")
            return False
        self._handle = handle
        return True

    def _start_anyway(self) -> bool:
        log.warning(
            "El nombre del candado de instancia única lo tiene otro programa (integridad "
            "baja o de otro usuario); Luzeira arranca igualmente."
        )
        self.foreign = True
        return True

    def release(self) -> None:
        """Suelta el candado. Seguro de llamar varias veces."""
        self.foreign = False
        if self._handle is None:
            return
        kernel32 = self._k32()
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
_READ_CONTROL = 0x00020000


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

    Si el evento lo creó otro programa (R4-08, ver arriba), ni se escucha ni se señala.

    Fuentes: «CreateEventW», «OpenEventW», «SetEvent», «WaitForSingleObject», Microsoft
    Learn (consultadas 2026-11-18).
    """

    def __init__(
        self,
        name: str = EDITOR_EVENT,
        kernel32: object | None = None,
        *,
        owner_check: OwnerCheck = object_is_ours,
        label: Callable[[], object | None] = _label,
    ) -> None:
        self.event_name = _PREFIX + name
        self._kernel32 = kernel32 if kernel32 is not None else _load_events()
        self._owner_check = owner_check
        self._label = label
        self._handle: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def request(self) -> bool:
        """Pide a la Luzeira abierta que abra el editor. ``False`` si no hay ninguna (o si
        el evento no es de Luzeira)."""
        k = self._kernel32
        if k is None:
            return False
        # READ_CONTROL: para poder mirar quién lo creó antes de señalarlo.
        handle = k.OpenEventW(_EVENT_MODIFY_STATE | _READ_CONTROL, False, self.event_name)
        if not handle:
            return False
        try:
            if not _check_or_distrust(self._owner_check, handle):
                log.warning("El aviso de abrir el editor lo creó otro programa; no se usa.")
                return False
            return bool(k.SetEvent(handle))
        finally:
            k.CloseHandle(handle)

    def listen(self, on_request: Callable[[], None], poll_ms: int = 500) -> bool:
        """Empieza a escuchar en un hilo. ``on_request`` se llama en ese hilo."""
        k = self._kernel32
        if k is None:
            return False
        # Reinicio automático (bManualReset = FALSE), empieza sin señalar.
        descriptor = self._label()
        try:
            ctypes.set_last_error(0)
            handle = k.CreateEventW(_attributes(descriptor), False, False, self.event_name)
            error = ctypes.get_last_error()
        finally:
            _close_label(descriptor)
        if not handle:
            log.warning("No se pudo crear el aviso para abrir el editor desde fuera")
            return False
        if error == ERROR_ALREADY_EXISTS and not _check_or_distrust(self._owner_check, handle):
            log.warning(
                "El aviso de abrir el editor ya lo había creado otro programa; no se escucha."
            )
            k.CloseHandle(handle)
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
