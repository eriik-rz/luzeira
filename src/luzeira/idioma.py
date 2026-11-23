"""Idiomas de la app (C-62): los textos que ve el usuario salen de catálogos JSON.

Concepto: en el código no se escribe «No encuentro la pantalla», sino una **clave**
(``t("pantalla.no_encontrada")``), y cada idioma tiene su catálogo con el texto. Así se
añade un idioma escribiendo ficheros, sin tocar el código.

- Catálogos en ``recursos/idiomas/<código>/<zona>.json``: un objeto plano
  ``{"clave": "texto"}``. Cada zona (``bandeja``, ``temas``…) es un fichero, para que varias
  personas puedan trabajar a la vez sin pisarse; una clave no puede estar en dos zonas.
- Variables con llaves: ``"Hay una versión nueva: la {version}."`` →
  ``t("…", version="0.2.0")``. Los textos no llevan llaves sueltas: sin variables se
  muestran tal cual, y con variables una llave rompería el formato (y el editor no
  desescapa ``{{``).
- Si al idioma elegido le falta una clave, se usa la inglesa, y si no la española (los
  dos se mantienen completos: lo vigila un test). Si no está en ninguno, sale la clave,
  que se ve rara a propósito para que se note.
- El idioma por defecto es el de Windows; si no lo tenemos, inglés.

Los logs **no** se traducen: son para quien depura, y siguen en español.
"""

from __future__ import annotations

import ctypes
import json
import locale
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).parent / "recursos" / "idiomas"
#: Los idiomas que se mantienen completos siempre. El resto puede ir algo por detrás.
COMPLETE = ("es", "en")
#: Si el de Windows no lo tenemos.
FALLBACK = "en"
#: Nombre de cada idioma en su propio idioma (así se reconoce en la lista). Solo se ofrecen
#: los que tienen carpeta de catálogos.
NATIVE_NAMES = {
    "es": "Español",
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português (Brasil)",
    "pl": "Polski",
    "tr": "Türkçe",
    "ru": "Русский",
    "zh": "简体中文",
    "ja": "日本語",
}

_lock = threading.Lock()
_catalogs: dict[str, dict[str, str]] = {}
_active = "es"


def available() -> dict[str, str]:
    """Código → nombre propio de los idiomas que tienen catálogo, en el orden de arriba."""
    return {code: name for code, name in NATIVE_NAMES.items() if (CATALOG_DIR / code).is_dir()}


def load(code: str, folder: Path | None = None) -> dict[str, str]:
    """Junta las zonas de un idioma. Lanza ``ValueError`` si una clave se repite entre
    zonas o si un fichero no es un objeto de textos (lo detectan los tests, no el usuario)."""
    merged: dict[str, str] = {}
    for path in sorted(((folder or CATALOG_DIR) / code).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            raise ValueError(f"{path.name} ({code}): tiene que ser un objeto de textos")
        repeated = merged.keys() & data.keys()
        if repeated:
            raise ValueError(f"{path.name} ({code}): claves repetidas {sorted(repeated)}")
        merged.update(data)
    return merged


def _catalog(code: str) -> dict[str, str]:
    with _lock:
        if code not in _catalogs:
            try:
                _catalogs[code] = load(code)
            except (OSError, ValueError):
                log.exception("No se pudo leer el catálogo de idioma %s", code)
                _catalogs[code] = {}
        return _catalogs[code]


def t(key: str, /, **variables: object) -> str:
    """El texto de ``key`` en el idioma activo, con sus variables puestas."""
    for code in (_active, FALLBACK, "es"):
        text = _catalog(code).get(key)
        if text is not None:
            break
    else:
        log.warning("Falta el texto %s en todos los idiomas", key)
        return key
    if not variables:
        return text
    try:
        return text.format(**variables)
    except (KeyError, IndexError, ValueError):
        log.warning("El texto %s (%s) no casa con sus variables", key, _active)
        return text


class Text(str):
    """Un texto ya traducido que **recuerda su clave y sus variables**.

    Concepto: se usa como un ``str`` normal (es una subclase: vale en ``f""``, en ``in``,
    en ``==``…) con el texto en el idioma de cuando se creó. Pero además guarda de dónde
    salió, y eso sirve para dos cosas al cambiar de idioma:

    - :func:`again` lo vuelve a escribir en el idioma de **ahora**;
    - :func:`ident` dice «qué mensaje es» sin depender del idioma: dos ``Text`` de la misma
      clave y variables son el mismo aviso aunque uno esté en español y otro en inglés.

    Lo usan los estados del servicio y los errores de la pantalla (C-62).
    """

    key: str
    variables: dict[str, object]

    def __new__(cls, key: str, /, **variables: object) -> Text:
        obj = super().__new__(cls, t(key, **variables))
        obj.key = key
        obj.variables = variables
        return obj

    def again(self) -> str:
        return t(self.key, **{name: again(value) for name, value in self.variables.items()})

    def ident(self) -> object:
        return (self.key, tuple(sorted((n, ident(v)) for n, v in self.variables.items())))


class Joined(Text):
    """Varios textos seguidos, separados por un espacio (p. ej. «… Lo sigo intentando.»)."""

    parts: tuple[object, ...]

    def __new__(cls, *parts: object) -> Joined:
        obj = str.__new__(cls, " ".join(str(p) for p in parts))
        obj.key, obj.variables, obj.parts = "", {}, parts
        return obj

    def again(self) -> str:
        return " ".join(str(again(p)) for p in self.parts)

    def ident(self) -> object:
        return ("", tuple(ident(p) for p in self.parts))


class Composed(Text):
    """Piezas puestas en un patrón fijo nuestro (``"  - {}: {}"``), no del catálogo.

    Cada pieza puede ser un ``Text``, un error con uno o un texto que no depende del idioma
    (el nombre de un campo). Así un mensaje compuesto (la lista de problemas de un
    ``theme.json``) también se reescribe entero al cambiar de idioma y cuenta como el mismo
    aviso. El patrón es siempre una constante del código: las piezas no se interpretan.
    """

    pattern: str
    parts: tuple[object, ...]

    def __new__(cls, pattern: str, *parts: object) -> Composed:
        obj = str.__new__(cls, pattern.format(*(str(p) for p in parts)))
        obj.key, obj.variables, obj.pattern, obj.parts = "", {}, pattern, parts
        return obj

    def again(self) -> str:
        return self.pattern.format(*(str(again(p)) for p in self.parts))

    def ident(self) -> object:
        return (self.pattern, tuple(ident(p) for p in self.parts))


def _text_in(value: object) -> Text | None:
    """El ``Text`` de un error creado con uno (``DisplayError(Text(...))``), si lo hay."""
    if isinstance(value, BaseException) and value.args and isinstance(value.args[0], Text):
        return value.args[0]
    return None


def again(value: object) -> object:
    """``value`` escrito otra vez en el idioma activo si es un ``Text`` (o un error con
    uno); cualquier otra cosa, tal cual."""
    if isinstance(value, Text):
        return value.again()
    inner = _text_in(value)
    return inner.again() if inner is not None else value


def ident(value: object) -> object:
    """Qué mensaje es, sin depender del idioma. Un error sin ``Text`` cuenta por su tipo:
    su texto ya viene traducido y cambiaría con el idioma."""
    if isinstance(value, Text):
        return value.ident()
    inner = _text_in(value)
    if inner is not None:
        return inner.ident()
    if isinstance(value, BaseException):
        return type(value).__name__
    return value


def message_of(exc: BaseException) -> str:
    """El mensaje de un error para el usuario: su ``Text`` si se creó con uno (así se puede
    reescribir al cambiar de idioma) y si no, ``str(exc)``."""
    inner = _text_in(exc)
    return inner if inner is not None else str(exc)


def current() -> str:
    return _active


_TRADITIONAL_CHINESE = {"zh_TW", "zh_HK", "zh_MO", "zh_CHT"}


def windows_language() -> str | None:
    """Código de dos letras del idioma de la interfaz de Windows (``es``, ``en``…)."""
    if sys.platform != "win32":
        return None
    try:
        langid = ctypes.WinDLL("kernel32").GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return None
    name = locale.windows_locale.get(langid)  # p. ej. «es_ES»
    if name in _TRADITIONAL_CHINESE:
        # Nuestro chino es el simplificado: en Taiwán, Hong Kong y Macao se escribe el
        # tradicional, así que ahí se cae al inglés (y se puede elegir a mano).
        return None
    return name.split("_")[0].lower() if name else None


def resolve(code: str | None, detected: str | None = None) -> str:
    """El idioma que se usa: el elegido si existe; si no (o es «automático»), el de
    Windows si lo tenemos; si no, inglés."""
    langs = available()
    if code in langs:
        return code
    detected = windows_language() if detected is None else detected
    return detected if detected in langs else FALLBACK


def set_language(code: str | None) -> str:
    """Activa el idioma (``None`` = el de Windows) y devuelve el que queda."""
    global _active
    _active = resolve(code)
    return _active
