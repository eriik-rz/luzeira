"""Fondos de las parejas que faltaban: Hielo (horizontal), Synthwave Vertical, Terminal
Vertical y Neón Vertical. Cada uno repite la receta de su pareja de ``fondos.py`` adaptada
a la otra orientación; las cajas que se pintan aquí son las mismas que usa su
``theme.json`` (si se mueve una, hay que mover la otra).

Se carga desde ``fondos.py``. Suelto: ``PYTHONPATH=scripts`` para que funcione el import.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from fondos import VH, VW, H, W, _glow, _gradient, _light, _vertical_gradient
from PIL import Image, ImageDraw

# --- Hielo (horizontal) ----------------------------------------------------------------

# Tres paneles abajo (CPU, GPU, RAM) y el de la red arriba a la derecha, junto al reloj.
HIELO_H_PANELS = [(16, 124, 160, 298), (168, 124, 312, 298), (320, 124, 464, 298)]
HIELO_H_NET = (276, 14, 464, 108)


def hielo() -> Image.Image:
    img = _gradient((W, H), [(0.0, (8, 24, 48)), (0.45, (5, 16, 34)), (1.0, (3, 10, 22))])
    img = img.convert("RGBA")
    _light(img, (-80, -90, 320, 140), (150, 220, 255, 55), 40)
    # Esquirlas tenues en las esquinas de arriba, como en la vertical.
    shards = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shards)
    for poly in (
        [(0, 0), (90, 0), (20, 110)],
        [(60, 0), (150, 0), (104, 64)],
        [(180, 0), (262, 0), (236, 74)],
        [(400, 0), (480, 0), (480, 90)],
        [(420, 110), (480, 116), (468, 0)],
    ):
        sd.polygon(poly, fill=(190, 235, 255, 18), outline=(200, 240, 255, 40))
    img.alpha_composite(shards)
    _glow(
        img,
        [*HIELO_H_PANELS, HIELO_H_NET],
        (120, 200, 255),
        1,
        5,
        radius=12,
        fill=(8, 24, 46, 255),
    )
    return img.convert("RGB")


# --- Synthwave Vertical ----------------------------------------------------------------

SYNTH_V_HORIZON = 224
SYNTH_V_PANELS = [(16, y, 304, y + 54) for y in (234, 294, 354, 414)]


def synthwave_vertical() -> Image.Image:
    hz = SYNTH_V_HORIZON
    img = _gradient(
        (VW, VH),
        [
            (0.0, (12, 3, 34)),
            (0.22, (44, 8, 82)),
            (hz / VH, (178, 44, 118)),
            (hz / VH + 0.003, (22, 3, 38)),
            (1.0, (8, 0, 16)),
        ],
    ).convert("RGBA")
    # Estrellas en lo alto del cielo, alrededor del reloj.
    rng = random.Random(1985)  # noqa: S311 (decoración)
    stars = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = rng.randrange(VW), rng.randrange(0, 130)
        v = rng.randrange(90, 230)
        stars.point((x, y), fill=(v, v, min(255, v + 25), 255))
    # Sol con franjas, centrado y medio hundido en el horizonte.
    cx, cy, r = 160, 172, 54
    _light(img, (cx - r - 26, cy - r - 26, cx + r + 26, cy + r + 26), (255, 70, 160, 120), 22)
    sun = _gradient(
        (2 * r, 2 * r), [(0.0, (255, 226, 110)), (0.55, (255, 120, 90)), (1.0, (255, 40, 150))]
    ).convert("RGBA")
    mask = Image.new("L", (2 * r, 2 * r), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
    y, gap = r + 4, 2
    while y < 2 * r:
        md.rectangle((0, y, 2 * r, y + gap - 1), fill=0)
        y += gap + 6
        gap += 1
    sun.putalpha(mask)
    img.alpha_composite(sun, (cx - r, cy - r))
    # Suelo: rejilla en perspectiva hacia el punto de fuga.
    floor = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    fd = ImageDraw.Draw(floor)
    vx = VW // 2
    for k in range(-10, 11):
        fd.line((vx, hz, vx + k * 60, VH), fill=(255, 60, 200, 150), width=1)
    n = 12
    for i in range(1, n + 1):
        yy = hz + round((VH - hz) * (i / n) ** 2)
        fd.line((0, yy, VW, yy), fill=(255, 60, 200, 150), width=1)
    img.alpha_composite(floor)
    _light(img, (-40, hz - 8, VW + 40, hz + 8), (255, 90, 200, 160), 6)
    ImageDraw.Draw(img).line((0, hz, VW, hz), fill=(255, 150, 220, 255), width=2)
    # Paneles opacos: CPU, GPU, RAM (los colores de la horizontal) y la red en lila.
    colors = [(255, 150, 70), (255, 60, 200), (0, 220, 255), (200, 140, 255)]
    for box, color in zip(SYNTH_V_PANELS, colors, strict=True):
        _glow(img, [box], color, 2, 5, radius=8, fill=(20, 4, 38, 255))
    return img.convert("RGB")


# --- Terminal Vertical -----------------------------------------------------------------

TERM_V_BAR = (296, 12)  # ancho y alto de las barras (y de la capa de segmentos)


def terminal_vertical() -> Image.Image:
    img = Image.new("RGBA", (VW, VH), (2, 9, 4, 255))
    _light(img, (20, 40, 300, 440), (20, 90, 40, 70), 70)
    d = ImageDraw.Draw(img)
    for y in range(0, VH, 3):  # líneas de barrido del monitor de fósforo
        d.line((0, y, VW, y), fill=(0, 0, 0, 70))
    d.rectangle((0, 0, VW, 29), fill=(6, 40, 18, 255))
    d.line((0, 30, VW, 30), fill=(51, 255, 102, 110))
    d.line((0, 403, VW, 403), fill=(51, 255, 102, 50))
    return img.convert("RGB")


def terminal_vertical_segmentos() -> Image.Image:
    """Capa que va ENCIMA de las barras y las corta en bloques (la misma idea que
    ``terminal_segmentos``, al ancho de las barras de la vertical)."""
    w, h = TERM_V_BAR
    seg = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(seg)
    for x in range(6, w, 8):
        d.rectangle((x, 0, x + 1, h - 1), fill=(2, 9, 4, 255))
    return seg


# --- Neón Vertical ---------------------------------------------------------------------

NEON_V_CYAN = [(10, 92, 310, 222), (10, 230, 310, 360)]
NEON_V_PINK = (10, 368, 310, 470)


def neon_vertical() -> Image.Image:
    img = _vertical_gradient((10, 6, 24), (4, 2, 10), (VW, VH)).convert("RGBA")
    grid = ImageDraw.Draw(img)
    for x in range(0, VW, 24):
        grid.line((x, 0, x, VH), fill=(40, 20, 70, 255))
    for y in range(0, VH, 24):
        grid.line((0, y, VW, y), fill=(40, 20, 70, 255))
    _glow(img, NEON_V_CYAN, (0, 230, 255), 2, 6)
    _glow(img, [NEON_V_PINK], (255, 40, 200), 2, 6)
    return img.convert("RGB")


# --- salida ----------------------------------------------------------------------------

FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("hielo", "fondo.png", hielo),
    ("synthwave-vertical", "fondo.png", synthwave_vertical),
    ("terminal-vertical", "fondo.png", terminal_vertical),
    ("terminal-vertical", "segmentos.png", terminal_vertical_segmentos),
    ("neon-vertical", "fondo.png", neon_vertical),
]
