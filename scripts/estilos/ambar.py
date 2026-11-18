"""Estilo Retro Ámbar: monitor CRT antiguo de fósforo ámbar sobre negro, cifras grandes,
un leve resplandor y líneas de barrido suaves.

Las líneas de barrido van en una capa aparte (``barrido.png``) que el tema pone ENCIMA de
todo, para que crucen también las cifras como en un tubo de verdad; son tenues para no
ensuciar la lectura. Las líneas separadoras que se pintan aquí coinciden con las cajas de
``ambar/theme.json`` y ``ambar-vertical/theme.json``.
"""

from __future__ import annotations

from collections.abc import Callable

from fondos import VH, VW, H, W, _light
from PIL import Image, ImageChops, ImageDraw, ImageFilter

AMBER = (255, 176, 0)
BLACK = (14, 8, 2)  # negro cálido del tubo apagado
SCAN_EVERY = 3  # una línea oscura cada 3 filas
SCAN_ALPHA = 46  # ~18 % más oscuro en esa fila: se nota de cerca, no tapa las cifras


def _vignette(img: Image.Image, strength: int) -> Image.Image:
    """Oscurece los bordes como el cristal curvo de un tubo."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse((-w // 4, -h // 4, w + w // 4, h + h // 4), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) // 5))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    dark.putalpha(ImageChops.invert(mask).point(lambda v: v * strength // 255))
    img.alpha_composite(dark)
    return img


def _bezel(img: Image.Image, radius: int) -> None:
    """Esquinas redondeadas en negro: la pantalla parece el frontal de un tubo."""
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=0)
    img.paste((2, 1, 0, 255), (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle(
        (1, 1, w - 2, h - 2), radius=radius, outline=(*AMBER, 40), width=1
    )


def _dashed(img: Image.Image, y: int, x0: int, x1: int) -> None:
    """Separador punteado en ámbar apagado."""
    d = ImageDraw.Draw(img)
    for x in range(x0, x1, 8):
        d.line((x, y, min(x + 4, x1), y), fill=(*AMBER, 90), width=2)


def _screen(size: tuple[int, int], glows: list[tuple[int, int, int, int]]) -> Image.Image:
    img = Image.new("RGBA", size, (*BLACK, 255))
    w, h = size
    _light(img, (-w // 4, -h // 4, w + w // 4, h + h // 4), (60, 30, 0, 90), 60)
    for box in glows:  # halo cálido detrás de las cifras grandes (fósforo encendido)
        _light(img, box, (255, 130, 0, 46), 28)
    return img


def barrido(size: tuple[int, int]) -> Image.Image:
    """Capa de líneas de barrido: transparente salvo una fila oscura cada ``SCAN_EVERY``."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(0, h, SCAN_EVERY):
        d.line((0, y, w, y), fill=(0, 0, 0, SCAN_ALPHA))
    return layer


# --- horizontal (480x320) --------------------------------------------------------------


def ambar() -> Image.Image:
    img = _screen((W, H), [(60, 50, 420, 150), (20, 180, 460, 250)])
    for y in (40, 156, 272):
        _dashed(img, y, 24, 456)
    _vignette(img, 150)
    _bezel(img, 22)
    return img.convert("RGB")


def ambar_vertical() -> Image.Image:
    img = _screen((VW, VH), [(10, 56, 310, 156), (10, 180, 200, 400)])
    for y in (44, 158, 412):
        _dashed(img, y, 24, 296)
    _vignette(img, 150)
    _bezel(img, 22)
    return img.convert("RGB")


FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("ambar", "fondo.png", ambar),
    ("ambar", "barrido.png", lambda: barrido((W, H))),
    ("ambar-vertical", "fondo.png", ambar_vertical),
    ("ambar-vertical", "barrido.png", lambda: barrido((VW, VH))),
]
