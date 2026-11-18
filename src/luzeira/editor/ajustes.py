"""Ajustes de la app desde el editor (ventana «Ajustes»).

Lo mismo que el menú del icono (brillo, girar, pantalla, inicio con Windows, acceso
directo, importar tema, carpeta de temas), pero desde la ventana del editor. Quien lo
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

from .almacen import EditorError


class SettingsBackend(Protocol):
    def snapshot(self) -> dict: ...

    def update(
        self, *, brightness: int | None, flip: bool | None, autostart: bool | None
    ) -> None: ...

    def choose_display(self, choice_id: str | None, *, confirmed: bool) -> None:
        """``None``: volver a detectarla sola. Lanza :class:`EditorError` si no vale."""
        ...

    def create_shortcut(self) -> str:
        """Crea el acceso del escritorio; devuelve el texto para el usuario."""
        ...

    def open_themes_folder(self) -> None: ...

    def import_theme(self, zip_path: Path) -> str:
        """Importa el zip y devuelve el nombre del tema nuevo."""
        ...


def _optional(body: dict, key: str, kind: type) -> object:
    value = body.get(key)
    # bool es un int en Python: un «true» no puede colarse como brillo.
    if value is None or (type(value) is kind):
        return value
    raise EditorError(400, "Algún ajuste no es válido.")


def parse_update(body: object) -> dict:
    """``{brightness?, flip?, autostart?}`` → argumentos de :meth:`SettingsBackend.update`."""
    if not isinstance(body, dict) or not body or set(body) - {"brightness", "flip", "autostart"}:
        raise EditorError(400, "Algún ajuste no es válido.")
    return {
        "brightness": _optional(body, "brightness", int),
        "flip": _optional(body, "flip", bool),
        "autostart": _optional(body, "autostart", bool),
    }


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
    raise EditorError(400, "La pantalla elegida no es válida.")
