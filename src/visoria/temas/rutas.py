"""Comprobaciones de rutas compartidas por el modelo y el cargador de temas."""

from __future__ import annotations

# Nombres de dispositivo de Windows. Valen con cualquier extensión («CON.png» abre la
# consola, «COM3.gif» el puerto serie, que puede ser justo el de la pantalla) y también
# como carpeta («aux/x.png»). ``Path.resolve()`` + ``is_relative_to`` NO los detecta:
# dice que «CON.png» está dentro de la carpeta (medido en la fase 3).
RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{dev}{n}" for dev in ("COM", "LPT") for n in [*"0123456789", "¹", "²", "³"]}
)


def is_reserved_windows_name(part: str) -> bool:
    """¿Es ``part`` (un componente de ruta) un nombre de dispositivo de Windows?

    Windows mira solo lo que hay antes del primer punto e ignora espacios y puntos
    finales, así que «con.png», «Nul.tar.gz» y «aux » cuentan. ``Path.is_reserved``
    no sirve: en 3.12 solo mira el último componente y está obsoleto desde 3.13.
    """
    base = part.split(".", 1)[0].rstrip(" .").upper()
    return base in RESERVED


def has_reserved_component(path: str) -> bool:
    return any(is_reserved_windows_name(part) for part in path.replace("\\", "/").split("/"))
