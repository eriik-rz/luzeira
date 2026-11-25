"""Driver beta de la WeAct Display FS (3,5" y 0,96") con un puerto simulado.

Bytes esperados escritos a mano desde ``docs/investigacion/tanda-11/weact-y-otras/
informe.md`` (sección «Protocolo») y los vectores de prueba de ``slipalison/bezel``
(``docs/reverse-engineering/protocol-weact.md``, apartado «Test vectors»).
"""

from types import SimpleNamespace

import pytest
from PIL import Image

from luzeira.pantallas import apertura
from luzeira.pantallas.comun import (
    ChosenDisplayMissing,
    DisplayError,
    ImageCut,
    ModelToChoose,
    ModelToConfirm,
    NoAnswer,
    Orientation,
    WrongModel,
)
from luzeira.pantallas.modelos import (
    ADVANCED,
    REV_A,
    WEACT,
    WEACT_096,
    WEACT_35,
    DisplayChoice,
    compatible_models,
    identify,
    list_ports,
    match_choice,
)
from luzeira.pantallas.weact import (
    PANEL_096,
    PANEL_35,
    PANELS,
    PIXEL_FORMATS,
    WeAct,
    brightness_command,
    parse_version,
)

VERSION = bytes.fromhex("C2 0A")
# 19 bytes, versión en ASCII en 1..8 («V1.0.0.0», TSP issue #863). Lo demás no está
# documentado: aquí, el código y ceros.
ANSWER = b"\xc2" + b"V1.0.0.0" + bytes(10)


class FakeWeAct:
    def __init__(self, answer: bytes = ANSWER) -> None:
        self.answer = answer
        self.writes: list[bytes] = []
        self.out = b""
        self.closed = False

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        if data == VERSION:
            self.out += self.answer
        return len(data)

    def read(self, size=1):
        answer, self.out = self.out[:size], self.out[size:]
        return answer

    def reset_input_buffer(self):
        self.out = b""

    def close(self):
        self.closed = True


def ready(panel=PANEL_35, answer=ANSWER):
    port = FakeWeAct(answer)
    display = WeAct(port, panel, sleep=lambda _s: None)
    display.probe()
    port.writes.clear()
    return display, port


# --- saludo (lectura de versión) -------------------------------------------------------


def test_probe_reads_the_version_and_sends_nothing_else():
    port = FakeWeAct()
    display = WeAct.open("COM13", PANEL_35, serial_factory=lambda _d: port)
    assert port.writes == [VERSION]
    assert display.version == "V1.0.0.0" and display.verified and display.device == "COM13"


@pytest.mark.parametrize(
    "answer",
    [
        VERSION,  # su propia orden de vuelta: un eco
        ANSWER[:18],  # no son 19
        b"\xc2" + b"V1.0\x01.00" + bytes(10),  # no es texto
        bytes(19),  # ceros
    ],
)
def test_anything_that_is_not_a_weact_answer_is_rejected(answer):
    assert parse_version(answer) is None
    port = FakeWeAct(answer)
    with pytest.raises(WrongModel):
        WeAct.open("COM13", PANEL_35, serial_factory=lambda _d: port, sleep=lambda _s: None)
    assert port.writes == [VERSION] and port.closed


def test_extra_bytes_are_not_read_at_all():
    # Se leen como mucho 19 y el resto se tira (``reset_input_buffer``): nada se acumula.
    assert parse_version(ANSWER + b"x") is None
    port = FakeWeAct(ANSWER + b"x" * 100)
    WeAct.open("COM13", PANEL_35, serial_factory=lambda _d: port)
    assert port.out == b""


def test_silent_port_gets_two_version_reads_and_no_answer():
    port = FakeWeAct(b"")
    with pytest.raises(NoAnswer):
        WeAct.open("COM13", PANEL_35, serial_factory=lambda _d: port, sleep=lambda _s: None)
    assert port.writes == [VERSION, VERSION] and port.closed


# --- órdenes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0, "03 00 E8 03 0A"), (25, "03 3F E8 03 0A"), (100, "03 FF E8 03 0A")],  # bezel
)
def test_brightness_bytes_are_those_of_the_reference(percent, expected):
    assert brightness_command(percent) == bytes.fromhex(expected)


def test_screen_off_is_brightness_zero_and_on_restores_it():
    display, port = ready()
    display.set_brightness(40)
    display.screen_off()
    display.screen_on()
    assert port.writes[1:] == [brightness_command(0), brightness_command(40)]


@pytest.mark.parametrize("orientation", list(Orientation))
def test_orientation_is_one_order_and_the_size_follows(orientation):
    display, port = ready(PANEL_096)
    display.set_orientation(orientation)
    assert port.writes == [bytes([0x02, int(orientation), 0x0A])]
    expected = (160, 80) if orientation.is_landscape else (80, 160)
    assert (display.width, display.height) == expected


def test_orientation_numbers_are_those_of_the_reference():
    # TSP: PORTRAIT 0, REVERSE_PORTRAIT 1, LANDSCAPE 2, REVERSE_LANDSCAPE 3 (``lcd_comm.py``).
    assert [int(o) for o in Orientation] == [0, 1, 2, 3]
    assert Orientation(2) is Orientation.LANDSCAPE


# --- imagen ----------------------------------------------------------------------------


def test_full_image_header_and_pixels():
    display, port = ready()
    image = Image.new("RGB", (320, 480), (255, 0, 0))
    display.draw_image(image)
    # Esquinas inclusivas, 16 bits con el byte bajo primero: 319 = 3F 01, 479 = DF 01.
    assert port.writes[0] == bytes.fromhex("05 00 00 00 00 3F 01 DF 01 0A")
    pixels = b"".join(port.writes[1:])
    assert pixels == bytes.fromhex("00 F8") * (320 * 480)  # rojo, RGB565 byte bajo primero
    assert {len(w) for w in port.writes[1:]} == {320 * 4}  # dos filas por escritura


def test_box_is_clipped_to_the_screen():
    display, port = ready(PANEL_096)
    display.set_orientation(Orientation.LANDSCAPE)  # 160 x 80
    port.writes.clear()
    display.draw_image(Image.new("RGB", (50, 50), "white"), 150, 70)
    assert port.writes[0] == bytes.fromhex("05 96 00 46 00 9F 00 4F 00 0A")  # 150,70 → 159,79
    assert len(b"".join(port.writes[1:])) == 10 * 10 * 2


def test_outside_the_screen_is_an_error():
    display, _port = ready()
    with pytest.raises(ValueError):
        display.draw_image(Image.new("RGB", (1, 1)), 320, 0)


def test_both_panels_use_rgb565_little_endian_by_default():
    # 0,96": la referencia, el fabricante y Libre Panel (un dueño lo confirma) dicen RGB565
    # byte bajo primero; WeAct35 dice «BRG565». Por defecto lo primero [SIN VERIFICAR].
    assert PANEL_35.pixel_format == PANEL_096.pixel_format == "rgb565-le"
    assert set(PANELS) == {WEACT_35.key, WEACT_096.key}


@pytest.mark.parametrize(
    ("rgb", "word"),
    # Los colores de ``Include/weact_display.h`` de WeAct35 (994fc44). Su verde dice
    # 0x001F en el número pero «011111» (6 bits) en el comentario: con 6 bits de verde,
    # el máximo es 0x003F, que es lo que sale aquí.
    [((255, 0, 0), 0x07C0), ((0, 0, 255), 0xF800), ((0, 255, 0), 0x003F),
     ((255, 255, 255), 0xFFFF), ((0, 0, 0), 0x0000)],
)  # fmt: skip
def test_the_alternative_brg565_format_is_there_to_switch_to(rgb, word):
    assert PIXEL_FORMATS["brg565-be"](Image.new("RGB", (1, 1), rgb)) == word.to_bytes(2, "big")


class ShortFake(FakeWeAct):
    def __init__(self, short_at: int) -> None:
        super().__init__()
        self.short_at = short_at

    def write(self, data):
        super().write(data)
        return len(data) - 1 if len(self.writes) == self.short_at else len(data)


def test_cut_image_asks_to_replug():
    display = WeAct(ShortFake(short_at=3), PANEL_35)
    with pytest.raises(ImageCut):
        display.draw_image(Image.new("RGB", (320, 480)))


def test_short_command_is_a_plain_error():
    display = WeAct(ShortFake(short_at=1), PANEL_35)
    with pytest.raises(DisplayError) as info:
        display.set_brightness(50)
    assert not isinstance(info.value, ImageCut)


def test_read_errors_are_display_errors():
    import serial

    class Broken(FakeWeAct):
        def read(self, size=1):
            raise serial.SerialException("ReadFile failed")

    with pytest.raises(DisplayError):
        WeAct(Broken(), PANEL_35).probe()


# --- modelos y apertura ----------------------------------------------------------------


def com(device, vid=None, pid=None, serial=None):
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial, description="")


WEACT_35_PORT = com("COM13", 0x1A86, 0xFE0C, "AB12345678")
WEACT_096_PORT = com("COM14", 0x1A86, 0xFE0C, "AD12345678")
WEACT_UNKNOWN = com("COM15", 0x1A86, 0xFE0C, "ZZ1")
REV_A_COM3 = com("COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")


def info(port):
    return list_ports([port])[0]


def test_weact_is_identified_by_usb_and_serial_prefix():
    assert (identify(info(WEACT_35_PORT)).model, identify(info(WEACT_35_PORT)).sure) == (
        WEACT_35,
        True,
    )
    assert identify(info(WEACT_096_PORT)).model is WEACT_096
    unknown = identify(info(WEACT_UNKNOWN))
    assert (unknown.model, unknown.sure) == (WEACT, False)
    # El prefijo con otro USB no es una WeAct (R5-07; TSP aceptaba VID:PID **o** prefijo).
    assert identify(info(com("COM9", 0x2341, 0x0043, "AB1"))) is None


def test_closed_list_offers_the_sizes():
    assert compatible_models(info(WEACT_35_PORT)) == [WEACT_35]
    assert compatible_models(info(WEACT_096_PORT)) == [WEACT_096]
    assert compatible_models(info(WEACT_UNKNOWN)) == [WEACT_35, WEACT_096]
    assert WEACT_35 not in ADVANCED and WEACT_096 not in ADVANCED


@pytest.mark.parametrize(
    ("ports", "error", "model"),
    [
        ([WEACT_35_PORT], ModelToConfirm, WEACT_35),
        ([WEACT_096_PORT], ModelToConfirm, WEACT_096),
        ([WEACT_UNKNOWN], ModelToChoose, None),
    ],
)
def test_auto_never_opens_a_weact(ports, error, model):
    opened: list[str] = []
    with pytest.raises(error) as found:
        apertura.open_auto(ports, serial_factory=opened.append)
    assert opened == []
    if model is not None:
        assert found.value.choice.model == model.key


def test_rev_a_still_opens_alone_next_to_a_weact():
    factory_ports: list[str] = []

    class Port(FakeWeAct):
        pass

    def factory(device):
        factory_ports.append(device)
        return Port(bytes([1] * 6))

    display = apertura.open_auto([WEACT_35_PORT, REV_A_COM3], serial_factory=factory)
    assert display.device == "COM3" and factory_ports == ["COM3"]


def test_chosen_weact_opens_with_its_panel():
    port = FakeWeAct()
    choice = DisplayChoice.for_port(WEACT_096, info(WEACT_096_PORT))
    display = apertura.open_chosen(choice, [WEACT_096_PORT], serial_factory=lambda _d: port)
    assert isinstance(display, WeAct) and (display.width, display.height) == (80, 160)
    assert display.model_key == "weact-096"


def test_weact_never_goes_to_another_device_even_in_advanced_mode():
    # R5-01/R5-02: una WeAct elegida (también a mano en el fichero, o en modo avanzado)
    # solo casa con un USB de WeAct.
    opened: list[str] = []
    for advanced in (False, True):
        choice = DisplayChoice.for_port(WEACT_35, info(REV_A_COM3), advanced=advanced)
        assert match_choice(choice, list_ports([REV_A_COM3])) is None
        with pytest.raises(ChosenDisplayMissing):
            apertura.open_chosen(choice, [REV_A_COM3], serial_factory=opened.append)
    assert opened == []


def test_other_drivers_never_reach_a_weact():
    # Revisor de la fase 5: ni en el modo avanzado. Su protocolo no tiene marco: el de otro
    # modelo se leería como píxeles u órdenes suyas. Como la rev C (R5-02).
    for port in (WEACT_35_PORT, WEACT_UNKNOWN):
        for advanced in (False, True):
            other = DisplayChoice.for_port(REV_A, info(port), advanced=advanced)
            assert match_choice(other, list_ports([port])) is None


def test_saved_weact_choice_follows_its_serial_to_another_com():
    choice = DisplayChoice.for_port(WEACT_35, info(WEACT_35_PORT))
    moved = com("COM20", 0x1A86, 0xFE0C, "AB12345678")
    assert match_choice(choice, list_ports([moved])).device == "COM20"
    other = com("COM21", 0x1A86, 0xFE0C, "AB99999999")
    assert match_choice(choice, list_ports([other])) is None


def test_only_the_096_asks_if_it_looks_right():
    # C-78: el formato de color del 0,96" está en duda (RGB565 LE o «BRG565»).
    assert WeAct(FakeWeAct(), PANEL_35).verified is True
    assert WeAct(FakeWeAct(), PANEL_096).verified is False


def test_weact_model_keys_fit_the_config_pattern():
    from luzeira.config import DisplaySetting

    for model in (WEACT_35, WEACT_096):
        DisplaySetting(model=model.key, device="COM13")
        assert model.beta and model.has_hello and model.supported
