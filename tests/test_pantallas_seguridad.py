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

from luzeira.pantallas import apertura, comun, kipye_rev_d, turing_rev_a, xuanfang_rev_b
from luzeira.pantallas.comun import DisplayError, ImageCut, Orientation
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
    return kipye_rev_d.KipyeRevD(port, **kwargs)


@pytest.mark.parametrize("name", ["rev_a", "rev_b", "kipye"])
def test_short_write_is_an_error_and_nothing_more_is_sent(name):
    # R5-03: escritura parcial sin excepción. Antes se seguía mandando y los bytes de
    # después se leían desalineados (píxeles tomados por órdenes).
    port = ShortRecorder(short_at=3)  # la tercera: ya dentro de la imagen en las tres
    display = make_driver(name, port)
    with pytest.raises(DisplayError):
        display.draw_image(Image.new("RGB", (display.width, display.height), "red"))
    assert len(port.writes) == 3


@pytest.mark.parametrize("name", ["rev_a", "rev_b", "kipye"])
def test_short_command_write_is_an_error(name):
    with pytest.raises(DisplayError):
        make_driver(name, ShortRecorder(short_at=1)).set_brightness(50)


@pytest.mark.parametrize("name", ["rev_b", "kipye"])
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
    ("name", "error"), [("rev_a", DisplayError), ("rev_b", ImageCut), ("kipye", ImageCut)]
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


@pytest.mark.parametrize("name", ["rev_a", "rev_b", "kipye"])
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
