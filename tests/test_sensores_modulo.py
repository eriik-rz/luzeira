"""Lector del módulo opcional de sensores (``modulo``, ADR-007).

La sección la escribe otro programa: todo lo que trae se trata como datos no fiables.
"""

from __future__ import annotations

import random
import struct
import sys

import pytest

from luzeira import idioma
from luzeira.sensores import modulo
from luzeira.sensores.base import SENSOR_ID
from luzeira.sensores.modulo import (
    ENTRY,
    FLAG_VALID,
    HEADER,
    MAGIC,
    MAX_ENTRIES,
    REOPEN_SECONDS,
    STALE_SECONDS,
    STATUS_NO_PAWNIO,
    BadSection,
    ModuleSource,
    SectionUntrusted,
    parse_section,
)


@pytest.fixture(autouse=True)
def _spanish():
    idioma.set_language("es")
    yield
    idioma.set_language("es")


def build(
    entries=(("cpu.temperature", 55.5, FLAG_VALID),),
    *,
    sequence=2,
    status=0,
    magic=MAGIC,
    version=1,
    count=None,
    header_size=HEADER.size,
    entry_size=ENTRY.size,
    module_version=b"0.2.0",
):
    """Una sección como la escribiría el servicio."""
    count = len(entries) if count is None else count
    data = HEADER.pack(
        magic,
        version,
        header_size,
        entry_size,
        count,
        sequence,
        1_700_000_000_000,
        module_version,
        status,
        0,
    )
    for sid, value, flags in entries:
        data += ENTRY.pack(sid.encode("ascii"), value, flags, 0)
    return data


class FakeReader:
    def __init__(self, data):
        self.data = data
        self.closed = False

    def read(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(data, *, packaged=False, clock=None):
    reader = FakeReader(data)
    opened = []

    def opener():
        opened.append(1)
        return reader

    src = ModuleSource(opener=opener, clock=clock or Clock(), packaged=packaged)
    return src, reader, opened


# --- formato ---------------------------------------------------------------------------


def test_layout_sizes_are_the_documented_ones():
    # El servicio en C# usa los mismos números: cambiarlos rompe el módulo instalado.
    assert HEADER.size == 64
    assert ENTRY.size == 48
    assert modulo.SECTION_SIZE == 64 + 64 * 48


def test_good_section_is_read():
    section = parse_section(build([("cpu.temperature", 61.25, FLAG_VALID)]))
    assert section.values == {"cpu.temperature": 61.25}
    assert section.module_version == "0.2.0"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"LUZSENS\0",
        build(magic=b"OTRACOSA"),
        build(version=0),
        build(header_size=32),
        build(entry_size=16),
        build(count=MAX_ENTRIES + 1),
        build(count=5),  # dice 5 entradas pero trae 1
        build(sequence=3),  # impar: a medio escribir
    ],
)
def test_malformed_sections_are_rejected(data):
    with pytest.raises(BadSection):
        parse_section(data)


@pytest.mark.parametrize(
    "entry",
    [
        ("cpu.temperature", float("nan"), FLAG_VALID),
        ("cpu.temperature", float("inf"), FLAG_VALID),
        ("cpu.temperature", 900.0, FLAG_VALID),
        ("cpu.temperature", -100.0, FLAG_VALID),
        ("cpu.temperature", 50.0, 0),  # sin la bandera de válido
        ("cpu.power", 5000.0, FLAG_VALID),
        ("cpu.voltage", 12.0, FLAG_VALID),
        ("disk.usage.c", 50.0, FLAG_VALID),  # fuera de la lista cerrada
        ("cpu.temp\xe9rature", 50.0, FLAG_VALID),  # no ASCII
    ],
)
def test_impossible_or_unknown_values_are_dropped(entry):
    sid, value, flags = entry
    raw = build().replace(b"cpu.temperature", b"\0" * 15)  # sin la entrada por defecto
    data = raw[: HEADER.size] + ENTRY.pack(sid.encode("latin-1"), value, flags, 0)
    assert parse_section(data).values == {}


def test_random_bytes_never_crash_the_parser():
    # Fuzz sencillo: con semilla fija, para que un fallo se pueda repetir.
    rng = random.Random(20261124)
    good = build([("cpu.temperature", 50.0, FLAG_VALID), ("cpu.power", 80.0, FLAG_VALID)])
    for _ in range(3000):
        data = bytearray(good)
        for _ in range(rng.randint(1, 12)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        data = bytes(data[: rng.randint(0, len(data))])
        try:
            section = parse_section(data)
        except BadSection:
            continue
        for sid, value in section.values.items():
            assert sid in {s.id for s in modulo.SENSORS}
            assert -20.0 <= value <= 1000.0


# --- la fuente -------------------------------------------------------------------------


def test_catalogue_is_fixed_and_follows_the_contract():
    src, _, _ = make(None)
    ids = [s.id for s in src.sensors()]
    assert ids[0] == "cpu.temperature"
    assert all(SENSOR_ID.match(i) for i in ids)


def test_without_the_module_every_sensor_says_how_to_get_it():
    src, _, _ = make(None)
    r = src.read_all()
    assert r["cpu.temperature"].value is None
    assert "Luzeira Sensores" in r["cpu.temperature"].reason


def test_store_version_never_mentions_the_module():
    # Norma 10.1.5 de la Store: la app no ofrece servicios ni drivers (ADR-007).
    src, _, _ = make(None, packaged=True)
    reason = src.read_all()["cpu.temperature"].reason
    assert "Luzeira Sensores" not in reason
    assert "módulo" not in reason


def test_store_version_reads_the_module_if_it_is_there():
    src, _, _ = make(build([("cpu.temperature", 47.0, FLAG_VALID)]), packaged=True)
    assert src.read_all()["cpu.temperature"].value == 47.0


def test_values_and_missing_values():
    src, _, _ = make(build([("cpu.temperature", 61.257, FLAG_VALID)]))
    r = src.read_all()
    assert r["cpu.temperature"].value == 61.26
    assert r["cpu.temperature"].unit == "°C"
    assert r["cpu.power"].value is None
    assert r["cpu.power"].reason


def test_missing_section_is_looked_for_again_only_every_so_often():
    clock = Clock()
    src, _, opened = make(None, clock=clock)
    src.read_all()
    src.read_all()
    assert len(opened) == 1
    clock.now += REOPEN_SECONDS
    src.read_all()
    assert len(opened) == 2


def test_a_module_that_stops_writing_is_not_responding():
    clock = Clock()
    src, _, _ = make(build(sequence=4), clock=clock)
    assert src.read_all()["cpu.temperature"].value == 55.5
    clock.now += STALE_SECONDS + 1
    r = src.read_all()["cpu.temperature"]
    assert r.value is None
    assert "no responde" in r.reason


def test_a_module_that_keeps_writing_stays_fresh():
    clock = Clock()
    src, reader, _ = make(build(sequence=4), clock=clock)
    for seq in range(6, 30, 2):
        clock.now += STALE_SECONDS - 1
        reader.data = build(sequence=seq)
        assert src.read_all()["cpu.temperature"].value == 55.5


def test_status_from_the_module_is_explained():
    src, _, _ = make(build(status=STATUS_NO_PAWNIO))
    r = src.read_all()["cpu.temperature"]
    assert r.value is None
    assert "PawnIO" in r.reason


def test_a_newer_layout_asks_to_update_luzeira():
    src, _, _ = make(build(version=2))
    assert "actualiza Luzeira" in src.read_all()["cpu.temperature"].reason


def test_an_untrusted_section_is_ignored_and_dropped():
    src, reader, _ = make(SectionUntrusted())
    r = src.read_all()["cpu.temperature"]
    assert r.value is None
    assert "no es de fiar" in r.reason
    assert reader.closed


def test_a_reader_error_never_breaks_the_source():
    src, reader, _ = make(OSError("OpenFileMappingW devolvió 5"))
    assert src.read_all()["cpu.temperature"].value is None
    assert reader.closed


def test_module_sensors_have_a_label_in_every_language():
    for lang in idioma.available():
        idioma.set_language(lang)
        src, _, _ = make(None)
        for info in src.sensors():
            assert info.label and not info.label.startswith("sensores."), (lang, info.id)
        for key in (
            modulo.NOT_INSTALLED,
            modulo.NOT_AVAILABLE,
            modulo.NOT_RESPONDING,
            modulo.NO_PAWNIO,
            modulo.UNSUPPORTED_CPU,
            modulo.STARTING,
            modulo.NEWER_MODULE,
            modulo.UNTRUSTED,
            modulo.NO_VALUE,
        ):
            assert idioma.t(key) != key, (lang, key)


def test_default_hub_gets_cpu_temperature_from_the_module():
    from luzeira.sensores.hub import SensorHub

    src, _, _ = make(build([("cpu.temperature", 50.0, FLAG_VALID)]))
    hub = SensorHub([src])
    hub.poll()
    assert hub.read("cpu.temperature").value == 50.0


@pytest.mark.skipif(sys.platform != "win32", reason="memoria compartida de Windows")
def test_real_reader_without_the_module_finds_nothing():
    # En el equipo de pruebas no está instalado el módulo: no existe la sección.
    from luzeira.sensores.windows_cpu import RealSectionReader

    reader = RealSectionReader(modulo.SECTION_NAME, modulo.SECTION_SIZE)
    try:
        assert reader.read() is None
    finally:
        reader.close()


@pytest.mark.skipif(sys.platform != "win32", reason="memoria compartida de Windows")
def test_real_reader_ignores_a_section_created_by_a_normal_user():
    # Cualquiera puede crear una sección en su propio espacio (``Local\\``): su
    # propietario es el usuario, no SYSTEM, así que el lector debe rechazarla.
    import ctypes
    from ctypes import wintypes

    from luzeira.sensores.windows_cpu import RealSectionReader

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileMappingW.restype = wintypes.HANDLE
    k32.CreateFileMappingW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPCWSTR,
    ]
    name = f"Local\\Luzeira-Prueba-{random.randrange(1 << 30)}"
    handle = k32.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, 4096, name)
    assert handle
    try:
        reader = RealSectionReader(name, modulo.SECTION_SIZE)
        with pytest.raises(SectionUntrusted):
            reader.read()
        reader.close()
    finally:
        k32.CloseHandle(handle)


def test_header_layout_matches_the_docstring():
    data = build(sequence=10)
    assert struct.unpack_from("<Q", data, 24)[0] == 10  # la secuencia, en el offset 24
