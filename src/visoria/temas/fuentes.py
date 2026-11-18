"""Elección de fuentes para pintar los textos de un tema, y detección de glifos que faltan.

Orden de búsqueda de ``resolve_font`` (la primera que se pueda abrir gana):

1. La fuente del propio tema (``Style.font``), tomada **solo** de las rutas que
   ``load_theme`` ya validó (``LoadedTheme.files``): aquí nunca se vuelve a construir
   la ruta a partir del texto del tema. Antes de abrirla se repite la firma y el tamaño
   (pudo cambiar en disco desde la carga).
2. La fuente incluida en Visoria (``visoria/recursos/fuentes/``). Si la carpeta o el
   fichero aún no existen, se salta sin error.
3. Segoe UI del sistema (``segoeui.ttf``; ``segoeuib.ttf`` en negrita), buscada con ruta
   absoluta en la carpeta de fuentes de Windows: nunca por nombre suelto, porque Pillow
   probaría primero la carpeta actual y un ``segoeui.ttf`` plantado ahí se colaría.
4. ``ImageFont.load_default(size)`` (Aileron, que viene dentro de Pillow). Ojo: le faltan
   «¡», «¿», «ñ» y todas las vocales con tilde, así que solo es el último recurso.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .cargar import FONT_SIGNATURES, MAX_FILE_BYTES
from .modelo import Style
from .rutas import is_reserved_windows_name

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

# Nombres provisionales: la carpeta aún no existe y la fuente incluida está por decidir.
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "recursos" / "fuentes"
BUNDLED_REGULAR = "regular.ttf"
BUNDLED_BOLD = "bold.ttf"

SYSTEM_REGULAR = "segoeui.ttf"
SYSTEM_BOLD = "segoeuib.ttf"

# U+10FFFF es un «no carácter» de Unicode: ninguna fuente lo tiene, así que siempre se
# pinta con el glifo de «falta» (.notdef, normalmente un rectángulo: el «cuadro»).
_NOTDEF_PROBE = "\U0010ffff"


def _system_fonts_dir() -> Path | None:
    windir = os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT")
    return Path(windir) / "Fonts" if windir else None


def _theme_font_path(style: Style, files: Mapping[str, Path] | None) -> Path | None:
    """Ruta validada de la fuente del tema; None si no hay o ya no vale.

    La ruta sale de ``files`` (lo que validó ``load_theme``), nunca de unir la carpeta
    con ``style.font``. Se repite lo barato por si el fichero cambió desde la carga:
    nombre reservado, que siga siendo un fichero normal, tamaño y firma.
    Queda una carrera mínima entre esta comprobación y la apertura; el render la cubre
    capturando ``OSError`` al pintar.
    """
    if not style.font or not files:
        return None
    path = files.get(style.font)
    if path is None or is_reserved_windows_name(path.name):
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        ext = path.suffix.lower().lstrip(".")
        with path.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return None
    if ext not in FONT_SIGNATURES or not head.startswith(FONT_SIGNATURES[ext]):
        return None
    return path


@lru_cache(maxsize=64)
def _load(path: str | None, size: int) -> Font | None:
    """Abre una fuente (caché por ruta y tamaño). None si no se puede."""
    if path is None:
        return None
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return None


def resolve_font(style: Style, files: Mapping[str, Path] | None = None) -> Font:
    """Devuelve la fuente con la que pintar un texto con ``style``.

    ``files`` es ``LoadedTheme.files`` (ruta del tema → ruta absoluta ya validada);
    sin él (o si la fuente no está ahí) se salta la fuente del tema.

    Nunca falla: en el peor caso devuelve la fuente por defecto de Pillow. Con la
    fuente del tema no se finge negrita (``bold`` solo elige fichero en las demás).
    """
    candidates: list[Path | None] = [_theme_font_path(style, files)]
    candidates.append(BUNDLED_DIR / (BUNDLED_BOLD if style.bold else BUNDLED_REGULAR))
    sys_dir = _system_fonts_dir()
    if sys_dir is not None:
        candidates.append(sys_dir / (SYSTEM_BOLD if style.bold else SYSTEM_REGULAR))

    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            font = _load(str(candidate), style.size)
            if font is not None:
                return font
    return _load_default(style.size)


@lru_cache(maxsize=16)
def _load_default(size: int) -> Font:
    return ImageFont.load_default(size)


def _glyph_signature(font: Font, ch: str) -> tuple[object, ...]:
    mask = font.getmask(ch)
    return (mask.size, bytes(mask), font.getlength(ch))


def missing_glyphs(font: Font, text: str) -> list[str]:
    """Caracteres de ``text`` que ``font`` no tiene (se pintarían como un cuadro). RQ-16.

    Método: Pillow no expone la tabla de caracteres (cmap) de la fuente, así que se
    pinta cada carácter y se compara (tamaño, píxeles y avance) con lo que sale para
    U+10FFFF, que no existe en ninguna fuente y por tanto da el glifo «.notdef».
    Si son idénticos, ese carácter falta.

    Límites conocidos:
    - Se mira carácter a carácter: no ve ligaduras ni secuencias combinadas como unidad.
    - Si la fuente tiene un .notdef vacío (algunas lo tienen), cualquier carácter que se
      pinte vacío se confundiría con él; por eso los espacios se ignoran en ese caso.
    - Los caracteres de control (``\\n``, ``\\t``…) se ignoran: no se pintan.
    - Un glifo real idéntico al .notdef a ese tamaño saldría como falso positivo (raro).
    - Pillow no hace sustitución de fuentes: lo que aquí falta, falta en pantalla.

    Devuelve cada carácter faltante una vez, en el orden en que aparece.
    """
    notdef = _glyph_signature(font, _NOTDEF_PROBE)
    notdef_is_blank = not any(notdef[1])  # type: ignore[arg-type]
    missing: list[str] = []
    for ch in dict.fromkeys(text):
        if unicodedata.category(ch) == "Cc":
            continue
        if notdef_is_blank and ch.isspace():
            continue
        if _glyph_signature(font, ch) == notdef:
            missing.append(ch)
    return missing
