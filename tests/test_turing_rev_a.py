"""Tests del driver de revisión A con un puerto simulado (no hace falta la pantalla)."""

import inspect
import random
from types import SimpleNamespace

import pytest
from PIL import Image

from visoria.pantallas import turing_rev_a
from visoria.pantallas.rgb565 import pixel_to_rgb565_le, to_rgb565_le
from visoria.pantallas.turing_rev_a import (
    TURING_3_5,
    USBMONITOR_5,
    Command,
    ConfirmationNeeded,
    DisplayBusy,
    DisplayError,
    Orientation,
    PortMatch,
    TuringRevA,
    brightness_to_byte,
    find_port,
    pack_command,
)


class FakeSerial:
    """Puerto falso: guarda cada write y responde a read con lo que se le diga."""

    def __init__(self, answer: bytes = b"") -> None:
        self.writes: list[bytes] = []
        self.answer = answer
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return self.answer[:size]

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def unpack(frame: bytes) -> tuple[int, int, int, int, int]:
    """Inversa de pack_command, escrita bit a bit para no reutilizar su lógica."""
    bits = int.from_bytes(frame[:5], "big")  # 40 bits = 4 campos de 10
    x, y, ex, ey = (bits >> 30) & 0x3FF, (bits >> 20) & 0x3FF, (bits >> 10) & 0x3FF, bits & 0x3FF
    return x, y, ex, ey, frame[5]


# --- empaquetado -------------------------------------------------------------


def test_pack_full_screen_bitmap_hand_computed():
    # x=0 y=0 ex=319 ey=479, calculado a mano con las fórmulas de la skill.
    assert pack_command(Command.DISPLAY_BITMAP, 0, 0, 319, 479) == bytes([0, 0, 4, 253, 223, 197])


def test_pack_mixed_fields_hand_computed():
    assert pack_command(110, 5, 17, 6, 300) == bytes([1, 65, 16, 25, 44, 110])


def test_pack_roundtrip_random():
    rng = random.Random(42)
    for _ in range(500):
        fields = [rng.randrange(1024) for _ in range(4)]
        cmd = rng.randrange(256)
        assert unpack(pack_command(cmd, *fields)) == (*fields, cmd)


@pytest.mark.parametrize("bad", [-1, 1024])
def test_pack_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        pack_command(Command.CLEAR, x=bad)


# --- brillo ------------------------------------------------------------------


@pytest.mark.parametrize(("percent", "expected"), [(100, 0), (0, 255), (25, 191), (50, 127)])
def test_brightness_is_inverted(percent, expected):
    assert brightness_to_byte(percent) == expected


def test_brightness_rejects_out_of_range():
    with pytest.raises(ValueError):
        brightness_to_byte(101)


def test_set_brightness_sends_value_in_x():
    port = FakeSerial()
    TuringRevA(port).set_brightness(100)
    assert unpack(port.writes[-1]) == (0, 0, 0, 0, Command.SET_BRIGHTNESS)


# --- RGB565 ------------------------------------------------------------------


def test_rgb565_known_colors():
    img = Image.new("RGB", (4, 1))
    img.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
    # rojo 0xF800, verde 0x07E0, azul 0x001F, blanco 0xFFFF -> little-endian
    assert to_rgb565_le(img) == bytes([0x00, 0xF8, 0xE0, 0x07, 0x1F, 0x00, 0xFF, 0xFF])


def test_rgb565_fast_matches_reference():
    rng = random.Random(7)
    pixels = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(2000)]
    img = Image.new("RGB", (40, 50))
    img.putdata(pixels)
    assert to_rgb565_le(img) == b"".join(pixel_to_rgb565_le(*p) for p in pixels)


def test_rgb565_converts_rgba():
    img = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    assert to_rgb565_le(img) == bytes([0x00, 0xF8])


# --- detección y apertura -----------------------------------------------------


OTHER = SimpleNamespace(device="COM4", serial_number="X", vid=0x1234, pid=0x1)
BY_SERIAL = SimpleNamespace(device="COM7", serial_number="USB35INCHIPSV2", vid=0x1A86, pid=0x5722)
BY_IDS = SimpleNamespace(device="COM9", serial_number=None, vid=0x1A86, pid=0x5722)


def test_find_port_by_serial_is_safe():
    assert find_port([OTHER, BY_SERIAL]) == PortMatch("COM7", by_serial=True)


def test_find_port_by_vid_pid_only_is_not_safe():
    assert find_port([OTHER, BY_IDS]) == PortMatch("COM9", by_serial=False)
    assert find_port([OTHER]) is None


def test_find_port_prefers_serial_over_earlier_vid_pid():
    # Un posible rev B (solo VID/PID) listado antes no debe ganar al rev A seguro.
    assert find_port([BY_IDS, BY_SERIAL]) == PortMatch("COM7", by_serial=True)


def test_open_sends_nothing_when_only_vid_pid_matches(monkeypatch):
    opened = []
    monkeypatch.setattr(turing_rev_a, "find_port", lambda: find_port([BY_IDS]))
    with pytest.raises(ConfirmationNeeded) as info:
        TuringRevA.open(serial_factory=lambda d: opened.append(d) or FakeSerial())
    assert info.value.device == "COM9"
    assert opened == []  # ni siquiera se abre el puerto: 0 bytes enviados


def test_open_by_serial_does_not_ask(monkeypatch):
    monkeypatch.setattr(turing_rev_a, "find_port", lambda: find_port([BY_SERIAL]))
    display = TuringRevA.open(serial_factory=lambda d: FakeSerial(bytes([1] * 6)))
    assert display.model.name.startswith("UsbPCMonitor 3,5")


def test_unplugged_usb_gives_user_error_not_traceback():
    class Unplugged(FakeSerial):
        def write(self, data):
            raise OSError("ClearCommError failed")  # lo que lanza pyserial al desenchufar

    display = TuringRevA(Unplugged())
    with pytest.raises(DisplayError, match="desenchufado"):
        display.screen_on()
    with pytest.raises(DisplayError):
        display.draw_image(Image.new("RGB", (10, 10)))


def test_driver_never_sends_reset():
    # R-02: nunca se envía RESET (una app ajena inutilizó un panel así). Guardián que lee
    # el fuente: si alguien añade un uso de Command.RESET al driver, cae.
    assert "Command.RESET" not in inspect.getsource(TuringRevA)


def test_hello_detects_models():
    assert TuringRevA(FakeSerial(b"")).hello() == TURING_3_5
    assert TuringRevA(FakeSerial(bytes([2] * 6))).hello() == USBMONITOR_5
    assert TuringRevA(FakeSerial(bytes([1, 2, 1, 1, 1, 1]))).hello() == TURING_3_5


def test_open_retries_until_port_works():
    calls = []

    def factory(device):
        calls.append(device)
        if len(calls) < 3:
            raise OSError("acceso denegado")
        return FakeSerial()

    display = TuringRevA.open("COM3", serial_factory=factory, sleep=lambda _s: None)
    assert calls == ["COM3"] * 3
    assert display.model == TURING_3_5


def test_open_gives_clear_error_when_busy():
    import serial

    def factory(_device):
        # El error REAL que da pyserial en Windows con el puerto en uso (medido).
        raise serial.SerialException(
            "could not open port 'COM3': PermissionError(13, 'Acceso denegado.', None, 5)"
        )

    with pytest.raises(DisplayBusy, match="en uso por otro programa") as info:
        TuringRevA.open("COM3", retries=2, serial_factory=factory, sleep=lambda _s: None)
    assert info.value.device == "COM3"
    assert "PermissionError" not in str(info.value)  # el detalle técnico no se enseña


def test_port_is_closed_when_hello_fails():
    opened = []

    class HelloFails(FakeSerial):
        def write(self, data):
            raise OSError("la pantalla se está reiniciando")

    def factory(_device):
        port = HelloFails()
        opened.append(port)
        return port

    with pytest.raises(DisplayError):
        TuringRevA.open("COM3", retries=3, serial_factory=factory, sleep=lambda _s: None)
    assert len(opened) == 3
    assert all(p.closed for p in opened)  # ninguno se queda abierto esperando al GC


def test_other_open_errors_are_not_reported_as_busy():
    def factory(_device):
        raise OSError("El sistema no puede encontrar el archivo especificado")

    with pytest.raises(DisplayError) as info:
        TuringRevA.open("COM3", retries=1, serial_factory=factory, sleep=lambda _s: None)
    assert not isinstance(info.value, DisplayBusy)


# --- orientación y borrado -----------------------------------------------------


def test_landscape_frame_swaps_size():
    port = FakeSerial()
    display = TuringRevA(port)
    display.set_orientation(Orientation.LANDSCAPE)
    frame = port.writes[-1]
    assert len(frame) == 16
    assert unpack(frame) == (0, 0, 0, 0, Command.SET_ORIENTATION)
    assert frame[6] == 102
    assert int.from_bytes(frame[7:9], "big") == 480
    assert int.from_bytes(frame[9:11], "big") == 320
    assert frame[11:] == bytes(5)
    assert (display.width, display.height) == (480, 320)


def test_clear_restores_previous_orientation():
    port = FakeSerial()
    display = TuringRevA(port)
    display.set_orientation(Orientation.REVERSE_LANDSCAPE)
    port.writes.clear()
    display.clear()
    assert [w[6] for w in port.writes if len(w) == 16] == [100, 103]
    assert unpack(port.writes[1])[4] == Command.CLEAR
    assert display.orientation == Orientation.REVERSE_LANDSCAPE


# --- bitmap ------------------------------------------------------------------


def test_full_screen_bitmap_is_sent_in_chunks():
    port = FakeSerial()
    TuringRevA(port).draw_image(Image.new("RGB", (320, 480), "white"))
    command, *chunks = port.writes
    assert unpack(command) == (0, 0, 319, 479, Command.DISPLAY_BITMAP)
    assert sum(len(c) for c in chunks) == 320 * 480 * 2
    assert len(chunks) == 120  # 307200 / (320 * 8)
    assert all(len(c) == 2560 for c in chunks)


def test_bitmap_is_cropped_to_screen():
    port = FakeSerial()
    TuringRevA(port).draw_image(Image.new("RGB", (100, 100)), x=300, y=450)
    [((x0, y0, x1, y1), data)] = bitmap_sends(port)
    # Lo visible es 20x30 (300..319, 450..479). El driver lo amplía hacia dentro de la
    # pantalla hasta un múltiplo de 64 bytes, pero nunca se sale de ella.
    assert x0 <= 300 and y0 <= 450 and (x1, y1) == (319, 479)
    assert len(data) == (x1 - x0 + 1) * (y1 - y0 + 1) * 2
    assert len(data) % 64 == 0


def bitmap_sends(port: FakeSerial) -> list[tuple[tuple[int, int, int, int], bytes]]:
    """Agrupa lo escrito en (rectángulo del comando, bytes de píxeles que le siguen)."""
    sends, current = [], None
    for w in port.writes:
        if len(w) == 6 and w[5] == Command.DISPLAY_BITMAP:
            current = [unpack(w)[:4], b""]
            sends.append(current)
        elif current is not None and len(w) != 16:
            current[1] += w
    return [(tuple(r), d) for r, d in sends]


def test_every_bitmap_is_a_whole_number_of_64_byte_blocks():
    # Medido en una pantalla real (2026-11-17): si los píxeles no ocupan un número exacto
    # de bloques de 64 bytes, la pantalla pierde el último bloque y quedan restos.
    rng = random.Random(3)
    port = FakeSerial()
    display = TuringRevA(port)
    display.set_orientation(Orientation.LANDSCAPE)
    for _ in range(300):
        x, y = rng.randrange(480), rng.randrange(320)
        w, h = rng.randrange(1, 481 - x), rng.randrange(1, 321 - y)
        display.draw_image(Image.new("RGB", (w, h), "red"), x, y)
    for (x0, y0, x1, y1), data in bitmap_sends(port):
        assert len(data) % 64 == 0
        assert len(data) == (x1 - x0 + 1) * (y1 - y0 + 1) * 2  # el comando cuadra con los datos


def test_padding_repaints_what_was_already_on_screen():
    port = FakeSerial()
    display = TuringRevA(port)
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (480, 320), (0, 0, 255)))  # todo azul
    port.writes.clear()
    display.draw_image(Image.new("RGB", (300, 22), (255, 0, 0)), 120, 40)  # 13.200 B: no cuadra
    [((x0, y0, x1, y1), data)] = bitmap_sends(port)
    assert (x0, y0, x1, y1) == (120, 40, 423, 61)  # ampliada a 304x22 = 13.376 B
    row = data[: (x1 - x0 + 1) * 2]
    red, blue = bytes([0x00, 0xF8]), bytes([0x1F, 0x00])
    assert row[: 300 * 2] == red * 300  # lo pedido
    assert row[300 * 2 :] == blue * 4  # lo añadido: lo que ya había (azul), no negro


def test_bitmap_outside_screen_is_rejected():
    with pytest.raises(ValueError):
        TuringRevA(FakeSerial()).draw_image(Image.new("RGB", (1, 1)), x=320, y=0)
