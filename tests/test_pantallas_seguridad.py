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

import pytest
from PIL import Image

from luzeira.pantallas import kipye_rev_d, turing_rev_a, xuanfang_rev_b
from luzeira.pantallas.comun import Orientation
from luzeira.pantallas.turing_rev_a import Command as RevACommand
from luzeira.pantallas.turing_rev_a import TuringRevA

FORBIDDEN = {0x7D, 0x83, 0x84} | set(range(0x64, 0x79))
REV_B_ALLOWED = {0xCA, 0xCB, 0xCC, 0xCE}
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
    in_picture = False
    seen = 0
    for w in port.writes:
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


def test_rev_a_never_sends_reset_even_when_used_hard():
    # La rev A no es beta y sus comandos normales caen en el rango de bytes de la rev C,
    # así que aquí se vigila lo suyo: ningún comando de 6 bytes con el código de RESET.
    port = Recorder(bytes([1] * 6))
    display = TuringRevA(port)
    display.hello()
    exercise(display)
    display.clear()
    for w in port.writes:
        if len(w) in (6, 16):  # comandos (la orientación es una trama de 16)
            assert w[5] != RevACommand.RESET, w.hex(" ")


@pytest.mark.parametrize("module", [xuanfang_rev_b, kipye_rev_d])
def test_beta_drivers_do_not_even_contain_dangerous_commands(module):
    source = inspect.getsource(module)
    # Ninguna constante con esos nombres (``reset_input_buffer`` de pyserial no cuenta:
    # vacía la entrada del puerto del PC, no manda nada a la pantalla).
    for name in ("RESET", "RESTART", "TURNOFF", "OPTIONS", "REBOOT", "FLASH", "UPLOAD"):
        assert not re.search(rf"\b{name}\b", source), name
    # Ningún literal hexadecimal de la lista negra (los de la rev C), ni como entero ni
    # como primer byte de una orden escrita con bytes (b"\x43\x48…").
    literals = {int(h, 16) for h in re.findall(r"0x([0-9a-fA-F]{2})\b", source)}
    literals |= {int(h, 16) for h in re.findall(r'b"\\x([0-9a-fA-F]{2})', source)}
    assert {0x43, 0x44, 0x41} <= literals or module is xuanfang_rev_b  # el regex funciona
    assert not literals & FORBIDDEN, sorted(hex(v) for v in literals & FORBIDDEN)


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
