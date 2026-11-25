"""🔴 C-44: los drivers beta no mandan nunca comandos peligrosos, ni los llevan dentro.

Peligrosos (informe ``docs/investigacion/tanda-4/otras-pantallas/informe.md``): reinicio,
escribir en el almacenamiento del panel y, de la rev C, ``OPTIONS`` 0x7D (guarda ajustes),
``RESTART`` 0x84, ``TURNOFF`` 0x83 y los de ficheros 0x64-0x78. Se ejercita todo lo que el
servicio puede pedir a cada driver con un puerto falso, se lee el flujo de bytes como lo
leería la pantalla y se comprueba cada comando contra una lista blanca y una negra.

La rev C (beta desde la fase 5) es la que tiene esas órdenes: su guardián lee el flujo de
250 en 250 como la pantalla y mira la lista negra en **cada** frontera de bloque, también
dentro de los píxeles (R5-02).
"""

import inspect
import random
import re
from types import SimpleNamespace

import pytest
from PIL import Image

from luzeira.pantallas import (
    apertura,
    comun,
    kipye_rev_d,
    turing_rev_a,
    turing_rev_c,
    weact,
    xuanfang_rev_b,
)
from luzeira.pantallas.comun import DisplayError, ImageCut, Orientation
from luzeira.pantallas.modelos import (
    KIPYE,
    REV_A,
    REV_B,
    REV_C_5,
    REV_C_21,
    REV_C_88,
    WEACT_096,
    WEACT_35,
    DisplayChoice,
    list_ports,
)
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


def assert_kipye_safe(writes: list[bytes], *, min_commands: int = 100) -> None:
    """Lee el flujo de la Kipye como la pantalla. Los píxeles de cada imagen tienen que ser
    justo los de la última ventana (43 41: x0, x1, y0, y1 inclusivas): si sobraran, la
    pantalla tomaría los de más por órdenes (revisor R5-10: antes no se cruzaban)."""
    in_picture = False
    seen = 0
    window: int | None = None  # bytes de píxeles que pide la última ventana
    pixels = 0
    for w in writes:
        assert w[0] not in FORBIDDEN, w.hex(" ")
        if w == bytes.fromhex("44 00 00 00"):
            assert not in_picture and window is not None, "imagen sin ventana"
            in_picture, pixels = True, 0
        elif w == bytes.fromhex("41 00 00 00"):
            assert in_picture
            assert pixels == window, f"{pixels} bytes de píxeles para una ventana de {window}"
            in_picture, window = False, None
        elif in_picture:
            assert w[0] == 0x50 and 2 <= len(w) <= 64, w[:4].hex(" ")  # píxeles
            pixels += len(w) - 1
            continue
        elif w[:2] == bytes.fromhex("43 43"):
            assert len(w) == 4 and int.from_bytes(w[2:], "big") <= 500
        elif w[:2] == bytes.fromhex("43 41"):
            assert len(w) == 10
            x0, x1, y0, y1 = (int.from_bytes(w[i : i + 2], "big") for i in (2, 4, 6, 8))
            assert 0 <= x0 <= x1 < 320 and 0 <= y0 <= y1 < 480, w.hex(" ")
            window = (x1 - x0 + 1) * (y1 - y0 + 1) * 2
        else:
            assert w in KIPYE_ALLOWED, w.hex(" ")
        seen += 1
    assert not in_picture and seen > min_commands


def test_kipye_reader_catches_pixels_that_do_not_match_the_window():
    # El lector, probado al revés: un paquete con píxeles de más cae.
    window = bytes.fromhex("43 41 00 00 00 00 00 00 00 00")  # 1 píxel = 2 bytes
    start, end = bytes.fromhex("44 00 00 00"), bytes.fromhex("41 00 00 00")
    assert_kipye_safe([window, start, bytes.fromhex("50 12 34"), end], min_commands=2)
    with pytest.raises(AssertionError):
        too_many = bytes.fromhex("50 12 34 56 78")
        assert_kipye_safe([window, start, too_many, end], min_commands=2)


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


@pytest.mark.parametrize("hello", [0x01, 0x02, 0x03])  # 3,5", 5" y 7" (R5-10)
def test_rev_a_never_sends_reset_even_when_used_hard(hello):
    # La rev A no es beta y sus comandos normales caen en el rango de bytes de la rev C,
    # así que aquí se vigila lo suyo: ningún RESET en todo el flujo, leído como la pantalla.
    # Con los tres tamaños: en la de 7" las coordenadas de 10 bits llegan a 1023. Se abre
    # con ``open`` (que es quien aplica el modelo del saludo): ``hello()`` solo lo devuelve,
    # y por eso este test con 0x01 siempre ejercitaba el tamaño de 3,5".
    port = Recorder(bytes([hello] * 6))
    display = TuringRevA.open("COM3", retries=1, serial_factory=lambda _d: port)
    assert display.model.width == {1: 320, 2: 480, 3: 600}[hello]
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


# --- rev C -----------------------------------------------------------------------------

# Lo que la rev C manda (``turing_rev_c.Command``): saludo, brillo, parar vídeo y medios
# (como la app y la referencia al empezar), aviso, inicio y cabecera de imagen completa,
# cabecera de parcial y estado.
REV_C_ALLOWED = {0x01, 0x7B, 0x79, 0x96, 0x86, 0x2C, 0xC8, 0xCC, 0xCF}
ORDER_TAIL = bytes.fromhex("EF 69 00 00 00")  # lo que sigue al código en toda orden corta
REV_C_HELLO_ANSWER = b"chs_5inch.dev1_rom1.89"


def rev_c_orders(writes: list[bytes]) -> list[int]:
    """Lee el flujo de la rev C como la pantalla: de 250 en 250 (#90 de TSP).

    - Dos barridos. El primero no se fía de nada: en **cada** frontera de 250 bytes del
      flujo, nada con la forma de una orden prohibida (``[prohibida] EF 69 00 00 00``),
      sea orden o píxeles.
    - El segundo separa órdenes y datos: tras una cabecera de imagen completa (``C8``,
      tamaño en bloques de 256 bytes) o parcial (``CC``, tamaño en 3 bytes, que cuenta la
      cola ``EF 69``) vienen esos datos con un ``00`` cada 249; ningún bloque de datos puede
      empezar como una orden, la que sea, y su byte 250 (que no se lee) va a cero.
    - Cada mensaje (orden o datos) empieza al principio de una escritura, y cada escritura
      son bloques enteros: nada de órdenes partidas ni dos órdenes en una escritura.
    """
    starts, position = set(), 0
    for w in writes:
        assert len(w) % 250 == 0, f"escritura de {len(w)} bytes: no son bloques enteros"
        starts.add(position)
        position += len(w)
    stream = b"".join(writes)
    for k in range(0, len(stream), 250):
        if stream[k + 1 : k + 6] == ORDER_TAIL:
            assert stream[k] not in FORBIDDEN, f"orden prohibida {stream[k]:#04x} en el byte {k}"
    orders, i = [], 0
    while i < len(stream):
        assert i in starts, f"una orden empieza a mitad de una escritura (byte {i})"
        block = stream[i : i + 250]
        code = block[0]
        assert code in REV_C_ALLOWED and code not in FORBIDDEN, block[:8].hex(" ")
        orders.append(code)
        i += 250
        if code == 0x2C:
            assert block == bytes([0x2C]) * 250
            continue
        if code == 0xC8:
            assert block[1:4] == bytes.fromhex("EF 69 00"), block[:8].hex(" ")
            data, tail = int.from_bytes(block[4:6], "big") * 256, 0
        elif code == 0xCC:
            assert block[1:4] == bytes.fromhex("EF 69 00"), block[:8].hex(" ")
            data, tail = int.from_bytes(block[4:7], "big") - 2, 2
        else:
            assert block[1:6] == ORDER_TAIL, block[:8].hex(" ")
            continue
        assert i in starts, "los datos no empiezan en su propia escritura"
        for _ in range(-(-(data + (data - 1) // 249 + tail) // 250)):
            data_block = stream[i : i + 250]
            assert len(data_block) == 250, "faltan datos de una imagen"
            assert data_block[1:6] != ORDER_TAIL, f"un bloque de datos parece una orden ({i})"
            assert data_block[249] == 0, "dato en el byte que la pantalla no lee"
            i += 250
    return orders


@pytest.fixture(autouse=True)
def fresh_session():
    """Cada test empieza sin despiertos comprobados en «esta sesión» (apertura)."""
    apertura.AWAKE_THIS_SESSION.clear()
    yield
    apertura.AWAKE_THIS_SESSION.clear()


def rev_c_display(size, answer=REV_C_HELLO_ANSWER):
    port = Recorder(answer)
    display = turing_rev_c.TuringRevC(port, size, sleep=lambda _s: None)
    display.probe()
    return display, port


REV_C_SIZES = [turing_rev_c.SIZE_2_1, turing_rev_c.SIZE_5, turing_rev_c.SIZE_8_8]


@pytest.mark.parametrize("size", REV_C_SIZES)
@pytest.mark.parametrize("answer", [REV_C_HELLO_ANSWER, b"chs_5inch.dev1_rom1.88"])
def test_rev_c_only_ever_sends_its_commands(size, answer):
    display, port = rev_c_display(size, answer)
    exercise(display)
    orders = rev_c_orders(port.writes)
    assert orders[0] == 0x01 and len(orders) > 200
    assert set(orders) == REV_C_ALLOWED  # y todas las que tiene se han ejercitado


def test_rev_c_hello_goes_before_anything_else():
    with pytest.raises(comun.NoAnswer):
        rev_c_display(turing_rev_c.SIZE_5, b"")  # no contesta: solo saludos, con tope
    port = Recorder(b"no soy una rev C")
    display = turing_rev_c.TuringRevC(port, turing_rev_c.SIZE_5, sleep=lambda _s: None)
    with pytest.raises(comun.WrongModel):
        display.probe()
    assert rev_c_orders(port.writes) == [0x01]


def rev_c_crafted_box() -> Image.Image:
    """Píxeles BGR que, al principio del segundo bloque de una parcial, dirían
    «66 EF 69 00 00 00»: la orden que borra ficheros (#90 de TSP) con su firma."""
    box = Image.new("RGB", (100, 1), (1, 1, 1))
    box.putpixel((81, 0), (0xEF, 0x66, 0x01))
    box.putpixel((82, 0), (0x00, 0x00, 0x69))
    box.putpixel((83, 0), (0x01, 0x01, 0x00))
    return box


def rev_c_draw_crafted() -> Recorder:
    display, port = rev_c_display(turing_rev_c.SIZE_2_1)  # la de 2,1": siempre BGR
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (480, 480)))
    display.draw_image(rev_c_crafted_box(), 0, 0)
    return port


def test_rev_c_defuses_pixels_that_spell_a_forbidden_order(monkeypatch):
    rev_c_orders(rev_c_draw_crafted().writes)

    # Al revés: sin el arreglo, el mismo tema mete la orden en el flujo y el guardián cae.
    def no_defusing(raw, _is_colour, *, trailer=b""):
        return turing_rev_c.padded(turing_rev_c.cut(bytes(raw)) + trailer)

    monkeypatch.setattr(turing_rev_c, "defuse", no_defusing)
    with pytest.raises(AssertionError, match="0x66"):
        rev_c_orders(rev_c_draw_crafted().writes)


def test_rev_c_reader_catches_what_it_must():
    hello = turing_rev_c.padded(bytes.fromhex("01 EF 69 00 00 00 01 00 00 00 C5 D3"))
    assert rev_c_orders([hello]) == [0x01]
    for bad in (
        [turing_rev_c.padded(bytes.fromhex("7D EF 69 00 00 00 05 00 00 00 2D"))],  # ajustes
        [turing_rev_c.padded(bytes.fromhex("84 EF 69 00 00 00 01"))],  # reiniciar
        [turing_rev_c.padded(bytes.fromhex("83 EF 69 00 00 00 01"))],  # apagar
        [turing_rev_c.padded(bytes.fromhex("66 EF 69 00 00 00 14"))],  # borrar
        [turing_rev_c.padded(bytes.fromhex("CA EF 69 00 17 70"))],  # no está en la lista
        [hello[:200]],  # bloque a medias
        [hello + hello],  # dos órdenes en una escritura
    ):
        with pytest.raises(AssertionError):
            rev_c_orders(bad)
    # Una parcial de 300 bytes (+2 de cola) cuyo segundo bloque parece una orden permitida.
    head = turing_rev_c.padded(bytes.fromhex("CC EF 69 00 00 01 2E 00 00 00 00 00 00 00"))
    data = bytes(250) + bytes.fromhex("01 EF 69 00 00 00") + bytes(244)
    with pytest.raises(AssertionError, match="parece una orden"):
        rev_c_orders([head, data])


@pytest.mark.parametrize("model", [REV_C_21, REV_C_5, REV_C_88])
def test_rev_c_opening_path_wakes_without_writing_and_sends_only_its_commands(model):
    """De principio a fin: despertar el puente (sin escribirle) y la pantalla despierta."""
    bridge = com("COM6", 0x1A86, 0xCA21, "CT21INCH")
    awake = com("COM7", 0x1D6B, 0x0106, "20080411")
    listed = [bridge]
    factory = Factory(REV_C_HELLO_ANSWER)

    def opening(device, *args, **kwargs):
        if device == "COM6":
            listed.append(awake)  # al abrir el puente aparece el despierto
        return factory(device, *args, **kwargs)

    display = apertura.open_chosen(
        choice(model, bridge),
        [bridge],
        serial_factory=opening,
        sleep=lambda _s: None,
        relist=lambda: listed,
    )
    exercise(display)
    bridge_port, awake_port = factory.ports
    assert bridge_port.writes == []
    rev_c_orders(awake_port.writes)


def test_rev_c_driver_does_not_even_contain_dangerous_commands():
    """Como el de las otras betas, para la rev C. Su firma ``EF 69`` lleva el byte 0x69
    (dentro del rango de los de ficheros): se quita esa línea antes de buscar."""
    source = inspect.getsource(turing_rev_c)
    for name in ("RESET", "RESTART", "TURNOFF", "TURNON", "OPTIONS", "REBOOT", "FLASH", "UPLOAD"):
        assert not re.search(rf"(?i)\b{name}\b", source), name
    mark = 'MARK = b"\\xef\\x69"'
    assert source.count(mark) == 1
    source = source.replace(mark, "")
    literals = {int(h, 16) for h in re.findall(r"\b0[xX]([0-9a-fA-F]+)\b", source)}
    literals |= {int(h, 16) for h in re.findall(r"\\x([0-9a-fA-F]{2})", source)}
    for text in re.findall(r"fromhex\(\s*[\"']([^\"']*)[\"']", source):
        literals |= set(bytes.fromhex(text))
    assert {0x7B, 0xCF, 0xC5} <= literals  # el regex funciona
    assert not literals & FORBIDDEN, sorted(hex(v) for v in literals & FORBIDDEN)


def test_rev_c_command_enum_is_the_allowlist():
    assert {c.value for c in turing_rev_c.Command} == REV_C_ALLOWED
    assert not REV_C_ALLOWED & FORBIDDEN


# --- WeAct -----------------------------------------------------------------------------

# Lo que la WeAct manda (``weact.Command``): orientación, brillo, imagen y lectura de
# versión. Nunca 0x04 (rellenar), 0x06 (informes del sensor), 0x07 («liberar», sin
# significado conocido), 0x15 (imagen comprimida) ni 0x81 («quién eres»): informe de la
# tanda 11 y Libre Panel (``docs/protocol/serial.md``).
WEACT_ALLOWED = {0x02, 0x03, 0x05, 0xC2}
WEACT_NEVER = {0x04, 0x06, 0x07, 0x15, 0x81}
WEACT_LENGTHS = {0x02: 3, 0x03: 5, 0x05: 10, 0xC2: 2}
WEACT_ANSWER = b"\xc2" + b"V1.0.0.0" + bytes(10)


def weact_commands(writes: list[bytes], panel) -> list[int]:
    """Lee el flujo de la WeAct como la pantalla. Este protocolo no tiene marco: tras una
    imagen (0x05) vienen (x1-x0+1)·(y1-y0+1)·2 bytes de píxeles y nada los cierra, así que
    la única defensa es contar justo (si sobrara o faltara un byte, lo siguiente se leería
    como orden). Aquí se cuentan, sin fiarse de cómo se trocearon las escrituras, y cada
    orden tiene que empezar en una escritura propia y acabar en 0x0A."""
    starts, position = set(), 0
    for w in writes:
        starts.add(position)
        position += len(w)
    stream = b"".join(writes)
    commands, i, orientation = [], 0, 0
    while i < len(stream):
        assert i in starts, f"una orden empieza a mitad de una escritura (byte {i})"
        code = stream[i]
        assert code in WEACT_ALLOWED and code not in WEACT_NEVER, f"orden {code:#04x}"
        frame = stream[i : i + WEACT_LENGTHS[code]]
        assert len(frame) == WEACT_LENGTHS[code] and frame[-1] == 0x0A, frame.hex(" ")
        commands.append(code)
        i += len(frame)
        if code == 0x02:
            assert frame[1] in range(4)
            orientation = frame[1]
        elif code == 0x03:
            assert int.from_bytes(frame[2:4], "little") == 1000  # el fundido de la referencia
        elif code == 0x05:
            x0, y0, x1, y1 = (int.from_bytes(frame[k : k + 2], "little") for k in (1, 3, 5, 7))
            w, h = (panel.height, panel.width) if orientation >= 2 else (panel.width, panel.height)
            assert 0 <= x0 <= x1 < w and 0 <= y0 <= y1 < h, frame.hex(" ")
            pixels = (x1 - x0 + 1) * (y1 - y0 + 1) * 2
            assert i + pixels <= len(stream), "faltan píxeles de un rectángulo"
            assert i in starts, "los píxeles no empiezan en su propia escritura"
            i += pixels
    return commands


@pytest.mark.parametrize("panel", [weact.PANEL_35, weact.PANEL_096])
def test_weact_only_ever_sends_its_commands(panel):
    port = Recorder(WEACT_ANSWER)
    display = weact.WeAct(port, panel, sleep=lambda _s: None)
    display.probe()
    exercise(display)
    commands = weact_commands(port.writes, panel)
    assert commands[0] == 0xC2 and commands.count(0xC2) == 1 and len(commands) > 100
    assert set(commands) == WEACT_ALLOWED


def test_weact_reader_catches_what_it_must():
    panel = weact.PANEL_096
    box = bytes.fromhex("05 00 00 00 00 00 00 00 00 0A")  # un píxel = 2 bytes
    assert weact_commands([box, bytes(2)], panel) == [0x05]
    for bad in (
        [bytes.fromhex("04 00 00 00 00 4F 00 9F 00 00 00 0A")],  # rellenar
        [bytes.fromhex("06 F4 01 0A")],  # informes del sensor
        [bytes.fromhex("07 0A")],  # liberar
        [bytes.fromhex("81 0A")],  # quién eres
        [box, bytes(3)],  # un píxel de más: lo siguiente se leería como orden
        [box, bytes(1)],  # uno de menos
        [box + bytes(2)],  # cabecera y píxeles en la misma escritura
        [bytes.fromhex("05 00 00 00 00 50 00 00 00 0A")],  # x1 = 80: fuera del 0,96"
        [bytes.fromhex("03 FF E8 03")],  # sin el 0x0A
    ):
        with pytest.raises((AssertionError, KeyError)):
            weact_commands(bad, panel)


def test_weact_opening_paths_send_only_its_commands():
    port_35 = com("COM13", 0x1A86, 0xFE0C, "AB0001")
    port_096 = com("COM14", 0x1A86, 0xFE0C, "AD0001")
    for model, port, panel in ((WEACT_35, port_35, weact.PANEL_35),
                               (WEACT_096, port_096, weact.PANEL_096)):  # fmt: skip
        factory = Factory(WEACT_ANSWER)
        display = apertura.open_chosen(choice(model, port), [port], serial_factory=factory)
        exercise(display)
        assert len(factory.ports) == 1
        weact_commands(factory.writes, panel)


def test_weact_driver_does_not_even_contain_other_commands():
    """Red de seguridad que lee el fuente, como la de las otras betas."""
    source = inspect.getsource(weact)
    for name in ("FULL", "FILL", "FREE", "HUMITURE", "FASTLZ", "WHO_AM_I", "RESET", "FLASH"):
        assert not re.search(rf"(?i)\b{name}\b", source), name
    literals = {int(h, 16) for h in re.findall(r"\b0[xX]([0-9a-fA-F]+)\b", source)}
    literals |= {int(h, 16) for h in re.findall(r"\\x([0-9a-fA-F]{2})", source)}
    for text in re.findall(r"fromhex\(\s*[\"']([^\"']*)[\"']", source):
        literals |= set(bytes.fromhex(text))
    assert {0x02, 0x05, 0x42, 0x0A} <= literals  # el regex funciona
    assert not literals & WEACT_NEVER, sorted(hex(v) for v in literals & WEACT_NEVER)


def test_weact_command_enum_is_the_allowlist():
    assert {c.value for c in weact.Command} == WEACT_ALLOWED


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


# --- R5: un USB defectuoso o malicioso -------------------------------------------------


class ShortRecorder(Recorder):
    """Puerto que en la escritura número ``short_at`` devuelve un byte menos de los pedidos,
    sin excepción: lo que hace pyserial en Windows si se cancela la escritura a medias.
    (Revisor R5-10: el ``Recorder`` siempre devolvía ``len(data)``.)"""

    def __init__(self, answer: bytes = b"", short_at: int = 1) -> None:
        super().__init__(answer)
        self.short_at = short_at

    def write(self, data):
        super().write(data)
        return len(data) - 1 if len(self.writes) == self.short_at else len(data)


def make_driver(name: str, port: Recorder, **kwargs):
    if name == "rev_a":
        return TuringRevA(port, **kwargs)
    if name == "rev_b":
        return xuanfang_rev_b.XuanFangRevB(port, sleep=lambda _s: None, **kwargs)
    if name == "rev_c":
        return turing_rev_c.TuringRevC(port, turing_rev_c.SIZE_5, sleep=lambda _s: None, **kwargs)
    if name == "weact":
        return weact.WeAct(port, weact.PANEL_35, sleep=lambda _s: None, **kwargs)
    return kipye_rev_d.KipyeRevD(port, **kwargs)


@pytest.mark.parametrize("name", ["rev_a", "rev_b", "kipye", "rev_c", "weact"])
def test_short_write_is_an_error_and_nothing_more_is_sent(name):
    # R5-03: escritura parcial sin excepción. Antes se seguía mandando y los bytes de
    # después se leían desalineados (píxeles tomados por órdenes).
    port = ShortRecorder(short_at=3)  # la tercera: ya dentro de la imagen en las tres
    display = make_driver(name, port)
    with pytest.raises(DisplayError):
        display.draw_image(Image.new("RGB", (display.width, display.height), "red"))
    assert len(port.writes) == 3


@pytest.mark.parametrize("name", ["rev_a", "rev_b", "kipye", "rev_c", "weact"])
def test_short_command_write_is_an_error(name):
    with pytest.raises(DisplayError):
        make_driver(name, ShortRecorder(short_at=1)).set_brightness(50)


@pytest.mark.parametrize("name", ["rev_b", "kipye", "rev_c", "weact"])
def test_cut_image_on_a_beta_driver_asks_to_replug(name):
    # Rev B y Kipye pueden quedarse esperando el resto de la imagen: el error lo dice
    # (ImageCut) para que el servicio no las salude a ciegas.
    port = ShortRecorder(short_at=4)
    display = make_driver(name, port)
    with pytest.raises(ImageCut, match="Desenchúfala"):
        display.draw_image(Image.new("RGB", (display.width, display.height), "red"))


def test_cut_image_on_rev_a_is_a_plain_connection_error():
    # La rev A se reinicia al abrir el puerto (medido): reabrir ya la deja limpia.
    with pytest.raises(DisplayError) as info:
        TuringRevA(ShortRecorder(short_at=4)).draw_image(Image.new("RGB", (320, 480), "red"))
    assert not isinstance(info.value, ImageCut)


class SlowClock:
    """Reloj falso que avanza ``step`` segundos cada vez que se mira."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.mark.parametrize(
    ("name", "error"),
    [("rev_a", DisplayError), ("rev_b", ImageCut), ("kipye", ImageCut), ("weact", ImageCut)],
)
def test_image_has_an_absolute_deadline(name, error):
    # R5-04: cada escritura cabe en su WRITE_TIMEOUT, pero la imagen entera no puede
    # tardar sin fin. Con un reloj que avanza 1 s por mirada, se corta en cuanto pasa el
    # plazo, no tras las 121 (o ~4.900) escrituras de una imagen completa.
    port = Recorder(bytes([1] * 6))
    display = make_driver(name, port, monotonic=SlowClock(1.0))
    with pytest.raises(error):
        display.draw_image(Image.new("RGB", (display.width, display.height), "red"))
    limit = kipye_rev_d.IMAGE_TIMEOUT if name == "kipye" else comun.IMAGE_TIMEOUT
    assert len(port.writes) <= limit + 3


@pytest.mark.parametrize("name", ["rev_a", "rev_b", "kipye", "rev_c", "weact"])
def test_deadline_does_not_cut_a_normal_image(name):
    port = Recorder(bytes([1] * 6))
    display = make_driver(name, port, monotonic=SlowClock(0.001))
    display.draw_image(Image.new("RGB", (display.width, display.height), "red"))


def test_deadline_does_not_change_rev_a_bytes():
    # El plazo solo corta antes: con tiempo de sobra, los mismos bytes que con el reloj
    # parado (lo que hacía el driver antes de tener plazo).
    ticking, stopped = Recorder(bytes([1] * 6)), Recorder(bytes([1] * 6))
    for port, clock in ((ticking, SlowClock(0.001)), (stopped, SlowClock(0.0))):
        display = TuringRevA(port, monotonic=clock)
        display.hello()
        exercise(display)
        display.clear()
    assert ticking.writes == stopped.writes and len(ticking.writes) > 1000


def test_kipye_flush_error_is_a_display_error_not_a_crash():
    # R5-05: vaciar la entrada fuera del try dejaba salir SerialException (un OSError) y
    # mataba el hilo del servicio, que solo atiende DisplayError.
    import serial

    class FlushFails(Recorder):
        def reset_input_buffer(self):
            raise serial.SerialException("PurgeComm failed")

    display = kipye_rev_d.KipyeRevD(FlushFails())
    with pytest.raises(DisplayError):
        display.set_brightness(50)
    with pytest.raises(DisplayError):
        display.draw_image(Image.new("RGB", (10, 10)))


def test_tests_cannot_open_a_real_serial_port(no_real_serial_ports):
    # El guardián de ``conftest.py``: ni ``serial.Serial`` ni ``serial_for_url`` ni la
    # fábrica por defecto de los drivers abren un puerto real, y la lista de puertos del
    # equipo sale vacía.
    import serial
    from serial.tools import list_ports

    from luzeira.pantallas.comun import default_serial_factory

    for opener in (serial.Serial, serial.serial_for_url, default_serial_factory):
        with pytest.raises(AssertionError, match="puerto real"):
            opener("COM3")
    with pytest.raises(AssertionError, match="puerto real"):
        TuringRevA.open("COM3", retries=1)  # su fábrica por defecto: pyserial de verdad
    assert list_ports.comports() == []
    assert len(no_real_serial_ports) == 4
    no_real_serial_ports.clear()  # los intentos de este test eran a propósito
