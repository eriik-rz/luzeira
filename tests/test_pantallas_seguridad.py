"""🔴 C-44: los drivers beta no mandan nunca comandos peligrosos, ni los llevan dentro.

Peligrosos (informe ``docs/investigacion/tanda-4/otras-pantallas/informe.md``): reinicio,
escribir en el almacenamiento del panel y, de la rev C, ``OPTIONS`` 0x7D (guarda ajustes),
``RESTART`` 0x84, ``TURNOFF`` 0x83 y los de ficheros 0x64-0x78. Se ejercita todo lo que el
servicio puede pedir a cada driver con un puerto falso, se lee el flujo de bytes como lo
leería la pantalla y se comprueba cada comando contra una lista blanca y una negra.
"""

import inspect
import random
import re
from types import SimpleNamespace

import pytest
from PIL import Image

from luzeira.pantallas import apertura, kipye_rev_d, turing_rev_a, xuanfang_rev_b
from luzeira.pantallas.comun import Orientation
from luzeira.pantallas.modelos import KIPYE, REV_A, REV_B, DisplayChoice, list_ports
from luzeira.pantallas.turing_rev_a import Command as RevACommand
from luzeira.pantallas.turing_rev_a import PortMatch, TuringRevA

FORBIDDEN = {0x7D, 0x83, 0x84} | set(range(0x64, 0x79))
REV_B_ALLOWED = {0xCA, 0xCB, 0xCC, 0xCE}
# Lo que la rev A manda de verdad. RESET (101) no está: solo existe en su enum para
# documentar el protocolo (R-02, fase 2).
REV_A_ALLOWED = {
    RevACommand.HELLO,
    RevACommand.CLEAR,
    RevACommand.SCREEN_OFF,
    RevACommand.SCREEN_ON,
    RevACommand.SET_BRIGHTNESS,
    RevACommand.SET_ORIENTATION,
    RevACommand.DISPLAY_BITMAP,
}
KIPYE_ALLOWED = {
    bytes.fromhex("43 48 00 00"),
    bytes.fromhex("43 47 00 00"),
    bytes.fromhex("44 00 00 00"),
    bytes.fromhex("41 00 00 00"),
}


class Recorder:
    def __init__(self, answer: bytes = b"") -> None:
        self.writes: list[bytes] = []
        self.answer = answer

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size=1):
        return self.answer[:size]

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def exercise(display) -> None:
    """Todo lo que el servicio usa (y algo más): brillos, giros, imágenes de mil tamaños."""
    rng = random.Random(5)
    display.screen_on()
    for level in range(0, 101, 7):
        display.set_brightness(level)
    for orientation in Orientation:
        display.set_orientation(orientation)
        display.draw_image(Image.new("RGB", (display.width, display.height), "white"))
        for _ in range(25):
            x, y = rng.randrange(display.width), rng.randrange(display.height)
            w, h = rng.randrange(1, 200), rng.randrange(1, 200)
            # Colores al azar: los píxeles pueden valer cualquier byte, también 0x7D.
            color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            display.draw_image(Image.new("RGB", (w, h), color), x, y)
    display.screen_off()


def rev_b_commands(writes: list[bytes]) -> list[bytes]:
    """Separa comandos de píxeles como la pantalla: tras un 0xCC vienen (x1-x0+1)·(y1-y0+1)·2
    bytes de imagen; lo demás tienen que ser paquetes de 10 bytes."""
    commands, pending = [], 0
    for w in writes:
        if pending:
            assert len(w) <= pending, "un trozo de imagen se sale de su rectángulo"
            pending -= len(w)
            continue
        commands.append(w)
        if w[0] == 0xCC:
            x0, y0, x1, y1 = (int.from_bytes(w[i : i + 2], "big") for i in (1, 3, 5, 7))
            pending = (x1 - x0 + 1) * (y1 - y0 + 1) * 2
    assert pending == 0
    return commands


def test_rev_b_only_ever_sends_its_four_commands():
    port = Recorder(bytes.fromhex("CA 48 45 4C 4C 4F 0A 11 00 CA"))
    display = xuanfang_rev_b.XuanFangRevB(port, sleep=lambda _s: None)
    display.probe()
    exercise(display)
    commands = rev_b_commands(port.writes)
    assert len(commands) > 100
    for c in commands:
        assert len(c) == 10 and c[0] == c[9], c.hex(" ")
        assert c[0] in REV_B_ALLOWED and c[0] not in FORBIDDEN, c.hex(" ")


def test_kipye_only_ever_sends_its_commands():
    port = Recorder()
    display = kipye_rev_d.KipyeRevD(port)
    display.probe()
    exercise(display)
    assert_kipye_safe(port.writes)


def assert_kipye_safe(writes: list[bytes]) -> None:
    in_picture = False
    seen = 0
    for w in writes:
        assert w[0] not in FORBIDDEN, w.hex(" ")
        if w == bytes.fromhex("44 00 00 00"):
            assert not in_picture
            in_picture = True
        elif w == bytes.fromhex("41 00 00 00"):
            assert in_picture
            in_picture = False
        elif in_picture:
            assert w[0] == 0x50 and 2 <= len(w) <= 64, w[:4].hex(" ")  # píxeles
            continue
        elif w[:2] == bytes.fromhex("43 43"):
            assert len(w) == 4 and int.from_bytes(w[2:], "big") <= 500
        elif w[:2] == bytes.fromhex("43 41"):
            assert len(w) == 10
        else:
            assert w in KIPYE_ALLOWED, w.hex(" ")
        seen += 1
    assert not in_picture and seen > 100


def rev_a_commands(writes: list[bytes]) -> list[int]:
    """Lee el flujo de la rev A como la pantalla, sin fiarse de cómo se trocearon las
    escrituras (una escritura de 12 bytes con dos comandos se lee como dos): tramas de 6
    bytes con el código en el sexto; tras un DISPLAY_BITMAP (0xC5) vienen
    (ex-x+1)·(ey-y+1)·2 bytes de píxeles que se saltan; la orientación (0x79) lleva 10
    bytes más (trama de 16)."""
    stream = b"".join(writes)
    commands, i = [], 0
    while i < len(stream):
        frame = stream[i : i + 6]
        assert len(frame) == 6, f"trama cortada al final: {frame.hex(' ')}"
        i += 6
        command = frame[5]
        commands.append(command)
        if command == RevACommand.DISPLAY_BITMAP:
            x = frame[0] << 2 | frame[1] >> 6
            y = (frame[1] & 0x3F) << 4 | frame[2] >> 4
            ex = (frame[2] & 0x0F) << 6 | frame[3] >> 2
            ey = (frame[3] & 0x03) << 8 | frame[4]
            pixels = (ex - x + 1) * (ey - y + 1) * 2
            assert i + pixels <= len(stream), "faltan píxeles de un rectángulo"
            i += pixels
        elif command == RevACommand.SET_ORIENTATION:
            i += 10
    return commands


def assert_rev_a_safe(writes: list[bytes]) -> None:
    commands = rev_a_commands(writes)
    assert RevACommand.RESET not in commands
    assert set(commands) <= REV_A_ALLOWED, sorted(set(commands) - REV_A_ALLOWED)


def com(device, vid=None, pid=None, serial=None):
    """Un puerto como los que da ``comports()`` de pyserial (sin abrir nada)."""
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial, description="")


def choice(model, port):
    return DisplayChoice.for_port(model, list_ports([port])[0])


class Factory:
    """``serial_factory`` falso: cada apertura da un ``Recorder`` nuevo."""

    def __init__(self, answer: bytes = b"") -> None:
        self.answer = answer
        self.ports: list[Recorder] = []

    def __call__(self, _device, *_args, **_kwargs) -> Recorder:
        self.ports.append(Recorder(self.answer))
        return self.ports[-1]

    @property
    def writes(self) -> list[bytes]:
        return [w for port in self.ports for w in port.writes]


def test_rev_a_never_sends_reset_even_when_used_hard():
    # La rev A no es beta y sus comandos normales caen en el rango de bytes de la rev C,
    # así que aquí se vigila lo suyo: ningún RESET en todo el flujo, leído como la pantalla.
    port = Recorder(bytes([1] * 6))
    display = TuringRevA(port)
    display.hello()
    exercise(display)
    display.clear()
    assert len(rev_a_commands(port.writes)) > 100
    assert_rev_a_safe(port.writes)


def test_rev_a_stream_reader_catches_a_reset_hidden_in_a_bigger_write():
    # El lector no se fía de los trozos: un RESET pegado a otro comando se ve igual.
    two = turing_rev_a.pack_command(RevACommand.SCREEN_ON) + turing_rev_a.pack_command(101)
    assert rev_a_commands([two]) == [RevACommand.SCREEN_ON, 101]
    with pytest.raises(AssertionError):
        assert_rev_a_safe([two])


@pytest.mark.parametrize("answer", [b"", bytes([1] * 6)])
def test_rev_a_opening_paths_never_send_reset(monkeypatch, answer):
    """Las formas de abrir la rev A (con puerto, sola y la elegida en el menú), de
    principio a fin con un puerto falso: lo que se manda al abrir también cuenta."""
    import serial

    factory = Factory(answer)
    found = PortMatch("COM3", by_serial=True)
    monkeypatch.setattr(turing_rev_a, "find_port", lambda *_a: found)
    monkeypatch.setattr(serial, "Serial", factory)  # la que usa ``open`` sin fábrica
    rev_a_com3 = com("COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")
    displays = [
        TuringRevA.open("COM3", retries=1, serial_factory=factory),
        TuringRevA.open(retries=1),
        apertura.open_auto([rev_a_com3]),
        apertura.open_chosen(choice(REV_A, rev_a_com3), [rev_a_com3]),
    ]
    for display in displays:
        exercise(display)
        display.clear()
        display.close()
    assert len(factory.ports) == 4
    assert_rev_a_safe(factory.writes)


def test_beta_opening_paths_send_only_their_commands():
    """``open`` (con ``open_probed``) y la apertura elegida, de principio a fin."""
    rev_b_port = com("COM6", 0x1A86, 0x5722, "2017-2-25")
    rev_b = Factory(bytes.fromhex("CA 48 45 4C 4C 4F 0A 11 00 CA"))
    for display in (
        xuanfang_rev_b.XuanFangRevB.open("COM6", serial_factory=rev_b, sleep=lambda _s: None),
        apertura.open_chosen(
            choice(REV_B, rev_b_port),
            [rev_b_port],
            serial_factory=rev_b,
            sleep=lambda _s: None,
        ),
    ):
        exercise(display)
    commands = rev_b_commands(rev_b.writes)
    assert len(rev_b.ports) == 2 and len(commands) > 200
    for c in commands:
        assert len(c) == 10 and c[0] == c[9] and c[0] in REV_B_ALLOWED, c.hex(" ")

    kipye_port = com("COM8", 0x454D, 0x4E41)
    kipye = Factory()
    for display in (
        kipye_rev_d.KipyeRevD.open("COM8", serial_factory=kipye),
        apertura.open_chosen(choice(KIPYE, kipye_port), [kipye_port], serial_factory=kipye),
    ):
        exercise(display)
    assert len(kipye.ports) == 2
    assert_kipye_safe(kipye.writes)


@pytest.mark.parametrize("module", [xuanfang_rev_b, kipye_rev_d])
def test_beta_drivers_do_not_even_contain_dangerous_commands(module):
    """Red de seguridad que lee el fuente. ⚠ No puede ser completa: un ``125`` decimal no
    se distingue de un número cualquiera (y 100-120, los de ficheros de la rev C, salen en
    cualquier cálculo de porcentajes). **El que protege de verdad es el de
    comportamiento** (los ``*_only_ever_sends_*`` y ``*_opening_paths_*``): lee lo que
    sale por el puerto como lo leería la pantalla. Este solo caza lo evidente."""
    source = inspect.getsource(module)
    # Ningún nombre así, en mayúsculas o no (``def reset()``, ``Restart``…).
    # ``reset_input_buffer`` de pyserial y ``_reset_shadow`` no cuentan (el ``_`` pegado
    # no deja límite de palabra): vacían cosas del PC, no mandan nada a la pantalla.
    for name in ("RESET", "RESTART", "TURNOFF", "OPTIONS", "REBOOT", "FLASH", "UPLOAD"):
        assert not re.search(rf"(?i)\b{name}\b", source), name
    # Ningún literal de la lista negra (los de la rev C): enteros hexadecimales de
    # cualquier ancho (0x7D, 0x007D), escapes de bytes en cualquier posición y con
    # cualquier comilla (b"\x43\x7d", b'\x7d') y cadenas de ``bytes.fromhex``.
    literals = {int(h, 16) for h in re.findall(r"\b0[xX]([0-9a-fA-F]+)\b", source)}
    literals |= {int(h, 16) for h in re.findall(r"\\x([0-9a-fA-F]{2})", source)}
    for text in re.findall(r"fromhex\(\s*[\"']([^\"']*)[\"']", source):
        literals |= set(bytes.fromhex(text))
    assert {0x43, 0x44, 0x41} <= literals or module is xuanfang_rev_b  # el regex funciona
    assert not literals & FORBIDDEN, sorted(hex(v) for v in literals & FORBIDDEN)


@pytest.mark.parametrize(
    "snippet",
    [
        "X = 0x007D",
        "X = b'\\x43\\x7d\\x00'",
        'X = b"\\x00\\x84"',
        "X = bytes.fromhex('43 83 00 00')",
        "def reset(self): ...",
        "Restart = 1",
    ],
)
def test_source_guard_catches_the_obvious_disguises(monkeypatch, snippet):
    # El guardián del fuente, probado al revés: con cada disfraz pegado al fuente, cae.
    real = inspect.getsource(kipye_rev_d)
    monkeypatch.setattr(inspect, "getsource", lambda _m: real + "\n" + snippet + "\n")
    with pytest.raises(AssertionError):
        test_beta_drivers_do_not_even_contain_dangerous_commands(kipye_rev_d)


def test_rev_b_command_enum_is_the_allowlist():
    assert {c.value for c in xuanfang_rev_b.Command} == REV_B_ALLOWED


def test_kipye_command_constants_are_the_allowlist():
    constants = {
        v
        for k, v in vars(kipye_rev_d).items()
        if k.isupper() and isinstance(v, bytes) and len(v) == 4
    }
    assert constants == KIPYE_ALLOWED


def test_rev_a_module_keeps_reset_only_as_documentation():
    # R-02 (fase 2): RESET existe en el enum de la rev A solo para documentar el protocolo.
    assert "Command.RESET" not in inspect.getsource(turing_rev_a.TuringRevA)
