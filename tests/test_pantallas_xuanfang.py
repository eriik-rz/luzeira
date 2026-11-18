"""Driver beta de la XuanFang 3,5" (rev B) con un puerto simulado.

Los bytes esperados se escriben a mano desde la tabla del informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (sección «Protocolo rev B»),
no con las funciones del driver.
"""

import pytest
from PIL import Image

from luzeira.pantallas.comun import DisplayError, Orientation, WrongModel
from luzeira.pantallas.rgb565 import pixel_to_rgb565_le, to_rgb565_be
from luzeira.pantallas.xuanfang_rev_b import (
    HELLO_ATTEMPTS,
    XuanFangRevB,
    brightness_value,
    packet,
)

HELLO_REQUEST = bytes.fromhex("CA 48 45 4C 4C 4F 00 00 00 CA")


def hello_answer(sub: int) -> bytes:
    return bytes.fromhex("CA 48 45 4C 4C 4F 0A") + bytes([sub, 0x00, 0xCA])


class FakeSerial:
    """Puerto falso: guarda cada write; cada read devuelve la siguiente respuesta."""

    def __init__(self, *answers: bytes) -> None:
        self.writes: list[bytes] = []
        self.answers = list(answers)
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return self.answers.pop(0)[:size] if self.answers else b""

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def ready(sub: int = 0x11) -> tuple[XuanFangRevB, FakeSerial, list[float]]:
    """Una pantalla ya saludada, con el puerto limpio y las esperas apuntadas."""
    port = FakeSerial(hello_answer(sub))
    sleeps: list[float] = []
    display = XuanFangRevB(port, sleep=sleeps.append)
    display.probe()
    port.writes.clear()
    return display, port, sleeps


# --- paquetes ------------------------------------------------------------------


def test_packet_has_command_at_both_ends_and_8_data_bytes():
    assert packet(0xCE, b"\x7f") == bytes.fromhex("CE 7F 00 00 00 00 00 00 00 CE")
    assert packet(0xCA, b"HELLO") == HELLO_REQUEST
    with pytest.raises(ValueError):
        packet(0xCC, bytes(9))


# --- saludo --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sub", "flagship", "full_range"),
    [(0x01, False, False), (0x02, True, False), (0x11, False, True), (0x12, True, True)],
)
def test_hello_sends_exact_bytes_and_reads_sub_revision(sub, flagship, full_range):
    port = FakeSerial(hello_answer(sub))
    display = XuanFangRevB(port)
    display.probe()
    assert port.writes == [HELLO_REQUEST]
    assert (display.flagship, display.full_range_brightness) == (flagship, full_range)


@pytest.mark.parametrize(
    "answer",
    [
        bytes([1] * 6),  # lo que contesta una rev A (UsbPCMonitor)
        hello_answer(0x11)[:9],  # corta
        bytes.fromhex("CB 48 45 4C 4C 4F 0A 11 00 CA"),  # marco mal
        bytes.fromhex("CA 48 45 4C 4C 4F 0A 11 00 CB"),
        bytes.fromhex("CA 48 41 4C 4C 4F 0A 11 00 CA"),  # no dice HELLO
        bytes.fromhex("CA 48 45 4C 4C 4F 0B 11 00 CA"),  # falta la marca 0A
        hello_answer(0x33),  # sub-revisión desconocida
    ],
)
def test_wrong_hello_answer_closes_port_and_sends_nothing_else(answer):
    ports = []

    def factory(_device):
        ports.append(FakeSerial(answer))
        return ports[-1]

    with pytest.raises(WrongModel, match="COM5"):
        XuanFangRevB.open("COM5", retries=3, serial_factory=factory, sleep=lambda _s: None)
    [port] = ports  # una respuesta mala no se reintenta
    assert port.closed
    assert port.writes == [HELLO_REQUEST]


def test_silent_display_gets_a_capped_number_of_hellos():
    port = FakeSerial()  # nunca contesta
    with pytest.raises(WrongModel):
        XuanFangRevB.open("COM5", serial_factory=lambda _d: port)
    assert port.writes == [HELLO_REQUEST] * HELLO_ATTEMPTS
    assert HELLO_ATTEMPTS == 2
    assert port.closed


def test_second_hello_can_succeed_after_a_silent_first():
    port = FakeSerial(b"", hello_answer(0x12))
    display = XuanFangRevB.open("COM5", serial_factory=lambda _d: port)
    assert display.flagship and display.device == "COM5"
    assert port.writes == [HELLO_REQUEST, HELLO_REQUEST]


def test_open_error_closes_and_retries():
    class Breaks(FakeSerial):
        def write(self, data):
            raise OSError("se está reiniciando")

    ports = []

    def factory(_device):
        ports.append(Breaks())
        return ports[-1]

    with pytest.raises(DisplayError) as info:
        XuanFangRevB.open("COM5", retries=2, serial_factory=factory, sleep=lambda _s: None)
    assert not isinstance(info.value, WrongModel)
    assert len(ports) == 2 and all(p.closed for p in ports)


# --- brillo --------------------------------------------------------------------


@pytest.mark.parametrize(("percent", "value"), [(0, 0), (50, 127), (100, 255), (25, 63)])
def test_full_range_brightness_is_not_inverted(percent, value):
    assert brightness_value(percent, full_range=True) == value


@pytest.mark.parametrize(("percent", "value"), [(0, 1), (1, 0), (100, 0)])
def test_on_off_brightness_only_zero_turns_off(percent, value):
    assert brightness_value(percent, full_range=False) == value


def test_set_brightness_and_screen_off_on():
    display, port, _ = ready(0x11)
    display.set_brightness(100)
    display.screen_off()
    display.screen_on()
    assert port.writes == [
        bytes.fromhex("CE FF 00 00 00 00 00 00 00 CE"),
        bytes.fromhex("CE 00 00 00 00 00 00 00 00 CE"),  # apagar = brillo 0
        bytes.fromhex("CE FF 00 00 00 00 00 00 00 CE"),  # encender = el de antes
    ]


def test_old_sub_revision_turns_off_with_one():
    display, port, _ = ready(0x01)
    display.set_brightness(40)
    display.screen_off()
    assert port.writes == [
        bytes.fromhex("CE 00 00 00 00 00 00 00 00 CE"),
        bytes.fromhex("CE 01 00 00 00 00 00 00 00 CE"),
    ]


# --- orientación ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("orientation", "value", "size"),
    [
        (Orientation.PORTRAIT, 0, (320, 480)),
        (Orientation.REVERSE_PORTRAIT, 0, (320, 480)),
        (Orientation.LANDSCAPE, 1, (480, 320)),
        (Orientation.REVERSE_LANDSCAPE, 1, (480, 320)),
    ],
)
def test_orientation_only_portrait_or_landscape_on_the_wire(orientation, value, size):
    display, port, _ = ready()
    display.set_orientation(orientation)
    assert port.writes == [bytes([0xCB, value, 0, 0, 0, 0, 0, 0, 0, 0xCB])]
    assert (display.width, display.height) == size


# --- imagen --------------------------------------------------------------------


def test_full_screen_bitmap_bytes():
    display, port, sleeps = ready()
    display.draw_image(Image.new("RGB", (320, 480), (255, 0, 0)))
    command, *chunks = port.writes
    # x0=0, y0=0, x1=319 (0x013F), y1=479 (0x01DF), 16 bits big-endian, inclusivas.
    assert command == bytes.fromhex("CC 00 00 00 00 01 3F 01 DF CC")
    assert len(chunks) == 120 and all(len(c) == 2560 for c in chunks)  # ancho x 8
    assert chunks[0][:4] == bytes([0xF8, 0x00, 0xF8, 0x00])  # rojo 0xF800, byte alto primero
    assert sleeps == [0.05]


def test_landscape_chunks_follow_the_width():
    display, port, _ = ready()
    display.set_orientation(Orientation.LANDSCAPE)
    port.writes.clear()
    display.draw_image(Image.new("RGB", (480, 320)))
    command, *chunks = port.writes
    assert command == bytes.fromhex("CC 00 00 00 00 01 DF 01 3F CC")
    assert all(len(c) == 3840 for c in chunks)


def test_reverse_portrait_mirrors_coordinates_and_turns_the_image():
    display, port, _ = ready()
    display.set_orientation(Orientation.REVERSE_PORTRAIT)
    port.writes.clear()
    strip = Image.new("RGB", (32, 1), (0, 0, 255))
    strip.putpixel((0, 0), (255, 0, 0))  # el primer píxel, rojo
    display.draw_image(strip, 0, 0)
    command, data = port.writes[0], b"".join(port.writes[1:])
    # 32x1 arriba a la izquierda → en la pantalla, abajo a la derecha: x 288..319, y 479.
    assert command == bytes.fromhex("CC 01 20 01 DF 01 3F 01 DF CC")
    assert data[-2:] == bytes([0xF8, 0x00])  # el rojo queda el último tras girar 180°
    assert data[:2] == bytes([0x00, 0x1F])


def test_partial_bitmaps_are_whole_64_byte_blocks_and_match_their_box():
    display, port, _ = ready()
    display.draw_image(Image.new("RGB", (300, 22)), 10, 40)  # 13.200 B: no cuadra
    command, data = port.writes[0], b"".join(port.writes[1:])
    x0, y0, x1, y1 = (int.from_bytes(command[i : i + 2], "big") for i in (1, 3, 5, 7))
    assert len(data) % 64 == 0
    assert len(data) == (x1 - x0 + 1) * (y1 - y0 + 1) * 2


def test_bitmap_outside_screen_is_rejected():
    display, _, _ = ready()
    with pytest.raises(ValueError):
        display.draw_image(Image.new("RGB", (1, 1)), 320, 0)


def test_unplugged_usb_gives_user_error():
    display, port, _ = ready()

    def broken(_data):
        raise OSError("ClearCommError failed")

    port.write = broken
    with pytest.raises(DisplayError, match="desenchufado"):
        display.set_brightness(10)


def test_rgb565_big_endian_is_the_little_endian_reference_swapped():
    img = Image.new("RGB", (3, 1))
    pixels = [(255, 0, 0), (12, 200, 99), (255, 255, 255)]
    img.putdata(pixels)
    expected = b"".join(pixel_to_rgb565_le(*p)[::-1] for p in pixels)
    assert to_rgb565_be(img) == expected
