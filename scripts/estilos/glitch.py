"""Estilo Glitch: lima eléctrica, magenta y cian sobre negro, esquinas cortadas en
diagonal, marcas de interfaz técnica y un toque de «glitch».

Estilo de género, pintado a mano aquí: nada sacado de un juego, película o marca concreta.
Las cajas de paneles, pestañas y barras son las mismas que usan los ``theme.json`` de
``glitch/`` y ``glitch-vertical/``: si se mueve una, hay que mover la otra.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable

from fondos import VH, VW, H, W, _gradient
from PIL import Image, ImageDraw, ImageFilter

RGB = tuple[int, int, int]
Box = tuple[int, int, int, int]

# Nombre y color cambiados el 2026-11-18: «Cyberpunk» es marca registrada en la UE y el
# amarillo #fae614 recordaba a la paleta de un juego concreto.
LIME = (198, 255, 0)  # #c6ff00, lima eléctrica
MAGENTA = (255, 45, 120)  # #ff2d78
CYAN = (0, 225, 240)  # #00e1f0
PANEL = (14, 13, 22)  # relleno liso de los paneles: los números cambian sobre él
CUT = 14  # largo del corte diagonal de las esquinas
TAB = (64, 22)  # pestaña con el nombre del panel (texto negro sobre el color)


def _chamfer(box: Box, cut: int = CUT) -> list[tuple[int, int]]:
    """Polígono de un rectángulo con dos esquinas cortadas (arriba-izquierda y abajo-derecha)."""
    x0, y0, x1, y1 = box
    return [(x0 + cut, y0), (x1, y0), (x1, y1 - cut), (x1 - cut, y1), (x0, y1), (x0, y0 + cut)]


def _halo(img: Image.Image, polys: Iterable[tuple[list[tuple[int, int]], RGB]], blur: int) -> None:
    """Resplandor suave detrás de los contornos (más contenido que el del Neón)."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for poly, color in polys:
        d.polygon(poly, outline=(*color, 150), width=4)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def _panel(img: Image.Image, box: Box, color: RGB, tab: bool = True) -> None:
    """Panel de esquinas cortadas con borde de color, pestaña rellena y marcas técnicas."""
    x0, y0, x1, y1 = box
    poly = _chamfer(box)
    _halo(img, [(poly, color)], 5)
    d = ImageDraw.Draw(img)
    d.polygon(poly, fill=(*PANEL, 255), outline=(*color, 255), width=2)
    if tab:
        # Pestaña: sigue el corte diagonal del panel y acaba en bisel.
        tw, th = TAB
        d.polygon(
            [(x0 + CUT, y0), (x0 + tw + 10, y0), (x0 + tw, y0 + th), (x0, y0 + th), (x0, y0 + CUT)],
            fill=(*color, 255),
        )
    # Marcas de interfaz: tres rayitas junto a la esquina cortada de abajo y una muesca.
    for k in range(3):
        x = x1 - CUT - 10 - k * 7
        d.line((x, y1 - 5, x + 4, y1 - 9), fill=(*color, 255), width=2)
    d.rectangle((x1 - 3, y0 + 8, x1 - 1, y0 + 22), fill=(*color, 255))


def _ticks(img: Image.Image, x: int, y: int, width: int, color: RGB) -> None:
    """Escala de 0 a 100 % debajo de una barra (marcas cada 10 %, largas cada 50 %)."""
    d = ImageDraw.Draw(img)
    for i in range(11):
        xx = round(x + (width - 1) * i / 10)
        d.line((xx, y, xx, y + (6 if i % 5 == 0 else 3)), fill=(*color, 170))


def _glitch(img: Image.Image, box: Box, rng: random.Random, slices: int) -> None:
    """Desplaza unas franjas horizontales de ``box`` con fantasmas magenta y cian (glitch).

    Solo cerca del borde de arriba y del de abajo: por el centro pasa el texto del panel y
    una franja de color detrás de las cifras las hace peor de leer."""
    x0, y0, x1, y1 = box
    for k in range(slices):
        y = rng.randrange(y0, y0 + 8) if k % 2 == 0 else rng.randrange(y1 - 10, y1 - 4)
        h = rng.randrange(2, 5)
        dx = rng.choice((-1, 1)) * rng.randrange(4, 12)
        band = img.crop((x0, y, x1, y + h))
        img.paste(band, (x0 + dx, y))
        tint = Image.new("RGBA", band.size, (*rng.choice((MAGENTA, CYAN)), 90))
        img.alpha_composite(tint, (x0 + dx // 2, y))


def _artifacts(img: Image.Image, area: Box, rng: random.Random, count: int) -> None:
    """Bloques sueltos de color, como píxeles corruptos."""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = area
    for _ in range(count):
        w, h = rng.randrange(6, 34), rng.randrange(2, 5)
        x, y = rng.randrange(x0, x1 - w), rng.randrange(y0, y1 - h)
        d.rectangle((x, y, x + w, y + h), fill=(*rng.choice((MAGENTA, CYAN, LIME)), 200))


def _base(size: tuple[int, int]) -> Image.Image:
    """Negro con un punto de rejilla técnica cada 16 px y una diagonal rayada en una esquina."""
    w, h = size
    img = _gradient(size, [(0.0, (12, 10, 20)), (1.0, (4, 4, 8))]).convert("RGBA")
    d = ImageDraw.Draw(img)
    for y in range(8, h, 16):
        for x in range(8, w, 16):
            d.point((x, y), fill=(46, 44, 64, 255))
    return img


def _hatch(img: Image.Image, box: Box, color: RGB) -> None:
    """Franja rayada en diagonal (cinta de advertencia, sin texto)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(-h, w, 10):
        d.polygon([(x, h), (x + 5, h), (x + 5 + h, 0), (x + h, 0)], fill=(*color, 255))
    img.alpha_composite(layer, (x0, y0))


def segments(size: tuple[int, int]) -> Image.Image:
    """Capa que va ENCIMA de una barra: la corta en bloques inclinados del color del panel."""
    w, h = size
    seg = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(seg)
    for x in range(8, w + h, 11):
        d.polygon([(x, h), (x + 3, h), (x + 3 + h // 2, 0), (x + h // 2, 0)], fill=(*PANEL, 255))
    return seg


# --- horizontal (480x320) --------------------------------------------------------------

H_DATE = (262, 12, 468, 80)
H_PANELS = [
    ((10, 92, 236, 214), LIME),  # CPU
    ((244, 92, 470, 214), MAGENTA),  # GPU
    ((10, 224, 236, 310), CYAN),  # RAM
    ((244, 224, 470, 310), LIME),  # RED
]
H_BAR = (198, 10)  # barras de CPU, GPU y RAM


def glitch() -> Image.Image:
    rng = random.Random(2077)  # noqa: S311 (decoración, no criptografía)
    img = _base((W, H))
    # Raya de advertencia bajo el reloj y marcas de cabecera.
    _hatch(img, (16, 80, 150, 85), (90, 82, 10))
    d = ImageDraw.Draw(img)
    d.line((156, 82, 248, 82), fill=(*LIME, 255), width=2)
    d.rectangle((250, 79, 254, 85), fill=(*LIME, 255))
    # Panel de la fecha: solo contorno cian, cortado por el glitch.
    poly = _chamfer(H_DATE, 12)
    _halo(img, [(poly, CYAN)], 5)
    ImageDraw.Draw(img).polygon(poly, fill=(*PANEL, 255), outline=(*CYAN, 255), width=2)
    _glitch(img, (H_DATE[0] - 6, H_DATE[1] - 4, H_DATE[2] + 6, H_DATE[3] + 4), rng, 7)
    _artifacts(img, (206, 8, 256, 72), rng, 6)  # a la derecha del reloj
    for box, color in H_PANELS:
        _panel(img, box, color)
    for box, color in H_PANELS[:2]:  # bajo las barras de CPU y GPU (la de RAM no tiene sitio)
        _ticks(img, box[0] + 14, box[1] + 104, H_BAR[0], color)
    return img.convert("RGB")


def glitch_segmentos() -> Image.Image:
    return segments(H_BAR)


# --- vertical (320x480) ----------------------------------------------------------------

V_DATE = (12, 92, 308, 120)
V_PANELS = [
    ((10, 132, 310, 222), LIME),  # CPU
    ((10, 232, 310, 322), MAGENTA),  # GPU
    ((10, 332, 310, 412), CYAN),  # RAM
    ((10, 422, 310, 470), LIME),  # RED
]
V_BAR = (130, 12)
V_BAR_Y = {132: 50, 232: 50, 332: 44}  # altura de la barra dentro de cada panel


def glitch_vertical() -> Image.Image:
    rng = random.Random(2078)  # noqa: S311 (decoración)
    img = _base((VW, VH))
    _hatch(img, (12, 6, 120, 10), (90, 82, 10))
    d = ImageDraw.Draw(img)
    d.line((126, 8, 300, 8), fill=(*LIME, 255), width=2)
    d.rectangle((302, 5, 308, 11), fill=(*LIME, 255))
    poly = _chamfer(V_DATE, 10)
    _halo(img, [(poly, CYAN)], 5)
    ImageDraw.Draw(img).polygon(poly, fill=(*PANEL, 255), outline=(*CYAN, 255), width=2)
    _glitch(img, (V_DATE[0] - 4, V_DATE[1] - 4, V_DATE[2] + 4, V_DATE[3] + 4), rng, 6)
    _artifacts(img, (14, 13, 306, 21), rng, 5)  # encima del reloj
    for box, color in V_PANELS:
        _panel(img, box, color, tab=box[3] - box[1] > 60)
    for box, color in V_PANELS[:3]:
        _ticks(img, box[0] + 156, box[1] + V_BAR_Y[box[1]] + 14, V_BAR[0], color)
    return img.convert("RGB")


def glitch_vertical_segmentos() -> Image.Image:
    return segments(V_BAR)


FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("glitch", "fondo.png", glitch),
    ("glitch", "segmentos.png", glitch_segmentos),
    ("glitch-vertical", "fondo.png", glitch_vertical),
    ("glitch-vertical", "segmentos.png", glitch_vertical_segmentos),
]
