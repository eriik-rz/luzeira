"""La marca de Luzeira: un lucero (estrella de cuatro puntas) sobre azul noche.

Una sola función la dibuja para todo: el icono de la bandeja (``bandeja.make_icon``, con
el color del estado) y el ``.ico`` de Windows (``scripts/icono.py``). El logo del editor
es el mismo dibujo en SVG (``editor/src/components/icons.tsx``, ``BrandMark``).

Se pinta a 4 veces el tamaño y se reduce: así las puntas finas salen nítidas también a
16 píxeles.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

STAR = (255, 176, 46)  # ámbar cálido, el del lucero al amanecer
NIGHT = (18, 24, 44)  # fondo azul noche
_SUPER = 4
_SHARPNESS = 2.6  # >1: lados curvados hacia dentro; cuanto más alto, puntas más finas


def _star_points(
    cx: float, cy: float, radius: float, steps: int = 240
) -> list[tuple[float, float]]:
    """Contorno de la estrella: un «astroide» suave (|x|^(2/p) + |y|^(2/p) = r^(2/p))."""
    points = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = math.copysign(abs(c) ** _SHARPNESS, c)
        y = math.copysign(abs(s) ** _SHARPNESS, s)
        points.append((cx + radius * x, cy - radius * y))
    return points


def draw_mark(size: int, star: tuple[int, int, int] = STAR) -> Image.Image:
    """El icono a ``size`` x ``size`` píxeles, con fondo transparente fuera del cuadrado."""
    big = size * _SUPER
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, big // 32)
    d.rounded_rectangle((pad, pad, big - 1 - pad, big - 1 - pad), radius=big * 7 // 32, fill=NIGHT)

    c = big / 2
    # Halo: un círculo del color de la estrella, muy difuminado y tenue.
    halo = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse(
        (c - big * 0.22, c - big * 0.22, c + big * 0.22, c + big * 0.22), fill=(*star, 110)
    )
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(big * 0.08)))
    # Recorta el halo a la forma del fondo (no se sale del cuadrado redondeado).
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (pad, pad, big - 1 - pad, big - 1 - pad), radius=big * 7 // 32, fill=255
    )
    img.putalpha(Image.composite(img.getchannel("A"), mask, mask))

    d = ImageDraw.Draw(img)
    d.polygon(_star_points(c, c, big * 0.36), fill=(*star, 255))
    # Un brillo pequeño arriba a la derecha (otra estrella, más lejana).
    d.polygon(_star_points(big * 0.75, big * 0.25, big * 0.085), fill=(*star, 230))
    return img.resize((size, size), Image.Resampling.LANCZOS)
