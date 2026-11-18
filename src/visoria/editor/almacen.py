"""Operaciones del editor sobre los temas: leer, guardar, copiar, borrar y subir recursos.

Es la parte «de disco» del servidor del editor, separada de HTTP para poder probarla
sola. Reglas (contrato de la fase 5 y skill ``seguridad-visoria``):

- Los temas incluidos son de **solo lectura**: se pueden leer, copiar y aplicar. Intentar
  cambiarlos es un **409** con mensaje (el 403 queda solo para los filtros de seguridad).
- Todo lo que llega del editor se valida igual que un tema compartido: esquema con
  ``Theme`` y cada fichero con las comprobaciones del cargador (``_check_file``).
- Escrituras **atómicas** (temporal + ``os.replace``) y carpetas nuevas montadas en una
  temporal ``.importando-*`` (la que limpia ``clean_import_leftovers`` tras un corte de luz).
- Borrar **mueve** a la papelera; nunca se borra un tema de verdad desde aquí.
- Los mensajes son para el usuario y nunca llevan rutas del disco.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from visoria.config import ThemeEntry, list_themes
from visoria.temas.cargar import (
    _PATH_FIELDS,
    _PYDANTIC_ES,
    FONT_SIGNATURES,
    IMAGE_SIGNATURES,
    MAX_FILE_BYTES,
    MAX_JSON_BYTES,
    MAX_UNZIPPED_BYTES,
    THEME_JSON,
    ThemeError,
    _check_file,
    _check_relpath,
    _free_name,
    _target_name,
    load_theme,
    slugify,
)
from visoria.temas.modelo import Theme

MAX_TITLE = 60  # igual que Theme.name
MAX_LISTED_FILES = 500  # tope de recursos que se listan de una carpeta
_WIDGET_TYPES = {"text", "clock", "sensor", "bar", "image"}
_IMPORT_PREFIX = ".importando-"  # lo limpia config.clean_import_leftovers
# Mensaje de Theme._widgets_inside_screen (en ``temas/modelo.py``, que también usa el
# cargador): «el widget 3 (text) se sale de la pantalla 480x320». Aquí se traduce a
# palabras del editor (RQ-12) y se cuelga de la caja de ese elemento.
_WIDGET_OFF_SCREEN = re.compile(r"^el widget (\d+) \((\w+)\) se sale de la pantalla (\d+)x(\d+)$")
# Los nombres de los tipos tal como salen en el botón «Añadir» del editor.
WIDGET_TYPE_ES = {
    "text": "Texto",
    "clock": "Reloj",
    "sensor": "Sensor",
    "bar": "Barra",
    "image": "Imagen",
}
# Mensajes de pydantic en palabras del usuario, sin jerga (true/false, JSON, objeto…).
# Lo que no esté aquí sale con ``GENERIC_INVALID``, nunca con el texto de pydantic (en
# inglés). Se parte del diccionario del cargador y se cambia lo que el editor enseña.
PYDANTIC_ES = {
    **_PYDANTIC_ES,
    "bool_type": "tiene que ser sí o no",
    "bool_parsing": "tiene que ser sí o no",
    "model_type": "faltan los datos de este apartado o están mal formados",
    "dict_type": "faltan los datos de este apartado o están mal formados",
    "union_tag_invalid": "tipo de elemento desconocido",
    "union_tag_not_found": "falta el tipo del elemento",
    "json_invalid": "los datos del tema están mal formados",
    "json_type": "los datos del tema están mal formados",
    "float_parsing": "tiene que ser un número",
    "int_from_float": "tiene que ser un número entero",
    "greater_than": "número demasiado pequeño",
    "less_than": "número demasiado grande",
    "too_short": "faltan elementos",
}
GENERIC_INVALID = "este valor no es válido"
# Mensajes de nuestros validadores que nombran campos del JSON.
_VALUE_ERRORS_ES = {
    "max tiene que ser mayor que min": "el máximo tiene que ser mayor que el mínimo",
}
TIMES = chr(0xD7)  # signo de multiplicar (así ruff no lo confunde con una equis)
DAMAGED = "Este tema está dañado y no se puede abrir."
GONE = "Ese tema ya no existe: puede que se haya borrado mientras tanto."

CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "ttf": "font/ttf",
    "otf": "font/otf",
}

READ_ONLY = "Los temas incluidos no se pueden cambiar. Usa «Guardar como…» para hacer tu copia."


class EditorError(Exception):
    """Error que se devuelve tal cual al editor: código HTTP, mensaje y detalles."""

    def __init__(self, status: int, message: str, details: list[dict] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or []

    def body(self) -> dict:
        out: dict = {"error": self.message}
        if self.details:
            out["details"] = self.details
        return out


# --- JSON y validación ---------------------------------------------------------------


def _reject_constant(name: str) -> float:
    raise ValueError(f"valor no permitido: {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # «1e400» → inf
        raise ValueError("número fuera de rango")
    return value


def parse_json(raw: bytes) -> object:
    """JSON del editor. Rechaza NaN/Infinity (el esquema los aceptaría en las barras)
    y el anidamiento absurdo (``RecursionError`` del parser)."""
    try:
        return json.loads(
            raw.decode("utf-8"), parse_constant=_reject_constant, parse_float=_finite_float
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EditorError(400, "Los datos enviados están mal formados.") from None


def friendly_theme_error(exc: ThemeError) -> str:
    """Mensaje de un ``ThemeError`` del cargador en palabras del editor.

    El cargador habla de ``theme.json`` y de «widgets» (lo lee también quien comparte
    temas); en el editor un tema roto es «dañado» y un widget es un «elemento».
    """
    message = str(exc)
    if message.startswith(THEME_JSON) or message.startswith(f"Falta el fichero {THEME_JSON}"):
        return DAMAGED
    return message.replace(" del widget ", " del elemento ")


def _loc_path(loc: tuple) -> str:
    """``('widgets', 3, 'sensor', 'box', 'x')`` → ``"widgets.3.box.x"``.

    Pydantic mete el tipo del widget (la «etiqueta» de la unión) en la ruta; el editor
    no lo usa, así que se quita. Los índices van desde 0, como en el JSON.
    """
    parts = list(loc)
    if (
        len(parts) >= 3
        and parts[0] == "widgets"
        and isinstance(parts[1], int)
        and parts[2] in _WIDGET_TYPES
    ):
        del parts[2]
    return ".".join(str(p) for p in parts)


def _item_message(item: dict) -> str:
    # Mismo criterio que cargar._friendly_validation (aquí por campo, no en un bloque).
    if item["type"] == "value_error":
        # Nuestros validadores ya hablan en español (``ctx.error``); si no hay, genérico.
        error = item.get("ctx", {}).get("error")
        if error is None:
            return GENERIC_INVALID
        return _VALUE_ERRORS_ES.get(str(error), str(error))
    if item["type"] == "string_pattern_mismatch" and tuple(item["loc"][-1:]) in _PATH_FIELDS:
        return (
            "no es un nombre de fichero válido (solo letras sin tilde, números, - y _, "
            "carpetas con «/», sin «..», «\\» ni «:»)"
        )
    return PYDANTIC_ES.get(item["type"], GENERIC_INVALID)


def _detail(item: dict) -> dict:
    """Un error de pydantic como ``{path, message}`` para el editor.

    Los errores de todo el tema (``loc`` vacío) que nombran un widget («el widget 3
    (text) se sale de la pantalla 480x320») se traducen («El elemento 3 (Texto) se sale
    de la pantalla (480 por 320)», con el signo de multiplicar ``TIMES``) y se cuelgan
    de su caja, ``widgets.2.box``, para que el editor marque ese elemento. El mensaje
    cuenta desde 1; la ruta, desde 0.
    """
    path = _loc_path(tuple(item["loc"]))
    message = _item_message(item)
    if not path and (m := _WIDGET_OFF_SCREEN.match(message)) and int(m.group(1)) >= 1:
        n, kind, width, height = m.groups()
        path = f"widgets.{int(n) - 1}.box"
        message = (
            f"El elemento {n} ({WIDGET_TYPE_ES.get(kind, kind)}) se sale de la pantalla "
            f"({width} {TIMES} {height})"
        )
    return {"path": path, "message": message}


def validation_details(err: ValidationError) -> list[dict]:
    return [_detail(item) for item in err.errors()[:50]]


def parse_theme(obj: object) -> Theme:
    """Valida un tema que llega del editor **igual que lo leerá luego** ``load_theme``:
    se serializa y se pasa por ``Theme.model_validate_json`` (modo JSON, estricto)."""
    if not isinstance(obj, dict):
        raise EditorError(400, "Los datos del tema están mal formados.")
    try:
        return Theme.model_validate_json(json.dumps(obj, ensure_ascii=False))
    except ValidationError as err:
        raise EditorError(400, "El tema no es válido.", validation_details(err)) from None


def theme_bytes(theme: Theme) -> bytes:
    data = json.dumps(theme.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    raw = data.encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise EditorError(413, "El tema es demasiado grande para guardarlo.")
    return raw


def atomic_write(path: Path, data: bytes) -> None:
    """Escribe en un temporal de la misma carpeta y lo renombra encima.

    En Windows ``os.replace`` falla con ``PermissionError`` si otro proceso tiene el
    fichero abierto en ese instante (el servicio leyéndolo): se reintenta un poco.
    """
    fd, tmp = tempfile.mkstemp(prefix=".guardando-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _gone_as_404():
    """La carpeta del tema desapareció a mitad de operación → 404 con mensaje claro."""
    try:
        yield
    except FileNotFoundError:
        raise EditorError(404, GONE) from None


# --- Almacén -------------------------------------------------------------------------


class ThemeStore:
    """Temas incluidos (solo lectura) + temas del usuario (``user_themes``)."""

    def __init__(
        self,
        user_themes: Path,
        trash: Path,
        *,
        active_theme: Callable[[], str | None] | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.user_themes = Path(user_themes)
        self.trash = Path(trash)
        self._active_theme = active_theme
        self._now = now
        # Un solo cerrojo para todo lo que escribe: el servidor atiende en varios hilos.
        self._lock = threading.RLock()
        # Caché de ficheros ya validados: (ruta, lstat, tipo, tamaños) → ruta real.
        # La vista previa se pide cada 300 ms y validar una fuente pinta una muestra.
        self._checked: dict[tuple, Path] = {}

    # --- consulta ---

    def entries(self) -> list[ThemeEntry]:
        return list_themes(self.user_themes)

    def entry(self, name: object) -> ThemeEntry:
        if isinstance(name, str):
            for entry in self.entries():
                if entry.name == name:
                    return entry
        raise EditorError(404, "No existe ese tema.")

    def _writable(self, name: object) -> ThemeEntry:
        entry = self.entry(name)
        if entry.builtin:
            raise EditorError(409, READ_ONLY)
        return entry

    def list(self) -> list[dict]:
        out = []
        for entry in self.entries():
            title = entry.name
            with contextlib.suppress(ThemeError, ValidationError, OSError):
                title = self._read_theme(entry.path).name
            out.append({"name": entry.name, "title": title, "builtin": entry.builtin})
        return out

    @staticmethod
    def _read_theme(directory: Path) -> Theme:
        path = directory / THEME_JSON
        with path.open("rb") as fh:
            raw = fh.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ThemeError("demasiado grande")
        return Theme.model_validate_json(raw)

    def get(self, name: object) -> dict:
        entry = self.entry(name)
        try:
            theme = self._read_theme(entry.path)
        except ValidationError as err:
            raise EditorError(400, DAMAGED, validation_details(err)) from None
        except FileNotFoundError:
            raise EditorError(404, GONE) from None
        except (ThemeError, OSError):
            raise EditorError(400, DAMAGED) from None
        return {
            "name": entry.name,
            "builtin": entry.builtin,
            "theme": theme.model_dump(mode="json"),
            "files": self.list_files(entry.path),
        }

    @staticmethod
    def list_files(directory: Path) -> list[str]:
        """Imágenes y fuentes de la carpeta con nombre válido (sin abrirlas: barato)."""
        allowed = set(IMAGE_SIGNATURES) | set(FONT_SIGNATURES)
        found: list[str] = []
        root = directory.resolve()
        for path in sorted(root.rglob("*")):
            if len(found) >= MAX_LISTED_FILES:
                break
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.rsplit(".", 1)[-1].lower() not in allowed:
                continue
            try:
                _check_relpath(rel, "El fichero")
            except ThemeError:
                continue
            found.append(rel)
        return found

    # --- validación de ficheros ---

    def _check_cached(
        self, directory: Path, rel: str, what: str, kind: str, sizes: frozenset[int]
    ) -> Path:
        _check_relpath(rel, what)  # antes de tocar el disco con ese nombre
        try:
            st = os.stat(directory / rel, follow_symlinks=False)
            key = (
                str(directory),
                rel,
                st.st_mtime_ns,
                st.st_size,
                st.st_ino,
                st.st_mode,
                kind,
                sizes,
            )
        except OSError:
            key = None
        if key is not None and key in self._checked:
            return self._checked[key]
        path = _check_file(directory, rel, what, kind, sizes)
        if key is not None:
            if len(self._checked) > 256:
                self._checked.clear()
            self._checked[key] = path
        return path

    def check_files(self, theme: Theme, directory: Path) -> dict[str, Path]:
        """Valida cada fichero que menciona ``theme`` dentro de ``directory``.

        Hace lo mismo que el bucle de ``load_theme``, pero sobre un tema que aún no está
        guardado (si no, una imagen recién subida no saldría en la vista previa) y
        devolviendo **todos** los problemas con su campo, para pintarlos en el editor.
        """
        directory = directory.resolve()
        refs: list[tuple[str, str, str, str]] = []  # (ruta, descripción, tipo, campo)
        font_sizes: dict[str, set[int]] = {}
        if theme.background.image:
            refs.append((theme.background.image, "La imagen de fondo", "image", "background.image"))
        for i, widget in enumerate(theme.widgets):
            if widget.type == "image":
                refs.append(
                    (widget.file, f"La imagen del elemento {i + 1}", "image", f"widgets.{i}.file")
                )
            if widget.style.font:
                refs.append(
                    (
                        widget.style.font,
                        f"La fuente del elemento {i + 1}",
                        "font",
                        f"widgets.{i}.style.font",
                    )
                )
                font_sizes.setdefault(widget.style.font, set()).add(widget.style.size)
        files: dict[str, Path] = {}
        failed: dict[str, str] = {}
        details: list[dict] = []
        for rel, what, kind, field in refs:
            if rel in files:
                continue
            if rel not in failed:
                try:
                    sizes = frozenset(font_sizes.get(rel, ()))
                    files[rel] = self._check_cached(directory, rel, what, kind, sizes)
                    continue
                except ThemeError as exc:
                    failed[rel] = str(exc)
            details.append({"path": field, "message": failed[rel]})
        if details:
            raise EditorError(400, "Algún fichero del tema no vale.", details)
        return files

    # --- escritura ---

    # Carreras con «borrar»: cada escritura vuelve a buscar el tema **dentro** del
    # cerrojo (``delete`` también lo coge), así que si lo acaban de mover a la papelera
    # sale un 404 claro y no un 500. ``_gone_as_404`` cubre lo que no pasa por aquí (la
    # carpeta borrada a mano en el Explorador a mitad de la operación).

    def save(self, name: object, obj: object) -> None:
        self._writable(name)  # 404/409 antes de validar nada
        theme = parse_theme(obj)
        data = theme_bytes(theme)
        with self._lock, _gone_as_404():
            entry = self._writable(name)
            self.check_files(theme, entry.path)
            atomic_write(entry.path / THEME_JSON, data)

    def copy(self, name: object, title: object, edited: object = None) -> str:
        """«Guardar como»: copia el tema (incluido o del usuario) a uno nuevo del usuario
        con ``title`` como nombre visible. Devuelve el nombre de la carpeta nueva.

        Si llega ``edited`` (el tema tal como está en el editor), la copia se guarda ya con
        él, en un solo paso: se valida el esquema y luego cada fichero **contra la carpeta
        nueva**, después de copiar los recursos del original. Si algo falla, la carpeta a
        medias se borra y no aparece ningún tema nuevo.
        """
        self.entry(name)  # 404 antes que los errores del título
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
            raise EditorError(
                400,
                "El nombre del tema nuevo no es válido.",
                [{"path": "title", "message": f"entre 1 y {MAX_TITLE} caracteres"}],
            )
        title = title.strip()
        if edited is not None and not isinstance(edited, dict):
            raise EditorError(400, "Los datos del tema están mal formados.")
        with self._lock, _gone_as_404():
            source = self.entry(name)
            try:
                loaded = load_theme(source.path)
            except ThemeError as exc:
                raise EditorError(400, friendly_theme_error(exc)) from None
            base = loaded.theme.model_dump(mode="json") if edited is None else edited
            theme = parse_theme({**base, "name": title})
            self.user_themes.mkdir(parents=True, exist_ok=True)
            root = self.user_themes.resolve()
            tmp = Path(tempfile.mkdtemp(prefix=_IMPORT_PREFIX, dir=root))
            try:
                self._copy_files(loaded.directory, tmp)
                if edited is not None:
                    self.check_files(theme, tmp)  # EditorError 400 con el campo
                atomic_write(tmp / THEME_JSON, theme_bytes(theme))
                load_theme(tmp)  # la copia tiene que valer por sí sola
                final = _free_name(root, slugify(title))
                tmp.rename(final)
            except ThemeError as exc:
                shutil.rmtree(tmp, ignore_errors=True)
                raise EditorError(400, friendly_theme_error(exc)) from None
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
        return final.name

    @staticmethod
    def _copy_files(src: Path, dest: Path) -> None:
        """Copia solo lo que admitiría un zip de tema (imágenes, fuentes y licencias
        ``.txt`` con nombre válido), sin enlaces y con los límites de tamaño."""
        budget = MAX_UNZIPPED_BYTES
        for path in sorted(src.rglob("*")):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(src):
                continue
            rel = path.relative_to(src).as_posix()
            if rel == THEME_JSON or _target_name(rel) is not None:
                continue  # theme.json se escribe aparte; lo demás no es del tema
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                continue
            budget -= size
            if budget < 0:
                raise ThemeError("El tema ocupa demasiado para copiarlo.")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def delete(self, name: object) -> None:
        with self._lock, _gone_as_404():
            self._delete_locked(name)

    def _delete_locked(self, name: object) -> None:
        entry = self.entry(name)
        if entry.builtin:
            raise EditorError(409, "Los temas incluidos no se pueden borrar.")
        if self._active_theme is not None and self._active_theme() == entry.name:
            raise EditorError(
                409, "Es el tema que está en la pantalla. Aplica otro antes de borrarlo."
            )
        self.trash.mkdir(parents=True, exist_ok=True)
        stamp = self._now().strftime("%Y%m%d-%H%M%S")
        dest = self.trash / f"{entry.name}-{stamp}"
        n = 2
        while dest.exists():
            dest = self.trash / f"{entry.name}-{stamp}-{n}"
            n += 1
        try:
            entry.path.rename(dest)
        except PermissionError:
            raise EditorError(
                409, "No se puede borrar ahora: algún fichero del tema está en uso."
            ) from None
        except FileNotFoundError:
            raise EditorError(404, GONE) from None
        except OSError:
            # Otra unidad (papelera fuera del disco de los temas): copiar y quitar.
            shutil.move(str(entry.path), str(dest))

    # --- recursos ---

    @staticmethod
    def _upload_target(suggested: str | None) -> tuple[str, str, str, str]:
        """(carpeta, nombre, extensión, tipo) saneados a partir del nombre sugerido."""
        bad = EditorError(400, "El nombre del fichero no es válido.")
        if not suggested or len(suggested) > 200:
            raise bad
        if any(c in suggested for c in "/\\:\x00") or ".." in suggested:
            raise bad
        stem, dot, ext = suggested.rpartition(".")
        if not dot or not stem:
            raise bad
        ext = ext.lower()
        if ext in IMAGE_SIGNATURES:
            return "img", slugify(stem), ext, "image"
        if ext in FONT_SIGNATURES:
            return "fuentes", slugify(stem), ext, "font"
        raise EditorError(400, "Solo se pueden subir imágenes PNG, JPEG o GIF y fuentes TTF u OTF.")

    def upload(self, name: object, suggested: str | None, data: bytes) -> str:
        """Guarda un recurso subido y devuelve su ruta dentro del tema (``img/logo.png``).

        El fichero se escribe y valida en una carpeta temporal y solo después entra en el
        tema, con un nombre libre (nunca pisa uno que ya exista).
        """
        self._writable(name)  # 404/409 antes de escribir nada
        folder, stem, ext, kind = self._upload_target(suggested)
        if len(data) > MAX_FILE_BYTES:
            raise EditorError(413, "El fichero es demasiado grande (máximo 10 MB).")
        self.user_themes.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=_IMPORT_PREFIX, dir=self.user_themes.resolve()))
        try:
            rel_tmp = f"{folder}/{stem}.{ext}"
            (tmp / folder).mkdir()
            (tmp / rel_tmp).write_bytes(data)
            try:
                _check_file(tmp, rel_tmp, "El fichero subido", kind)
            except ThemeError as exc:
                raise EditorError(400, str(exc)) from None
            with self._lock, _gone_as_404():
                directory = self._writable(name).path.resolve()
                rel = rel_tmp
                n = 2
                while (directory / rel).exists():
                    rel = f"{folder}/{stem}-{n}.{ext}"
                    n += 1
                (directory / folder).mkdir(exist_ok=True)
                os.replace(tmp / rel_tmp, directory / rel)
            return rel
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def resource(self, name: object, rel: str) -> tuple[bytes, str]:
        entry = self.entry(name)
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in CONTENT_TYPES:
            raise EditorError(404, "No existe ese recurso.")
        directory = entry.path.resolve()
        try:
            _check_relpath(rel, "El recurso")
        except ThemeError:
            raise EditorError(404, "No existe ese recurso.") from None
        target = directory / rel
        if target.is_symlink() or not target.is_file():
            raise EditorError(404, "No existe ese recurso.")
        kind = "image" if ext in IMAGE_SIGNATURES else "font"
        try:
            path = self._check_cached(directory, rel, "El recurso", kind, frozenset())
        except ThemeError as exc:
            raise EditorError(400, str(exc)) from None
        try:
            return path.read_bytes(), CONTENT_TYPES[ext]
        except FileNotFoundError:  # borrado entre la comprobación y la lectura
            raise EditorError(404, "No existe ese recurso.") from None
