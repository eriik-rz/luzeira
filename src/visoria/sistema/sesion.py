"""Aviso de apagado, reinicio o cierre de sesión sin consola (RQ-07).

Concepto: con ``pythonw`` no hay consola, así que ``SetConsoleCtrlHandler`` no sirve (y
además deja de recibir el apagado en cuanto el proceso carga user32, como hacen pystray
y Pillow). Windows avisa del fin de sesión a las **ventanas de nivel superior** con dos
mensajes:

1. ``WM_QUERYENDSESSION``: «¿puedo cerrar?». Se responde TRUE enseguida; Visoria nunca
   bloquea el apagado.
2. ``WM_ENDSESSION`` con ``wParam`` TRUE: «se cierra de verdad». Aquí se apaga la
   pantalla. Después de volver, Windows puede terminar el proceso en cualquier momento.

Para recibirlos, ``SessionWatcher`` crea **su propia ventana oculta** (nunca se muestra)
en un hilo con su bucle de mensajes. No puede ser *message-only* (``HWND_MESSAGE``):
esas no reciben mensajes de difusión.

Fuentes (Microsoft Learn, consultadas 2026-11-17):

- WM_QUERYENDSESSION (devolver TRUE; 5 s antes de que Windows señale a la app):
  https://learn.microsoft.com/en-us/windows/win32/shutdown/wm-queryendsession
- WM_ENDSESSION (wParam TRUE = la sesión termina; devolver 0; no hace falta
  DestroyWindow ni PostQuitMessage al cerrar sesión):
  https://learn.microsoft.com/en-us/windows/win32/shutdown/wm-endsession
- Apps sin ventana visible que devuelven FALSE se terminan sin más:
  https://learn.microsoft.com/en-us/windows/win32/shutdown/shutdown-changes-for-windows-vista
- SetConsoleCtrlHandler no recibe el apagado si se carga user32/gdi32; remite a una
  ventana oculta: https://learn.microsoft.com/en-us/windows/console/setconsolectrlhandler
- Las ventanas message-only no reciben difusiones:
  https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features
- DestroyWindow solo desde el hilo que creó la ventana (por eso ``stop()`` envía
  WM_CLOSE y es el hilo de la ventana quien la destruye):
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-destroywindow
- UnregisterClassW (antes hay que destruir las ventanas de la clase):
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-unregisterclassw
- RegisterClassExW, CreateWindowExW, GetMessageW, DispatchMessageW, DefWindowProcW,
  PostMessageW, PostQuitMessage: referencia de winuser.h en
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import uuid
from collections.abc import Callable
from ctypes import wintypes
from types import TracebackType
from typing import Self

log = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WS_POPUP = 0x80000000

_START_TIMEOUT = 5.0
_STOP_TIMEOUT = 5.0

if sys.platform == "win32":
    LRESULT = wintypes.LPARAM  # LONG_PTR: mismo tamaño que LPARAM
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

else:  # pragma: no cover - solo fuera de Windows
    LRESULT = WNDPROC = WNDCLASSEXW = None


def _load_user32() -> tuple[ctypes.WinDLL, ctypes.WinDLL] | None:
    """user32 y kernel32 con firmas declaradas, o ``None`` fuera de Windows."""
    if sys.platform != "win32":
        return None
    # WinDLL propios para que estos argtypes no choquen con los de pystray u otros.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h, u, wp, lp = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        h, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]  # fmt: skip
    user32.CreateWindowExW.restype = h
    user32.DefWindowProcW.argtypes = [h, u, wp, lp]
    user32.DefWindowProcW.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), h, u, u]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [h, u, wp, lp]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [h]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, kernel32


class SessionWatcher:
    """Llama a ``on_end_session`` cuando Windows se apaga, reinicia o cierra la sesión.

    ``on_end_session`` debe ser **rápida** (apagar la pantalla): Windows da unos 5 s y
    puede terminar el proceso en cuanto vuelve. Corre en el hilo de la ventana.

    Uso::

        with SessionWatcher(apagar_pantalla):
            ...
    """

    def __init__(self, on_end_session: Callable[[], object]) -> None:
        self._on_end_session = on_end_session
        self.class_name = f"VisoriaSessionWatcher-{uuid.uuid4().hex}"
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._api: tuple[ctypes.WinDLL, ctypes.WinDLL] | None = None
        # La callback nativa vive en el objeto: si el GC la liberase con la ventana viva,
        # Windows saltaría a memoria liberada.
        self._wndproc = WNDPROC(self._window_proc) if WNDPROC else None

    @property
    def hwnd(self) -> int | None:
        """Handle de la ventana oculta mientras está en marcha (para tests)."""
        return self._hwnd

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._hwnd is not None

    def start(self) -> bool:
        """Crea la ventana en su hilo. ``False`` (con el motivo en el log) si no se pudo."""
        if self.active:
            return True
        self._api = _load_user32()
        if self._api is None or self._wndproc is None:
            log.warning(
                "Aviso de apagado no disponible en este sistema: la pantalla no se "
                "apagará sola al apagar el equipo"
            )
            return False
        ready = threading.Event()
        thread = threading.Thread(
            target=self._run, args=(ready,), name="visoria-sesion", daemon=True
        )
        self._thread = thread
        thread.start()
        if not ready.wait(_START_TIMEOUT):
            log.error("La ventana de aviso de apagado no arrancó a tiempo")
            return False
        if self._hwnd is None:
            thread.join(_STOP_TIMEOUT)
            self._thread = None
            return False
        log.info("Escuchando avisos de apagado y cierre de sesión")
        return True

    def stop(self) -> None:
        """Cierra la ventana, termina el hilo y da de baja la clase. Seguro de repetir."""
        thread, hwnd = self._thread, self._hwnd
        if thread is None:
            return
        if hwnd is not None and self._api is not None:
            user32 = self._api[0]
            if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
                log.error(
                    "No se pudo pedir el cierre de la ventana de aviso (código %d)",
                    ctypes.get_last_error(),
                )
        if thread is threading.current_thread():
            return  # llamado desde la propia callback: el hilo acabará solo
        thread.join(_STOP_TIMEOUT)
        if thread.is_alive():
            log.error("El hilo de aviso de apagado no terminó a tiempo")
            return
        self._thread = None

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

    # --- hilo de la ventana ---------------------------------------------------------

    def _run(self, ready: threading.Event) -> None:
        assert self._api is not None  # noqa: S101 - start() ya lo comprobó
        user32, kernel32 = self._api
        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = self.class_name
        try:
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                log.error(
                    "No se pudo registrar la clase de ventana de aviso (código %d)",
                    ctypes.get_last_error(),
                )
                return
            try:
                # Sin padre (nivel superior) y sin WS_VISIBLE: nunca se muestra.
                hwnd = user32.CreateWindowExW(
                    0, self.class_name, "Visoria", WS_POPUP, 0, 0, 0, 0, None, None, hinstance, None
                )
                if not hwnd:
                    log.error(
                        "No se pudo crear la ventana de aviso (código %d)", ctypes.get_last_error()
                    )
                    return
                self._hwnd = hwnd
                ready.set()
                self._message_loop(user32)
            finally:
                self._hwnd = None
                if not user32.UnregisterClassW(self.class_name, hinstance):
                    log.error(
                        "No se pudo dar de baja la clase de ventana de aviso (código %d)",
                        ctypes.get_last_error(),
                    )
        finally:
            ready.set()  # que start() no espere en vano si algo falló

    @staticmethod
    def _message_loop(user32: ctypes.WinDLL) -> None:
        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0:  # WM_QUIT
                return
            if result == -1:
                log.error("Error en el bucle de mensajes (código %d)", ctypes.get_last_error())
                return
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Procedimiento de ventana. Nunca deja escapar una excepción hacia Windows."""
        try:
            if msg == WM_QUERYENDSESSION:
                return 1  # TRUE: Visoria nunca bloquea el apagado
            if msg == WM_ENDSESSION:
                if wparam:
                    log.info("Windows cierra la sesión o se apaga")
                    try:
                        self._on_end_session()
                    except Exception:
                        log.exception("Falló on_end_session al cerrar la sesión")
                return 0
            if msg == WM_CLOSE:
                self._api[0].DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._api[0].PostQuitMessage(0)
                return 0
        except BaseException:  # nada debe volver a Windows en forma de excepción
            log.exception("Error atendiendo el mensaje de ventana %#x", msg)
            return 1 if msg == WM_QUERYENDSESSION else 0
        return self._api[0].DefWindowProcW(hwnd, msg, wparam, lparam)
