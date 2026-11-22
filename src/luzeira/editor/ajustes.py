"""Ajustes de la app desde el editor (ventana «Ajustes»).

Lo mismo que el menú del icono (brillo, girar, pantalla, inicio con Windows, acceso
directo, importar tema, carpeta de temas, actualizaciones), pero desde la ventana del
editor. Quien lo
implementa de verdad es la bandeja (:class:`luzeira.bandeja.TrayApp`); el servidor solo
conoce este contrato y valida lo que llega por HTTP antes de pasárselo.

Elegir pantalla va por **identificador**: la bandeja da en ``snapshot()`` la lista
cerrada (y la del modo avanzado) con un ``id`` por opción, y al elegir se busca ese ``id``
en una lista recién calculada. Así el editor no puede pedir un par puerto/modelo que no
se le haya ofrecido.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luzeira.idioma import t

from .almacen import EditorError


class SettingsBackend(Protocol):
    def snapshot(self) -> dict: ...

    def update(
        self,
        *,
        brightness: int | None,
        flip: bool | None,
        autostart: bool | None,
        updates: bool | None = None,
        language: str | None = None,
    ) -> None:
        """``language``: un código de idioma o ``"auto"`` (el de Windows)."""
        ...

    def updates_question(self) -> bool:
        """¿Hay que preguntar una vez si avisar de versiones nuevas? (ADR-006)"""
        ...

    def update_available(self) -> str | None:
        """La versión nueva ya comprobada que se puede instalar, si hay (y no se está
        descargando). El editor la ofrece en una ventana."""
        ...

    def check_updates(self) -> str:
        """Busca una versión nueva ahora (bloquea); devuelve el texto para el usuario."""
        ...

    def install_update(self) -> str:
        """Descarga, comprueba e instala la versión ofrecida (Luzeira se cierra después).
        Lanza :class:`EditorError` si no se puede."""
        ...

    def choose_display(self, choice_id: str | None, *, confirmed: bool) -> None:
        """``None``: volver a detectarla sola. Lanza :class:`EditorError` si no vale."""
        ...

    def create_shortcut(self) -> str:
        """Crea el acceso del escritorio; devuelve el texto para el usuario."""
        ...

    def open_themes_folder(self) -> None: ...

    def open_startup_settings(self) -> None:
        """Abre Configuración → Aplicaciones → Inicio (cuando solo se puede activar allí)."""
        ...

    def import_theme(self, zip_path: Path) -> str:
        """Importa el zip y devuelve el nombre del tema nuevo."""
        ...

    def question(self) -> dict | None:
        """La pregunta «¿se ve bien?» pendiente en el editor (``{title, text}``), si hay."""
        ...

    def answer(self, looks_right: bool) -> str:
        """Contesta esa pregunta; devuelve el texto para el usuario."""
        ...


def _optional(body: dict, key: str, kind: type) -> object:
    value = body.get(key)
    # bool es un int en Python: un «true» no puede colarse como brillo.
    if value is None or (type(value) is kind):
        return value
    raise EditorError(400, t("servidor.ajuste_no_valido"))


def parse_update(body: object) -> dict:
    """``{brightness?, flip?, autostart?, updates?, language?}`` → argumentos de
    :meth:`SettingsBackend.update`."""
    allowed = {"brightness", "flip", "autostart", "updates", "language"}
    if not isinstance(body, dict) or not body or set(body) - allowed:
        raise EditorError(400, t("servidor.ajuste_no_valido"))
    return {
        "brightness": _optional(body, "brightness", int),
        "flip": _optional(body, "flip", bool),
        "autostart": _optional(body, "autostart", bool),
        "updates": _optional(body, "updates", bool),
        "language": _language(body),
    }


def _language(body: dict) -> str | None:
    """Código corto (``es``, ``pt``…) o ``auto``; si existe lo decide la bandeja."""
    value = _optional(body, "language", str)
    if value is not None and not (
        value == "auto"
        or (len(value) == 2 and value.isascii() and value.isalpha() and value.islower())
    ):
        raise EditorError(400, t("servidor.ajuste_no_valido"))
    return value


def parse_answer(body: object) -> bool:
    """``{"looks_right": bool}``."""
    if (
        isinstance(body, dict)
        and set(body) == {"looks_right"}
        and type(body["looks_right"]) is bool
    ):
        return body["looks_right"]
    raise EditorError(400, t("servidor.respuesta_no_valida"))


def parse_display(body: object) -> tuple[str | None, bool]:
    """``{"auto": true}`` o ``{"id": "...", "confirmed": bool}``."""
    if isinstance(body, dict) and body == {"auto": True}:
        return None, False
    if (
        isinstance(body, dict)
        and set(body) <= {"id", "confirmed"}
        and isinstance(body.get("id"), str)
        and 0 < len(body["id"]) <= 200
        and type(body.get("confirmed", False)) is bool
    ):
        return body["id"], body.get("confirmed", False)
    raise EditorError(400, t("servidor.pantalla_no_valida"))
