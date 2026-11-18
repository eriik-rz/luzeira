"""Genera los fondos de los temas incluidos (para que sean reproducibles y sin derechos de
terceros: los pinta este script). Uso:  uv run python scripts/fondos.py

Cada fondo va con el ``theme.json`` de su carpeta: las coordenadas de paneles, tarjetas y
escalas que se pintan aquí son las mismas que usan los widgets del tema. Si se mueve una,
hay que mover la otra. Nada es aleatorio de verdad (semillas fijas): dos ejecuciones dan
los mismos ficheros.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

TEMAS = Path(__file__).resolve().parent.parent / "src" / "visoria" / "recursos" / "temas"
W, H = 480, 320  # horizontal
VW, VH = 320, 480  # vertical

RGB = tuple[int, int, int]
Box = tuple[int, int, int, int]


# --- utilidades ------------------------------------------------------------------------


def _gradient(size: tuple[int, int], stops: list[tuple[float, RGB]]) -> Image.Image:
    """Degradado vertical con varias paradas ``(posición 0..1, color)``."""
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        for (t0, c0), (t1, c1) in pairwise(stops):
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0) if t1 > t0 else 0
                color = tuple(round(a + (b - a) * k) for a, b in zip(c0, c1, strict=True))
                draw.line((0, y, w, y), fill=color)
                break
    return img


def _vertical_gradient(top: RGB, bottom: RGB, size: tuple[int, int] = (W, H)) -> Image.Image:
    return _gradient(size, [(0.0, top), (1.0, bottom)])


def _glow(
    base: Image.Image,
    shapes: Iterable[Box],
    color: RGB,
    width: int,
    blur: int,
    radius: int = 10,
    fill: tuple[int, int, int, int] | None = None,
) -> None:
    """Dibuja ``shapes`` (rectángulos redondeados) con un halo difuminado de neón."""
    shapes = list(shapes)
    halo = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(halo)
    for box in shapes:
        d.rounded_rectangle(box, radius=radius, outline=(*color, 200), width=width + 4)
    halo = halo.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(halo)
    sharp = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(sharp)
    for box in shapes:
        ds.rounded_rectangle(box, radius=radius, fill=fill, outline=(*color, 255), width=width)
    base.alpha_composite(sharp)


def _light(base: Image.Image, box: Box, color: tuple[int, int, int, int], blur: int) -> None:
    """Una mancha de luz suave (elipse difuminada)."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=color)
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def _cards(
    base: Image.Image,
    cards: Iterable[tuple[Box, RGB]],
    radius: int,
    shadow: int = 0,
    border: RGB | None = None,
) -> None:
    """Tarjetas lisas con sombra suave opcional."""
    cards = list(cards)
    if shadow:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for (x0, y0, x1, y1), _ in cards:
            d.rounded_rectangle((x0, y0 + 2, x1, y1 + 3), radius=radius, fill=(0, 0, 0, shadow))
        base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(4)))
    d = ImageDraw.Draw(base)
    for box, color in cards:
        d.rounded_rectangle(box, radius=radius, fill=color, outline=border, width=1)


def _noise(base: Image.Image, amount: int, seed: int) -> Image.Image:
    """Grano fino y reproducible (textura de chapa del panel de instrumentos)."""
    rng = random.Random(seed)  # noqa: S311 (decoración, no criptografía)
    w, h = base.size
    grain = Image.new("L", (w, h))
    grain.putdata([128 + rng.randint(-amount, amount) for _ in range(w * h)])
    grain = grain.filter(ImageFilter.GaussianBlur(0.6))
    # canal + grano - 128: el grano (centrado en 128) aclara u oscurece un poco cada píxel.
    channels = [ImageChops.add(c, grain, offset=-128) for c in base.convert("RGB").split()]
    return Image.merge("RGB", channels).convert("RGBA")


# --- familia Neón ----------------------------------------------------------------------


def neon() -> Image.Image:
    img = _vertical_gradient((10, 6, 24), (4, 2, 10)).convert("RGBA")
    grid = ImageDraw.Draw(img)
    for x in range(0, W, 24):
        grid.line((x, 0, x, H), fill=(40, 20, 70, 255))
    for y in range(0, H, 24):
        grid.line((0, y, W, y), fill=(40, 20, 70, 255))
    _glow(img, [(10, 76, 236, 228), (244, 76, 470, 228)], (0, 230, 255), 2, 6)
    _glow(img, [(10, 238, 470, 310)], (255, 40, 200), 2, 6)
    return img.convert("RGB")


SYNTH_HORIZON = 196
SYNTH_PANELS = [(16, 212, 160, 306), (168, 212, 312, 306), (320, 212, 464, 306)]


def synthwave() -> Image.Image:
    hz = SYNTH_HORIZON
    img = _gradient(
        (W, H),
        [
            (0.0, (12, 3, 34)),
            (0.30, (44, 8, 82)),
            (hz / H, (178, 44, 118)),
            (hz / H + 0.004, (22, 3, 38)),
            (1.0, (8, 0, 16)),
        ],
    ).convert("RGBA")
    # Estrellas en la parte alta del cielo, lejos del sol.
    rng = random.Random(1984)  # noqa: S311 (decoración)
    stars = ImageDraw.Draw(img)
    for _ in range(70):
        x, y = rng.randrange(W), rng.randrange(0, 120)
        v = rng.randrange(90, 230)
        stars.point((x, y), fill=(v, v, min(255, v + 25), 255))
    # Sol con franjas, medio hundido en el horizonte.
    cx, cy, r = 372, 126, 68
    _light(img, (cx - r - 30, cy - r - 30, cx + r + 30, cy + r + 30), (255, 70, 160, 120), 26)
    sun = _gradient(
        (2 * r, 2 * r), [(0.0, (255, 226, 110)), (0.55, (255, 120, 90)), (1.0, (255, 40, 150))]
    ).convert("RGBA")
    mask = Image.new("L", (2 * r, 2 * r), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
    y, gap = r + 6, 2
    while y < 2 * r:
        md.rectangle((0, y, 2 * r, y + gap - 1), fill=0)
        y += gap + 7
        gap += 1
    sun.putalpha(mask)
    img.alpha_composite(sun, (cx - r, cy - r))
    # Suelo: rejilla en perspectiva hacia el punto de fuga.
    floor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(floor)
    vx = 240
    for k in range(-12, 13):
        fd.line((vx, hz, vx + k * 70, H), fill=(255, 60, 200, 150), width=1)
    n = 9
    for i in range(1, n + 1):
        yy = hz + round((H - hz) * (i / n) ** 2)
        fd.line((0, yy, W, yy), fill=(255, 60, 200, 150), width=1)
    img.alpha_composite(floor)
    # Línea del horizonte encendida.
    _light(img, (-40, hz - 8, W + 40, hz + 8), (255, 90, 200, 160), 6)
    ImageDraw.Draw(img).line((0, hz, W, hz), fill=(255, 150, 220, 255), width=2)
    # Paneles opacos para los datos: los números cambian sobre color liso.
    colors = [(255, 150, 70), (255, 60, 200), (0, 220, 255)]
    for box, color in zip(SYNTH_PANELS, colors, strict=True):
        _glow(img, [box], color, 2, 5, radius=8, fill=(20, 4, 38, 255))
    return img.convert("RGB")


def terminal() -> Image.Image:
    img = Image.new("RGBA", (W, H), (2, 9, 4, 255))
    _light(img, (40, 40, 440, 300), (20, 90, 40, 70), 70)
    d = ImageDraw.Draw(img)
    for y in range(0, H, 3):  # líneas de barrido del monitor de fósforo
        d.line((0, y, W, y), fill=(0, 0, 0, 70))
    d.rectangle((0, 0, W, 29), fill=(6, 40, 18, 255))
    d.line((0, 30, W, 30), fill=(51, 255, 102, 110))
    d.line((0, 279, W, 279), fill=(51, 255, 102, 50))
    return img.convert("RGB")


def terminal_segmentos() -> Image.Image:
    """Capa que va ENCIMA de las barras: las corta en bloques, como «█████░░░» de consola."""
    seg = Image.new("RGBA", (176, 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(seg)
    for x in range(6, 176, 8):
        d.rectangle((x, 0, x + 1, 11), fill=(2, 9, 4, 255))
    return seg


HIELO_PANELS = [(12, y, 308, y + 76) for y in (136, 220, 304, 388)]


def hielo_vertical() -> Image.Image:
    img = _gradient(
        (VW, VH), [(0.0, (8, 24, 48)), (0.45, (5, 16, 34)), (1.0, (3, 10, 22))]
    ).convert("RGBA")
    _light(img, (-60, -80, 380, 150), (150, 220, 255, 55), 40)
    # Esquirlas de hielo muy tenues arriba (detrás del reloj, que cambia una vez por minuto).
    shards = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shards)
    for poly in (
        [(0, 0), (90, 0), (20, 120)],
        [(230, 0), (320, 0), (320, 90)],
        [(60, 0), (140, 0), (100, 70)],
        [(260, 40), (320, 130), (300, 0)],
    ):
        sd.polygon(poly, fill=(190, 235, 255, 18), outline=(200, 240, 255, 40))
    img.alpha_composite(shards)
    _glow(img, HIELO_PANELS, (120, 200, 255), 1, 5, radius=12, fill=(8, 24, 46, 255))
    return img.convert("RGB")


# --- familia Reloj ---------------------------------------------------------------------


def reloj() -> Image.Image:
    img = _vertical_gradient((18, 28, 52), (6, 8, 16)).convert("RGBA")
    # Un círculo de luz suave detrás del reloj.
    light = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(light).ellipse((90, -40, 390, 220), fill=(255, 140, 0, 70))
    img.alpha_composite(light.filter(ImageFilter.GaussianBlur(60)))
    ImageDraw.Draw(img).line((40, 252, 440, 252), fill=(255, 255, 255, 40), width=1)
    return img.convert("RGB")


def reloj_minimalista() -> Image.Image:
    img = Image.new("RGBA", (W, H), (7, 7, 9, 255))
    _light(img, (60, 20, 420, 300), (34, 34, 42, 255), 80)
    ImageDraw.Draw(img).line((216, 214, 264, 214), fill=(255, 255, 255, 60), width=1)
    return img.convert("RGB")


def reloj_segundos() -> Image.Image:
    img = _vertical_gradient((24, 24, 28), (9, 9, 11)).convert("RGBA")
    d = ImageDraw.Draw(img)
    # Regla de 60 marcas, como el bisel de un cronógrafo.
    x0, x1 = 24, 456
    for i in range(61):
        x = round(x0 + (x1 - x0) * i / 60)
        major = i % 5 == 0
        d.line(
            (x, 22, x, 36 if major else 30),
            fill=(255, 179, 71, 230) if major else (140, 140, 150, 160),
            width=2 if major else 1,
        )
    d.line((24, 220, 456, 220), fill=(255, 255, 255, 36))
    return img.convert("RGB")


def reloj_vertical() -> Image.Image:
    img = _vertical_gradient((18, 28, 52), (5, 7, 14), (VW, VH)).convert("RGBA")
    _light(img, (10, 40, 310, 300), (255, 140, 0, 60), 60)
    ImageDraw.Draw(img).line((32, 290, 288, 290), fill=(255, 255, 255, 40), width=1)
    return img.convert("RGB")


# --- familia Esencial ------------------------------------------------------------------


CLARO_CARDS = [(16, 78, 236, 190), (244, 78, 464, 190), (16, 198, 236, 310), (244, 198, 464, 310)]


def esencial_claro() -> Image.Image:
    img = Image.new("RGBA", (W, H), (241, 243, 247, 255))
    _cards(img, [(c, (255, 255, 255)) for c in CLARO_CARDS], 12, shadow=28, border=(222, 226, 233))
    return img.convert("RGB")


def esencial_detalle() -> Image.Image:
    img = Image.new("RGBA", (W, H), (12, 16, 28, 255))
    d = ImageDraw.Draw(img)
    line = (34, 42, 62, 255)
    d.line((16, 48, 464, 48), fill=line)
    d.line((240, 60, 240, 258), fill=line)
    d.line((16, 268, 464, 268), fill=line)
    return img.convert("RGB")


VERT_TILES = [(x, y, x + 140, y + 112) for y in (112, 232, 352) for x in (16, 164)]


def esencial_vertical() -> Image.Image:
    img = Image.new("RGBA", (VW, VH), (12, 16, 28, 255))
    _cards(img, [(t, (19, 25, 42)) for t in VERT_TILES], 10, border=(30, 38, 60))
    return img.convert("RGB")


# --- familia Instrumentos (panel de cabina) --------------------------------------------


def _gauges(img: Image.Image, columns: list[int], col_w: int, top: int, bottom: int) -> None:
    """Ranuras de los indicadores verticales: bisel hundido y escala con zona roja."""
    d = ImageDraw.Draw(img)
    for x0 in columns:
        bx = x0 + (col_w - 24) // 2 + 4  # la barra (24 px) va un poco a la derecha de la escala
        d.rectangle((bx - 3, top - 3, bx + 26, bottom + 2), fill=(8, 9, 11), outline=(70, 74, 82))
        for i in range(11):
            y = round(bottom - (bottom - top) * i / 10)
            major = i % 5 == 0
            color = (220, 60, 50) if i >= 9 else (170, 174, 182)
            d.line((bx - (14 if major else 10), y, bx - 6, y), fill=color, width=2 if major else 1)


def _screws(img: Image.Image, size: tuple[int, int]) -> None:
    d = ImageDraw.Draw(img)
    w, h = size
    for cx, cy in ((7, 7), (w - 8, 7), (7, h - 8), (w - 8, h - 8)):
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(70, 74, 80), outline=(20, 21, 24))
        d.line((cx - 2, cy + 2, cx + 2, cy - 2), fill=(25, 26, 30))


def _display(img: Image.Image, box: Box) -> None:
    ImageDraw.Draw(img).rounded_rectangle(box, radius=4, fill=(5, 6, 7), outline=(70, 74, 82))


INSTR_COLUMNS = [16 + i * 64 for i in range(7)]


def instrumentos() -> Image.Image:
    img = _noise(_vertical_gradient((34, 36, 40), (22, 24, 27)), 5, seed=7)
    _screws(img, (W, H))
    for box in ((16, 10, 136, 50), (144, 10, 304, 50), (312, 10, 464, 50)):
        _display(img, box)
    _gauges(img, INSTR_COLUMNS, 64, 66, 230)
    ImageDraw.Draw(img).line((16, 284, 464, 284), fill=(60, 64, 70))
    return img.convert("RGB")


INSTR_V_COLUMNS = [16 + i * 72 for i in range(4)]


def instrumentos_vertical() -> Image.Image:
    img = _noise(_vertical_gradient((34, 36, 40), (22, 24, 27), (VW, VH)), 5, seed=8)
    _screws(img, (VW, VH))
    _display(img, (16, 16, 304, 80))
    for box in ((16, 396, 156, 464), (164, 396, 304, 464)):
        _display(img, box)
    _gauges(img, INSTR_V_COLUMNS, 72, 130, 326)
    return img.convert("RGB")


# --- familia Tarjetas (pastel sobre carbón) --------------------------------------------

CORAL, PEACH, MINT = (255, 183, 170), (255, 214, 176), (184, 234, 214)
LAVENDER, BUTTER = (204, 208, 240), (255, 236, 168)
CHARCOAL = (32, 29, 38)


def tarjetas() -> Image.Image:
    img = Image.new("RGBA", (W, H), (*CHARCOAL, 255))
    _cards(
        img,
        [
            ((16, 16, 168, 304), CORAL),
            ((176, 16, 316, 156), PEACH),
            ((324, 16, 464, 156), MINT),
            ((176, 164, 316, 304), LAVENDER),
            ((324, 164, 464, 304), BUTTER),
        ],
        16,
    )
    return img.convert("RGB")


def tarjetas_vertical() -> Image.Image:
    img = Image.new("RGBA", (VW, VH), (*CHARCOAL, 255))
    _cards(
        img,
        [
            ((16, 16, 304, 136), CORAL),
            ((16, 144, 156, 276), PEACH),
            ((164, 144, 304, 276), MINT),
            ((16, 284, 156, 416), LAVENDER),
            ((164, 284, 304, 416), BUTTER),
            ((16, 424, 304, 464), (226, 222, 234)),
        ],
        16,
    )
    return img.convert("RGB")


# --- salida ----------------------------------------------------------------------------

FONDOS: list[tuple[str, str, Callable[[], Image.Image]]] = [
    ("neon", "fondo.png", neon),
    ("synthwave", "fondo.png", synthwave),
    ("terminal", "fondo.png", terminal),
    ("terminal", "segmentos.png", terminal_segmentos),
    ("hielo-vertical", "fondo.png", hielo_vertical),
    ("reloj", "fondo.png", reloj),
    ("reloj-minimalista", "fondo.png", reloj_minimalista),
    ("reloj-segundos", "fondo.png", reloj_segundos),
    ("reloj-vertical", "fondo.png", reloj_vertical),
    ("esencial-claro", "fondo.png", esencial_claro),
    ("esencial-detalle", "fondo.png", esencial_detalle),
    ("esencial-vertical", "fondo.png", esencial_vertical),
    ("instrumentos", "fondo.png", instrumentos),
    ("instrumentos-vertical", "fondo.png", instrumentos_vertical),
    ("tarjetas", "fondo.png", tarjetas),
    ("tarjetas-vertical", "fondo.png", tarjetas_vertical),
]


def _style_modules() -> list[tuple[str, str, Callable[[], Image.Image]]]:
    """Fondos de ``scripts/estilos/*.py``: cada módulo, un estilo (o varios), con su propia
    lista ``FONDOS``. Así varios estilos se pueden escribir a la vez sin tocar este fichero;
    las utilidades de aquí (``_gradient``, ``_glow``…) se importan con ``from fondos import``."""
    import importlib.util
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    found: list[tuple[str, str, Callable[[], Image.Image]]] = []
    for path in sorted((Path(__file__).resolve().parent / "estilos").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"estilos.{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found += module.FONDOS
    return found


def main() -> None:
    for folder, filename, make in [*FONDOS, *_style_modules()]:
        out = TEMAS / folder / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        make().save(out, optimize=True)
        print(f"{out.relative_to(TEMAS.parent.parent.parent.parent)}  {out.stat().st_size} B")


if __name__ == "__main__":
    main()
