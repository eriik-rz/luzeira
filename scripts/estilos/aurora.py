"""Estilo Aurora: cortinas suaves de verde, turquesa y violeta, como una aurora boreal,
sobre azul noche, con paneles translúcidos de cristal esmerilado. Tranquilo y elegante.

Los paneles se pintan aquí (cristal = el fondo de debajo difuminado y oscurecido): las
cifras cambian sobre ellos y siguen leyéndose bien. Sus cajas son las mismas que usan
``aurora/theme.json`` y ``aurora-vertical/theme.json``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from fondos import VH, VW, H, W, _gradient
from PIL import Image, ImageDraw, ImageFilter

RGB = tuple[int, int, int]
Box = tuple[int, int, int, int]

NIGHT_TOP, NIGHT_BOTTOM = (5, 10, 30), (8, 16, 40)
GREEN, TEAL, VIOLET = (60, 255, 160), (40, 210, 230), (160, 100, 255)


def _mix(a: RGB, b: RGB, k: float) -> RGB:
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b, strict=True))


def _palette(t: float) -> RGB:
    """Color a lo largo de la cortina: verde → turquesa → violeta."""
    return _mix(GREEN, TEAL, t * 2) if t < 0.5 else _mix(TEAL, VIOLET, (t - 0.5) * 2)


def _curtain(
    img: Image.Image,
    base: Callable[[float], float],
    height: Callable[[float], float],
    reverse: bool,
    strength: int,
    rng: random.Random,
) -> None:
    """Una cortina de aurora: rayos verticales que salen de una curva ``base(x)`` hacia
    arriba, brillantes abajo y desvaneciéndose arriba. ``reverse`` invierte el degradado
    de color a lo ancho."""
    w, h = img.size
    fade = Image.linear_gradient("L").resize((1, 256))  # 0 arriba → 255 abajo
    mask = Image.new("L", (w, h), 0)
    phase = rng.uniform(0, math.tau)
    for x in range(w):
        t = x / (w - 1)
        y0 = base(t)
        length = max(4, round(height(t)))
        # Rayos: la intensidad varía columna a columna (seno lento + un poco de ruido fijo).
        ray = 0.62 + 0.25 * math.sin(t * 38 + phase) + 0.13 * rng.random()
        col = fade.resize((1, length)).point(lambda v, r=ray: round(v * r * strength / 255))
        mask.paste(col, (x, round(y0) - length), col)
        # Borde inferior brillante y corto, donde la cortina «toca».
        ImageDraw.Draw(mask).line((x, round(y0), x, round(y0) + 3), fill=round(strength * ray))
    color = Image.new("RGB", (w, 1))
    for x in range(w):
        t = x / (w - 1)
        color.putpixel((x, 0), _palette(1 - t if reverse else t))
    color = color.resize((w, h)).convert("RGBA")
    color.putalpha(mask.filter(ImageFilter.GaussianBlur(1.5)))
    glow = color.filter(ImageFilter.GaussianBlur(16))
    img.alpha_composite(glow)
    img.alpha_composite(glow)  # dos veces: el halo se nota más que los rayos
    img.alpha_composite(color)


def _stars(img: Image.Image, count: int, rng: random.Random) -> None:
    d = ImageDraw.Draw(img)
    w, h = img.size
    for _ in range(count):
        x, y = rng.randrange(w), rng.randrange(h)
        v = rng.randrange(70, 190)
        d.point((x, y), fill=(v, v, min(255, v + 30), 255))


def _glass(img: Image.Image, box: Box, radius: int = 16) -> None:
    """Panel de cristal esmerilado: el fondo de debajo difuminado, oscurecido hacia azul
    noche y con un borde fino claro. Opaco en la práctica (las cifras cambian sobre él)."""
    x0, y0, x1, y1 = box
    region = img.crop(box).filter(ImageFilter.GaussianBlur(10))
    tint = Image.new("RGBA", region.size, (10, 18, 44, 255))
    region = Image.blend(region, tint, 0.62)
    # Un velo blanco muy leve arriba: el cristal coge la luz de la aurora.
    sheen = _gradient(region.size, [(0.0, (255, 255, 255)), (1.0, (0, 0, 0))]).convert("L")
    white = Image.new("RGBA", region.size, (255, 255, 255, 255))
    region.paste(white, (0, 0), sheen.point(lambda v: v * 18 // 255))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius, fill=255)
    img.paste(region, (x0, y0), mask)
    ImageDraw.Draw(img).rounded_rectangle(box, radius, outline=(190, 230, 255, 70), width=1)


def _rule(img: Image.Image, y: int, x0: int, x1: int) -> None:
    ImageDraw.Draw(img).line((x0, y, x1, y), fill=(190, 230, 255, 40), width=1)


def _sky(size: tuple[int, int]) -> Image.Image:
    return _gradient(size, [(0.0, NIGHT_TOP), (1.0, NIGHT_BOTTOM)]).convert("RGBA")


# --- horizontal (480x320) --------------------------------------------------------------

H_PANEL = (256, 14, 466, 306)  # CPU, GPU y RAM en filas de 96 px
H_NET = (16, 234, 240, 306)


def aurora() -> Image.Image:
    rng = random.Random(6021)  # noqa: S311 (decoración, no criptografía)
    img = _sky((W, H))
    _stars(img, 60, rng)
    # Dos cortinas cruzadas: una baja de izquierda a derecha y otra más alta y violeta.
    _curtain(
        img,
        lambda t: 226 - 40 * t + 12 * math.sin(t * 6.0),
        lambda t: 96 + 26 * math.sin(t * 3.1 + 0.6),
        reverse=False,
        strength=235,
        rng=rng,
    )
    _curtain(
        img,
        lambda t: 96 + 22 * math.sin(t * 4.0 + 1.3) - 30 * t,
        lambda t: 80 + 24 * math.cos(t * 2.3),
        reverse=True,
        strength=150,
        rng=rng,
    )
    _glass(img, H_PANEL)
    for y in (110, 206):
        _rule(img, y, H_PANEL[0] + 16, H_PANEL[2] - 16)
    _glass(img, H_NET, 14)
    return img.convert("RGB")


# --- vertical (320x480) ----------------------------------------------------------------

V_PANEL = (16, 170, 304, 404)  # filas de 78 px
V_NET = (16, 414, 304, 466)


def aurora_vertical() -> Image.Image:
    rng = random.Random(6022)  # noqa: S311 (decoración)
    img = _sky((VW, VH))
    _stars(img, 50, rng)
    _curtain(
        img,
        lambda t: 186 - 30 * t + 14 * math.sin(t * 4.4),
        lambda t: 120 + 30 * math.sin(t * 2.7 + 0.4),
        reverse=False,
        strength=230,
        rng=rng,
    )
    _curtain(
        img,
        lambda t: 100 + 20 * math.sin(t * 3.6 + 1.1),
        lambda t: 76 + 20 * math.cos(t * 2.1),
        reverse=True,
        strength=150,
        rng=rng,
    )
    _glass(img, V_PANEL)
    for y in (248, 326):
        _rule(img, y, V_PANEL[0] + 16, V_PANEL[2] - 16)
    _glass(img, V_NET, 14)
    return img.convert("RGB")


FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("aurora", "fondo.png", aurora),
    ("aurora-vertical", "fondo.png", aurora_vertical),
]
