"""Estilo Carbono: telemetría de coche de carreras sobre fibra de carbono.

La fibra es un tejido sarga 2x2 pintado por código: celdas de 6 px que alternan hilos
horizontales y verticales en diagonal, cada hilo con un brillo en el centro (sección
redonda). Es oscura a propósito (nunca pasa de gris 45): el blanco de las cifras
contrasta igual encima. Los paneles de datos son la misma fibra «ahumada» (al 40 %), como
un cristal tintado, para que la barra y las cifras vayan sobre un fondo casi liso. Al
repetirse cada 24 px, el PNG la comprime muy bien.

Las cajas de paneles, barras y marcas son las mismas que usan los widgets del
``theme.json``: si se mueve una, hay que mover la otra.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from fondos import VH, VW, H, W
from PIL import Image, ImageDraw

RED = (225, 6, 0)
SMOKE = 0.4  # brillo de la fibra bajo los paneles
EDGE = (44, 44, 50)
CELL = 6  # lado de cada celda del tejido

# Horizontal: filas de telemetría (y de inicio, 54 px de alto) y celdas del pie.
CARBONO_ROWS = [70, 132, 194]
# Barra partida en dos tramos (x, ancho, desde %, hasta %): blanco hasta el 80 % y zona
# roja del 80 al 100, como el cuentarrevoluciones. Cada tramo es una barra del tema.
CARBONO_SEGMENTS = [(216, 190, 0, 80), (410, 46, 80, 100)]
CARBONO_FOOT = [(12, 260, 155, 307), (168, 260, 311, 307), (324, 260, 467, 307)]
# Vertical.
CARBONO_V_ROWS = [116, 206, 296]
CARBONO_V_FOOT = [(12, y, 307, y + 39) for y in (390, 434)]  # GPU (°C y W) · red (↓ ↑)


def _weave_tile() -> Image.Image:
    """Una baldosa de 4x4 celdas de sarga 2x2 (24x24 px), que se repite sin costuras."""
    size = CELL * 4
    tile = Image.new("RGB", (size, size))
    px = tile.load()
    for j in range(4):
        for i in range(4):
            horizontal = (i + j) % 4 < 2  # escalera en diagonal de dos celdas
            for v in range(CELL):
                for u in range(CELL):
                    across = v if horizontal else u  # posición a lo ancho del hilo
                    along = u if horizontal else v  # posición a lo largo
                    bulge = math.sin(math.pi * (across + 0.5) / CELL)  # hilo redondo
                    sheen = 0.85 + 0.15 * (along / (CELL - 1))  # reflejo a lo largo
                    level = round((12 + 26 * bulge) * sheen + (4 if horizontal else 0))
                    px[i * CELL + u, j * CELL + v] = (level, level, level + 2)
    return tile


def _carbon(size: tuple[int, int]) -> Image.Image:
    tile = _weave_tile()
    img = Image.new("RGB", size)
    for y in range(0, size[1], tile.height):
        for x in range(0, size[0], tile.width):
            img.paste(tile, (x, y))
    return img


def _stripes(d: ImageDraw.ImageDraw, x: int, y0: int, y1: int, count: int = 3) -> None:
    """Franjas rojas inclinadas, como las de un capó."""
    slant = (y1 - y0) // 2
    for k in range(count):
        x0 = x + k * 14
        d.polygon([(x0 + slant, y0), (x0 + slant + 8, y0), (x0 + 8, y1), (x0, y1)], fill=RED)


def _bar_x(percent: float) -> int:
    """Columna de la barra horizontal en la que cae ``percent`` (para las marcas)."""
    for x, width, lo, hi in CARBONO_SEGMENTS:
        if lo <= percent <= hi:
            return min(x + round(width * (percent - lo) / (hi - lo)), x + width - 2)
    raise ValueError(percent)


def _panel(img: Image.Image, box: tuple[int, int, int, int], tab: bool = True) -> None:
    """Panel de cristal ahumado: la fibra de debajo, oscurecida, con canto gris."""
    x0, y0, x1, y1 = box
    region = (x0, y0, x1 + 1, y1 + 1)
    img.paste(img.crop(region).point(lambda v: round(v * SMOKE)), region[:2])
    d = ImageDraw.Draw(img)
    d.rectangle(box, outline=EDGE)
    if tab:
        d.rectangle((box[0], box[1], box[0] + 5, box[3]), fill=RED)


def carbono() -> Image.Image:
    img = _carbon((W, H))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 56, W - 1, 58), fill=RED)
    _stripes(d, 172, 12, 44)
    for y in CARBONO_ROWS:
        _panel(img, (12, y, W - 13, y + 53))
        for k in range(11):  # escala bajo la barra: cada 10 %, rojas desde el 80
            x = _bar_x(k * 10)
            tall = k % 5 == 0
            d.rectangle((x, y + 38, x + 1, y + (46 if tall else 42)), fill=RED if k >= 8 else EDGE)
    for box in CARBONO_FOOT:
        _panel(img, box, tab=False)
        d.rectangle((box[0], box[3] - 2, box[2], box[3]), fill=RED)
    return img


def carbono_vertical() -> Image.Image:
    img = _carbon((VW, VH))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 100, VW - 1, 102), fill=RED)
    for y in CARBONO_V_ROWS:
        _panel(img, (12, y, VW - 13, y + 81))
    for box in CARBONO_V_FOOT:
        _panel(img, box, tab=False)
        d.rectangle((box[0], box[3] - 2, box[2], box[3]), fill=RED)
    return img


FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("carbono", "fondo.png", carbono),
    ("carbono-vertical", "fondo.png", carbono_vertical),
]
