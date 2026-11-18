"""Conversión de imágenes al formato de píxel de la pantalla: RGB565.

Cada píxel ocupa 2 bytes: 5 bits de rojo, 6 de verde y 5 de azul
(``RRRRRGGG GGGBBBBB``). La rev A envía primero el byte bajo (little-endian); la rev B
y la Kipye, primero el alto (big-endian).

La conversión se hace con tablas de consulta de Pillow (en C), no con un bucle de
Python: con 153.600 píxeles por pantalla, el bucle es el doble de lento.
"""

from PIL import Image, ImageChops

# Tablas de consulta (256 entradas, una por valor posible del canal).
# Byte bajo  = GGGBBBBB -> 3 bits bajos del verde de 6 bits + azul de 5 bits.
# Byte alto  = RRRRRGGG -> rojo de 5 bits + 3 bits altos del verde de 6 bits.
# Los bits de cada par no se solapan, así que sumarlos equivale a un OR.
_LOW_FROM_G = [((v >> 2) & 0x07) << 5 for v in range(256)]
_LOW_FROM_B = [v >> 3 for v in range(256)]
_HIGH_FROM_R = [(v >> 3) << 3 for v in range(256)]
_HIGH_FROM_G = [(v >> 2) >> 3 for v in range(256)]


def to_rgb565_le(image: Image.Image) -> bytes:
    """Devuelve los píxeles de ``image`` en RGB565 little-endian, fila a fila."""
    r, g, b = image.convert("RGB").split()
    low = ImageChops.add(g.point(_LOW_FROM_G), b.point(_LOW_FROM_B))
    high = ImageChops.add(r.point(_HIGH_FROM_R), g.point(_HIGH_FROM_G))
    # "LA" intercala dos bytes por píxel: primero L (bajo) y luego A (alto).
    return Image.merge("LA", (low, high)).tobytes()


def _low_high(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    r, g, b = image.convert("RGB").split()
    low = ImageChops.add(g.point(_LOW_FROM_G), b.point(_LOW_FROM_B))
    high = ImageChops.add(r.point(_HIGH_FROM_R), g.point(_HIGH_FROM_G))
    return low, high


def to_rgb565_be(image: Image.Image) -> bytes:
    """Devuelve los píxeles de ``image`` en RGB565 big-endian (byte alto primero)."""
    low, high = _low_high(image)
    return Image.merge("LA", (high, low)).tobytes()


def pixel_to_rgb565_le(r: int, g: int, b: int) -> bytes:
    """Versión de referencia, píxel a píxel. La usan los tests para validar la rápida."""
    value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    return value.to_bytes(2, "little")
