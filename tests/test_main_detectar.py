"""«luzeira detectar»: dice qué pantalla hay en cada puerto sin abrir ninguno."""

from types import SimpleNamespace

import pytest
import serial

from luzeira import __main__ as cli


def com(device, vid=None, pid=None, serial_number=None):
    """Un puerto como los que da ``comports()`` de pyserial."""
    return SimpleNamespace(
        device=device, vid=vid, pid=pid, serial_number=serial_number, description=""
    )


REV_A_COM3 = com("COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")
REV_B = com("COM6", 0x1A86, 0x5722, "2017-2-25")
UNKNOWN_CH552 = com("COM9", 0x1A86, 0x5722)
KIPYE = com("COM8", 0x454D, 0x4E41)
REV_C = com("COM11", 0x1A86, 0xCA21, "CT21INCH")
BLUETOOTH = com("COM4")


@pytest.fixture(autouse=True)
def own_config_folder(monkeypatch, tmp_path):
    """``main`` lee el idioma de la configuración: la de un sitio vacío, no la del equipo."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_port_is_ever_opened(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("«detectar» no debe abrir ningún puerto")

    monkeypatch.setattr(serial, "Serial", refuse)


def test_every_known_display_is_listed_with_model_and_certainty(capsys):
    assert cli._detect([REV_A_COM3, BLUETOOTH, REV_B, UNKNOWN_CH552, KIPYE, REV_C]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines] == ["COM3", "COM6", "COM9", "COM8", "COM11"]
    rev_a, rev_b, unknown, kipye, rev_c = lines
    assert 'Turing / UsbPCMonitor 3,5"' in rev_a and "número de serie" in rev_a
    assert "beta" not in rev_a
    assert "XuanFang" in rev_b and "beta" in rev_b and "número de serie" in rev_b
    assert "revisión A" in unknown and "XuanFang" in unknown and "confirmación" in unknown
    assert "Kipye" in kipye and "no es seguro" in kipye
    # Fase 5: la rev C ya tiene driver. Con CT21INCH (2,1", 2,8" o 5") no se sabe el
    # tamaño: se dice que lo elija, y ya no «todavía no compatible».
    assert "revisión C" in rev_c and "tamaño" in rev_c and "confirmación" in rev_c
    assert "todavía no compatible" not in rev_c


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        (com("COM12", 0x1A86, 0xCA50, "CT50INCH"), 'Turing de 5" (revisión C) (beta)'),
        (com("COM13", 0x1A86, 0xFE0C, "AB0001"), 'WeAct Display FS 3,5" (beta)'),
        (com("COM14", 0x1A86, 0xFE0C, "AD0001"), 'WeAct Display FS 0,96" (beta)'),
        (com("COM15", 0x1A86, 0xFE0C, "XX"), "WeAct Display FS (beta), pero no sé"),
        (com("COM16", 0x1234, 0x5678, "CT21INCH"), "todavía no compatible"),
    ],
)
def test_rev_c_and_weact_lines_say_what_detection_will_do(capsys, port, expected):
    # Ninguna de estas se abre sola (``open_auto``): o se propone o se elige el tamaño.
    assert cli._detect([port]) == 0
    line = capsys.readouterr().out.strip()
    assert expected in line
    assert "número de serie" in line or "tamaño" in line or "no compatible" in line


def test_nothing_known_says_so(capsys):
    assert cli._detect([BLUETOOTH]) == 1
    assert capsys.readouterr().out.strip() == "No encuentro ninguna pantalla compatible conectada."


def test_detect_command_uses_the_port_list_of_windows(monkeypatch, capsys):
    from serial.tools import list_ports

    monkeypatch.setattr(list_ports, "comports", lambda: [KIPYE])
    assert cli.main(["detectar"]) == 0
    assert capsys.readouterr().out.startswith("COM8: ")


def test_the_command_line_speaks_the_language_chosen_in_settings(
    own_config_folder, monkeypatch, capsys
):
    # C-62: el idioma elegido en Ajustes vale también para la línea de órdenes.
    from serial.tools import list_ports

    from luzeira.config import AppConfig, save_config

    monkeypatch.setattr(list_ports, "comports", lambda: [BLUETOOTH])

    folder = own_config_folder / "Luzeira"
    folder.mkdir()
    save_config(AppConfig(language="en"), folder / "config.json")
    assert cli.main(["detectar"]) == 1  # sin pantallas: el comportamiento no cambia
    assert capsys.readouterr().out.strip() == "I can't find any supported display connected."


def test_the_command_line_reads_the_language_without_touching_a_damaged_config(
    tmp_path, monkeypatch
):
    from luzeira import __main__ as cli

    monkeypatch.setenv("APPDATA", str(tmp_path))
    folder = tmp_path / "Luzeira"
    folder.mkdir()
    (folder / "config.json").write_text("{ roto", encoding="utf-8")
    assert cli._saved_language() is None
    assert (folder / "config.json").exists()  # no lo aparta como «.danado»: eso es de la bandeja
    (folder / "config.json").write_text('{"language": "en"}', encoding="utf-8")
    assert cli._saved_language() == "en"
    (folder / "config.json").write_text('{"language": "../x"}', encoding="utf-8")
    assert cli._saved_language() is None
