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
    assert compatible_models(info(REV_A_COM3)) == [REV_A, REV_B]
    assert compatible_models(info(KIPYE_PORT)) == [KIPYE]
    assert compatible_models(info(BLUETOOTH)) == []  # sin USB: solo en modo avanzado
    assert compatible_models(info(REV_C_SLEEPING)) == []  # sin driver


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


def test_auto_rev_a_by_serial_opens_exactly_as_before(monkeypatch):
    calls = []
    monkeypatch.setattr(TuringRevA, "open", classmethod(lambda cls, *a, **k: calls.append((a, k))))
    factory = Factory()
    apertura.open_auto([REV_B_PORT, REV_A_COM3, KIPYE_PORT], serial_factory=factory)
    assert calls == [((None,), {"retries": 1})]  # la misma llamada que hacía open_once
    assert factory.opened == []


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
    choice = DisplayChoice.for_port(REV_B, info(REV_A_COM3))
    with pytest.raises(WrongModel):
        apertura.open_chosen(choice, [REV_A_COM3], serial_factory=factory)
    assert factory.opened == ["COM3"]  # una apertura, un modelo
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
