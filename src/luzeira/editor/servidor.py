"""Servidor local del editor visual (fase 5, RQ-14).

El editor (React dentro de una ventana pywebview) habla con el núcleo por HTTP en
``127.0.0.1`` con un puerto aleatorio. Cualquier web abierta en el navegador del usuario
puede intentar hablar con ese puerto, así que cada petición pasa tres filtros:

1. **Host** exactamente ``127.0.0.1:<puerto>``: corta el *DNS rebinding* (una web
   ``evil.com`` que hace que su dominio apunte a 127.0.0.1 manda ``Host: evil.com``).
2. **Origin**, si viene, exactamente el nuestro (o el de Vite en modo desarrollo):
   corta las peticiones lanzadas desde otra web (CSRF).
3. **Token** aleatorio por arranque en la cabecera ``X-Luzeira-Token`` para ``/api/*``.
   Nunca va en la URL (acabaría en historiales y logs). Un formulario o un ``<img>`` de
   otra web no puede poner cabeceras propias, y un ``fetch`` con cabeceras propias a
   otro origen necesita CORS, que aquí solo se abre al origen de desarrollo.

Todo lo que falla en esos filtros recibe un 403 sin detalles. El 403 es **solo** para
eso: lo que no se puede hacer por las reglas de los temas (cambiar uno incluido) es un
409 con mensaje. Los ficheros estáticos del editor (``/``, ``/assets/*``) no llevan
token: la página tiene que cargarse antes de poder pedirlo a pywebview; aun así pasan el
filtro de Host, y salen con la cabecera ``Content-Security-Policy`` (``CSP``).

Subir un recurso: ``POST /api/temas/{tema}/recursos`` con los bytes como cuerpo y el
nombre sugerido en la cabecera ``X-Luzeira-Filename``, codificado como en una URL
(``encodeURIComponent``). En la query no se admite.

Ajustes de la app (``/api/ajustes``, ``/api/pantalla``, ``/api/acceso-escritorio``,
``/api/carpeta-temas``, ``/api/importar-tema``, ``/api/actualizaciones/buscar`` e
``/api/actualizaciones/instalar``): los mismos filtros; los implementa la
bandeja (ver ``ajustes.py``). Importar sube el ``.zip`` como cuerpo, con su nombre en
``X-Luzeira-Filename``.

Vista previa «superada»: las vistas previas se pintan de una en una. Si cuando a una le
llega el turno ya ha entrado otra más nueva, no se pinta y responde **409** con
``{"error": …, "superseded": true}`` (``PREVIEW_SUPERSEDED``). La interfaz la ignora: ya
ha cancelado esa petición al mandar la nueva. Si llega a mitad de pintar, se deja de
pintar en la siguiente imagen.
"""

from __future__ import annotations

import hmac
import io
import json
import logging
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from luzeira import __version__, idioma
from luzeira.idioma import t
from luzeira.render.motor import (
    Renderer,
    format_clock,
)
from luzeira.sensores.base import Reading, SensorInfo
from luzeira.temas import ThemeError, font_for_text, load_theme, missing_glyphs, resolve_font
from luzeira.temas.cargar import MAX_ZIP_BYTES
from luzeira.temas.modelo import (
    CANVAS_SIZES,
    ORIENTATION_SIZES,
    ClockWidget,
    SensorWidget,
    TextWidget,
    localized,
)
from luzeira.temas.rutas import is_reserved_windows_name

from .ajustes import SettingsBackend, parse_answer, parse_display, parse_update
from .almacen import EditorError, ThemeStore, friendly_theme_error, parse_json, parse_theme

log = logging.getLogger(__name__)

MAX_JSON_BODY = 1024 * 1024
MAX_UPLOAD_BODY = 10 * 1024 * 1024
MAX_DRAIN = 32 * 1024 * 1024  # cuerpo demasiado grande que aún se lee para poder responder 413
TOKEN_HEADER = "X-Luzeira-Token"  # noqa: S105 (nombre de cabecera, no un secreto)
FILENAME_HEADER = "X-Luzeira-Filename"
# Vista previa: letras que la fuente de algún texto no tiene (RQ-16, C-17). JSON en ASCII
# (``\uXXXX``): una cabecera HTTP no admite otra cosa.
MISSING_GLYPHS_HEADER = "X-Luzeira-Missing-Glyphs"
WEB_ROOT = Path(__file__).parent / "web"


# Tipos fijos: ``mimetypes`` lee el registro de Windows, donde algunos programas dejan
# «.js» como text/plain y el navegador se niega a ejecutarlo.
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".map": "application/json",
}
_STATIC_SEGMENT = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,100}$")
# ``Content-Length``: solo cifras ASCII. ``str.isdigit()`` no vale: da True con «²» (que
# llega en una cabecera, decodificada en latin-1) y luego ``int("²")`` lanza ValueError.
_CONTENT_LENGTH = re.compile(r"[0-9]{1,10}")
# Métodos que se apuntan en el log tal cual; cualquier otra cosa sale como «?».
_KNOWN_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"})
# Conexiones a la vez. El editor usa unas pocas (WebView2 abre como mucho 6 por
# servidor); sin tope, 500 conexiones a medio abrir eran 500 hilos esperando.
MAX_CONNECTIONS = 32
# Plazo **total** para recibir la línea de petición y las cabeceras. El ``timeout`` de
# 30 s es por cada lectura: quien manda un byte cada 29 s tendría el hilo para siempre.
HEADER_DEADLINE = 10.0
# Plazo total para leer el cuerpo, **solo** cuando el token ya es bueno (lo más grande
# son 20 MB de un .zip, que por localhost tardan menos de un segundo). Hasta entonces
# sigue corriendo el plazo de las cabeceras: quien no tiene token no ocupa un hueco más.
BODY_DEADLINE = 60.0
# Cuerpo que se lee y se tira de una petición rechazada antes de comprobar el token (para
# que el 403 llegue en vez de un «error de red»). Con más, se cierra sin leerlo.
DENIED_DRAIN = 64 * 1024
# Cortes de conexión del otro lado: no son fallos del editor (se apuntan en DEBUG).
_CONNECTION_ERRORS = (ConnectionError, TimeoutError)

# La página del editor solo carga lo suyo y solo habla con este servidor.
CSP = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' blob: data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


PREVIEW_SUPERSEDED = 409  # código de una vista previa que ya no hace falta (ver arriba)
# Memoria máxima de la caché de imágenes ya escaladas. Una imagen de pantalla completa
# (480x320 RGBA) son 600 KB: caben unas cien, de sobra para los 64 elementos de un tema.
# Con el lienzo mayor (480x1920, 3,7 MB) caben 17 de pantalla completa; las imágenes de
# un tema suelen ser mucho más pequeñas, y si no caben solo se recalculan (más lento).
SCALED_CACHE_BYTES = 64 * 1024 * 1024


class PreviewSuperseded(EditorError):
    """Hay una vista previa más nueva esperando: esta no se pinta."""

    def __init__(self) -> None:
        super().__init__(PREVIEW_SUPERSEDED, t("servidor.vista_superada"))

    def body(self) -> dict:
        return {**super().body(), "superseded": True}


class _Cancelled(Exception):
    """Se deja de pintar a medias: ha entrado una vista previa más nueva."""


class ScaledImageCache:
    """Imágenes del tema ya decodificadas y escaladas, por (fichero, lstat, tamaño, modo).

    Decodificar un JPEG de 4096x4096 y escalarlo cuesta unos 200 ms; la vista previa se
    pide cada 300 ms mientras se edita y un tema puede tener 64 imágenes (medido: 14 s
    por vista previa sin esta caché). Con ella solo se recalcula la imagen que cambia de
    tamaño o de fichero; mover un elemento no recalcula nada.

    La clave lleva ``lstat`` (fecha, tamaño, inodo): si el fichero cambia en disco, la
    clave es otra y se vuelve a abrir con las comprobaciones del motor. Límite de
    memoria con expulsión de la menos usada (LRU). Sin cerrojo propio: solo se usa con
    ``EditorServer._render_lock`` cogido.
    """

    def __init__(self, limit_bytes: int = SCALED_CACHE_BYTES) -> None:
        self.limit = limit_bytes
        self.used = 0
        self._items: OrderedDict[tuple, tuple[Image.Image, int]] = OrderedDict()

    @staticmethod
    def key(path: Path, size: tuple[int, int], how: str) -> tuple | None:
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            return None  # sin clave no se guarda: el motor dará su error al abrirlo
        return (str(path), st.st_mtime_ns, st.st_size, st.st_ino, st.st_mode, size, how)

    def get(self, key: tuple | None) -> Image.Image | None:
        if key is None or key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key][0]

    def put(self, key: tuple | None, image: Image.Image) -> None:
        cost = image.width * image.height * len(image.getbands())
        if key is None or cost > self.limit:
            return
        if key in self._items:
            self.used -= self._items.pop(key)[1]
        self._items[key] = (image, cost)
        self.used += cost
        while self.used > self.limit:
            _, (_, old) = self._items.popitem(last=False)
            self.used -= old

    def __len__(self) -> int:
        return len(self._items)


class _PreviewRenderer(Renderer):
    """El ``Renderer`` de siempre con la caché de imágenes escaladas y un freno.

    Solo cambia ``_scaled``: el escalado es el del motor (``super()._scaled``), así que
    la vista previa es la misma que va a la pantalla (lo comprueba además
    ``test_cached_preview_matches_plain_renderer``, píxel a píxel).
    """

    def __init__(
        self,
        theme,
        *,
        cache: ScaledImageCache,
        cancelled: Callable[[], bool],
        **kwargs,
    ) -> None:
        self._cache = cache
        self._cancelled = cancelled
        super().__init__(theme, **kwargs)

    def _scaled(self, rel: str, size: tuple[int, int], how: str) -> Image.Image:
        path = self._files.get(rel)
        key = ScaledImageCache.key(path, size, how) if path is not None else None
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if self._cancelled():
            raise _Cancelled
        img = super()._scaled(rel, size, how)
        self._cache.put(key, img)
        return img


def find_missing_glyphs(theme, files, moment: datetime | None = None) -> list[dict]:
    """Elementos con texto cuya fuente no tiene alguna letra: ``[{index, chars}]``.

    Se miran los textos fijos (texto, prefijo y sufijo de un sensor, el reloj tal y como
    sale ahora), en el idioma activo: lo mismo que se pinta. Los valores de los sensores
    son números y unidades: los tiene cualquier fuente que tenga el resto. Los espacios
    no cuentan (no tienen dibujo que comparar).
    """
    moment = moment or datetime.now()
    lang = idioma.current()
    found = []
    for index, widget in enumerate(theme.widgets):
        if isinstance(widget, TextWidget):
            text = localized(widget.text, widget.text_i18n, lang)
        elif isinstance(widget, SensorWidget):
            text = localized(widget.prefix, widget.prefix_i18n, lang) + localized(
                widget.suffix, widget.suffix_i18n, lang
            )
        elif isinstance(widget, ClockWidget):
            text = format_clock(widget.format, moment)
        else:
            continue
        text = "".join(ch for ch in text if not ch.isspace())
        if not text:
            continue
        missing = _missing_in_font(widget.style, files, text, lang)
        if missing:
            found.append({"index": index, "chars": "".join(dict.fromkeys(missing))})
    return found


def _missing_in_font(style, files, text: str, lang: str) -> list[str]:
    """Letras de ``text`` que no tiene la fuente con que se pinta (la misma que usa el
    motor: con la de CJK de Windows si a la del tema le faltan).

    Mirarlas rasteriza letra a letra: una fuente del tema que pasa la validación (solo
    pinta una muestra) puede fallar con un glifo inflado («DecompressionBombError», que
    no hereda de OSError) o roto (OSError). Entonces el motor pinta con la de Luzeira
    (``render/motor.py``), así que se miran las letras en esa. Si también falla, no se
    avisa de nada: mejor sin aviso que un 500 en cada vista previa.
    """
    for theme_files in (files, None):  # None: sin la fuente del tema (la de Luzeira)
        try:
            font = font_for_text(resolve_font(style, theme_files), style, text, lang)
            return missing_glyphs(font, text)
        except (OSError, ValueError, Image.DecompressionBombError):
            log.warning("Una fuente falló al mirar sus letras; se prueba con la de Luzeira.")
    return []


def _sensor_row(info: SensorInfo, reading: Reading) -> dict:
    return {
        "id": info.id,
        "label": info.label,
        "unit": info.unit,
        "category": info.category,
        "kind": info.kind,
        "value": reading.value,
        "text": reading.text,
        "reason": reading.reason,
    }


class EditorServer:
    """Servidor del editor. Todo lo que toca la app de verdad llega inyectado.

    - ``sensors``: sensores disponibles (``SensorHub.available``).
    - ``read_sensor``: lectura actual de un sensor (``SensorHub.read``).
    - ``apply_theme``: pone un tema en la pantalla y lo guarda en la configuración.
    - ``status``: ``(estado, mensaje)`` del servicio de la pantalla.
    - ``active_theme``: nombre del tema en pantalla (para no dejar borrarlo); opcional.
    - ``user_themes`` / ``trash``: carpetas de temas del usuario y papelera.
    - ``dev_origin``: origen de Vite en modo desarrollo, que es
      **``http://127.0.0.1:5173``** (Vite escucha solo en 127.0.0.1 y reenvía ``/api``
      a este servidor). Se compara tal cual: ``http://localhost:5173`` es otro origen y
      no vale. Solo con él puesto se admite ese ``Origin`` y se responde CORS a él (y
      solo a él).
    """

    def __init__(
        self,
        *,
        sensors: Callable[[], list[SensorInfo]],
        read_sensor: Callable[[str], Reading],
        apply_theme: Callable[[str], None],
        status: Callable[[], tuple[str, str]],
        user_themes: Path,
        trash: Path,
        active_theme: Callable[[], str | None] | None = None,
        web_root: Path | None = None,
        dev_origin: str | None = None,
        settings: SettingsBackend | None = None,
    ) -> None:
        self._sensors = sensors
        self._settings = settings
        self._read_sensor = read_sensor
        self._apply_theme = apply_theme
        self._status = status
        self._active_theme = active_theme
        self.store = ThemeStore(user_themes, trash, active_theme=active_theme)
        self.web_root = (web_root or WEB_ROOT).resolve()
        self.dev_origin = dev_origin.rstrip("/") if dev_origin else None
        self.token = secrets.token_urlsafe(32)
        self._token_bytes = self.token.encode("ascii")
        # La vista previa se pinta de una en una: si el editor manda muchas seguidas,
        # esperan en cola en vez de comerse varios núcleos a la vez. Cada una se apunta
        # con un número de generación; al llegarle el turno, si ya hay otra más nueva, no
        # se pinta (las que el editor cancela no se apilan comiéndose la CPU).
        self._render_lock = threading.Lock()
        self._generation_lock = threading.Lock()
        self._generation = 0
        self._scaled = ScaledImageCache()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- ciclo de vida ---

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = _HTTPServer(("127.0.0.1", 0), _Handler)
        httpd.editor = self
        self._httpd = httpd
        self._thread = threading.Thread(
            # poll_interval: cada cuánto mira si le piden parar (stop() espera eso).
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="luzeira-editor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            # Fallo de programación, no un texto para el usuario.
            raise RuntimeError("El servidor del editor no está arrancado.")  # i18n: no
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # --- filtros ---

    def host_ok(self, values: list[str]) -> bool:
        return len(values) == 1 and values[0] == f"127.0.0.1:{self.port}"

    def origin_ok(self, values: list[str]) -> bool:
        if not values:
            return True  # navegación normal o cliente que no es navegador
        if len(values) != 1:
            return False
        return values[0] == self.origin or (
            self.dev_origin is not None and values[0] == self.dev_origin
        )

    def token_ok(self, values: list[str]) -> bool:
        if len(values) != 1:
            return False
        given = values[0].encode("utf-8", "replace")
        return hmac.compare_digest(given, self._token_bytes)

    # --- operaciones de la API (sin HTTP) ---

    def info(self) -> dict:
        status, message = self._status()
        sizes = {k: {"width": w, "height": h} for k, (w, h) in ORIENTATION_SIZES.items()}
        # Lienzos que admite un tema (``canvas``), en vertical y horizontal: la misma
        # tabla que ``CANVAS_SIZES_PORTRAIT`` de editor/src/model/types.ts (lo comprueba
        # test_editor_servidor).
        canvases = [{"width": w, "height": h} for w, h in CANVAS_SIZES]
        active = self._active_theme() if self._active_theme is not None else None
        return {
            "version": __version__,
            # Idioma activo (C-62): el editor se traduce con él y lo sigue si cambia.
            "language": idioma.current(),
            "status": str(status),
            "message": message,
            "sizes": sizes,
            "canvases": canvases,
            "active_theme": active,
            # «¿Se ve bien?» de una pantalla elegida en el editor: la interfaz la muestra
            # en cuanto la ve aquí (pregunta por /api/info cada pocos segundos).
            "question": self._settings.question() if self._settings is not None else None,
            # «¿Avisarte de versiones nuevas?»: se pregunta una vez (ADR-006).
            "updates_question": (
                self._settings.updates_question() if self._settings is not None else False
            ),
            # Versión nueva lista para instalar: el editor la ofrece en una ventana.
            "update_available": (
                self._settings.update_available() if self._settings is not None else None
            ),
        }

    def sensor_rows(self) -> list[dict]:
        return [_sensor_row(info, self._read_sensor(info.id)) for info in self._sensors()]

    def preview(self, body: object) -> tuple[bytes, list[dict]]:
        """PNG del tema ``body["theme"]`` (sin guardar) con los ficheros de ``body["name"]``.

        Lanza ``PreviewSuperseded`` (409) si entra otra vista previa antes de terminar.
        """
        with self._generation_lock:
            self._generation += 1
            generation = self._generation

        def superseded() -> bool:
            return self._generation != generation

        if not isinstance(body, dict):
            raise EditorError(400, t("servidor.faltan_name_theme"))
        entry = self.store.entry(body.get("name"))
        theme = parse_theme(body.get("theme"))
        files = self.store.check_files(theme, entry.path)
        with self._render_lock:
            if superseded():
                raise PreviewSuperseded
            try:
                renderer = _PreviewRenderer(
                    theme,
                    cache=self._scaled,
                    cancelled=superseded,
                    font_for=lambda style: resolve_font(style, files),
                    read_sensor=self._read_sensor,
                    files=files,
                )
                image = renderer.full_frame()
            except _Cancelled:
                raise PreviewSuperseded from None
            except ThemeError as exc:  # un fichero cambió en disco entre medias
                raise EditorError(400, friendly_theme_error(exc)) from None
            missing = find_missing_glyphs(theme, files)
        buf = io.BytesIO()
        # compress_level=1: el PNG viaja por localhost; comprimir más solo gasta CPU.
        image.save(buf, "PNG", compress_level=1)
        return buf.getvalue(), missing

    def settings(self) -> SettingsBackend:
        """Los ajustes de la app; sin ellos (tests, ``--desarrollo`` suelto), 404."""
        if self._settings is None:
            raise EditorError(404, t("servidor.no_existe"))
        return self._settings

    def import_theme(self, data: bytes, suggested: str) -> str:
        """Importa un tema ``.zip`` subido desde el editor (las mismas reglas que desde
        el menú: ``import_theme_zip`` valida rutas, tamaños e imágenes)."""
        if not suggested.lower().endswith(".zip"):
            raise EditorError(400, t("servidor.solo_zip"))
        backend = self.settings()
        # Carpeta temporal propia: el zip nunca se escribe con el nombre que manda la página.
        with tempfile.TemporaryDirectory(prefix="luzeira-importar-") as tmp:
            path = Path(tmp) / "tema.zip"
            path.write_bytes(data)
            try:
                return backend.import_theme(path)
            except ThemeError as exc:
                raise EditorError(400, str(exc)) from None

    def apply(self, name: object) -> None:
        entry = self.store.entry(name)
        try:
            load_theme(entry.path)
        except ThemeError as exc:
            raise EditorError(400, friendly_theme_error(exc)) from None
        self._apply_theme(entry.name)


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Sin SO_REUSEADDR: en Windows esa opción no es «reutilizar un puerto que se acaba de
    # cerrar» (como en Linux) sino dejar que dos sockets se aten al mismo puerto.
    allow_reuse_address = False
    editor: EditorServer
    max_connections = MAX_CONNECTIONS

    def __init__(self, *args, **kwargs) -> None:
        # Un hueco por conexión: se coge al aceptarla y se suelta al cerrarla.
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address) -> None:
        """Con todos los huecos ocupados, la conexión nueva se cierra sin crear hilo."""
        if not self._slots.acquire(blocking=False):
            log.debug("Editor: demasiadas conexiones a la vez; se cierra una nueva.")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()  # no llegó a haber hilo que lo soltara
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address) -> None:
        """Lo que se escapa de un hilo: solo el tipo, nunca la traza (``socketserver``
        la imprime entera, con rutas que pueden llevar el nombre del usuario)."""
        exc = sys.exc_info()[1]
        if isinstance(exc, _CONNECTION_ERRORS):
            log.debug("Editor: conexión cortada (%s).", type(exc).__name__)
        else:
            log.error("Error interno del editor (%s).", type(exc).__name__)

    def server_bind(self) -> None:
        # Quitar SO_REUSEADDR no impide que **otro** socket use SO_REUSEADDR para atarse
        # a nuestro puerto: eso lo corta SO_EXCLUSIVEADDRUSE (solo existe en Windows).
        # Medido en Windows 10 (2026-11-17): ese robo ya falla sin él (10013, la
        # «seguridad mejorada de sockets»); se pone igual, como recomienda Microsoft
        # para servidores, por si otra versión se comporta distinto.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _DeadlineReader(io.RawIOBase):
    """Lee del socket con un plazo total opcional (``deadline``, en ``time.monotonic``).

    Con plazo, cada lectura espera como mucho lo que queda de él (y nunca más que
    ``idle``); al acabarse, ``TimeoutError``, que ``http.server`` ya trata cerrando la
    conexión. Sin plazo, solo ``idle`` por lectura. El plazo lo pone el manejador: el de
    las cabeceras al esperar cada petición y, si el token es bueno, el del cuerpo.
    """

    def __init__(self, sock: socket.socket, idle: float) -> None:
        super().__init__()
        self._sock = sock
        self._idle = idle
        self.deadline: float | None = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        wait = self._idle
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("plazo de las cabeceras agotado")  # i18n: no (interno)
            wait = min(wait, left)
        self._sock.settimeout(wait)
        return self._sock.recv_into(buffer)


def _route_label(path: str) -> str:
    """Ruta para los logs sin nombres de temas ni ficheros: ``/api/temas/*/recursos``."""
    parts = path.split("?", 1)[0].split("/")
    if len(parts) > 2 and parts[1] == "api":
        known = {
            "info", "temas", "sensores", "vista-previa", "copiar", "aplicar", "recursos",
            "ajustes", "pantalla", "respuesta", "acceso-escritorio", "carpeta-temas",
            "importar-tema", "actualizaciones", "buscar", "instalar",
        }  # fmt: skip
        return "/" + "/".join(p if p in known or p == "api" else "*" for p in parts[1:6])
    return "/(estático)"  # i18n: no (solo va a los logs)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Luzeira"
    sys_version = ""
    timeout = 30  # una conexión lenta no se queda un hilo para siempre
    header_deadline = HEADER_DEADLINE
    body_deadline = BODY_DEADLINE
    error_content_type = "application/json; charset=utf-8"

    @property
    def error_message_format(self) -> str:  # type: ignore[override]
        """Cuerpo de los errores que responde el propio ``http.server`` (petición mal
        formada…), en el idioma activo. Lo rellena con ``%``: «%(code)d» es el código."""
        text = t("servidor.peticion_no_valida", code="%(code)d")
        return json.dumps({"error": text}, ensure_ascii=False)

    server: _HTTPServer

    # --- conexión: plazo para las cabeceras ---

    def setup(self) -> None:
        super().setup()
        # El ``rfile`` de siempre, pero sobre un lector con plazo total (ver arriba).
        self.rfile.close()
        self._reader = _DeadlineReader(self.connection, self.timeout)
        self.rfile = io.BufferedReader(self._reader)

    def handle_one_request(self) -> None:
        # Cuenta desde que se espera la petición: también corta una conexión keep-alive
        # que se queda callada ocupando un hueco.
        self._reader.deadline = time.monotonic() + self.header_deadline
        self._authorized = False  # hasta que el token sea bueno (ver ``_drain``)
        super().handle_one_request()

    # --- logs: sin rutas ni nombres del usuario ---

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # Un método desconocido llega tal cual lo mandó el otro lado: no se apunta.
        method = self.command if self.command in _KNOWN_METHODS else "?"
        log.debug("Editor: %s %s -> %s", method, _route_label(self.path), code)

    def log_error(self, format: str, *args: object) -> None:
        log.debug("Editor: petición rechazada por el servidor HTTP.")

    def log_message(self, format: str, *args: object) -> None:
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Errores del propio ``http.server`` sin su texto: el de un método desconocido
        repite lo que mandó el otro lado («Unsupported method ('{}GET')»)."""
        super().send_error(code)

    def end_headers(self) -> None:
        """Cabeceras de seguridad en **todas** las respuestas, también en los errores que
        responde ``http.server`` sin pasar por ``_send``."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        # Ninguna otra web puede cargar nuestras respuestas (ni como <img> ni <script>).
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    # --- métodos ---

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    # --- respuesta ---

    def _send(
        self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None
    ) -> None:
        # El cuerpo que la ruta no ha leído (``aplicar`` recibe ``{}`` y no lo mira) se
        # lee aquí: si se quedara en el socket, con keep-alive sería el principio de la
        # siguiente petición («{}GET /api/info» -> 501). Si no se puede leer, se cierra.
        complete = self._drain()
        # La última lectura pudo dejar el socket con lo poco que quedaba de plazo: para
        # escribir, el ``timeout`` de siempre.
        self.connection.settimeout(self.timeout)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        editor = self.server.editor
        if editor.dev_origin is not None and origin == editor.dev_origin:
            self.send_header("Access-Control-Allow-Origin", editor.dev_origin)
            self.send_header("Access-Control-Expose-Headers", MISSING_GLYPHS_HEADER)
            self.send_header("Vary", "Origin")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if code >= 400 or not complete:
            # Un error, o un cuerpo que no se ha podido leer: no se reutiliza la conexión.
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _deny(self) -> None:
        self._json(403, {"error": t("servidor.denegado")})

    # --- cuerpo ---

    def _body(self, limit: int) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise EditorError(411, t("servidor.falta_longitud"))
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            raise EditorError(411, t("servidor.falta_longitud"))
        if not _CONTENT_LENGTH.fullmatch(lengths[0]):
            raise EditorError(400, t("servidor.longitud_no_valida"))
        length = int(lengths[0])
        if length > limit:
            raise EditorError(413, t("servidor.datos_grandes", max=limit // (1024 * 1024)))
        self._consumed = True
        data = self.rfile.read(length)
        if len(data) != length:
            raise EditorError(400, t("servidor.datos_incompletos"))
        return data

    def _drain(self) -> bool:
        """Lee y tira el cuerpo que nadie ha leído antes de responder. True si no queda
        nada de él en el socket (la conexión se puede reutilizar). **Nunca lanza.**

        Si se cierra la conexión con datos sin leer, Windows la resetea y el navegador ve
        un «error de red» en vez del 403/413 con su mensaje. Solo hasta ``MAX_DRAIN`` y,
        si la petición no ha pasado el token, solo hasta ``DENIED_DRAIN`` (y dentro del
        plazo de las cabeceras, que sigue corriendo): un cuerpo que gotea sin token no
        retiene el hilo.
        """
        if getattr(self, "_consumed", True):
            # Ya leído por la ruta (o petición que no pasó por ``_handle``). Si se leyó a
            # medias, ``_body`` da 400 y el 400 cierra la conexión igualmente.
            return True
        self._consumed = True
        if self.headers.get("Transfer-Encoding"):
            return False  # por trozos (chunked): no se lee, se cierra
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            return True  # sin cuerpo
        if len(lengths) != 1 or not _CONTENT_LENGTH.fullmatch(lengths[0]):
            return False
        left = int(lengths[0])
        if left > (MAX_DRAIN if getattr(self, "_authorized", False) else DENIED_DRAIN):
            return False
        try:
            while left > 0 and (chunk := self.rfile.read(min(left, 64 * 1024))):
                left -= len(chunk)
        except OSError:
            return False
        return left == 0

    def _json_body(self) -> object:
        return parse_json(self._body(MAX_JSON_BODY))

    # --- despacho ---

    def _handle(self, method: str) -> None:
        editor = self.server.editor
        self._consumed = False
        # El plazo de las cabeceras sigue corriendo hasta que el token sea bueno.
        try:
            if not editor.host_ok(self.headers.get_all("Host") or []):
                return self._deny()
            origins = self.headers.get_all("Origin") or []
            if not editor.origin_ok(origins):
                return self._deny()
            # El navegador dice de dónde viene la petición: de otra web, nunca (también
            # para los estáticos). Sin la cabecera (cliente que no es navegador), pasa.
            fetch_site = self.headers.get_all("Sec-Fetch-Site") or []
            if any(v.strip().lower() == "cross-site" for v in fetch_site):
                return self._deny()
            split = urlsplit(self.path)
            if method == "OPTIONS":
                return self._preflight(origins)
            if split.path.startswith("/api/"):
                if not editor.token_ok(self.headers.get_all(TOKEN_HEADER) or []):
                    return self._deny()
                # Con token: el cuerpo tiene su propio plazo total, más largo.
                self._authorized = True
                self._reader.deadline = time.monotonic() + self.body_deadline
                return self._api(method, split.path, split.query)
            if method == "GET":
                return self._static(split.path)
            raise EditorError(404, t("servidor.no_existe"))
        except EditorError as exc:
            self._reply_error(exc.status, exc.body())
        except _CONNECTION_ERRORS as exc:
            # El otro lado cortó (cerró la pestaña, canceló el fetch): no es un fallo.
            log.debug("Editor: conexión cortada (%s).", type(exc).__name__)
            self.close_connection = True
        except Exception as exc:
            # Solo el tipo: el mensaje y la traza pueden llevar rutas con el usuario.
            log.error("Error interno del editor (%s).", type(exc).__name__)
            self._reply_error(500, {"error": t("servidor.error_interno")})

    def _reply_error(self, code: int, data: object) -> None:
        """Responde un error sin dejar que un fallo al escribirlo se escape del hilo."""
        # Si una respuesta se quedó a medias (cabeceras en el búfer sin enviar), se tira:
        # si no, saldrían dos líneas de estado seguidas («403 … 500»).
        self._headers_buffer = []
        try:
            self._json(code, data)
        except OSError as exc:
            log.debug("Editor: no se pudo responder (%s).", type(exc).__name__)
            self.close_connection = True

    def _preflight(self, origins: list[str]) -> None:
        editor = self.server.editor
        if editor.dev_origin is None or origins != [editor.dev_origin]:
            return self._deny()
        self._send(
            204,
            b"",
            "text/plain",
            {
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
                "Access-Control-Allow-Headers": f"{TOKEN_HEADER}, {FILENAME_HEADER}, Content-Type",
                "Access-Control-Max-Age": "600",
            },
        )

    def _api(self, method: str, path: str, query: str) -> None:
        editor = self.server.editor
        store = editor.store
        seg = [unquote(s) for s in path.split("/")[2:]]
        route = (method, *seg)

        match route:
            case ("GET", "info"):
                return self._json(200, editor.info())
            case ("GET", "sensores"):
                return self._json(200, editor.sensor_rows())
            case ("GET", "temas"):
                return self._json(200, store.list())
            case ("POST", "vista-previa"):
                png, missing = editor.preview(self._json_body())
                header = json.dumps(missing, ensure_ascii=True, separators=(",", ":"))
                return self._send(200, png, "image/png", {MISSING_GLYPHS_HEADER: header})
            case ("GET", "temas", name):
                return self._json(200, store.get(name))
            case ("PUT", "temas", name):
                store.save(name, self._json_body())
                return self._json(200, {"ok": True})
            case ("DELETE", "temas", name):
                store.delete(name)
                return self._json(200, {"ok": True})
            case ("POST", "temas", name, "copiar"):
                body = self._json_body()
                if not isinstance(body, dict):
                    raise EditorError(400, t("servidor.falta_nombre_nuevo"))
                new = store.copy(name, body.get("title"), body.get("theme"))
                return self._json(200, {"name": new})
            case ("POST", "temas", name, "aplicar"):
                editor.apply(name)
                return self._json(200, {"ok": True})
            case ("POST", "temas", name, "recursos"):
                store._writable(name)  # 404/409 antes de leer 10 MB
                if query:
                    # Antes iba en ``?nombre=``; que nadie lo siga mandando por ahí.
                    raise EditorError(400, t("servidor.nombre_en_cabecera", header=FILENAME_HEADER))
                suggested = self._upload_filename()
                store._upload_target(suggested)  # 400 antes de leer 10 MB
                data = self._body(MAX_UPLOAD_BODY)
                return self._json(200, {"path": store.upload(name, suggested, data)})
            case ("GET", "ajustes"):
                return self._json(200, editor.settings().snapshot())
            case ("PUT", "ajustes"):
                editor.settings().update(**parse_update(self._json_body()))
                return self._json(200, {"ok": True})
            case ("POST", "pantalla"):
                choice_id, confirmed = parse_display(self._json_body())
                editor.settings().choose_display(choice_id, confirmed=confirmed)
                return self._json(200, {"ok": True})
            case ("POST", "pantalla", "respuesta"):
                looks_right = parse_answer(self._json_body())
                return self._json(200, {"message": editor.settings().answer(looks_right)})
            case ("POST", "actualizaciones", "buscar"):
                return self._json(200, {"message": editor.settings().check_updates()})
            case ("POST", "actualizaciones", "instalar"):
                return self._json(200, {"message": editor.settings().install_update()})
            case ("POST", "acceso-escritorio"):
                return self._json(200, {"message": editor.settings().create_shortcut()})
            case ("POST", "carpeta-temas"):
                editor.settings().open_themes_folder()
                return self._json(200, {"ok": True})
            case ("POST", "configuracion-inicio"):
                editor.settings().open_startup_settings()
                return self._json(200, {"ok": True})
            case ("POST", "importar-tema"):
                editor.settings()  # 404 antes de leer 20 MB
                suggested = self._upload_filename()
                data = self._body(MAX_ZIP_BYTES)
                return self._json(200, {"name": editor.import_theme(data, suggested)})
            case ("GET", "temas", name, "recursos", *rest) if rest:
                data, ctype = store.resource(name, "/".join(rest))
                return self._send(200, data, ctype)
        raise EditorError(404, t("servidor.no_existe"))

    def _upload_filename(self) -> str:
        """Nombre sugerido de la cabecera ``X-Luzeira-Filename``, ya decodificado.

        Llega codificado como en una URL (solo ASCII); se decodifica en UTF-8 estricto y
        luego ``ThemeStore._upload_target`` aplica las reglas de siempre (sin «/», barra
        invertida, «:», «..», ni carácter nulo; extensión permitida).
        """
        bad = EditorError(400, t("servidor.nombre_fichero_no_valido"))
        values = self.headers.get_all(FILENAME_HEADER) or []
        if len(values) != 1 or not values[0].isascii():
            raise bad
        try:
            return unquote(values[0], errors="strict")
        except UnicodeDecodeError:
            raise bad from None

    def _static(self, path: str) -> None:
        root = self.server.editor.web_root
        parts = [p for p in unquote(path).split("/") if p]
        if not parts:
            parts = ["index.html"]
        if len(parts) > 4 or not all(_STATIC_SEGMENT.match(p) and ".." not in p for p in parts):
            raise EditorError(404, t("servidor.no_existe"))
        # «COM3.js», «NUL.html», «con/x.css»: nombres de dispositivo de Windows. Se cortan
        # **antes** de tocar el disco: ``resolve`` o ``is_file`` abrirían el dispositivo
        # (COM3 puede ser justo el puerto de la pantalla).
        if any(is_reserved_windows_name(p) for p in parts):
            raise EditorError(404, t("servidor.no_existe"))
        target = root.joinpath(*parts)
        if not target.resolve().is_relative_to(root) or target.is_symlink() or not target.is_file():
            raise EditorError(404, t("servidor.no_existe"))
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if ctype is None:
            raise EditorError(404, t("servidor.no_existe"))
        # La CSP en todos los estáticos (no solo en el HTML): un SVG o un JSON abierto
        # directamente en el navegador tampoco puede cargar ni ejecutar nada de fuera.
        self._send(200, target.read_bytes(), ctype, {"Content-Security-Policy": CSP})
