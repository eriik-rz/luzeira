"""Avisos de suspensión y reanudación del equipo (RQ-07), sin ventana y sin admin.

Concepto: Windows puede llamar a una función nuestra cuando el equipo va a dormir o
vuelve. Se registra con ``PowerRegisterSuspendResumeNotification`` (powrprof.dll)
pasando ``DEVICE_NOTIFY_CALLBACK``: así no hace falta ventana ni bucle de mensajes;
Windows llama a la callback desde **un hilo suyo**.

Fuentes (Microsoft Learn, consultadas 2026-11-17):

- PowerRegisterSuspendResumeNotification (Flags debe ser DEVICE_NOTIFY_CALLBACK;
  devuelve 0 si va bien; Windows 8+, «desktop apps»):
  https://learn.microsoft.com/en-us/windows/win32/api/powerbase/nf-powerbase-powerregistersuspendresumenotification
- PowerUnregisterSuspendResumeNotification:
  https://learn.microsoft.com/en-us/windows/win32/api/powerbase/nf-powerbase-powerunregistersuspendresumenotification
- DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS (Callback + Context):
  https://learn.microsoft.com/en-us/windows/win32/api/powrprof/ns-powrprof-device_notify_subscribe_parameters
- DEVICE_NOTIFY_CALLBACK_ROUTINE: ``ULONG f(PVOID Context, ULONG Type, PVOID Setting)``:
  https://learn.microsoft.com/en-us/windows/win32/api/powrprof/nc-powrprof-device_notify_callback_routine
- Valores: PBT_APMSUSPEND = 4, PBT_APMRESUMESUSPEND = 7, PBT_APMRESUMEAUTOMATIC = 18:
  https://learn.microsoft.com/en-us/windows/win32/power/wm-powerbroadcast
- PBT_APMSUSPEND: Windows da **unos 2 s** para atenderlo; después puede interrumpir:
  https://learn.microsoft.com/en-us/windows/win32/power/pbt-apmsuspend
- PBT_APMRESUMEAUTOMATIC llega **siempre** al volver; PBT_APMRESUMESUSPEND solo si
  hubo actividad del usuario (no llega con Wake-on-LAN). Si el equipo despierta solo
  para hibernar enseguida, no llega ninguno:
  https://learn.microsoft.com/en-us/windows/win32/power/pbt-apmresumeautomatic
  https://learn.microsoft.com/en-us/windows/win32/power/pbt-apmresumesuspend

Consecuencias para quien use esta clase:

- ``on_suspend`` debe ser **rápida** (apagar la pantalla, nada más): corre dentro del
  plazo de ~2 s que da Windows y bloquea la callback nativa mientras dura.
- ``on_resume`` se llama **una vez** por cada vuelta, con el primer aviso de reanudación
  que llegue (normalmente PBT_APMRESUMEAUTOMATIC); el segundo se descarta.
- Las dos callbacks corren en un hilo de Windows, no en el hilo principal.

DEVICE_NOTIFY_CALLBACK = 2 (winuser.h del SDK de Windows; la página de la API lo nombra
pero no da el número).
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

PBT_APMSUSPEND = 0x4
PBT_APMRESUMESUSPEND = 0x7
PBT_APMRESUMEAUTOMATIC = 0x12
DEVICE_NOTIFY_CALLBACK = 2
ERROR_SUCCESS = 0

_RESUME_EVENTS = frozenset({PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND})

# Firma de la callback nativa. WINFUNCTYPE = convención stdcall (la de las callbacks de
# Windows; en 64 bits solo hay una convención y da igual). Solo existe en Windows.
if sys.platform == "win32":
    _CALLBACK_TYPE = ctypes.WINFUNCTYPE(
        ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p
    )

    class _SubscribeParameters(ctypes.Structure):
        _fields_ = [("Callback", _CALLBACK_TYPE), ("Context", ctypes.c_void_p)]

else:  # pragma: no cover - solo fuera de Windows
    _CALLBACK_TYPE = None
    _SubscribeParameters = None

# Escuchadores registrados ahora mismo. Esta referencia global impide que el recolector
# de basura libere la callback mientras Windows aún puede llamarla (si pasara, Windows
# saltaría a memoria liberada y el proceso caería sin aviso).
_ACTIVE: set[PowerListener] = set()


def _load_api() -> tuple[Callable[..., int], Callable[..., int]] | None:
    """Carga las dos funciones de powrprof.dll, o ``None`` si no están (otro SO, Win7)."""
    if sys.platform != "win32":
        return None
    try:
        powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
        register = powrprof.PowerRegisterSuspendResumeNotification
        unregister = powrprof.PowerUnregisterSuspendResumeNotification
    except (OSError, AttributeError):
        return None
    register.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    register.restype = ctypes.c_ulong
    unregister.argtypes = [ctypes.c_void_p]
    unregister.restype = ctypes.c_ulong
    return register, unregister


class PowerListener:
    """Llama a ``on_suspend`` al suspender y a ``on_resume`` al volver.

    Uso::

        with PowerListener(screen_off, screen_on):
            ...  # la app corriendo

    ``start()`` devuelve ``True`` si quedó escuchando. Si la API no existe (otro sistema)
    o Windows la rechaza, registra un aviso en el log y devuelve ``False``, sin lanzar.
    """

    def __init__(self, on_suspend: Callable[[], object], on_resume: Callable[[], object]) -> None:
        self._on_suspend = on_suspend
        self._on_resume = on_resume
        self._lock = threading.Lock()
        self._handle: ctypes.c_void_p | None = None
        self._unregister: Callable[..., int] | None = None
        # Último tipo de evento atendido: "suspend", "resume" o None (aún ninguno).
        self._last: str | None = None
        # La callback nativa y la estructura se guardan en el objeto (y el objeto en
        # _ACTIVE mientras está registrado) para que no las recoja el GC.
        self._native = _CALLBACK_TYPE(self._native_callback) if _CALLBACK_TYPE else None
        self._params = _SubscribeParameters(self._native, None) if _SubscribeParameters else None

    @property
    def active(self) -> bool:
        """``True`` mientras está registrado en Windows."""
        return self._handle is not None

    def start(self) -> bool:
        """Se registra en Windows. Llamarlo dos veces no registra dos veces."""
        if self._handle is not None:
            return True
        api = _load_api()
        if api is None or self._params is None:
            log.warning(
                "Avisos de suspensión no disponibles en este sistema: la pantalla no se "
                "apagará sola al suspender"
            )
            return False
        register, unregister = api
        handle = ctypes.c_void_p()
        result = register(DEVICE_NOTIFY_CALLBACK, ctypes.byref(self._params), ctypes.byref(handle))
        if result != ERROR_SUCCESS or not handle.value:
            log.error("Windows rechazó el registro de avisos de suspensión (código %d)", result)
            return False
        self._handle = handle
        self._unregister = unregister
        _ACTIVE.add(self)
        log.info("Escuchando avisos de suspensión y reanudación")
        return True

    def stop(self) -> None:
        """Se da de baja. Seguro de llamar aunque no se haya arrancado."""
        if self._handle is None or self._unregister is None:
            return
        result = self._unregister(self._handle)
        if result != ERROR_SUCCESS:
            log.error("No se pudo cancelar el registro de avisos de suspensión (código %d)", result)
        self._handle = None
        _ACTIVE.discard(self)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def _native_callback(self, context: int | None, event: int, setting: int | None) -> int:
        """Lo que llama Windows. Nunca deja escapar una excepción y siempre devuelve 0."""
        try:
            self._dispatch(event)
        except BaseException:  # nada debe volver a Windows en forma de excepción
            log.exception("Error atendiendo el aviso de energía %d", event)
        return ERROR_SUCCESS

    def _dispatch(self, event: int) -> None:
        if event == PBT_APMSUSPEND:
            with self._lock:
                self._last = "suspend"
            log.info("El equipo se va a suspender")
            self._call(self._on_suspend, "on_suspend")
        elif event in _RESUME_EVENTS:
            with self._lock:
                if self._last == "resume":
                    return  # segundo aviso de la misma vuelta: ya atendido
                self._last = "resume"
            log.info("El equipo ha vuelto de la suspensión")
            self._call(self._on_resume, "on_resume")
        else:
            log.debug("Aviso de energía ignorado: %d", event)

    @staticmethod
    def _call(func: Callable[[], object], label: str) -> None:
        try:
            func()
        except Exception:
            log.exception("Falló %s al atender un aviso de energía", label)
