"""Configuración del usuario: qué tema, brillo y giro se usan. Se recuerda entre arranques.

Vive en ``%APPDATA%\\Luzeira\\config.json``. Los temas del usuario, en
``%APPDATA%\\Luzeira\\temas\\<nombre>``. Los temas incluidos, dentro del paquete.

Nada de datos personales aquí: ni rutas absolutas del perfil ni nombres. El tema se
guarda por su nombre de carpeta, no por su ruta.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

APP_NAME = "Luzeira"
BUILTIN_THEMES = Path(__file__).parent / "recursos" / "temas"
DEFAULT_THEME = "esencial"
_THEME_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,59}$")  # el slug que usa import_theme_zip


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def user_themes_dir() -> Path:
    return config_dir() / "temas"


#: La app se llamó «Visoria» hasta el 2026-11-18 (cambiada por una marca registrada ajena,
#: D-14). Solo la usó quien la desarrolla, sin versión publicada: esta migración y el viejo
#: identificador de formato se pueden quitar tras la primera release.
_OLD_NAME = "Visoria"
_OLD_FORMAT = '"visoria-theme/1"'
_NEW_FORMAT = '"luzeira-theme/1"'


def migrate_old_name(new: Path | None = None, old: Path | None = None) -> bool:
    """Copia los datos de la carpeta del nombre antiguo a la nueva, una sola vez.

    Solo si la nueva aún no existe: nunca pisa nada. La vieja se deja tal cual (por si
    algo falla, nada se pierde). Los temas copiados pasan al identificador de formato
    nuevo. Devuelve si ha migrado."""
    new = new or config_dir()
    old = old or new.with_name(_OLD_NAME)
    if new.exists() or not old.is_dir():
        return False
    shutil.copytree(old, new)
    for theme_json in new.glob("*/*/theme.json"):  # temas/<nombre> y papelera/<nombre>
        with contextlib.suppress(OSError, UnicodeDecodeError):
            text = theme_json.read_text(encoding="utf-8")
            if _OLD_FORMAT in text:
                theme_json.write_text(text.replace(_OLD_FORMAT, _NEW_FORMAT), encoding="utf-8")
    log.info("Datos copiados desde la carpeta del nombre anterior de la app.")
    return True


class DisplaySetting(BaseModel):
    """La pantalla que el usuario eligió a mano (C-44): modelo + el USB de su puerto.

    Se revalida en cada arranque contra lo que dice Windows (``pantallas.modelos.
    match_choice``). El COM se guarda solo para desempatar (o, en modo avanzado, para un
    puerto sin USB): el número de COM puede cambiar. El serie es el del aparato USB
    (p. ej. ``USB35INCHIPSV2``), no un dato de la persona.
    """

    model_config = ConfigDict(extra="ignore")

    model: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{0,39}$")
    device: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.\\:/-]+$")
    vid: int | None = Field(default=None, ge=0, le=0xFFFF)
    pid: int | None = Field(default=None, ge=0, le=0xFFFF)
    serial: str = Field(default="", max_length=128)
    advanced: bool = False


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")  # campos de versiones futuras: se ignoran

    theme: str = Field(default=DEFAULT_THEME, pattern=_THEME_NAME.pattern)
    brightness: int = Field(default=60, ge=0, le=100)
    flip: bool = False
    display: DisplaySetting | None = None  # None: detección sola
    # Avisar de versiones nuevas (ADR-006). None: aún no se ha preguntado, y sin un «sí»
    # la app no se conecta a nada.
    updates: bool | None = None
    # La versión más nueva que se ha visto firmada: nunca se ofrece una más vieja.
    updates_max_seen: str | None = Field(
        default=None,  # el mismo patrón que luzeira.actualizar._VERSION
        pattern=r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$",
    )
    updates_last_check: float | None = None  # hora (time.time()) de la última comprobación
    # Idioma de la app (C-62). None: el de Windows (ver luzeira.idioma.resolve).
    language: str | None = None

    @field_validator("language", mode="after")
    @classmethod
    def _forget_unknown_language(cls, value: str | None) -> str | None:
        """Un código que no tenemos (o uno raro a mano) se olvida: vuelve al de Windows."""
        from luzeira import idioma

        if value is not None and value not in idioma.available():
            log.warning("El idioma guardado no está disponible; se usa el de Windows.")
            return None
        return value

    @field_validator("display", mode="wrap")
    @classmethod
    def _forget_broken_display(cls, value: object, handler):
        """Una elección de pantalla dañada se olvida (se vuelve a detectar sola) sin
        tirar el resto de la configuración por «dañada»."""
        try:
            return handler(value)
        except ValidationError:
            log.warning("La pantalla guardada en la configuración no es válida; se olvida.")
            return None

    @field_validator("updates", "updates_max_seen", "updates_last_check", mode="wrap")
    @classmethod
    def _forget_broken_updates(cls, value: object, handler):
        """Igual con lo de las actualizaciones: un valor raro se olvida (y sin «sí» no
        hay red), sin perder el tema ni la pantalla."""
        try:
            return handler(value)
        except ValidationError:
            log.warning("Un ajuste de actualizaciones no es válido; se olvida.")
            return None


def load_config(path: Path | None = None) -> AppConfig:
    """Lee la configuración. Si no existe, la de por defecto. Si está dañada, se aparta
    como ``config.json.danado`` (para no perderla) y se usa la de por defecto."""
    path = path or config_dir() / "config.json"
    try:
        # utf-8-sig: acepta el BOM que añade el Bloc de notas al guardar en UTF-8.
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return AppConfig()
    except (UnicodeDecodeError, OSError):
        # Otra codificación (UTF-16 del Bloc de notas) o ilegible: nunca impedir arrancar.
        raw = None
    try:
        if raw is None:
            raise ValueError("configuración ilegible")
        return AppConfig.model_validate_json(raw)
    except (ValidationError, ValueError):
        log.warning("Configuración dañada; se aparta y se usa la de por defecto.")
        with contextlib.suppress(OSError):  # si no se puede apartar, al menos arrancamos
            path.replace(path.with_name(path.name + ".danado"))
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Guarda de forma atómica: se escribe un temporal y se renombra encima. Un corte de
    luz a mitad deja la versión anterior entera, nunca un fichero a medias."""
    path = path or config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(config.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


@dataclass(frozen=True)
class ThemeEntry:
    name: str  # nombre de carpeta (lo que se guarda en la configuración)
    path: Path
    builtin: bool
    title: str = ""  # el «name» del tema, para enseñarlo («Neón Synthwave»)


def _read_title(theme_json: Path, fallback: str) -> str:
    """El nombre legible del tema, leído con cuidado: un tema roto no rompe el menú."""
    try:
        raw = json.loads(theme_json.read_bytes()[:262_144])
        title = raw.get("name") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return fallback
    return title.strip()[:60] if isinstance(title, str) and title.strip() else fallback


def list_themes(user_dir: Path | None = None) -> list[ThemeEntry]:
    """Temas disponibles: primero los incluidos, luego los del usuario. Si un tema del
    usuario se llama igual que uno incluido, gana el incluido (no se puede suplantar).
    Las carpetas temporales de importación (``.importando-*``) no son temas."""
    user_dir = user_dir if user_dir is not None else user_themes_dir()
    entries: dict[str, ThemeEntry] = {}
    for base, builtin in ((BUILTIN_THEMES, True), (user_dir, False)):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if (
                child.is_dir()
                and _THEME_NAME.match(child.name)
                and (child / "theme.json").is_file()
                and child.name not in entries
            ):
                title = _read_title(child / "theme.json", child.name)
                entries[child.name] = ThemeEntry(child.name, child, builtin, title)
    return list(entries.values())


def theme_path(name: str, user_dir: Path | None = None) -> Path:
    """Carpeta del tema ``name``; si no existe, la del tema por defecto."""
    for entry in list_themes(user_dir):
        if entry.name == name:
            return entry.path
    log.warning("No existe el tema configurado; uso el de por defecto.")
    return BUILTIN_THEMES / DEFAULT_THEME


def clean_import_leftovers(user_dir: Path | None = None) -> int:
    """C-16: borra carpetas ``.importando-*`` que un corte de luz dejara a medias."""
    import shutil

    user_dir = user_dir if user_dir is not None else user_themes_dir()
    removed = 0
    if user_dir.is_dir():
        for child in user_dir.iterdir():
            if child.is_dir() and child.name.startswith(".importando-"):
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
    return removed
