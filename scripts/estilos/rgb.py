"""Estilo Gaming RGB: fondo casi negro con marcos de luz arcoíris, como los periféricos.

El arcoíris de los marcos es un degradado cónico (el tono gira alrededor del centro de la
pantalla), así el color recorre el borde de punta a punta igual que una tira LED. Las
barras de colores no salen de aquí: son bloques de barra del ``theme.json``, cada uno con
su tramo del sensor y su color. Las cajas de los paneles son las mismas que usan los
widgets: si se mueve una, hay que mover la otra.
"""

from __future__ import annotations

import colorsys
import math
from collections.abc import Callable

from fondos import VH, VW, H, W
from PIL import Image, ImageChops, ImageDraw, ImageFilter

BG = (9, 9, 14)
PANEL = (17, 17, 26)

# Horizontal: tres paneles CPU · GPU · RAM y una franja abajo.
RGB_PANELS = [(18 + i * 152, 88, 158 + i * 152, 232) for i in range(3)]
RGB_STRIP = (18, 244, 461, 302)
# Vertical: tres paneles apilados y uno abajo.
RGB_V_PANELS = [(16, y, 303, y + 80) for y in (122, 210, 298)]
RGB_V_STRIP = (16, 390, 303, 462)


def hue_color(t: float, value: float = 1.0) -> tuple[int, int, int]:
    """Color del arcoíris para ``t`` en 0..1 (0 rojo, 1/3 verde, 2/3 azul)."""
    r, g, b = colorsys.hsv_to_rgb(t % 1.0, 0.85, value)
    return round(r * 255), round(g * 255), round(b * 255)


def _conic(size: tuple[int, int]) -> Image.Image:
    """Arcoíris que gira alrededor del centro: cada píxel toma el tono de su ángulo."""
    w, h = size
    cx, cy = (w - 1) / 2, (h - 1) / 2
    # Se calcula una vez por cada uno de 720 ángulos y se reutiliza: mucho más rápido.
    palette = [hue_color(i / 720) for i in range(720)]
    data = []
    for y in range(h):
        for x in range(w):
            a = (math.atan2(y - cy, x - cx) / (2 * math.pi)) % 1.0
            data.append(palette[int(a * 720) % 720])
    img = Image.new("RGB", size)
    img.putdata(data)
    return img


def _rainbow_frames(
    size: tuple[int, int], frame: tuple[int, int, int, int], panels: list, radius: int
) -> Image.Image:
    """Fondo oscuro + marco exterior y paneles con borde arcoíris y halo."""
    img = Image.new("RGB", size, BG)
    rainbow = _conic(size)
    # Rellenos de los paneles (los números cambian sobre color liso).
    d = ImageDraw.Draw(img)
    for box in panels:
        d.rounded_rectangle(box, radius=10, fill=PANEL)
    # Máscara de las líneas: marco exterior más grueso, paneles finos.
    lines = Image.new("L", size, 0)
    ld = ImageDraw.Draw(lines)
    ld.rounded_rectangle(frame, radius=radius, outline=255, width=3)
    for box in panels:
        ld.rounded_rectangle(box, radius=10, outline=255, width=2)
    # Halo: las líneas difuminadas, a media intensidad, solo fuera de los paneles.
    halo = lines.filter(ImageFilter.GaussianBlur(4)).point(lambda v: min(255, v * 2) * 6 // 10)
    inside = Image.new("L", size, 0)
    idr = ImageDraw.Draw(inside)
    for box in panels:
        idr.rounded_rectangle(box, radius=10, fill=255)
    halo = ImageChops.subtract(halo, inside)
    img.paste(rainbow, mask=halo)
    img.paste(rainbow, mask=lines)
    return img


def rgb() -> Image.Image:
    return _rainbow_frames((W, H), (3, 3, W - 4, H - 4), [*RGB_PANELS, RGB_STRIP], 16)


def rgb_vertical() -> Image.Image:
    return _rainbow_frames((VW, VH), (3, 3, VW - 4, VH - 4), [*RGB_V_PANELS, RGB_V_STRIP], 16)


FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("rgb", "fondo.png", rgb),
    ("rgb-vertical", "fondo.png", rgb_vertical),
]
