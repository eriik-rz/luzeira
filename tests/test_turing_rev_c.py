"""Driver beta de la Turing rev C con una pantalla simulada.

Los bytes esperados se han escrito a mano desde el informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (sección «Protocolo rev C») y su
contraste con la fuente (``lcd_comm_rev_c.py`` de turing-smart-screen-python y los issues
#90, #1030, #1075 de ese proyecto; ver el docstring del driver). La pantalla simulada lee
lo que le llega de 250 en 250, como la de verdad, contesta lo que contestaría y guarda la
imagen en su memoria, para comprobar dónde acaba cada píxel.
"""

import random
from types import SimpleNamespace

import pytest
from PIL import Image

from luzeira.pantallas import apertura, turing_rev_c
from luzeira.pantallas.comun import (
    ChosenDisplayMissing,
    DisplayBusy,
    DisplayError,
    ImageCut,
    ModelToChoose,
    ModelToConfirm,
    NoAnswer,
    Orientation,
    UnsupportedModel,
    WrongModel,
)
from luzeira.pantallas.modelos import (
    REV_A,
    REV_C_5,
    REV_C_21,
    REV_C_88,
    REV_C_SIZES,
    DisplayChoice,
    PortInfo,
    compatible_models,
    list_ports,
    match_choice,
)
from luzeira.pantallas.turing_rev_c import SIZE_2_1, SIZE_5, SIZE_8_8, TuringRevC, parse_hello

HELLO = bytes.fromhex("01 EF 69 00 00 00 01 00 00 00 C5 D3")  # informe, rev C
ANSWER_88 = b"chs_5inch.dev1_rom1.88"  # #1075 y #1022 (5", Linux)
ANSWER_89 = b"chs_5inch.dev1_rom1.89"  # #1030 (5", captura del programa oficial en Windows)


def pad(data: bytes, fill: int = 0) -> bytes:
    return data + bytes([fill]) * (-len(data) % 250)


def reply(text: bytes) -> bytes:
    """Las respuestas de la pantalla vienen rellenadas a 1024 bytes (#90)."""
    return text.ljust(1024, b"\0")


def uncut(data: bytes) -> bytes:
    """Lo contrario del troceado: el byte 250 de cada bloque no se lee (#90)."""
    return b"".join(data[i : i + 249] for i in range(0, len(data), 250))


def blocks_for(length: int, tail: int = 0) -> int:
    """Bloques que ocupan ``length`` bytes de datos con un 00 cada 249, y ``tail`` más."""
    cut = length + (length - 1) // 249 if length else 0
    return -(-(cut + tail) // 250)


def rom_of(answer: bytes) -> int:
    """La ROM como la saca la referencia: tercer campo tras los puntos, 80-100 o 87."""
    try:
        rom = int(answer.split(b".")[2])
    except (IndexError, ValueError):
        return 87
    return rom if 80 <= rom <= 100 else 87


class FakeRevC:
    """Una rev C despierta, simulada. Lee de 250 en 250; tras una cabecera de imagen
    (``C8``/``CC``) toma como datos los bloques que la cabecera anuncia."""

    def __init__(self, size, answer=ANSWER_88 + b"\n", *, need_resend_after=None):
        self.size = size
        self.answer = answer
        self.rom = rom_of(answer)
        self.writes: list[bytes] = []
        self.orders: list[int] = []  # primer byte de cada orden recibida
        self.out = bytearray()
        self.pending = b""
        self.expect: tuple[str, int] | None = None  # («full»/«box», bytes de datos)
        self.memory = Image.new("RGB", (size.columns, size.rows), "gray")
        self.brightness: int | None = None
        self.counts: list[int] = []
        self.need_resend_after = need_resend_after  # nº de parcial tras la que pide reenvío
        self.closed = False

    # lo que usa el driver
    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        assert len(data) % 250 == 0, f"escritura de {len(data)} bytes: no son bloques enteros"
        self.pending += data
        self._consume()
        return len(data)

    def read(self, size=1):
        answer, self.out = bytes(self.out[:size]), self.out[size:]
        return answer

    def reset_input_buffer(self):
        self.out.clear()

    def close(self):
        self.closed = True

    # la pantalla
    @property
    def bgra_boxes(self) -> bool:
        return self.size is not SIZE_2_1 and self.rom > 88

    def _consume(self):
        while True:
            if self.expect is not None:
                kind, length = self.expect
                need = blocks_for(length, 2 if kind == "box" else 0) * 250
                if len(self.pending) < need:
                    return
                message, self.pending = self.pending[:need], self.pending[need:]
                self.expect = None
                for start in range(0, need, 250):
                    assert message[start + 249] == 0, "el byte 250 de un bloque lleva datos"
                (self._full if kind == "full" else self._box)(uncut(message), length)
                continue
            if len(self.pending) < 250:
                return
            block, self.pending = self.pending[:250], self.pending[250:]
            self._order(block)

    def _order(self, block: bytes):
        code = block[0]
        self.orders.append(code)
        if block == pad(HELLO):
            self.out += self.answer
        elif block == bytes([0x2C]) * 250:
            pass
        elif code == 0xC8:
            assert block[1:4] == bytes.fromhex("EF 69 00"), block[:8].hex(" ")
            assert block[6:8] == bytes.fromhex("0E 10"), "el segundo campo es siempre 0E10"
            self.expect = ("full", int.from_bytes(block[4:6], "big") * 256)
        elif code == 0xCC:
            assert block[1:4] == bytes.fromhex("EF 69 00") and block[7:10] == bytes(3)
            self.counts.append(int.from_bytes(block[10:14], "big"))
            self.expect = ("box", int.from_bytes(block[4:7], "big") - 2)
        else:
            assert block[1:6] == bytes.fromhex("EF 69 00 00 00"), block[:8].hex(" ")
            if code == 0x7B:
                assert block[6:10] == bytes.fromhex("01 00 00 00")
                self.brightness = block[10]
            elif code == 0x96:
                self.out += reply(b"")
            elif code == 0xCF:
                lost = len(self.counts) == self.need_resend_after
                if lost:
                    self.need_resend_after = None  # lo pide una vez
                self.out += reply(b"needReSend:%d|renderCnt:1" % lost)
            else:
                assert code in (0x79, 0x86), f"orden desconocida {code:#04x}"

    def _full(self, data: bytes, length: int):
        assert length == self.size.columns * self.size.rows * 4
        frame = Image.frombytes("RGBA", self.memory.size, data[:length], "raw", "BGRA")
        assert frame.getchannel("A").getextrema() == (255, 255)
        self.memory = frame.convert("RGB")
        self.out += reply(b"full_png_sucess")  # #1030 (sic)

    def _box(self, data: bytes, length: int):
        assert data[length : length + 2] == bytes.fromhex("EF 69"), "falta la cola EF 69"
        bpp = 4 if self.bgra_boxes else 3
        i = 0
        while i < length:
            offset = int.from_bytes(data[i : i + 3], "big")
            width = int.from_bytes(data[i + 3 : i + 5], "big")
            row, column = divmod(offset, self.size.columns)
            assert width > 0 and column + width <= self.size.columns and row < self.size.rows
            line = data[i + 5 : i + 5 + width * bpp]
            mode = "BGRA" if bpp == 4 else "BGR"
            pixels = Image.frombytes("RGBA" if bpp == 4 else "RGB", (width, 1), line, "raw", mode)
            self.memory.paste(pixels.convert("RGB"), (column, row))
            i += 5 + width * bpp
        assert i == length


@pytest.fixture(autouse=True)
def fresh_session():
    """Cada test empieza sin despiertos comprobados en «esta sesión» (apertura)."""
    apertura.AWAKE_THIS_SESSION.clear()
    yield
    apertura.AWAKE_THIS_SESSION.clear()


def ready(size=SIZE_5, answer=ANSWER_88 + b"\n", **kwargs):
    port = FakeRevC(size, answer, **kwargs)
    display = TuringRevC(port, size, sleep=lambda _s: None)
    display.probe()
    return display, port


# --- saludo ----------------------------------------------------------------------------


def test_hello_is_the_bytes_of_the_report_padded_to_250():
    display, port = ready()
    assert port.writes == [pad(HELLO)]
    assert display.rom == 88


@pytest.mark.parametrize(
    ("answer", "rom"),
    [
        (ANSWER_88, 88),
        (ANSWER_89, 89),
        (ANSWER_88 + b"\n", 88),
        (ANSWER_88 + b"\0", 88),
        (b"chs_5inch.dev1_rom1.79", 87),  # fuera de 80-100: el valor por defecto (R5, 7)
        (b"chs_5inch.dev1_rom1.101", 87),
        (b"chs_88inch.dev1", 87),  # sin ROM que entender: también
    ],
)
def test_rev_c_answers_are_accepted_with_their_rom(answer, rom):
    assert parse_hello(answer) == rom


@pytest.mark.parametrize(
    "answer",
    [
        pad(HELLO)[:23],  # su propio saludo de vuelta: un eco, no una pantalla
        b"CHS_5inch.dev1_rom1.88",
        b"xchs_5inch.dev1_rom1.88",  # basura delante: la referencia la tiraba, aquí no
        b"chs_5inch\x01dev1_rom1.88",
        b"chs_" + b"a" * 20,  # más largo que lo que se lee
        bytes([0x45] * 6),  # el saludo de la rev A
    ],
)
def test_anything_else_is_not_a_rev_c(answer):
    assert parse_hello(answer) is None


def test_wrong_answer_closes_and_sends_nothing_more():
    port = FakeRevC(SIZE_5, b"CA HELLO")
    with pytest.raises(WrongModel):
        TuringRevC.open("COM7", SIZE_5, serial_factory=lambda _d: port, sleep=lambda _s: None)
    assert port.writes == [pad(HELLO)] and port.closed


def test_silent_display_gets_a_limited_number_of_hellos():
    # La referencia saluda cada segundo sin fin (#1030); aquí hay tope.
    port = FakeRevC(SIZE_5, b"")
    sleeps: list[float] = []
    with pytest.raises(NoAnswer):
        TuringRevC.open("COM7", SIZE_5, serial_factory=lambda _d: port, sleep=sleeps.append)
    assert turing_rev_c.HELLO_ATTEMPTS <= 5
    assert port.writes == [pad(HELLO)] * turing_rev_c.HELLO_ATTEMPTS
    assert len(sleeps) == turing_rev_c.HELLO_ATTEMPTS - 1 and port.closed


def test_zeros_are_no_answer_not_a_wrong_model():
    port = FakeRevC(SIZE_5, bytes(23))
    with pytest.raises(NoAnswer):
        TuringRevC.open("COM7", SIZE_5, serial_factory=lambda _d: port, sleep=lambda _s: None)


# --- órdenes ---------------------------------------------------------------------------


def test_brightness_goes_0_to_255_brighter_is_higher():
    display, port = ready()
    display.set_brightness(100)
    assert port.writes[-1] == pad(bytes.fromhex("7B EF 69 00 00 00 01 00 00 00 FF"))
    display.set_brightness(50)
    assert port.brightness == 127
    display.screen_off()
    assert port.brightness == 0
    display.screen_on()
    assert port.brightness == 127


def test_screen_on_stops_what_the_display_plays_by_itself():
    display, port = ready()
    port.orders.clear()
    display.screen_on()
    assert port.orders == [0x79, 0x96, 0x7B]
    assert port.out == b""  # la respuesta de STOP_MEDIA se ha leído


def test_orientation_sends_nothing():
    display, port = ready()
    before = list(port.writes)
    for orientation in Orientation:
        display.set_orientation(orientation)
    assert port.writes == before


@pytest.mark.parametrize(
    ("size", "portrait", "landscape"),
    [(SIZE_2_1, (480, 480), (480, 480)), (SIZE_5, (480, 800), (800, 480)),
     (SIZE_8_8, (480, 1920), (1920, 480))],
)  # fmt: skip
def test_size_follows_the_model_and_the_orientation(size, portrait, landscape):
    display, _port = ready(size)
    assert (display.width, display.height) == portrait
    display.set_orientation(Orientation.LANDSCAPE)
    assert (display.width, display.height) == landscape
    assert display.model_key == size.model_key


# --- imagen completa -------------------------------------------------------------------


def test_full_image_messages_are_those_of_the_reference():
    display, port = ready(SIZE_2_1)
    display.set_orientation(Orientation.LANDSCAPE)  # sin giro: los bytes salen tal cual
    port.writes.clear()
    display.draw_image(Image.new("RGB", (480, 480), (1, 2, 3)))
    assert port.writes[0] == pad(bytes.fromhex("86 EF 69 00 00 00 01"))
    assert port.writes[1] == bytes([0x2C]) * 250
    assert port.writes[2] == pad(bytes.fromhex("C8 EF 69 00 0E 10 0E 10"))
    body = b"".join(port.writes[3:-1])
    assert len(body) == blocks_for(480 * 480 * 4) * 250
    assert body[:8] == bytes.fromhex("03 02 01 FF 03 02 01 FF")  # BGRA
    # 249 bytes de datos (62 píxeles y el azul del 63), el separador y sigue el verde.
    assert body[249] == 0 and body[250:253] == bytes.fromhex("02 01 FF")
    assert port.writes[-1] == pad(bytes.fromhex("CF EF 69 00 00 00 01"))
    assert max(len(w) for w in port.writes) <= 64_000  # el plazo se mira entre trozos
    assert port.out == b""  # «full_png_sucess» y el estado, leídos


@pytest.mark.parametrize(("size", "code"), [(SIZE_2_1, "0E 10"), (SIZE_5, "17 70"),
                                            (SIZE_8_8, "38 40")])  # fmt: skip
def test_full_image_header_carries_the_size_code_of_the_reference(size, code):
    display, port = ready(size)
    display.draw_image(Image.new("RGB", (display.width, display.height)))
    assert pad(bytes.fromhex("C8 EF 69 00") + bytes.fromhex(code) + bytes.fromhex("0E 10")) in (
        port.writes
    )


def native_spot(size, orientation, x, y):
    """Dónde pone la referencia el píxel (x, y) de una imagen completa: sus giros
    (``_generate_full_image``) escritos a mano como fila y columna de la memoria."""
    if size is SIZE_8_8:
        return {
            Orientation.PORTRAIT: (1919 - y, 479 - x),  # rotate(180)
            Orientation.REVERSE_PORTRAIT: (y, x),  # sin giro
            Orientation.LANDSCAPE: (x, 479 - y),  # rotate(270)
            Orientation.REVERSE_LANDSCAPE: (1919 - x, y),  # rotate(90)
        }[orientation]
    last = size.height - 1  # 479 en la de 2,1" y 799 en la de 5"
    return {
        Orientation.PORTRAIT: (479 - x, y),  # rotate(90)
        Orientation.REVERSE_PORTRAIT: (x, last - y),  # rotate(270)
        Orientation.LANDSCAPE: (y, x),  # sin giro
        Orientation.REVERSE_LANDSCAPE: (479 - y, last - x),  # rotate(180)
    }[orientation]


@pytest.mark.parametrize("size", [SIZE_2_1, SIZE_5, SIZE_8_8])
@pytest.mark.parametrize("orientation", list(Orientation))
def test_full_image_pixels_land_where_the_reference_puts_them(size, orientation):
    display, port = ready(size)
    display.set_orientation(orientation)
    image = Image.new("RGB", (display.width, display.height), "black")
    spots = [(0, 0), (display.width - 1, 0), (7, display.height - 3)]
    for n, (x, y) in enumerate(spots, start=1):
        image.putpixel((x, y), (n * 40, 0, 0))
    display.draw_image(image)
    for n, (x, y) in enumerate(spots, start=1):
        row, column = native_spot(size, orientation, x, y)
        assert port.memory.getpixel((column, row)) == (n * 40, 0, 0), (x, y)


# --- actualización parcial -------------------------------------------------------------


def test_partial_update_bytes_written_by_hand():
    # 5" horizontal (sin giro), ROM 88 → BGR. Dos píxeles en (10, 20): posición
    # 20·800 + 10 = 16010 = 3E 8A, ancho 2, colores BGR, cola EF 69 (informe e issue #90).
    display, port = ready(SIZE_5)
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (800, 480)))  # la primera va entera
    port.writes.clear()
    box = Image.new("RGB", (2, 1))
    box.putdata([(1, 2, 3), (4, 5, 6)])
    display.draw_image(box, 10, 20)
    raw = bytes.fromhex("00 3E 8A 00 02 03 02 01 06 05 04")
    assert port.writes == [
        pad(bytes.fromhex("CC EF 69 00 00 00 0D 00 00 00 00 00 00 00")),  # tamaño 11 + 2
        pad(raw + bytes.fromhex("EF 69")),
        pad(bytes.fromhex("CF EF 69 00 00 00 01")),
    ]
    assert port.memory.getpixel((10, 20)) == (1, 2, 3)
    display.draw_image(box, 10, 20)
    assert port.counts == [0, 1]  # el número de actualización sube


@pytest.mark.parametrize(
    ("size", "answer", "bgra"),
    [(SIZE_5, ANSWER_89, True), (SIZE_5, ANSWER_88, False), (SIZE_8_8, ANSWER_89, True),
     (SIZE_2_1, ANSWER_89, False), (SIZE_5, b"chs_5inch", False)],
)  # fmt: skip
def test_partial_colour_format_follows_the_rom_and_the_size(size, answer, bgra):
    display, port = ready(size, answer)
    assert display.bgra_partials is bgra
    display.draw_image(Image.new("RGB", (display.width, display.height)))
    display.draw_image(Image.new("RGB", (3, 2), (9, 8, 7)), 5, 5)
    assert port.memory.getpixel(port_spot(display, 5, 5)) == (9, 8, 7)  # la simulada lo lee


def port_spot(display, x, y):
    row, column = native_spot(display._size, display.orientation, x, y)
    return column, row


@pytest.mark.parametrize("size", [SIZE_2_1, SIZE_5, SIZE_8_8])
@pytest.mark.parametrize("orientation", list(Orientation))
@pytest.mark.parametrize("answer", [ANSWER_88, ANSWER_89])
def test_partials_and_full_images_agree(size, orientation, answer):
    # Lo que dejan en la pantalla una imagen completa y luego recuadros sueltos tiene que
    # ser lo mismo que una sola imagen completa del resultado (así cuadra con los giros de
    # la referencia; la del 8,8" en vertical la tenía mal la referencia, Libre Panel no).
    rng = random.Random(f"{size.model_key}{orientation}{answer}")
    display, port = ready(size, answer)
    display.set_orientation(orientation)
    picture = Image.new("RGB", (display.width, display.height), (10, 20, 30))
    display.draw_image(picture)
    for _ in range(12):
        w, h = rng.randrange(1, 90), rng.randrange(1, 90)
        x, y = rng.randrange(display.width), rng.randrange(display.height)
        box = Image.new("RGB", (w, h), tuple(rng.randrange(256) for _ in range(3)))
        box.putpixel((0, 0), (255, 0, 255))
        display.draw_image(box, x, y)
        picture.paste(
            box.crop((0, 0, min(w, display.width - x), min(h, display.height - y))), (x, y)
        )
    assert port.counts == list(range(len(port.counts))) and len(port.counts) >= 12
    other, other_port = ready(size, answer)
    other.set_orientation(orientation)
    other.draw_image(picture)
    assert port.memory.tobytes() == other_port.memory.tobytes()


def test_box_whose_tail_the_display_would_lose_goes_out_as_two():
    # Libre Panel: si la cola EF 69 empieza un bloque, queda partida o lo acaba justo, la
    # pantalla pierde la actualización. Una fila BGR de 49 píxeles = 5 + 147 = 152 bytes no;
    # una de 81 = 5 + 243 = 248: la cola acabaría el bloque justo. Va en dos.
    display, port = ready(SIZE_2_1)
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (480, 480)))
    port.counts.clear()
    display.draw_image(Image.new("RGB", (49, 1), "red"), 0, 0)
    assert len(port.counts) == 1
    display.draw_image(Image.new("RGB", (81, 1), "red"), 0, 0)
    assert len(port.counts) == 3
    assert port.memory.getpixel((80, 0)) == (255, 0, 0)


def test_needresend_makes_the_next_image_whole_once():
    display, port = ready(SIZE_5, need_resend_after=1)  # tras la 1.ª parcial: «la perdí»
    display.draw_image(Image.new("RGB", (480, 800)))
    display.draw_image(Image.new("RGB", (4, 4), "red"), 10, 10)
    port.orders.clear()
    display.draw_image(Image.new("RGB", (4, 4), "blue"), 50, 50)
    assert 0xC8 in port.orders and 0xCC not in port.orders  # la copia entera
    port.orders.clear()
    display.draw_image(Image.new("RGB", (4, 4), "green"), 90, 90)
    assert 0xCC in port.orders and 0xC8 not in port.orders  # y luego otra vez parciales
    assert port.memory.getpixel(port_spot(display, 11, 11)) == (255, 0, 0)  # no se perdió


def test_first_image_after_rotating_is_whole():
    display, port = ready(SIZE_5)
    display.draw_image(Image.new("RGB", (480, 800)))
    display.set_orientation(Orientation.LANDSCAPE)
    port.orders.clear()
    display.draw_image(Image.new("RGB", (5, 5)), 1, 1)
    assert 0xC8 in port.orders


# --- píxeles que parecen órdenes (R5, punto 5) -----------------------------------------


def crafted_box() -> Image.Image:
    """Una fila de 100 píxeles cuyos bytes BGR, en el segundo bloque (byte 249 de los
    datos: 5 de cabecera + 244 de píxeles), dirían «66 EF 69 00 00 00»: la orden que
    borra ficheros (#90) seguida de la firma. Píxel 81: B=?, G=66, R=EF; 82: 69, 00, 00;
    83: B=00."""
    box = Image.new("RGB", (100, 1), (1, 1, 1))
    box.putpixel((81, 0), (0xEF, 0x66, 0x01))
    box.putpixel((82, 0), (0x00, 0x00, 0x69))
    box.putpixel((83, 0), (0x01, 0x01, 0x00))
    return box


def test_pixels_that_spell_an_order_are_changed_by_one_bit():
    display, port = ready(SIZE_2_1)  # la de 2,1" siempre en BGR
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (480, 480)))
    port.writes.clear()
    display.draw_image(crafted_box(), 0, 0)
    body = port.writes[1]
    naive = bytes.fromhex("00 00 00 00 64") + crafted_box().tobytes("raw", "BGR")
    assert naive[249:255] == bytes.fromhex("66 EF 69 00 00 00")  # el tema lo había logrado
    assert body[250:256] != bytes.fromhex("66 EF 69 00 00 00")
    sent = uncut(body)[: len(naive)]
    differing = [i for i in range(len(naive)) if sent[i] != naive[i]]
    assert differing == [250] and sent[250] == 0xEF ^ 1  # un bit de un color, nada más
    assert port.memory.getpixel((81, 0)) == (0xEE, 0x66, 0x01)


def test_the_tail_cannot_spell_an_order_either():
    # Si la cola EF 69 cayera en los bytes 1-2 de un bloque, detrás vienen los ceros del
    # relleno: «xx EF 69 00 00 00» sin ningún color que tocar. Se parte el recuadro.
    display, port = ready(SIZE_2_1)
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (480, 480)))
    port.counts.clear()
    port.writes.clear()
    # Dos filas de 40 píxeles: 2·(5 + 120) = 250 bytes → cortados, 251: el último color
    # abre el bloque 2 y la cola va justo detrás.
    display.draw_image(Image.new("RGB", (40, 2), (0x66, 0x66, 0x66)), 0, 0)
    assert len(port.counts) == 2
    for write in port.writes:
        for start in range(0, len(write), 250):
            assert write[start + 1 : start + 6] != bytes.fromhex("EF 69 00 00 00") or (
                write[start] in (0xCC, 0xCF)
            )


def test_full_images_never_need_defusing():
    # Con el alfa a FF cada 4 bytes, ninguna ventana de 6 bytes puede ser una orden:
    # una imagen que lo intenta con todas sus fuerzas sale tal cual.
    display, port = ready(SIZE_2_1)
    display.set_orientation(Orientation.LANDSCAPE)
    image = Image.new("RGB", (480, 480))
    image.putdata([(0x69, 0xEF, 0x66), (0, 0, 0)] * (480 * 240))
    display.draw_image(image)
    sent = uncut(b"".join(port.writes[4:-1]))[: 480 * 480 * 4]
    assert sent == image.convert("RGBA").tobytes("raw", "BGRA")


# --- cortes y plazos -------------------------------------------------------------------


class ShortFake(FakeRevC):
    def __init__(self, *args, short_at: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.short_at = short_at

    def write(self, data):
        if len(self.writes) + 1 == self.short_at:
            self.writes.append(bytes(data))
            return len(data) - 1
        return super().write(data)


def test_cut_image_asks_to_replug_and_stops():
    port = ShortFake(SIZE_5, short_at=4)  # el saludo, el aviso, 2C… y la cabecera, corta
    display = TuringRevC(port, SIZE_5, sleep=lambda _s: None)
    display.probe()
    with pytest.raises(ImageCut):
        display.draw_image(Image.new("RGB", (480, 800)))
    assert len(port.writes) == 4


def test_short_command_is_an_error():
    port = ShortFake(SIZE_5, short_at=2)
    display = TuringRevC(port, SIZE_5, sleep=lambda _s: None)
    display.probe()
    with pytest.raises(DisplayError):
        display.set_brightness(50)


class SlowClock:
    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_image_has_an_absolute_deadline():
    port = FakeRevC(SIZE_8_8)
    display = TuringRevC(port, SIZE_8_8, sleep=lambda _s: None, monotonic=SlowClock(5.0))
    display.probe()
    with pytest.raises(ImageCut):
        display.draw_image(Image.new("RGB", (480, 1920)))
    sent = sum(len(w) for w in port.writes)
    assert sent < 480 * 1920 * 4  # cortada a medias, no entera


def test_reads_are_bounded_and_read_errors_are_display_errors():
    import serial

    class Broken(FakeRevC):
        def read(self, size=1):
            raise serial.SerialException("ReadFile failed")

    display = TuringRevC(Broken(SIZE_5), SIZE_5, sleep=lambda _s: None)
    with pytest.raises(DisplayError):
        display.probe()

    class Chatty(FakeRevC):
        def read(self, size=1):
            assert size <= 1024
            return b"x" * (size + 50)  # una pantalla que manda de más

    chatty = TuringRevC(Chatty(SIZE_5, b""), SIZE_5, sleep=lambda _s: None)
    assert chatty._read(23) == b"x" * 23


# --- modelos, despertar y apertura -----------------------------------------------------


BRIDGE = PortInfo("COM6", 0x1A86, 0xCA21, "CT21INCH")
BRIDGE_5 = PortInfo("COM6", 0x1A86, 0xCA50, "CT50INCH")
AWAKE = PortInfo("COM7", 0x1D6B, 0x0106, "20080411")
PI_GADGET = PortInfo("COM9", 0x1D6B, 0x0106, "20080411")  # otro aparato con los mismos ids
REV_A_PORT = PortInfo("COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")


def com(port: PortInfo):
    """Como lo da ``comports()`` de pyserial (el serie va en ``serial_number``)."""
    return SimpleNamespace(
        device=port.device, vid=port.vid, pid=port.pid, serial_number=port.serial, description=""
    )


class Ports:
    """La lista de puertos de Windows, que cambia: el despierto aparece tras ``after``
    aperturas del puente. Apunta qué se abre y qué se escribe en cada puerto."""

    def __init__(self, ports, *, appears=None, after=1, answer=ANSWER_89):
        self.ports = list(ports)
        self.appears = list(appears or [])
        self.after = after
        self.answer = answer
        self.opened: list[str] = []
        self.fakes: dict[str, FakeRevC] = {}

    def __call__(self):
        return [com(p) for p in self.ports]

    def factory(self, device):
        self.opened.append(device)
        if self.appears and self.opened.count(BRIDGE.device) >= self.after:
            self.ports += self.appears
            self.appears = []
        fake = FakeRevC(SIZE_5, self.answer)
        self.fakes.setdefault(device, fake)
        return self.fakes[device]


def open_c(choice, ports: Ports):
    return apertura.open_chosen(
        choice, ports(), serial_factory=ports.factory, sleep=lambda _s: None, relist=ports
    )


def test_rev_c_ports_offer_the_three_sizes():
    for port in (BRIDGE, AWAKE):
        assert compatible_models(port) == list(REV_C_SIZES)
    assert compatible_models(PortInfo("COM8", 0x1234, 0x5678, "CT21INCH")) == []  # R5-07
    assert compatible_models(PortInfo("COM9", 0x1D6B, 0x0106, "abc")) == []  # una Raspberry Pi


def test_chosen_bridge_is_woken_and_only_the_new_awake_device_is_used():
    ports = Ports([BRIDGE, PI_GADGET], appears=[AWAKE], after=3)
    display = open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    assert isinstance(display, TuringRevC) and display.device == "COM7"
    assert ports.opened == ["COM6", "COM6", "COM6", "COM7"]  # el gadget de antes, ni tocado
    assert ports.fakes["COM6"].writes == []  # al puente no se le escribe nada
    assert ports.fakes["COM7"].writes == [pad(HELLO)]
    assert display.verified is False and (display.width, display.height) == (480, 800)


def test_awake_device_that_was_already_there_is_not_taken():
    # Despierto ya y sin haberlo comprobado Luzeira en esta sesión (ni guardado en la
    # elección): no se sabe si es el suyo. Lo que sí vale está en los dos tests siguientes.
    ports = Ports([BRIDGE, AWAKE])
    with pytest.raises(DisplayError) as error:
        open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    assert error.value.says_retrying
    assert set(ports.opened) == {"COM6"}
    assert len(ports.opened) == turing_rev_c.WAKE_ATTEMPTS


def test_two_new_awake_devices_and_none_is_opened():
    ports = Ports([BRIDGE], appears=[AWAKE, PI_GADGET])
    with pytest.raises(DisplayError):
        open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    assert set(ports.opened) == {"COM6"}


def test_awake_device_remembered_from_last_time_is_used_without_waking():
    # Al recordar la elección, la bandeja guarda el COM con el que se abrió (el despierto).
    choice = DisplayChoice("turing-rev-c-5", "COM7", 0x1A86, 0xCA21, "CT21INCH")
    ports = Ports([BRIDGE, AWAKE])
    display = open_c(choice, ports)
    assert display.device == "COM7" and ports.opened == ["COM7"]


def test_bridge_in_use_by_another_program_is_busy():
    class Busy(Ports):
        def factory(self, device):
            raise OSError("could not open port: PermissionError(13, 'Acceso denegado.')")

    with pytest.raises(DisplayBusy):
        open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), Busy([BRIDGE], appears=[AWAKE]))


def test_awake_device_that_does_not_answer_like_a_rev_c_is_closed():
    ports = Ports([BRIDGE], appears=[AWAKE], answer=b"hello")
    with pytest.raises(WrongModel):
        open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    assert ports.fakes["COM7"].writes == [pad(HELLO)] and ports.fakes["COM7"].closed


def test_chosen_awake_device_needs_the_same_com():
    choice = DisplayChoice.for_port(REV_C_88, AWAKE)
    assert match_choice(choice, [AWAKE]) == AWAKE
    assert match_choice(choice, [PI_GADGET]) is None  # mismo USB y serie, otro COM
    display = open_c(choice, Ports([AWAKE]))
    assert display.device == "COM7" and display.model_key == "turing-rev-c-88"


def test_chosen_bridge_follows_its_serial_to_another_com():
    choice = DisplayChoice.for_port(REV_C_21, BRIDGE)
    moved = PortInfo("COM12", 0x1A86, 0xCA21, "CT21INCH")
    assert match_choice(choice, [moved]) == moved
    assert match_choice(choice, [PortInfo("COM12", 0x1A86, 0xCA21, "CT88INCH")]) is None


def test_rev_c_model_never_matches_another_display():
    # El modo avanzado de la bandeja ofrece todos los modelos en cualquier puerto: una rev C
    # sobre una rev A no se abre (ni con la elección hecha a mano en el fichero).
    for advanced in (False, True):
        choice = DisplayChoice.for_port(REV_C_5, REV_A_PORT, advanced=advanced)
        assert match_choice(choice, [REV_A_PORT]) is None
        ports = Ports([REV_A_PORT])
        with pytest.raises(ChosenDisplayMissing):
            open_c(choice, ports)
        assert ports.opened == []


@pytest.mark.parametrize("model", [REV_A])
def test_other_models_still_never_match_a_rev_c(model):
    for port in (BRIDGE, AWAKE):
        assert match_choice(DisplayChoice.for_port(model, port, advanced=True), [port]) is None


@pytest.mark.parametrize(
    ("ports", "error", "model"),
    [
        ([BRIDGE_5], ModelToConfirm, REV_C_5),  # CT50INCH: un 5" (#1030)
        ([PortInfo("COM6", 0x1A86, 0xCA21, "CT88INCH"), AWAKE], ModelToConfirm, REV_C_88),
        ([PortInfo("COM6", 0x1A86, 0xCA21, "USB7INCH")], ModelToConfirm, REV_C_5),
        ([BRIDGE], ModelToChoose, None),  # CT21INCH: 2,1", 2,8" o 5"
        ([AWAKE], ModelToChoose, None),
        ([PortInfo("COM8", 0x1234, 0x5678, "CT21INCH")], UnsupportedModel, None),
    ],
)
def test_auto_never_opens_or_wakes_a_rev_c(ports, error, model):
    opened: list[str] = []
    with pytest.raises(error) as info:
        apertura.open_auto([com(p) for p in ports], serial_factory=opened.append)
    assert opened == []
    if model is not None:
        assert info.value.choice.model == model.key
        assert info.value.choice.device == "COM6"  # el puente, no el despierto


def test_choose_message_says_nothing_is_sent():
    with pytest.raises(ModelToChoose) as info:
        apertura.open_auto([com(BRIDGE)])
    text = str(info.value)
    assert "no le mando nada" in text and "Elegir modelo y puerto" in text
    assert "intentando" not in text


def test_rev_c_model_keys_fit_the_config_pattern():
    from luzeira.config import DisplaySetting

    for model in REV_C_SIZES:
        DisplaySetting(model=model.key, device="COM7")
        assert model.key in turing_rev_c.SIZES and model.beta and model.has_hello


def test_saved_choice_round_trips_and_reopens():
    choice = DisplayChoice.for_port(REV_C_5, BRIDGE_5)
    assert match_choice(choice, list_ports([com(BRIDGE_5)])) == BRIDGE_5


def test_an_interrupting_sleep_aborts_the_opening_and_closes_what_was_open():
    # Para la propuesta de servicio.py: si el ``sleep`` que se pasa lanza OpenInterrupted
    # (stop() o suspend() mientras se despierta o se saluda), la apertura se corta ya, sin
    # más intentos, y el puerto que llegó a abrirse queda cerrado.
    from luzeira.pantallas.comun import OpenInterrupted

    def interrupted(_seconds):
        raise OpenInterrupted

    ports = Ports([BRIDGE], appears=[AWAKE], after=99)  # no despierta: esperaría 15 s
    with pytest.raises(OpenInterrupted):
        apertura.open_chosen(
            DisplayChoice.for_port(REV_C_5, BRIDGE), ports(), serial_factory=ports.factory,
            sleep=interrupted, relist=ports,
        )  # fmt: skip
    assert ports.opened == ["COM6"]  # un intento de despertar y se para
    silent = FakeRevC(SIZE_5, b"")
    with pytest.raises(OpenInterrupted):
        TuringRevC.open("COM7", SIZE_5, serial_factory=lambda _d: silent, sleep=interrupted)
    assert silent.writes == [pad(HELLO)] and silent.closed


def test_choosing_it_again_by_its_bridge_reuses_the_awake_device_of_this_session():
    # Revisor de la fase 5: tras «No se ve bien» + otro tamaño, una conexión perdida o la
    # vuelta del reposo, la bandeja la vuelve a elegir con el COM del puente. Antes se
    # despertaba otra vez, no aparecía nada nuevo y pedía desenchufarla.
    ports = Ports([BRIDGE], appears=[AWAKE])
    open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    assert ports.opened == ["COM6", "COM7"]
    again = open_c(DisplayChoice.for_port(REV_C_21, BRIDGE), ports)  # otro tamaño
    assert again.device == "COM7" and ports.opened == ["COM6", "COM7", "COM7"]


def test_the_awake_device_of_this_session_is_forgotten_if_it_stops_being_a_rev_c():
    ports = Ports([BRIDGE], appears=[AWAKE])
    open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    ports.fakes["COM7"].answer = b"otra cosa"
    with pytest.raises(WrongModel):
        open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    assert apertura.AWAKE_THIS_SESSION == {}
    # Otro aparato en el mismo COM pero con otro USB tampoco sería «el de esta sesión».
    ports = Ports([BRIDGE], appears=[AWAKE])
    open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)
    ports.ports = [BRIDGE, PortInfo("COM7", 0x0525, 0xA4A7, "20080411")]
    with pytest.raises(DisplayError):
        open_c(DisplayChoice.for_port(REV_C_5, BRIDGE), ports)


def test_bridge_without_serial_keeps_its_com_when_remembered():
    # Revisor de la fase 5: sin serie, match_choice exige el mismo COM del puente; guardar el
    # del despierto la dejaba sin casar en cada arranque.
    from luzeira.pantallas.modelos import remembered_device

    bare = DisplayChoice("turing-rev-c-5", "COM6", 0x1A86, 0xCA21, "")
    assert remembered_device(bare, "COM7") == "COM6"
    assert match_choice(bare, [PortInfo("COM6", 0x1A86, 0xCA21, "")]) is not None
    with_serial = DisplayChoice.for_port(REV_C_5, BRIDGE)
    assert remembered_device(with_serial, "COM7") == "COM7"
    other = DisplayChoice("xuanfang-rev-b", "COM6", 0x1A86, 0x5722, "2017-2-25")
    assert remembered_device(other, "COM10") == "COM10"


@pytest.mark.parametrize(
    ("size", "orientation", "box"),
    [
        # Filas de la pantalla de 61 px en BGRA (ROM 89) = 5 + 244 = 249 bytes: quitar filas
        # no arregla nada. Antes: RecursionError (8,8") o 2880 escrituras (5").
        (SIZE_8_8, Orientation.PORTRAIT, (61, 990)),
        (SIZE_5, Orientation.LANDSCAPE, (61, 480)),
        (SIZE_5, Orientation.LANDSCAPE, (310, 3)),
    ],
)
def test_box_whose_every_row_loses_the_tail_is_cut_by_columns(size, orientation, box):
    display, port = ready(size, ANSWER_89)
    display.set_orientation(orientation)
    display.draw_image(Image.new("RGB", (display.width, display.height)))
    port.writes.clear()
    port.counts.clear()
    display.draw_image(Image.new("RGB", box, "red"), 0, 0)
    assert 1 < len(port.counts) <= turing_rev_c.MAX_BOXES
    assert len(port.writes) < 40
    w, h = box
    for x, y in ((0, 0), (w - 1, h - 1), (w - 1, 0)):
        assert port.memory.getpixel(port_spot(display, x, y)) == (255, 0, 0), (x, y)


def test_too_many_pieces_send_the_whole_image(monkeypatch):
    monkeypatch.setattr(turing_rev_c, "MAX_BOXES", 1)
    display, port = ready(SIZE_5, ANSWER_89)
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (800, 480)))
    port.orders.clear()
    display.draw_image(Image.new("RGB", (61, 480), "red"), 0, 0)
    assert 0xC8 in port.orders and 0xCC not in port.orders
    assert port.memory.getpixel((60, 479)) == (255, 0, 0)


def test_the_width_of_a_row_is_never_touched_by_defusing():
    # Revisor (M24): el byte bajo del ancho (239 = 00 EF) justo al principio de un bloque y
    # detrás los colores 69 00 00 00: «00 EF 69 00 00 00». Se cambia un color (el 69), nunca
    # el ancho. 2,1" horizontal, BGR: filas de 5 + 717 = 722 bytes; la fila 30 empieza en el
    # byte 21660, así que su ancho cae en el 21663 = 87 · 249, el principio de un bloque.
    display, port = ready(SIZE_2_1)
    display.set_orientation(Orientation.LANDSCAPE)
    display.draw_image(Image.new("RGB", (480, 480)))
    port.writes.clear()
    box = Image.new("RGB", (239, 31), (1, 1, 1))
    box.putpixel((0, 30), (0x00, 0x00, 0x69))
    box.putpixel((1, 30), (0x01, 0x01, 0x00))
    display.draw_image(box, 0, 0)
    sent = uncut(port.writes[1])
    assert sent[21663:21665] == bytes.fromhex("00 EF")  # el ancho, intacto
    assert sent[21665] == 0x69 ^ 1  # y un bit de color cambiado
    assert port.memory.getpixel((238, 30)) == (1, 1, 1)  # la fila entera, en su sitio
