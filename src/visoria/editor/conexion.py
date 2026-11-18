"""Une el servidor del editor con el resto de la app (bandeja o línea de órdenes).

El editor tiene sus **propios** sensores (un ``SensorHub`` aparte): si dependiera de los
del servicio de la pantalla, sin pantalla conectada sus valores se quedarían congelados.
Se leen como mucho una vez por segundo y solo cuando el editor los pide.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from visoria.config import config_dir, user_themes_dir
from visoria.editor.servidor import WEB_ROOT, EditorServer
from visoria.editor.ventana import EditorWindow
from visoria.sensores import default_hub
from visoria.sensores.base import Reading, SensorInfo


class _LiveSensors:
    """Sensores para el editor: se refrescan bajo demanda, como mucho cada ``min_age``."""

    def __init__(self, min_age: float = 1.0) -> None:
        self._hub = default_hub()
        self._lock = threading.Lock()
        self._last_poll = 0.0
        self._min_age = min_age

    def _fresh(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_poll >= self._min_age:
                self._hub.poll()
                self._last_poll = now

    def available(self) -> list[SensorInfo]:
        self._fresh()
        return self._hub.available()

    def read(self, sensor_id: str) -> Reading:
        self._fresh()
        return self._hub.read(sensor_id)

    def close(self) -> None:
        self._hub.close()


class EditorLink:
    """Servidor + ventana del editor.

    El servidor (y su token) y los sensores del editor **viven lo que vive la ventana**:
    se crean al abrirla y se paran en cuanto su proceso termina. Cada apertura estrena
    token: uno filtrado no sirve para la siguiente.

    ``on_window_exit(código)`` se llama (desde el hilo vigilante) cuando la ventana
    termina; p. ej. con ``EXIT_NO_WEBVIEW2`` la bandeja avisa de que falta WebView2.
    """

    def __init__(
        self,
        *,
        apply_theme: Callable[[str], None],
        status: Callable[[], tuple[str, str]],
        active_theme: Callable[[], str | None],
        dev_origin: str | None = None,
        on_window_exit: Callable[[int], None] | None = None,
    ) -> None:
        self._apply_theme = apply_theme
        self._status = status
        self._active_theme = active_theme
        self._dev_origin = dev_origin
        self._on_window_exit = on_window_exit
        self._lock = threading.RLock()
        self._server: EditorServer | None = None
        self._sensors: _LiveSensors | None = None
        self.window = EditorWindow(on_exit=self._window_exited)

    @staticmethod
    def is_built() -> bool:
        """¿Está compilado el editor (``npm run build`` en ``editor/``)?"""
        return (WEB_ROOT / "index.html").is_file()

    def server(self) -> EditorServer:
        """El servidor en marcha (lo arranca si no lo está)."""
        with self._lock:
            if self._server is None:
                self._sensors = _LiveSensors()
                self._server = EditorServer(
                    sensors=self._sensors.available,
                    read_sensor=self._sensors.read,
                    apply_theme=self._apply_theme,
                    status=self._status,
                    user_themes=user_themes_dir(),
                    trash=config_dir() / "papelera",
                    active_theme=self._active_theme,
                    dev_origin=self._dev_origin,
                )
                self._server.start()
            return self._server

    def open_window(self) -> bool:
        """Abre la ventana del editor (False si ya estaba abierta).

        Con servidor y token **nuevos**: si quedaba uno de una ventana anterior (su aviso
        de cierre aún no había llegado), se para antes."""
        with self._lock:
            if self.window.is_open:
                return False
            self._stop_server()
            server = self.server()
            return self.window.open(server.url, server.token)

    def _window_exited(self, code: int) -> None:
        with self._lock:
            # Si entretanto ya se abrió otra ventana, su servidor es otro: no tocarlo.
            if not self.window.is_open:
                self._stop_server()
        if self._on_window_exit is not None:
            self._on_window_exit(code)

    def _stop_server(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
                self._server = None
            if self._sensors is not None:
                self._sensors.close()
                self._sensors = None

    def close(self) -> None:
        self.window.close()
        self._stop_server()
