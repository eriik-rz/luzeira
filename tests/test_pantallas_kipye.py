"""Driver beta de la Kipye Qiye 3,5" con un puerto simulado.

Bytes esperados escritos a mano desde el informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (sección «Protocolo rev D»).
"""

import pytest
from PIL import Image

from luzeira.pantallas.comun import DisplayError, Orientation
from luzeira.pantallas.kipye_rev_d import KipyeRevD, brightness_command

PICTURE_START = bytes.fromhex("44 00 00 00")
PICTURE_END = bytes.fromhex("41 00 00 00")


class FakeSerial:
    """Puerto falso que apunta escrituras y vaciados de entrada en el mismo orden."""

    def __init__(self) -> None:
        self.events: list[bytes | str] = []
        self.closed = False

    @property
    def writes(self) -> list[bytes]:
        return [e for e in self.events if isinstance(e, bytes)]

    def write(self, data: bytes) -> int:
        self.events.append(bytes(data))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return b""

    def reset_input_buffer(self) -> None:
        self.events.append("flush")

    def close(self) -> None:
        self.closed = True


def ready() -> tuple[KipyeRevD, FakeSerial]:
    port = FakeSerial()
    return KipyeRevD(port), port


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(100, "43 43 01 F4"), (50, "43 43 00 FA"), (0, "43 43 00 00"), (1, "43 43 00 05")],
)
def test_brightness_goes_0_to_500_big_endian(percent, expected):
    assert brightness_command(percent) == bytes.fromhex(expected)


def test_brightness_is_sent_twice_and_input_is_flushed_after_each():
    display, port = ready()
    display.set_brightness(100)
    cmd = bytes.fromhex("43 43 01 F4")
    assert port.events == [cmd, "flush", cmd, "flush"]


def test_screen_off_is_brightness_zero_and_on_restores_it():
    display, port = ready()
    display.set_brightness(20)
    port.events.clear()
    display.screen_off()
    display.screen_on()
    assert port.writes == [bytes.fromhex("43 43 00 00")] * 2 + [bytes.fromhex("43 43 00 64")] * 2


def test_probe_is_one_brightness_and_nothing_else():
    port = FakeSerial()
    display = KipyeRevD.open("COM8", serial_factory=lambda _d: port)
    assert display.device == "COM8"
    assert port.writes == [bytes.fromhex("43 43 00 FA")] * 2


def test_probe_that_cannot_write_closes_the_port():
    class Stuck(FakeSerial):
        def write(self, data):
            raise OSError("Write timeout")  # SerialTimeoutException es un OSError

    port = Stuck()
    with pytest.raises(DisplayError):
        KipyeRevD.open("COM8", serial_factory=lambda _d: port)
    assert port.closed


@pytest.mark.parametrize(
    ("orientation", "expected", "size"),
    [
        (Orientation.PORTRAIT, "43 48 00 00", (320, 480)),
        (Orientation.REVERSE_PORTRAIT, "43 47 00 00", (320, 480)),
        (Orientation.LANDSCAPE, "43 48 00 00", (480, 320)),
        (Orientation.REVERSE_LANDSCAPE, "43 47 00 00", (480, 320)),
    ],
)
def test_orientation_commands(orientation, expected, size):
    display, port = ready()
    display.set_orientation(orientation)
    assert port.events == [bytes.fromhex(expected), "flush"]
    assert (display.width, display.height) == size


def test_bitmap_window_order_is_x0_x1_y0_y1_inclusive():
    display, port = ready()
    display.draw_image(Image.new("RGB", (2, 2), (255, 0, 0)), 10, 20)
    assert port.events == [
        bytes.fromhex("43 41 00 0A 00 0B 00 14 00 15"),  # x 10..11, y 20..21
        "flush",
        PICTURE_START,
        "flush",
        bytes([0x50]) + bytes([0xF8, 0x00]) * 4,  # RGB565 big-endian
        PICTURE_END,
        "flush",
    ]


def test_pixels_go_in_64_byte_packets_and_the_last_one_is_shorter():
    display, port = ready()
    display.draw_image(Image.new("RGB", (100, 1)), 0, 0)  # 200 bytes de píxeles
    packets = port.writes[2:-1]
    assert [len(p) for p in packets] == [64, 64, 64, 12]  # 63 + 63 + 63 + 11, sin relleno
    assert all(p[0] == 0x50 for p in packets)


def test_landscape_is_turned_in_software():
    display, port = ready()
    display.set_orientation(Orientation.LANDSCAPE)
    port.events.clear()
    img = Image.new("RGB", (3, 2), (0, 0, 255))
    img.putpixel((0, 0), (255, 0, 0))  # esquina superior izquierda (horizontal), rojo
    display.draw_image(img, 0, 0)
    window, data = port.writes[0], b"".join(p[1:] for p in port.writes[2:-1])
    # El punto (X, Y) horizontal es la columna 319 - Y y la fila X en vertical: una imagen
    # 3x2 en (0, 0) ocupa las columnas 318..319 y las filas 0..2.
    assert window == bytes.fromhex("43 41 01 3E 01 3F 00 00 00 02")
    assert len(data) == 3 * 2 * 2
    # Fila 0 de la ventana (2 píxeles): el rojo va en la columna 319, la segunda.
    assert data[:4] == bytes([0x00, 0x1F, 0xF8, 0x00])


def test_partial_bitmap_is_cropped_to_screen():
    display, port = ready()
    display.draw_image(Image.new("RGB", (50, 50)), 300, 470)
    assert port.writes[0] == bytes.fromhex("43 41 01 2C 01 3F 01 D6 01 DF")  # 300..319, 470..479
    assert sum(len(p) - 1 for p in port.writes[2:-1]) == 20 * 10 * 2
