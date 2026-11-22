"""Ventana del editor visual (pywebview sobre WebView2).

**Va en un proceso aparte.** El icono de la bandeja (pystray) y la ventana de pywebview
necesitan cada uno el hilo principal de su proceso, así que no pueden convivir. Además,
si la ventana falla, la bandeja y la pantalla siguen funcionando.

El proceso de la bandeja (dueño del servidor local del editor) lanza este proceso y le
pasa la dirección y el token por **stdin**: no aparecen en la línea de órdenes (que
cualquier programa del usuario puede listar) ni en variables de entorno. La página
recibe el token por el puente de pywebview (``window.pywebview.api.token()``), nunca en
la URL.

Después, **stdin sigue abierto** como canal de órdenes de la bandeja a la ventana, una
por línea (contrato «Remate 2» de la fase 5):

- ``cerrar``: cerrar por el camino normal (guardando lo pendiente y preguntando si queda
  algo sin guardar). La bandeja solo mata el proceso si no termina en 5 s.
- ``mostrar``: traer la ventana al frente (clic en el icono con el editor ya abierto).
- ``idioma <código>``: el usuario ha cambiado de idioma (C-62). Cambia el título y el
  aviso de «¿cerrar sin guardar?», que son de Python; la página cambia sola.

El idioma de arranque llega con la dirección y el token (``language``).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from luzeira import idioma
from luzeira.idioma import t

log = logging.getLogger(__name__)

# Claves del catálogo (zona ``sistema``): se traducen al usarlas, en el idioma de ese
# momento (el proceso de la ventana lo recibe de la bandeja).
WINDOW_TITLE = "sistema.ventana_titulo"

# Códigos de salida del proceso de la ventana (la bandeja los vigila).
EXIT_OK = 0
EXIT_BAD_CONFIG = 2
EXIT_NO_WEBVIEW2 = 3  # sin Microsoft Edge WebView2 Runtime: no se abre nada

CLOSE_TIMEOUT = 5.0  # cuánto espera la bandeja a que la ventana se cierre sola
FLUSH_TIMEOUT = 5.0  # cuánto espera la ventana a que la página guarde lo pendiente

# Siempre una promesa (``Promise.resolve``): pywebview solo llama a la callback cuando el
# resultado es una promesa; si no, devuelve el valor directamente y la callback no llega.
FLUSH_JS = (
    "Promise.resolve(window.luzeiraEditor ? window.luzeiraEditor.flush()"
    " : {saved: true, pending: 'none'})"
)
_PENDING_TEXT = {
    "invalid": "sistema.ventana_pendiente_errores",
    "builtin": "sistema.ventana_pendiente_incluido",
    "error": "sistema.ventana_pendiente_fallo",
}
_UNKNOWN_PENDING = "sistema.ventana_pendiente_desconocido"


def _python_for_window() -> str:
    """pythonw.exe si existe (sin consola negra detrás de la ventana)."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def is_local_editor_url(url: str) -> bool:
    """¿Es ``http://127.0.0.1:<puerto>/…`` y nada más?

    Se analiza la URL entera (``urlsplit``) en vez de mirar cómo empieza: con
    ``startswith`` pasaba ``http://127.0.0.1:1@evil.com/``, que en realidad va a
    ``evil.com`` con el usuario «127.0.0.1» y la contraseña «1».
    """
    try:
        parts = urlsplit(url)
        port = parts.port  # ValueError si no es un número válido
    except ValueError:
        return False
    return (
        parts.scheme == "http"
        and parts.hostname == "127.0.0.1"
        and parts.username is None
        and parts.password is None
        and port is not None
        and parts.netloc == f"127.0.0.1:{port}"  # nada de mayúsculas raras ni ceros
    )


class EditorWindow:
    """Lanza y vigila el proceso de la ventana. Una sola ventana a la vez.

    ``on_exit(código)`` se llama (desde un hilo vigilante) cuando el proceso de la ventana
    termina, sea como sea. Solo para el proceso actual: si ya se abrió otro, no.
    """

    def __init__(self, on_exit: Callable[[int], None] | None = None) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._on_exit = on_exit
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def open(self, url: str, token: str) -> bool:
        """Abre la ventana. Si ya está abierta, no abre otra (devuelve False). Le pasa
        también el idioma activo, para su título y sus avisos."""
        with self._lock:
            if self.is_open:
                return False
            if getattr(sys, "frozen", False):
                command = [sys.executable, "editor-ventana"]
            else:
                command = [_python_for_window(), "-m", "luzeira", "editor-ventana"]
            process = subprocess.Popen(  # noqa: S603 — orden fija, sin datos del usuario
                command, stdin=subprocess.PIPE, close_fds=True
            )
            self._process = process
            # stdin queda abierto: por ahí van luego «cerrar» y «mostrar».
            config = {"url": url, "token": token, "language": idioma.current()}
            self._send(process, json.dumps(config))
        threading.Thread(
            target=self._watch, args=(process,), name="luzeira-editor-ventana", daemon=True
        ).start()
        return True

    def _watch(self, process: subprocess.Popen[bytes]) -> None:
        code = process.wait()
        with contextlib.suppress(OSError, ValueError):
            if process.stdin is not None:
                process.stdin.close()
        if self._on_exit is not None and self._process is process:
            try:
                self._on_exit(code)
            except Exception:
                log.exception("Falló el aviso de cierre de la ventana del editor")

    @staticmethod
    def _send(process: subprocess.Popen[bytes], line: str) -> bool:
        """Una línea al proceso de la ventana. False si ya no escucha."""
        if process.stdin is None:
            return False
        try:
            process.stdin.write(line.encode() + b"\n")
            process.stdin.flush()
        except (OSError, ValueError):  # tubería rota o cerrada: el proceso ya no está
            return False
        return True

    def bring_to_front(self) -> bool:
        """Trae la ventana abierta al frente. False si no hay ventana o no responde."""
        process = self._process
        if process is None or process.poll() is not None:
            return False
        # Windows no deja que un proceso en segundo plano se ponga delante por su cuenta;
        # la bandeja acaba de recibir el clic del usuario y puede cederle ese permiso.
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.AllowSetForegroundWindow(process.pid)
        return self._send(process, "mostrar")

    def set_language(self, code: str) -> bool:
        """Avisa a la ventana abierta de que el idioma ha cambiado. False si no hay
        ventana o no responde (no pasa nada: al abrirla de nuevo ya lleva el nuevo)."""
        process = self._process
        if process is None or process.poll() is not None:
            return False
        return self._send(process, f"idioma {code}")

    def close(self, timeout: float = CLOSE_TIMEOUT) -> None:
        """Pide a la ventana que se cierre (guardando lo pendiente) y espera ``timeout``
        segundos; solo si no termina, la mata."""
        process = self._process
        if process is None or process.poll() is not None:
            return
        self._send(process, "cerrar")
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("La ventana del editor no se cerró a tiempo: se cierra a la fuerza.")
            process.terminate()


class _Bridge:
    """Lo que la página puede pedir a Python por ``window.pywebview.api``. Solo el token."""

    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


class _WindowController:
    """Cierre sin perder cambios y órdenes de la bandeja, dentro del proceso de la ventana.

    🔑 Por qué el cierre va en dos pasos (pywebview 6.2.1, ``platforms/winforms.py``):
    ``on_closing`` llama a ``closing.set()`` **en el hilo de la ventana** y cancela si algún
    manejador devuelve ``False``. Desde ese hilo no se puede esperar a la página:
    ``EdgeChrome.evaluate_js`` (``platforms/edgechromium.py``) se queda en
    ``semaphore.acquire()`` esperando una continuación que tiene que correr en ese mismo
    hilo, y la respuesta de la promesa (``pywebviewAsyncCallback``) también llega por él:
    se colgaría la ventana. Así que el primer intento de cierre **se cancela siempre**, un
    hilo aparte pide a la página que guarde, pregunta si hace falta y, si se puede cerrar,
    vuelve a cerrar con ``destroy()``; ese segundo ``closing`` ya se deja pasar.
    """

    def __init__(self, window: Any, flush_timeout: float = FLUSH_TIMEOUT) -> None:
        self.window = window
        self._flush_timeout = flush_timeout
        self._lock = threading.Lock()
        self._may_close = False
        self._checking = False
        self._minimized = False

    # --- eventos de pywebview (sin parámetros: pywebview mira la firma) ---

    def on_closing(self) -> bool:
        with self._lock:
            if self._may_close:
                return True
            if not self._checking:
                self._checking = True
                threading.Thread(
                    target=self._check_and_close, name="luzeira-cerrar", daemon=True
                ).start()
        return False  # cancela este cierre; lo repite _check_and_close si procede

    def on_minimized(self) -> None:
        self._minimized = True

    def on_restored(self) -> None:
        self._minimized = False

    # --- cierre ---

    def flush(self) -> dict[str, Any] | None:
        """Pide a la página que guarde ya. El resultado de su promesa, o None si no
        contesta a tiempo o falla."""
        done = threading.Event()
        box: dict[str, Any] = {}

        def callback(result: Any) -> None:
            box["result"] = result
            done.set()

        try:
            self.window.evaluate_js(FLUSH_JS, callback)
        except Exception as exc:
            log.warning("La página del editor no respondió al guardar (%s).", type(exc).__name__)
            return None
        if not done.wait(self._flush_timeout):
            log.warning("La página del editor no terminó de guardar a tiempo.")
            return None
        result = box.get("result")
        return result if isinstance(result, dict) else None

    def _check_and_close(self) -> None:
        try:
            result = self.flush()
            pending = None if result is None else result.get("pending")
            if pending != "none":
                reason = t(_PENDING_TEXT.get(str(pending), _UNKNOWN_PENDING))
                if not self.window.create_confirmation_dialog(
                    "Luzeira", t("sistema.ventana_sin_guardar", reason=reason)
                ):
                    return  # el usuario sigue editando
            with self._lock:
                self._may_close = True
            self.window.destroy()
        except Exception:
            log.exception("Falló el cierre de la ventana del editor")
        finally:
            with self._lock:
                self._checking = False

    # --- órdenes de la bandeja por stdin ---

    def show(self) -> None:
        if self._minimized:
            self.window.restore()  # restore() también quitaría «maximizada»: solo si hace falta
        self.window.show()  # Show() + Activate() en WinForms

    def set_language(self, code: str) -> None:
        """La bandeja ha cambiado de idioma: el título y los avisos de aquí, también. Un
        código que no tenemos acaba en el de Windows (``idioma.set_language``)."""
        idioma.set_language(code or None)
        self.window.set_title(t(WINDOW_TITLE))

    def listen(self, lines: Any) -> None:
        """Lee órdenes línea a línea hasta que la bandeja cierra stdin.

        Si stdin se cierra (la bandeja ha terminado sin avisar), la ventana se cierra
        también por el camino normal: sin bandeja ya no hay servidor detrás."""
        for raw in lines:
            command = raw.strip()
            if command == "cerrar":
                # Pasa por on_closing: guarda y pregunta. Si el usuario dice que no, la
                # ventana sigue y se siguen escuchando órdenes.
                self.window.destroy()
            elif command == "mostrar":
                try:
                    self.show()
                except Exception:
                    log.exception("No se pudo traer al frente la ventana del editor")
            elif command.startswith("idioma "):
                try:
                    self.set_language(command.removeprefix("idioma ").strip())
                except Exception:
                    log.exception("No se pudo cambiar el idioma de la ventana del editor")
        with contextlib.suppress(Exception):
            self.window.destroy()


def _identify_window(window: Any) -> None:
    """Que al anclar la ventana salga «Luzeira» con su icono (ver sistema/identidad.py)."""
    from luzeira.sistema.accesos import ICON, relaunch_command
    from luzeira.sistema.identidad import set_window_relaunch

    try:
        hwnd = int(window.native.Handle.ToInt64())  # Form de WinForms (pythonnet)
    except Exception:
        log.warning("No se encontró la ventana del editor para identificarla")
        return
    set_window_relaunch(hwnd, relaunch_command(), ICON)


def _webview2_ready(webview: Any) -> bool:
    """¿Usará pywebview Edge WebView2?

    pywebview 6.2.1 **no falla** sin WebView2: ``platforms/winforms.py`` calcula
    ``is_chromium`` al importarse y, si no lo encuentra, pasa a MSHTML (el motor de
    Internet Explorer 11) con solo un aviso en el log; ``gui="edgechromium"`` no lo impide
    (``guilib.initialize`` en Windows importa winforms igual). Así que se pregunta antes
    con ``webview.initialize`` (el de ``guilib``; ojo: ``webview.guilib`` es una variable
    que vale None hasta ``start``). Es idempotente (módulo ya importado y ``setup_app``
    con guarda), y ``start`` lo vuelve a llamar sin efecto.
    """
    try:
        guilib = webview.initialize("edgechromium")
    except Exception as exc:
        log.error("No se pudo preparar la ventana del editor (%s).", type(exc).__name__)
        return False
    return getattr(guilib, "renderer", None) == "edgechromium"


def run_window_from_stdin() -> int:
    """Punto de entrada del proceso de la ventana (``luzeira editor-ventana``)."""
    import webview  # pywebview: solo se importa en este proceso

    try:
        config = json.loads(sys.stdin.readline())
        url, token = str(config["url"]), str(config["token"])
        language = config.get("language")
    except (ValueError, KeyError, TypeError, AttributeError):
        log.error("La ventana del editor no recibió su configuración.")
        return EXIT_BAD_CONFIG
    if not is_local_editor_url(url):
        # Defensa: esta ventana solo enseña el editor local de Luzeira.
        log.error("Dirección del editor no permitida.")
        return EXIT_BAD_CONFIG
    # El de la bandeja; sin él (una bandeja antigua), el de Windows.
    idioma.set_language(language if isinstance(language, str) else None)
    if not _webview2_ready(webview):
        log.error("Falta Microsoft Edge WebView2 Runtime: no se abre el editor.")
        return EXIT_NO_WEBVIEW2
    window = webview.create_window(
        t(WINDOW_TITLE),
        url,
        js_api=_Bridge(token),
        width=1280,
        height=820,
        min_size=(960, 640),
        background_color="#12151c",
    )
    controller = _WindowController(window)
    window.events.closing += controller.on_closing
    window.events.minimized += controller.on_minimized
    window.events.restored += controller.on_restored
    window.events.maximized += controller.on_restored
    window.events.shown += lambda: _identify_window(window)
    threading.Thread(
        target=controller.listen, args=(sys.stdin,), name="luzeira-ordenes", daemon=True
    ).start()
    from luzeira.sistema.accesos import ICON

    # private_mode: sin caché ni cookies persistentes. icon: el de Luzeira en la barra de
    # tareas (sin él, pywebview pone el de pythonw.exe).
    webview.start(gui="edgechromium", private_mode=True, icon=str(ICON))
    return EXIT_OK
