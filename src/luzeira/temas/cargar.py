"""Carga e importación segura de temas (RQ-10).

Los temas los comparte la gente, así que todo lo que viene de un tema se trata como
**hostil** hasta que se demuestra lo contrario:

- ``load_theme`` abre una carpeta con ``theme.json``, valida el JSON con los modelos
  de ``modelo.py`` y comprueba en disco cada fichero que el tema menciona.
- ``import_theme_zip`` mete un ``.zip`` compartido en la carpeta de temas: lo extrae
  **a mano** (nunca con ``ZipFile.extractall``) a una carpeta temporal, lo valida con
  ``load_theme`` y solo entonces le da su nombre definitivo.

Todos los errores salen como ``ThemeError`` con un mensaje pensado para el usuario, en
el idioma activo (catálogo ``temas``): qué fichero y qué le pasa, sin trazas de Python.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError
from pydantic import TypeAdapter, ValidationError

from luzeira.idioma import Text, t

from .modelo import RelPath, Theme
from .rutas import is_reserved_windows_name

# --- Límites -----------------------------------------------------------------------

THEME_JSON = "theme.json"
MAX_JSON_BYTES = 256 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 4096
# C-15: hoy un GIF se pinta quieto (solo el fotograma 0), pero un GIF con miles de
# fotogramas diminutos cabe en 10 MB y cada uno se descomprime al contarlos. Se acota
# ya, para que el día que se animen no haya temas compartidos que lo pasen.
MAX_GIF_FRAMES = 300

MB = 1024 * 1024
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 200
# Un PNG o JPEG ya va comprimido (ratio ~1) y una fuente se queda en 2-3x. Más de 100x
# por fichero solo lo da algo hecho a propósito (bomba de descompresión). Por debajo de
# MIN_RATIO_BYTES no se mira: ficheros diminutos dan ratios raros sin peligro.
MAX_COMPRESSION_RATIO = 100
MIN_RATIO_BYTES = 1024 * 1024

# Extensión → firmas válidas (primeros bytes reales del fichero).
IMAGE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}
# TrueType empieza por 00 01 00 00 (o «true», de Apple); OpenType/CFF por «OTTO».
# Muchas .ttf son en realidad CFF y al revés, así que ambas extensiones aceptan ambas.
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"true", b"OTTO")
FONT_SIGNATURES: dict[str, tuple[bytes, ...]] = {"ttf": _FONT_MAGIC, "otf": _FONT_MAGIC}
PILLOW_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "gif": "GIF"}

# En el zip, además de theme.json, imágenes y fuentes, se admiten textos sueltos: las
# fuentes libres (OFL) obligan a acompañarlas de su licencia. Nunca se abren.
ZIP_EXTRA_EXTENSIONS = frozenset({"txt"})

_relpath_adapter: TypeAdapter[str] = TypeAdapter(RelPath)


@dataclass(frozen=True)
class Subject:
    """De qué fichero habla un error («La imagen del widget 3»): clave del catálogo y,
    si lo lleva, el número del widget. Se guarda en el error para poder contarlo luego
    con otras palabras (el editor dice «elemento» donde el cargador dice «widget»)."""

    key: str
    n: int | None = None

    def text(self) -> str:
        return Text(self.key) if self.n is None else Text(self.key, n=self.n)


BACKGROUND_IMAGE = Subject("temas.que.fondo")
#: Claves de los sujetos con número de widget («La imagen del widget {n}»).
WIDGET_IMAGE = "temas.que.imagen_widget"
WIDGET_FONT = "temas.que.fuente_widget"


class ThemeError(Exception):
    """Un tema no se puede usar. El mensaje es para el usuario, en el idioma activo.

    - ``damaged``: el problema es el propio ``theme.json`` (falta, es enorme o no cumple
      el formato). El editor lo cuenta como «tema dañado».
    - ``key``/``variables``/``subject``: si el mensaje habla de un fichero del tema, con
      qué texto del catálogo se compuso; ``retold`` lo vuelve a componer con otro sujeto.
    """

    def __init__(
        self,
        message: str,
        *,
        damaged: bool = False,
        key: str | None = None,
        variables: dict[str, object] | None = None,
        subject: Subject | None = None,
    ) -> None:
        super().__init__(message)
        self.damaged = damaged
        self.key = key
        self.variables = variables or {}
        self.subject = subject

    def retold(self, subject: Subject) -> str:
        """El mismo error, hablando de ``subject`` en vez del sujeto original."""
        if self.key is None:
            return str(self)
        return t(self.key, what=subject.text(), **self.variables)


def _what(what: Subject | str) -> str:
    return what if isinstance(what, str) else what.text()


def _file_error(key: str, what: Subject | str, **variables: object) -> ThemeError:
    """Error sobre un fichero del tema: «{what} «{rel}» …»."""
    return ThemeError(
        Text(key, what=_what(what), **variables),
        key=key,
        variables=variables,
        subject=what if isinstance(what, Subject) else None,
    )


@dataclass(frozen=True)
class LoadedTheme:
    """Un tema ya validado: el modelo, su carpeta y sus ficheros comprobados.

    ``files`` va de la ruta tal como sale en ``theme.json`` a la ruta absoluta real,
    ya comprobada (dentro de la carpeta, tipo real, tamaño y, si es imagen, abrible).
    """

    theme: Theme
    directory: Path
    files: dict[str, Path] = field(default_factory=dict)


# --- Utilidades de rutas -----------------------------------------------------------


def _check_relpath(rel: str, what: Subject | str) -> None:
    """Comprueba una ruta del tema por forma, antes de tocar el disco.

    Los nombres reservados de Windows se miran **antes** que el patrón, y aquí mismo:
    hoy ``RelPath`` (el modelo) también los rechaza, pero el cargador es la frontera de
    seguridad y no debe depender de que el modelo siga haciéndolo. Mirarlos primero hace
    además que esta comprobación se pueda alcanzar (y probar) llamando a la función.
    """
    for part in rel.split("/"):
        if is_reserved_windows_name(part):
            raise _file_error("temas.nombre_reservado", what, rel=rel, part=part)
    try:
        _relpath_adapter.validate_python(rel)
    except ValidationError:
        raise _file_error("temas.nombre_no_valido", what, rel=rel) from None


def _inside(path: Path, root: Path) -> bool:
    """¿``path`` resuelto (con enlaces y junctions seguidos) queda dentro de ``root``?"""
    return path.resolve().is_relative_to(root)


# --- Comprobación de ficheros ------------------------------------------------------


def _read_head(path: Path, n: int = 16) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _check_gif_frames(im: Image.Image, rel: str) -> None:
    """Cuenta los fotogramas de un GIF sin pasar de ``MAX_GIF_FRAMES`` (no usa
    ``n_frames``, que los recorrería todos aunque fueran millones)."""
    frames = 1
    try:
        while True:
            im.seek(frames)
            frames += 1
            if frames > MAX_GIF_FRAMES:
                raise ThemeError(Text("temas.gif_largo", rel=rel, max=MAX_GIF_FRAMES))
    except EOFError:
        pass
    im.seek(0)


def _check_image(path: Path, rel: str, ext: str) -> None:
    """Abre la imagen con Pillow: tipo real, tamaño en píxeles y datos completos."""
    fmt = PILLOW_FORMATS[ext]
    try:
        # formats=[...] impide que Pillow pruebe otros decodificadores con este fichero.
        with Image.open(path, formats=[fmt]) as im:
            width, height = im.size
            # El límite propio se mira con la cabecera, antes de descomprimir nada.
            # (Image.MAX_IMAGE_PIXELS solo da error al doble del límite; entre 1x y 2x
            # es un simple aviso, así que no basta.)
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                raise ThemeError(_too_big_image(rel, width, height))
            if fmt == "GIF":
                _check_gif_frames(im, rel)
            im.verify()  # sumas de control y estructura (sobre todo en PNG)
        # verify() deja la imagen inservible y no detecta un JPEG cortado: se vuelve
        # a abrir y se decodifica entera (como mucho 4096x4096, acotado).
        with Image.open(path, formats=[fmt]) as im:
            im.load()
    except ThemeError:
        raise
    except Image.DecompressionBombError:
        raise ThemeError(Text("temas.imagen_bomba", rel=rel)) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        # Pillow señala PNG rotos con SyntaxError y ficheros cortados con OSError.
        raise ThemeError(Text("temas.imagen_danada", rel=rel)) from None


def _too_big_image(rel: str, width: int, height: int) -> str:
    return Text("temas.imagen_enorme", rel=rel, width=width, height=height, max=MAX_IMAGE_SIDE)


def open_validated_image(path: Path, ext: str) -> Image.Image:
    """Abre una imagen ya validada por ``load_theme`` para pintarla. Para el render.

    Entre la carga y el pintado el fichero podría haber cambiado en disco, así que
    aquí se repite lo esencial sobre los bytes que de verdad se usan:

    - solo el decodificador que toca por la extensión (``formats=[...]``): un fichero
      cambiado por otro formato no se abre;
    - el límite de lado (``MAX_IMAGE_SIDE``) con la cabecera, antes de descomprimir;
    - solo el primer fotograma (un GIF animado se pinta quieto).

    Devuelve una copia RGBA ya en memoria, sin el fichero abierto. Si algo falla,
    lanza ``ThemeError``.
    """
    ext = ext.lower().lstrip(".")
    fmt = PILLOW_FORMATS.get(ext)
    if fmt is None:
        raise ThemeError(Text("temas.imagen_extension", rel=path.name))
    try:
        with Image.open(path, formats=[fmt]) as im:
            width, height = im.size
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                raise ThemeError(_too_big_image(path.name, width, height))
            im.seek(0)
            im.load()
            # convert() crea una imagen nueva: no queda nada ligado al fichero.
            return im.convert("RGBA")
    except ThemeError:
        raise
    except Image.DecompressionBombError:
        raise ThemeError(Text("temas.imagen_bomba", rel=path.name)) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError):
        raise ThemeError(Text("temas.imagen_danada", rel=path.name)) from None


# Muestra que se pinta con cada fuente del tema: cifras, signos de las unidades y lo
# típico del español. Una fuente puede abrirse bien y fallar al pintar (bytecode de
# hinting roto: «invalid opcode», «code overflow»…), así que hay que pintar de verdad.
# No es un texto para el usuario: no se traduce.
FONT_SAMPLE = "0123456789 %°¡¿ñÑáéíóú—↑↓:,.ABCabc"  # i18n: no
FONT_CHECK_SIZE = 20  # tamaño fijo que se prueba siempre, además de los del tema


def _check_font(path: Path, rel: str, sizes: frozenset[int] = frozenset()) -> None:
    """Abre la fuente y pinta ``FONT_SAMPLE`` a cada tamaño que el tema usa con ella.

    No cubre todos los glifos ni todos los tamaños posibles: el render, además,
    captura ``OSError`` al pintar y cae a la fuente incluida.
    """
    from PIL import ImageDraw, ImageFont

    # El lienzo puede ser diminuto: Pillow rasteriza el texto entero antes de recortar.
    draw = ImageDraw.Draw(Image.new("L", (8, 8)))
    for size in sorted({FONT_CHECK_SIZE, *sizes}):
        try:
            font = ImageFont.truetype(str(path), size)
        except (OSError, ValueError):
            raise ThemeError(Text("temas.fuente_danada", rel=rel)) from None
        try:
            draw.text((0, 0), FONT_SAMPLE, font=font, fill=255)
        except (OSError, ValueError):
            raise ThemeError(Text("temas.fuente_falla", rel=rel, size=size)) from None


def _check_file(
    directory: Path,
    rel: str,
    what: Subject | str,
    kind: str,
    sizes: frozenset[int] = frozenset(),
) -> Path:
    """Valida un fichero referenciado por el tema y devuelve su ruta real."""
    _check_relpath(rel, what)
    ext = rel.rsplit(".", 1)[1].lower()
    signatures = IMAGE_SIGNATURES if kind == "image" else FONT_SIGNATURES
    if ext not in signatures:
        allowed = ", ".join(sorted(signatures))
        raise _file_error("temas.extension_no_permitida", what, rel=rel, allowed=allowed)

    path = directory / rel
    if not _inside(path, directory):
        raise _file_error("temas.fuera_de_carpeta", what, rel=rel)
    if path.is_symlink() or not path.is_file():
        raise _file_error("temas.no_existe", what, rel=rel)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise _file_error(
            "temas.fichero_grande",
            what,
            rel=rel,
            size=size // (1024 * 1024),
            max=MAX_FILE_BYTES // (1024 * 1024),
        )
    if not _read_head(path).startswith(signatures[ext]):
        raise _file_error("temas.tipo_falso", what, rel=rel, ext=ext)
    if kind == "image":
        _check_image(path, rel, ext)
    else:
        _check_font(path, rel, sizes)
    return path.resolve()


# --- Errores de validación legibles --------------------------------------------------

#: Tipos de error de pydantic que tienen texto propio en el catálogo
#: (``temas.pydantic.<tipo>``). Lo que no esté aquí sale con el texto de pydantic.
PYDANTIC_TYPES = frozenset(
    {
        "missing",
        "extra_forbidden",
        "string_pattern_mismatch",
        "literal_error",
        "string_too_long",
        "string_too_short",
        "too_long",
        "greater_than_equal",
        "less_than_equal",
        "int_type",
        "int_parsing",
        "float_type",
        "bool_type",
        "string_type",
        "list_type",
        "model_type",
        "union_tag_invalid",
        "union_tag_not_found",
        "json_invalid",
    }
)


def pydantic_message(kind: str) -> str | None:
    """Un error de pydantic en palabras del usuario, o None si no lo tenemos."""
    return t(f"temas.pydantic.{kind}") if kind in PYDANTIC_TYPES else None


_PATH_FIELDS = {("image",), ("file",), ("font",)}


def _friendly_validation(err: ValidationError) -> str:
    lines = []
    for item in err.errors()[:5]:
        where = " > ".join(str(p + 1) if isinstance(p, int) else str(p) for p in item["loc"])
        if item["type"] == "value_error":
            # Nuestros validadores ya hablan en el idioma activo; pydantic antepone
            # «Value error, ».
            msg = str(item.get("ctx", {}).get("error", item["msg"]))
        elif item["type"] == "string_pattern_mismatch" and item["loc"][-1:] in _PATH_FIELDS:
            msg = t("temas.ruta_no_valida", input=item["input"])
        else:
            msg = pydantic_message(item["type"]) or item["msg"]
        lines.append(f"  - {where}: {msg}" if where else f"  - {msg}")
    more = len(err.errors()) - len(lines)
    if more > 0:
        lines.append("  - " + t("temas.mas_problemas", n=more))
    return "\n".join(lines)


# --- Carga de una carpeta ------------------------------------------------------------


def load_theme(path: str | os.PathLike[str]) -> LoadedTheme:
    """Carga y valida el tema de la carpeta ``path``.

    Lanza ``ThemeError`` si el ``theme.json`` falta, es enorme o no cumple el formato,
    o si algún fichero que menciona no está, sale de la carpeta, no es del tipo que
    dice ser, es demasiado grande, (si es imagen) no se puede abrir o (si es fuente)
    no se puede abrir o falla al pintar una muestra a los tamaños que usa el tema.
    """
    directory = Path(path)
    if not directory.is_dir():
        raise ThemeError(Text("temas.sin_carpeta", name=directory.name))
    directory = directory.resolve()

    json_path = directory / THEME_JSON
    if json_path.is_symlink() or not json_path.is_file() or not _inside(json_path, directory):
        raise ThemeError(Text("temas.falta_json", file=THEME_JSON), damaged=True)
    with json_path.open("rb") as fh:
        raw = fh.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ThemeError(
            Text("temas.json_grande", file=THEME_JSON, kb=MAX_JSON_BYTES // 1024), damaged=True
        )

    try:
        theme = Theme.model_validate_json(raw)
    except ValidationError as err:
        raise ThemeError(
            Text("temas.json_no_valido", file=THEME_JSON, details=_friendly_validation(err)),
            damaged=True,
        ) from None

    files: dict[str, Path] = {}
    refs: list[tuple[str, Subject, str]] = []  # (ruta, de qué fichero se trata, tipo)
    font_sizes: dict[str, set[int]] = {}  # fuente → tamaños con que la usa el tema
    if theme.background.image:
        refs.append((theme.background.image, BACKGROUND_IMAGE, "image"))
    for i, widget in enumerate(theme.widgets, start=1):
        if widget.type == "image":
            refs.append((widget.file, Subject(WIDGET_IMAGE, i), "image"))
        if widget.style.font:
            refs.append((widget.style.font, Subject(WIDGET_FONT, i), "font"))
            font_sizes.setdefault(widget.style.font, set()).add(widget.style.size)
    for rel, what, kind in refs:
        if rel not in files:
            sizes = frozenset(font_sizes.get(rel, ()))
            files[rel] = _check_file(directory, rel, what, kind, sizes)

    return LoadedTheme(theme=theme, directory=directory, files=files)


# --- Importación de un zip -----------------------------------------------------------


def _zip_entry_problem(info: zipfile.ZipInfo) -> str | None:
    """Devuelve por qué una entrada del zip es inaceptable (o None si vale por forma).

    Ojo: en Windows, ``zipfile`` convierte «\\» en «/» al leer el nombre, así que la
    barra invertida se busca en ``orig_filename`` (el nombre tal cual viene en el zip).
    """
    name = info.filename
    if "\\" in info.orig_filename or "\\" in name:
        return Text("temas.zip.barra")
    if name.startswith("/") or PurePosixPath(name).is_absolute():
        return Text("temas.zip.absoluta")
    if ":" in name:
        return Text("temas.zip.unidad")
    parts = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return Text("temas.zip.salir")
    if any(is_reserved_windows_name(p) for p in parts):
        return Text("temas.zip.reservado")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return Text("temas.zip.enlace")
    if info.flag_bits & 0x1:
        return Text("temas.zip.cifrado")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        return Text("temas.zip.compresion")
    return None


def _common_root(names: list[str]) -> str | None:
    """Si todo el zip cuelga de una sola carpeta sin theme.json en la raíz, la devuelve.

    Es lo que sale al comprimir una carpeta con el explorador de Windows: «Mi tema/…».
    """
    if THEME_JSON in names:
        return None
    firsts = {n.split("/", 1)[0] for n in names}
    if len(firsts) == 1 and all("/" in n.rstrip("/") or n.endswith("/") for n in names):
        return firsts.pop()
    return None


def _target_name(rel: str) -> str | None:
    """Comprueba el nombre (ya sin carpeta raíz) de un fichero del zip."""
    if rel == THEME_JSON:
        return None
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    allowed = set(IMAGE_SIGNATURES) | set(FONT_SIGNATURES) | ZIP_EXTRA_EXTENSIONS
    if not ext:
        return Text("temas.zip.sin_extension")  # antes salía «tipo no permitido (.sin extensión)»
    if ext not in allowed:
        return Text("temas.zip.tipo", ext=ext)
    try:
        _relpath_adapter.validate_python(rel)
    except ValidationError:
        return Text("temas.zip.nombre")
    return None


def slugify(name: str) -> str:
    """Nombre de carpeta seguro a partir del nombre del tema («Mi Tema Ñ» → «mi-tema-n»)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    if not slug:
        slug = "tema"
    if is_reserved_windows_name(slug):
        slug = f"tema-{slug}"
    return slug


def _extract_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, budget: int) -> int:
    """Copia una entrada a ``dest`` contando los bytes **reales**. Devuelve cuántos."""
    written = 0
    limit_ratio = max(MIN_RATIO_BYTES, MAX_COMPRESSION_RATIO * max(info.compress_size, 1))
    with zf.open(info) as src, dest.open("xb") as out:  # «x»: nunca pisa nada
        while chunk := src.read(64 * 1024):
            written += len(chunk)
            if written > MAX_FILE_BYTES:
                raise ThemeError(
                    Text("temas.zip.fichero_grande", name=info.filename, max=MAX_FILE_BYTES // MB)
                )
            if written > budget:
                raise ThemeError(Text("temas.zip.total_grande", max=MAX_UNZIPPED_BYTES // MB))
            if written > limit_ratio:
                raise ThemeError(Text("temas.zip.bomba", name=info.filename))
            out.write(chunk)
    return written


def import_theme_zip(zip_path: str | os.PathLike[str], themes_dir: str | os.PathLike[str]) -> Path:
    """Importa un tema compartido en ``.zip`` dentro de ``themes_dir``.

    Devuelve la carpeta final del tema. Si algo falla, lanza ``ThemeError`` y no deja
    nada a medias en ``themes_dir``.
    """
    zip_path = Path(zip_path)
    themes_root = Path(themes_dir)
    themes_root.mkdir(parents=True, exist_ok=True)
    themes_root = themes_root.resolve()

    if not zip_path.is_file():
        raise ThemeError(Text("temas.zip.no_encuentro", name=zip_path.name))
    if zip_path.stat().st_size > MAX_ZIP_BYTES:
        raise ThemeError(
            Text("temas.zip.demasiado_grande", name=zip_path.name, max=MAX_ZIP_BYTES // MB)
        )

    # Carpeta temporal oculta dentro de la de temas: así el renombrado final es en el
    # mismo disco (atómico) y, si se corta la luz, el resto se reconoce y se borra.
    tmp = Path(tempfile.mkdtemp(prefix=".importando-", dir=themes_root))
    try:
        _extract_zip(zip_path, tmp)
        loaded = load_theme(tmp)
        final = _free_name(themes_root, slugify(loaded.theme.name))
        tmp.rename(final)
        return final
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


_BUILTIN_THEMES = Path(__file__).resolve().parent.parent / "recursos" / "temas"


def _free_name(root: Path, slug: str) -> Path:
    """Primera carpeta libre: «slug», «slug-2», «slug-3»…

    También evita los nombres de los temas incluidos: si no, un tema importado que se
    llame «Esencial» quedaría tapado por el incluido y no aparecería (revisor fase 4).
    """
    candidate = root / slug
    n = 2
    while candidate.exists() or (_BUILTIN_THEMES / candidate.name).exists():
        candidate = root / f"{slug}-{n}"
        n += 1
    return candidate


def _extract_zip(zip_path: Path, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ThemeError(Text("temas.zip.no_valido", name=zip_path.name)) from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ThemeError(Text("temas.zip.muchos", max=MAX_ZIP_ENTRIES))

        # 1) Todo se comprueba por nombre ANTES de escribir un solo byte.
        for info in infos:
            problem = _zip_entry_problem(info)
            if problem:
                raise ThemeError(Text("temas.zip.no_seguro", name=info.filename, problem=problem))
        names = [i.filename for i in infos]
        root = _common_root(names)
        plan: list[tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        for info in infos:
            rel = info.filename
            if root is not None:
                rel = rel[len(root) + 1 :]
            if info.is_dir() or not rel:
                continue
            problem = _target_name(rel)
            if problem:
                raise ThemeError(Text("temas.zip.no_seguro", name=info.filename, problem=problem))
            key = rel.casefold()  # Windows no distingue mayúsculas: «A.png» = «a.png»
            if key in seen:
                raise ThemeError(Text("temas.zip.repetido", rel=rel))
            seen.add(key)
            plan.append((info, rel))

        # 2) Extracción a mano, contando bytes reales y sin pisar nada.
        budget = MAX_UNZIPPED_BYTES
        for info, rel in plan:
            target = dest / rel
            if not target.resolve().is_relative_to(dest.resolve()):  # defensa en profundidad
                raise ThemeError(Text("temas.zip.sale", name=info.filename))
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                budget -= _extract_entry(zf, info, target, budget)
            except ThemeError:
                raise
            except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError):
                raise ThemeError(Text("temas.zip.danado", name=info.filename)) from None
