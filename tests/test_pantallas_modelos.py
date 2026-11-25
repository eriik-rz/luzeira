"""Detección sin enviar bytes, lista cerrada, revalidación y apertura (puertos falsos)."""

from types import SimpleNamespace

import pytest

from luzeira.pantallas import apertura
from luzeira.pantallas.comun import (
    ChosenDisplayMissing,
    ConfirmationNeeded,
    DisplayError,
    ModelToConfirm,
    UnsupportedModel,
    WrongModel,
)
from luzeira.pantallas.modelos import (
    KIPYE,
    REV_A,
    REV_B,
    REV_C,
    DisplayChoice,
    PortInfo,
    compatible_models,
    identify,
    list_ports,
    match_choice,
)
from luzeira.pantallas.turing_rev_a import TuringRevA
from luzeira.pantallas.xuanfang_rev_b import XuanFangRevB


def com(device, vid=None, pid=None, serial=None, manufacturer=None, description=""):
    """Un puerto como los que da ``comports()`` de pyserial."""
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        serial_number=serial,
        manufacturer=manufacturer,
        description=description,
    )


# Una rev A real, tal como lo da Windows (medido 2026-11-18 con list_ports, sin abrirlo).
REV_A_COM3 = com(
    "COM3", 0x1A86, 0x5722, "USB35INCHIPSV2", "Microsoft", "Dispositivo serie USB (COM3)"
)
BLUETOOTH = com("COM4", manufacturer="Microsoft", description="Serie estándar sobre Bluetooth")
REV_B_PORT = com("COM6", 0x1A86, 0x5722, "2017-2-25")
UNKNOWN_CH552 = com("COM9", 0x1A86, 0x5722, None)
KIPYE_PORT = com("COM8", 0x454D, 0x4E41)
REV_C_SLEEPING = com("COM11", 0x1A86, 0xCA21, "CT21INCH")


def info(port) -> PortInfo:
    return list_ports([port])[0]


# --- identificar ---------------------------------------------------------------


def test_typical_rev_a_is_identified_for_sure():
    found = identify(info(REV_A_COM3))
    assert (found.model, found.sure) == (REV_A, True)


def test_rev_b_serial_identifies_rev_b():
    found = identify(info(REV_B_PORT))
    assert (found.model, found.sure) == (REV_B, True)


def test_rev_b_text_in_manufacturer_field_does_not_count():
    # Muchas rev A traen «2017-2-25» como *fabricante* (issues #512, #263 de TSP).
    port = com("COM9", 0x1A86, 0x5722, None, manufacturer="2017-2-25")
    found = identify(info(port))
    assert (found.model, found.sure) == (None, False)  # A o B: se pregunta


def test_rev_b_serial_with_other_usb_id_is_not_rev_b():
    assert identify(info(com("COM6", 0x1234, 0x5678, "2017-2-25"))) is None


def test_kipye_is_only_proposed():
    found = identify(info(KIPYE_PORT))
    assert (found.model, found.sure) == (KIPYE, False)


@pytest.mark.parametrize(
    "port",
    [
        REV_C_SLEEPING,
        com("COM12", 0x1A86, 0xCA50, "CT50INCH"),
        com("COM13", 0x0525, 0xA4A7, "20080411"),
        com("COM14", 0x1D6B, 0x0106, "20080411"),
    ],
)
def test_rev_c_is_recognised(port):
    assert identify(info(port)).model is REV_C


def test_generic_gadget_ids_without_rev_c_serial_are_ignored():
    # Una Raspberry Pi en modo gadget tiene el mismo 1d6b:0106.
    assert identify(info(com("COM15", 0x1D6B, 0x0106, "abc"))) is None


def test_bluetooth_port_is_nothing():
    assert identify(info(BLUETOOTH)) is None


# --- lista cerrada -------------------------------------------------------------


def test_closed_list_is_filtered_by_usb_id():
    assert compatible_models(info(UNKNOWN_CH552)) == [REV_A, REV_B]
    assert compatible_models(info(KIPYE_PORT)) == [KIPYE]
    assert compatible_models(info(BLUETOOTH)) == []  # sin USB: solo en modo avanzado
    assert compatible_models(info(REV_C_SLEEPING)) == []  # sin driver


def test_closed_list_offers_only_the_model_its_serial_proves():
    # Remate C-44: una rev A reconocida por su serie no ofrece «XuanFang (beta)» (ni al
    # revés); los demás modelos quedan para el modo avanzado.
    assert compatible_models(info(REV_A_COM3)) == [REV_A]
    assert compatible_models(info(REV_B_PORT)) == [REV_B]


def test_normal_choice_of_another_model_on_a_proven_port_is_not_valid():
    # Una elección normal «rev B sobre una rev A segura» (de un fichero de antes del
    # remate, o escrito a mano) no vale; en modo avanzado, sí.
    forged = DisplayChoice.for_port(REV_B, info(REV_A_COM3))
    assert match_choice(forged, list_ports([REV_A_COM3])) is None
    advanced = DisplayChoice.for_port(REV_B, info(REV_A_COM3), advanced=True)
    assert match_choice(advanced, list_ports([REV_A_COM3])).device == "COM3"


def test_beta_models_say_so():
    assert REV_B.menu_label.endswith("(beta)") and KIPYE.menu_label.endswith("(beta)")
    assert "beta" not in REV_A.menu_label


# --- revalidar la elección -----------------------------------------------------


def test_saved_choice_follows_the_usb_when_the_com_number_changes():
    choice = DisplayChoice.for_port(REV_B, info(REV_B_PORT))
    moved = com("COM10", 0x1A86, 0x5722, "2017-2-25")
    assert match_choice(choice, list_ports([BLUETOOTH, moved])).device == "COM10"


def test_saved_choice_prefers_the_same_com_among_twins():
    choice = DisplayChoice.for_port(REV_A, info(UNKNOWN_CH552))
    twin = com("COM2", 0x1A86, 0x5722, None)
    assert match_choice(choice, list_ports([twin, UNKNOWN_CH552])).device == "COM9"


def test_saved_choice_does_not_match_another_serial_or_usb():
    choice = DisplayChoice.for_port(REV_B, info(REV_B_PORT))
    assert match_choice(choice, list_ports([REV_A_COM3])) is None
    assert match_choice(choice, list_ports([KIPYE_PORT])) is None


def test_normal_choice_must_stay_in_the_closed_list():
    # Una elección normal «Kipye en un 1a86:5722» no existe; si el fichero la trae, no vale.
    forged = DisplayChoice(KIPYE.key, "COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")
    assert match_choice(forged, list_ports([REV_A_COM3])) is None
    advanced = DisplayChoice(KIPYE.key, "COM3", 0x1A86, 0x5722, "USB35INCHIPSV2", advanced=True)
    assert match_choice(advanced, list_ports([REV_A_COM3])).device == "COM3"


def test_advanced_choice_without_usb_needs_the_same_com():
    choice = DisplayChoice.for_port(REV_A, info(BLUETOOTH), advanced=True)
    assert match_choice(choice, list_ports([BLUETOOTH])).device == "COM4"
    assert match_choice(choice, list_ports([com("COM5")])) is None


def test_unknown_or_unsupported_model_never_matches():
    assert (
        match_choice(DisplayChoice("otra", "COM3", 0x1A86, 0x5722), list_ports([REV_A_COM3]))
        is None
    )
    rev_c = DisplayChoice.for_port(REV_C, info(REV_C_SLEEPING), advanced=True)
    assert match_choice(rev_c, list_ports([REV_C_SLEEPING])) is None


# --- apertura automática -------------------------------------------------------


class FakeSerial:
    def __init__(self, answer=b"") -> None:
        self.writes: list[bytes] = []
        self.answer = answer
        self.closed = False

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size=1):
        return self.answer[:size]

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


REV_B_HELLO = bytes.fromhex("CA 48 45 4C 4C 4F 0A 11 00 CA")


class Factory:
    """``serial_factory`` falso que apunta qué puertos se han abierto."""

    def __init__(self, answer=b"") -> None:
        self.opened: list[str] = []
        self.ports: list[FakeSerial] = []
        self.answer = answer

    def __call__(self, device):
        self.opened.append(device)
        self.ports.append(FakeSerial(self.answer))
        return self.ports[-1]


def test_auto_rev_a_by_serial_opens_exactly_as_before():
    # Antes este test congelaba la llamada ``TuringRevA.open(None, retries=1)``: con
    # ``None`` la rev A volvía a buscar su puerto en los puertos REALES del equipo y abría
    # ``serial.Serial`` de verdad, sin mirar ``ports`` ni ``serial_factory`` (R5-08). Lo que
    # tiene que seguir igual no es la llamada, sino lo que ve la pantalla: el mismo puerto,
    # una sola apertura y los mismos bytes que la apertura de siempre de la rev A.
    factory = Factory(bytes([1] * 6))
    display = apertura.open_auto([REV_B_PORT, REV_A_COM3, KIPYE_PORT], serial_factory=factory)
    assert isinstance(display, TuringRevA) and display.device == "COM3"
    assert factory.opened == ["COM3"]
    before = Factory(bytes([1] * 6))
    TuringRevA.open("COM3", retries=1, serial_factory=before)
    assert factory.ports[0].writes == before.ports[0].writes == [bytes([0x45] * 6)]


def test_auto_rev_b_by_serial_uses_its_driver():
    factory = Factory(REV_B_HELLO)
    display = apertura.open_auto([BLUETOOTH, REV_B_PORT], serial_factory=factory)
    assert isinstance(display, XuanFangRevB) and display.device == "COM6"
    assert factory.opened == ["COM6"]


@pytest.mark.parametrize(
    ("ports", "error"),
    [
        ([UNKNOWN_CH552], ConfirmationNeeded),
        ([com("COM9", 0x1A86, 0x5722, None, manufacturer="2017-2-25")], ConfirmationNeeded),
        ([KIPYE_PORT], ModelToConfirm),
        ([REV_C_SLEEPING], UnsupportedModel),
        ([BLUETOOTH], DisplayError),
        ([], DisplayError),
    ],
)
def test_auto_never_opens_what_it_cannot_identify(ports, error):
    factory = Factory()
    with pytest.raises(error):
        apertura.open_auto(ports, serial_factory=factory)
    assert factory.opened == []


def test_plain_confirmation_is_for_rev_a_and_kipye_brings_its_choice():
    with pytest.raises(ConfirmationNeeded) as plain:
        apertura.open_auto([UNKNOWN_CH552])
    assert type(plain.value) is ConfirmationNeeded and plain.value.choice is None
    with pytest.raises(ModelToConfirm) as kipye:
        apertura.open_auto([KIPYE_PORT])
    assert kipye.value.choice == DisplayChoice(KIPYE.key, "COM8", 0x454D, 0x4E41, "")
    assert "beta" in str(kipye.value)


def test_no_display_message_is_the_old_one():
    with pytest.raises(DisplayError) as info_:
        apertura.open_auto([BLUETOOTH])
    assert str(info_.value) == "No encuentro la pantalla. ¿Está conectada por USB?"


def test_unknown_ch552_is_asked_before_kipye():
    with pytest.raises(ConfirmationNeeded) as info_:
        apertura.open_auto([KIPYE_PORT, UNKNOWN_CH552])
    assert info_.value.device == "COM9" and info_.value.choice is None


# --- apertura de la elegida ----------------------------------------------------


def test_chosen_rev_b_opens_with_its_driver():
    factory = Factory(REV_B_HELLO)
    choice = DisplayChoice.for_port(REV_B, info(REV_B_PORT))
    display = apertura.open_chosen(choice, [REV_B_PORT], serial_factory=factory)
    assert isinstance(display, XuanFangRevB)


def test_chosen_model_that_does_not_answer_is_closed_and_no_other_model_is_tried():
    factory = Factory(bytes([1] * 6))  # contesta como una rev A
    choice = DisplayChoice.for_port(REV_B, info(UNKNOWN_CH552))
    with pytest.raises(WrongModel):
        apertura.open_chosen(choice, [UNKNOWN_CH552], serial_factory=factory)
    assert factory.opened == ["COM9"]  # una apertura, un modelo
    assert factory.ports[0].closed
    assert factory.ports[0].writes == [bytes.fromhex("CA 48 45 4C 4C 4F 00 00 00 CA")]


def test_chosen_display_missing_opens_nothing():
    factory = Factory()
    choice = DisplayChoice.for_port(REV_B, info(REV_B_PORT))
    with pytest.raises(ChosenDisplayMissing):
        apertura.open_chosen(choice, [REV_A_COM3], serial_factory=factory)
    assert factory.opened == []


@pytest.mark.parametrize("answer", [b"", bytes([1] * 6), bytes([3] * 6)])
def test_chosen_rev_a_accepts_its_possible_answers(answer):
    factory = Factory(answer)
    choice = DisplayChoice.for_port(REV_A, info(UNKNOWN_CH552))
    display = apertura.open_chosen(choice, [UNKNOWN_CH552], serial_factory=factory)
    assert isinstance(display, TuringRevA) and display.device == "COM9"


def test_chosen_rev_a_with_a_garbled_answer_is_rejected():
    factory = Factory(bytes.fromhex("CA 48 45 4C 4C 4F"))
    choice = DisplayChoice.for_port(REV_A, info(UNKNOWN_CH552))
    with pytest.raises(WrongModel):
        apertura.open_chosen(choice, [UNKNOWN_CH552], serial_factory=factory)
    assert factory.ports[0].closed


# --- ¿la prueba ha demostrado algo? (remate C-44) -------------------------------


@pytest.mark.parametrize("answer", [b"", bytes([1] * 6)])
def test_chosen_rev_a_on_its_usb_counts_as_verified(answer):
    choice = DisplayChoice.for_port(REV_A, info(UNKNOWN_CH552))
    display = apertura.open_chosen(choice, [UNKNOWN_CH552], serial_factory=Factory(answer))
    assert display.verified is True  # el Turing oficial no contesta: el silencio vale


def test_silent_rev_a_outside_its_usb_is_not_verified():
    choice = DisplayChoice.for_port(REV_A, info(BLUETOOTH), advanced=True)
    display = apertura.open_chosen(choice, [BLUETOOTH], serial_factory=Factory(b""))
    assert display.verified is False
    answering = apertura.open_chosen(choice, [BLUETOOTH], serial_factory=Factory(bytes([1] * 6)))
    assert answering.verified is True


def test_kipye_is_never_verified():
    choice = DisplayChoice.for_port(KIPYE, info(KIPYE_PORT))
    display = apertura.open_chosen(choice, [KIPYE_PORT], serial_factory=Factory())
    assert display.verified is False
    assert KIPYE.has_hello is False and REV_B.has_hello and REV_A.has_hello


def test_rev_b_that_answers_is_verified():
    choice = DisplayChoice.for_port(REV_B, info(REV_B_PORT))
    display = apertura.open_chosen(choice, [REV_B_PORT], serial_factory=Factory(REV_B_HELLO))
    assert display.verified is True


def test_unsupported_model_message_is_coherent():
    with pytest.raises(UnsupportedModel) as info_:
        apertura.open_auto([REV_C_SLEEPING])
    text = str(info_.value)
    assert "no le mando nada" in text and "intentando" not in text


# --- R5: un USB defectuoso o malicioso -------------------------------------------------

CH340_COM5 = com("COM5", 0x1A86, 0x7523, None)  # el chip de muchas impresoras 3D y GRBL


def test_rev_a_serial_on_another_usb_is_not_a_rev_a():
    # R5-07: un Arduino (2341:0043) que anuncia el serie de la rev A no se abre solo ni
    # sale en la lista cerrada; solo queda el modo avanzado.
    fake = com("COM9", 0x2341, 0x0043, "USB35INCHIPSV2")
    assert identify(info(fake)) is None
    assert compatible_models(info(fake)) == []
    factory = Factory(bytes([1] * 6))
    with pytest.raises(DisplayError) as error:
        apertura.open_auto([fake], serial_factory=factory)
    assert type(error.value) is DisplayError and factory.opened == []


def test_rev_a_serial_on_kipye_usb_is_a_kipye_proposal():
    found = identify(info(com("COM8", 0x454D, 0x4E41, "USB35INCHIPSV2")))
    assert (found.model, found.sure) == (KIPYE, False)


def test_advanced_choice_without_serial_does_not_follow_the_usb_to_another_com():
    # R5-01: la pantalla elegida en modo avanzado sobre un CH340 sin serie no se puede
    # distinguir de otro CH340 (la impresora 3D de al lado). En otro COM no vale: se pregunta.
    choice = DisplayChoice.for_port(REV_A, info(CH340_COM5), advanced=True)
    printer = com("COM7", 0x1A86, 0x7523, None)
    assert match_choice(choice, list_ports([printer])) is None
    assert match_choice(choice, list_ports([printer, CH340_COM5])).device == "COM5"
    # El mismo COM con otro USB tampoco es el mismo aparato.
    assert match_choice(choice, list_ports([com("COM5", 0x2341, 0x0043, None)])) is None
    factory = Factory()
    with pytest.raises(ChosenDisplayMissing):
        apertura.open_chosen(choice, [printer], serial_factory=factory)
    assert factory.opened == []  # ni un byte a la impresora


def test_advanced_choice_with_serial_also_needs_the_same_com():
    choice = DisplayChoice.for_port(REV_B, info(REV_A_COM3), advanced=True)
    moved = com("COM10", 0x1A86, 0x5722, "USB35INCHIPSV2")
    assert match_choice(choice, list_ports([moved])) is None


def test_normal_choice_without_serial_needs_the_same_com():
    # Un 1a86:5722 sin serie elegido de la lista cerrada: el mismo motivo (R5-01).
    choice = DisplayChoice.for_port(REV_A, info(UNKNOWN_CH552))
    assert match_choice(choice, list_ports([com("COM2", 0x1A86, 0x5722, None)])) is None
    assert match_choice(choice, list_ports([UNKNOWN_CH552])).device == "COM9"


@pytest.mark.parametrize("model", [REV_A, REV_B, KIPYE])
def test_advanced_choice_never_matches_a_rev_c(model):
    # R5-02: la rev C es el único protocolo con órdenes que borran; un tema puede formar
    # una con sus píxeles si se le manda el protocolo de otro modelo. Ni en modo avanzado.
    for port in (REV_C_SLEEPING, com("COM13", 0x0525, 0xA4A7, "20080411")):
        choice = DisplayChoice.for_port(model, info(port), advanced=True)
        assert match_choice(choice, list_ports([port])) is None
        factory = Factory()
        with pytest.raises(ChosenDisplayMissing):
            apertura.open_chosen(choice, [port], serial_factory=factory)
        assert factory.opened == []


def test_rev_a_that_echoes_its_hello_is_not_a_rev_a():
    # R5-06: un cable en bucle (o un aparato que repite) devuelve 45 45 45 45 45 45.
    factory = Factory(bytes([0x45] * 6))
    choice = DisplayChoice.for_port(REV_A, info(CH340_COM5), advanced=True)
    with pytest.raises(WrongModel):
        apertura.open_chosen(choice, [CH340_COM5], serial_factory=factory)
    assert factory.ports[0].closed
    assert factory.ports[0].writes == [bytes([0x45] * 6)]  # solo el saludo


@pytest.mark.parametrize(("answer", "verified"), [(0x01, True), (0x03, True), (0xFF, False)])
def test_only_real_rev_a_answers_count_as_verified(answer, verified):
    # 6 bytes iguales que no son 0x01-0x03 no demuestran nada: sin verificar (el usuario
    # confirma que se ve bien); antes contaba como rev A verificada.
    choice = DisplayChoice.for_port(REV_A, info(UNKNOWN_CH552))
    factory = Factory(bytes([answer] * 6))
    display = apertura.open_chosen(choice, [UNKNOWN_CH552], serial_factory=factory)
    assert display.verified is verified


def test_confirmed_rev_a_checks_its_hello_too():
    # El camino de «Sí, es una rev A» (servicio.open_once con puerto) también valida.
    echo = Factory(bytes([0x45] * 6))
    with pytest.raises(WrongModel):
        apertura.open_confirmed_rev_a("COM9", [UNKNOWN_CH552], serial_factory=echo)
    assert echo.ports[0].closed
    good = Factory(bytes([1] * 6))
    display = apertura.open_confirmed_rev_a("COM9", [UNKNOWN_CH552], serial_factory=good)
    assert isinstance(display, TuringRevA) and display.verified is True
    silent = apertura.open_confirmed_rev_a("COM9", [UNKNOWN_CH552], serial_factory=Factory())
    assert silent.verified is True  # el Turing oficial no contesta: con su USB, vale


def test_auto_rev_a_uses_the_given_ports_and_factory():
    # R5-08: antes la rev A volvía a enumerar los puertos del equipo y abría pyserial de
    # verdad. Con una rev A en ``ports`` y ninguna en el equipo, abre la de ``ports``.
    factory = Factory()
    display = apertura.open_auto([REV_A_COM3], serial_factory=factory)
    assert display.device == "COM3" and factory.opened == ["COM3"]
