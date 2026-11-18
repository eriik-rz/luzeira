"""El lucero de Luzeira: icono de la bandeja y del .ico."""

import pytest

from luzeira.marca import NIGHT, STAR, draw_mark


@pytest.mark.parametrize("size", [16, 32, 64, 256])
def test_mark_has_size_transparent_corners_and_the_star(size):
    img = draw_mark(size)
    assert img.size == (size, size) and img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] < 16  # esquina fuera del cuadrado (suavizado: casi 0)
    r, g, b, a = img.getpixel((size // 2, size // 2))  # centro: la estrella
    assert a == 255 and (r, g, b) == pytest.approx(STAR, abs=12)


def test_mark_takes_the_state_color():
    red = (220, 50, 50)
    center = draw_mark(64, red).getpixel((32, 32))[:3]
    assert center == pytest.approx(red, abs=12)
    # Fondo azul noche lejos de la estrella (abajo a la izquierda).
    assert draw_mark(64).getpixel((10, 54))[:3] == pytest.approx(NIGHT, abs=20)
