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


def current() -> str:
    return _active


def windows_language() -> str | None:
    """Código de dos letras del idioma de la interfaz de Windows (``es``, ``en``…)."""
    if sys.platform != "win32":
        return None
    try:
        langid = ctypes.WinDLL("kernel32").GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return None
    name = locale.windows_locale.get(langid)  # p. ej. «es_ES»
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
