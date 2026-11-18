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
``/api/carpeta-temas``, ``/api/importar-tema``): los mismos filtros; los implementa la
bandeja (ver ``ajustes.py``). Importar sube el ``.zip`` como cuerpo, con su nombre en
``X-Luzeira-Filename``.

Vista previa «superada»: las vistas previas se pintan de una en una. Si cuando a una le
llega el turno ya ha entrado otra más nueva, no se pinta y responde **409** con
``{"error": …, "superseded": true}`` (``PREVIEW_SUPERSEDED``). La interfaz la ignora: ya
ha cancelado esa petición al mandar la nueva. Si llega a mitad de pintar, se deja de
pintar en la siguiente imagen.
"""

from __future__ import annotations

import contextlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import socket
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from luzeira import __version__
from luzeira.render.motor import Renderer, _box_of, _fit_contain, _fit_cover, _Layer
from luzeira.sensores.base import Reading, SensorInfo
from luzeira.temas import ThemeError, load_theme, resolve_font
from luzeira.temas.cargar import MAX_ZIP_BYTES
from luzeira.temas.modelo import ORIENTATION_SIZES, ImageWidget

from .ajustes import SettingsBackend, parse_answer, parse_display, parse_update
from .almacen import EditorError, ThemeStore, friendly_theme_error, parse_json, parse_theme

log = logging.getLogger(__name__)

MAX_JSON_BODY = 1024 * 1024
MAX_UPLOAD_BODY = 10 * 1024 * 1024
MAX_DRAIN = 32 * 1024 * 1024  # cuerpo demasiado grande que aún se lee para poder responder 413
TOKEN_HEADER = "X-Luzeira-Token"  # noqa: S105 (nombre de cabecera, no un secreto)
FILENAME_HEADER = "X-Luzeira-Filename"
WEB_ROOT = Path(__file__).parent / "web"

DENIED = {"error": "Acceso denegado."}

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

# La página del editor solo carga lo suyo y solo habla con este servidor.
CSP = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' blob: data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


PREVIEW_SUPERSEDED = 409  # código de una vista previa que ya no hace falta (ver arriba)
# Memoria máxima de la caché de imágenes ya escaladas. Una imagen de pantalla completa
# (480x320 RGBA) son 600 KB: caben unas cien, de sobra para los 64 elementos de un tema.
SCALED_CACHE_BYTES = 64 * 1024 * 1024


class PreviewSuperseded(EditorError):
    """Hay una vista previa más nueva esperando: esta no se pinta."""

    def __init__(self) -> None:
        super().__init__(PREVIEW_SUPERSEDED, "Hay una vista previa más reciente en camino.")

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

    Repite el escalado de ``Renderer._build_background`` y de la capa de imagen de
    ``Renderer._make_layer`` con las mismas funciones (``_fit_cover``, ``_fit_contain``);
    el test ``test_cached_preview_matches_plain_renderer`` comprueba que el resultado es
    idéntico píxel a píxel al del motor (lo que va a la pantalla).
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
        if how == "cover":
            img = _fit_cover(self._open(rel).convert("RGB"), size)
        else:
            img = _fit_contain(self._open(rel), size)
        self._cache.put(key, img)
        return img

    def _build_background(self) -> Image.Image:
        bg = self.theme.background
        base = Image.new("RGBA", self.size, bg.color)
        if bg.image:
            base.paste(self._scaled(bg.image, self.size, "cover"))
        return base

    def _make_layer(self, widget):
        if not isinstance(widget, ImageWidget):
            return super()._make_layer(widget)
        b = widget.box
        layer = _Layer(widget, _box_of(widget), self._blank(widget), False)
        img = self._scaled(widget.file, (b.width, b.height), "contain")
        layer.image.alpha_composite(img, ((b.width - img.width) // 2, (b.height - img.height) // 2))
        return layer


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
            raise RuntimeError("El servidor del editor no está arrancado.")
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
        active = self._active_theme() if self._active_theme is not None else None
        return {
            "version": __version__,
            "status": str(status),
            "message": message,
            "sizes": sizes,
            "active_theme": active,
            # «¿Se ve bien?» de una pantalla elegida en el editor: la interfaz la muestra
            # en cuanto la ve aquí (pregunta por /api/info cada pocos segundos).
            "question": self._settings.question() if self._settings is not None else None,
        }

    def sensor_rows(self) -> list[dict]:
        return [_sensor_row(info, self._read_sensor(info.id)) for info in self._sensors()]

    def preview(self, body: object) -> bytes:
        """PNG del tema ``body["theme"]`` (sin guardar) con los ficheros de ``body["name"]``.

        Lanza ``PreviewSuperseded`` (409) si entra otra vista previa antes de terminar.
        """
        with self._generation_lock:
            self._generation += 1
            generation = self._generation

        def superseded() -> bool:
            return self._generation != generation

        if not isinstance(body, dict):
            raise EditorError(400, "Faltan «name» y «theme».")
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
        buf = io.BytesIO()
        # compress_level=1: el PNG viaja por localhost; comprimir más solo gasta CPU.
        image.save(buf, "PNG", compress_level=1)
        return buf.getvalue()

    def settings(self) -> SettingsBackend:
        """Los ajustes de la app; sin ellos (tests, ``--desarrollo`` suelto), 404."""
        if self._settings is None:
            raise EditorError(404, "No existe.")
        return self._settings

    def import_theme(self, data: bytes, suggested: str) -> str:
        """Importa un tema ``.zip`` subido desde el editor (las mismas reglas que desde
        el menú: ``import_theme_zip`` valida rutas, tamaños e imágenes)."""
        if not suggested.lower().endswith(".zip"):
            raise EditorError(400, "El tema tiene que ser un fichero .zip.")
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

    def server_bind(self) -> None:
        # Quitar SO_REUSEADDR no impide que **otro** socket use SO_REUSEADDR para atarse
        # a nuestro puerto: eso lo corta SO_EXCLUSIVEADDRUSE (solo existe en Windows).
        # Medido en Windows 10 (2026-11-17): ese robo ya falla sin él (10013, la
        # «seguridad mejorada de sockets»); se pone igual, como recomienda Microsoft
        # para servidores, por si otra versión se comporta distinto.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _route_label(path: str) -> str:
    """Ruta para los logs sin nombres de temas ni ficheros: ``/api/temas/*/recursos``."""
    parts = path.split("?", 1)[0].split("/")
    if len(parts) > 2 and parts[1] == "api":
        known = {
            "info", "temas", "sensores", "vista-previa", "copiar", "aplicar", "recursos",
            "ajustes", "pantalla", "respuesta", "acceso-escritorio", "carpeta-temas",
            "importar-tema",
        }  # fmt: skip
        return "/" + "/".join(p if p in known or p == "api" else "*" for p in parts[1:6])
    return "/(estático)"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Luzeira"
    sys_version = ""
    timeout = 30  # una conexión lenta no se queda un hilo para siempre
    error_content_type = "application/json; charset=utf-8"
    error_message_format = '{"error": "Petición no válida (%(code)d)."}'
    server: _HTTPServer

    # --- logs: sin rutas ni nombres del usuario ---

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        log.debug("Editor: %s %s -> %s", self.command, _route_label(self.path), code)

    def log_error(self, format: str, *args: object) -> None:
        log.debug("Editor: petición rechazada por el servidor HTTP.")

    def log_message(self, format: str, *args: object) -> None:
        pass

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
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        editor = self.server.editor
        if editor.dev_origin is not None and origin == editor.dev_origin:
            self.send_header("Access-Control-Allow-Origin", editor.dev_origin)
            self.send_header("Vary", "Origin")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if code >= 400:
            # El cuerpo de la petición puede no haberse leído: no se reutiliza la conexión.
            self._drain()
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _deny(self) -> None:
        self._json(403, DENIED)

    # --- cuerpo ---

    def _body(self, limit: int) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise EditorError(411, "Falta la longitud de los datos (Content-Length).")
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            raise EditorError(411, "Falta la longitud de los datos (Content-Length).")
        if not lengths[0].isdigit():
            raise EditorError(400, "La longitud de los datos no es válida.")
        length = int(lengths[0])
        if length > limit:
            raise EditorError(
                413, f"Los datos son demasiado grandes (máximo {limit // (1024 * 1024)} MB)."
            )
        self._consumed = True
        data = self.rfile.read(length)
        if len(data) != length:
            raise EditorError(400, "Los datos llegaron incompletos.")
        return data

    def _drain(self) -> None:
        """Lee y tira el cuerpo que no se ha leído antes de responder un error.

        Si se cierra la conexión con datos sin leer, Windows la resetea y el navegador ve
        un «error de red» en vez del 403/413 con su mensaje. Solo hasta ``MAX_DRAIN``.
        """
        if getattr(self, "_consumed", True):
            return
        self._consumed = True
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > MAX_DRAIN:
            return
        left = int(lengths[0])
        try:
            while left > 0 and (chunk := self.rfile.read(min(left, 64 * 1024))):
                left -= len(chunk)
        except OSError:
            pass

    def _json_body(self) -> object:
        return parse_json(self._body(MAX_JSON_BODY))

    # --- despacho ---

    def _handle(self, method: str) -> None:
        editor = self.server.editor
        self._consumed = False
        try:
            if not editor.host_ok(self.headers.get_all("Host") or []):
                return self._deny()
            origins = self.headers.get_all("Origin") or []
            if not editor.origin_ok(origins):
                return self._deny()
            split = urlsplit(self.path)
            if method == "OPTIONS":
                return self._preflight(origins)
            if split.path.startswith("/api/"):
                if not editor.token_ok(self.headers.get_all(TOKEN_HEADER) or []):
                    return self._deny()
                return self._api(method, split.path, split.query)
            if method == "GET":
                return self._static(split.path)
            raise EditorError(404, "No existe.")
        except EditorError as exc:
            self._json(exc.status, exc.body())
        except Exception as exc:
            # Solo el tipo: el mensaje y la traza pueden llevar rutas con el usuario.
            log.error("Error interno del editor (%s).", type(exc).__name__)
            with contextlib.suppress(OSError):
                self._json(500, {"error": "Error interno. Prueba otra vez."})

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
                png = editor.preview(self._json_body())
                return self._send(200, png, "image/png")
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
                    raise EditorError(400, "Falta el nombre del tema nuevo.")
                new = store.copy(name, body.get("title"), body.get("theme"))
                return self._json(200, {"name": new})
            case ("POST", "temas", name, "aplicar"):
                editor.apply(name)
                return self._json(200, {"ok": True})
            case ("POST", "temas", name, "recursos"):
                store._writable(name)  # 404/409 antes de leer 10 MB
                if query:
                    # Antes iba en ``?nombre=``; que nadie lo siga mandando por ahí.
                    raise EditorError(
                        400, f"El nombre del fichero va en la cabecera {FILENAME_HEADER}."
                    )
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
            case ("POST", "acceso-escritorio"):
                return self._json(200, {"message": editor.settings().create_shortcut()})
            case ("POST", "carpeta-temas"):
                editor.settings().open_themes_folder()
                return self._json(200, {"ok": True})
            case ("POST", "importar-tema"):
                editor.settings()  # 404 antes de leer 20 MB
                suggested = self._upload_filename()
                data = self._body(MAX_ZIP_BYTES)
                return self._json(200, {"name": editor.import_theme(data, suggested)})
            case ("GET", "temas", name, "recursos", *rest) if rest:
                data, ctype = store.resource(name, "/".join(rest))
                return self._send(200, data, ctype)
        raise EditorError(404, "No existe.")

    def _upload_filename(self) -> str:
        """Nombre sugerido de la cabecera ``X-Luzeira-Filename``, ya decodificado.

        Llega codificado como en una URL (solo ASCII); se decodifica en UTF-8 estricto y
        luego ``ThemeStore._upload_target`` aplica las reglas de siempre (sin «/», barra
        invertida, «:», «..», ni carácter nulo; extensión permitida).
        """
        bad = EditorError(400, "El nombre del fichero no es válido.")
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
            raise EditorError(404, "No existe.")
        target = root.joinpath(*parts)
        if not target.resolve().is_relative_to(root) or target.is_symlink() or not target.is_file():
            raise EditorError(404, "No existe.")
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if ctype is None:
            raise EditorError(404, "No existe.")
        # La CSP en todos los estáticos (no solo en el HTML): un SVG o un JSON abierto
        # directamente en el navegador tampoco puede cargar ni ejecutar nada de fuera.
        self._send(200, target.read_bytes(), ctype, {"Content-Security-Policy": CSP})
