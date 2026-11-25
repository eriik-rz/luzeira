"""Lector del módulo opcional de sensores (``modulo``, ADR-007 y ADR-008).

El fichero lo escribe otro programa: todo lo que trae se trata como datos no fiables.
"""

from __future__ import annotations

import functools
import os
import random
import struct
import sys
from pathlib import Path

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
    # Una entrada de cada sensor, para que el fuzz pase por el rango de todos (R4-15).
    good = build([(s.id, (s.low + s.high) / 2, FLAG_VALID) for s in modulo.SENSORS])
    wild = [float("nan"), float("inf"), -float("inf"), -1e300, 1e300, -0.0]
    parsed = 0
    seen: set[str] = set()
    for _ in range(3000):
        data = bytearray(good)
        for _ in range(rng.randint(1, 12)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.5:
            # Además de bytes sueltos, un valor entero cambiado por uno cualquiera.
            entry = rng.randrange(len(modulo.SENSORS))
            value = rng.choice(wild) if rng.random() < 0.1 else rng.uniform(-100.0, 2000.0)
            struct.pack_into("<d", data, HEADER.size + entry * ENTRY.size + 32, value)
        if rng.random() < 0.2:
            # Cortado: casi siempre lo rechaza. Antes se cortaba siempre, y así casi
            # ningún caso llegaba a leer valores (medido: el fuzz no veía los rangos).
            data = data[: rng.randint(0, len(data))]
        try:
            section = parse_section(bytes(data))
        except BadSection:
            continue
        parsed += 1
        for sid, value in section.values.items():
            # R4-15: el rango de cada sensor, no uno que valga para todos.
            sensor = next(s for s in modulo.SENSORS if s.id == sid)
            assert sensor.low <= value <= sensor.high, (sid, value)
            seen.add(sid)
    # Que el fuzz de verdad llegue a los valores de todos los sensores.
    assert parsed > 500
    assert seen == {s.id for s in modulo.SENSORS}


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


def test_a_module_that_stops_writing_is_let_go_and_reopened_later():
    # R4-10 (ADR-008): sin datos nuevos en STALE_SECONDS se suelta el fichero (para que el
    # servicio pueda volver a crearlo) y no se reabre hasta pasado REOPEN_SECONDS.
    clock = Clock()
    src, reader, opened = make(build(sequence=4), clock=clock)
    assert src.read_all()["cpu.temperature"].value == 55.5
    clock.now += STALE_SECONDS + 1
    assert src.read_all()["cpu.temperature"].value is None
    assert reader.closed
    reader.closed = False
    clock.now += REOPEN_SECONDS - 1
    r = src.read_all()["cpu.temperature"]
    assert len(opened) == 1  # todavía no
    assert "no responde" in r.reason  # y no dice «instala el módulo»
    clock.now += 1
    # Al reabrir con la misma secuencia sigue sin responder (no parece fresca).
    assert src.read_all()["cpu.temperature"].value is None
    assert len(opened) == 2
    assert reader.closed
    reader.closed = False
    clock.now += REOPEN_SECONDS
    reader.data = build(sequence=6)  # el servicio ha vuelto
    assert src.read_all()["cpu.temperature"].value == 55.5
    assert len(opened) == 3


def test_an_untrusted_file_is_reported_while_waiting_to_reopen():
    clock = Clock()
    src, _, opened = make(SectionUntrusted(), clock=clock)
    src.read_all()
    clock.now += 1
    assert "no es de fiar" in src.read_all()["cpu.temperature"].reason
    assert len(opened) == 1


def test_the_header_layout_matches_the_docstring():
    data = build(sequence=10)
    assert struct.unpack_from("<Q", data, 24)[0] == 10  # la secuencia, en el offset 24


# --- quién puede escribir (la decisión, sin Windows) ------------------------------------

from luzeira.sistema import permisos  # noqa: E402

USER = "S-1-5-21-1-2-3-1001"
USERS = "S-1-5-32-545"
FULL = 0x1F01FF
READ = 0x1200A9
INHERIT_ONLY = 0x0B  # OBJECT_INHERIT | CONTAINER_INHERIT | INHERIT_ONLY


def _security(owner, *aces):
    return permisos.Security(
        owner, tuple(permisos.Ace(kind, flags, mask, sid) for sid, mask, flags, kind in aces)
    )


def _ace(sid, mask, flags=0, kind=0):
    return (sid, mask, flags, kind)


#: Lo que debe poner el instalador (ADR-008): SYSTEM y Administradores, todo; Usuarios, leer.
GOOD = (
    _ace(permisos.SID_SYSTEM, FULL, 0x3),
    _ace(permisos.SID_ADMINISTRATORS, FULL, 0x3),
    _ace(USERS, READ, 0x3),
)


@pytest.mark.parametrize("owner", [permisos.SID_SYSTEM, permisos.SID_ADMINISTRATORS])
@pytest.mark.parametrize("folder", [True, False])
def test_system_and_administrators_are_accepted(owner, folder):
    from luzeira.sensores.windows_cpu import check_security

    check_security(_security(owner, *GOOD), folder=folder, owners=permisos.ADMIN_OWNERS)


@pytest.mark.parametrize("owner", [USER, permisos.SID_TRUSTED_INSTALLER, None])
def test_any_other_owner_is_rejected(owner):
    from luzeira.sensores.windows_cpu import check_security

    with pytest.raises(SectionUntrusted):
        check_security(_security(owner, *GOOD), folder=False, owners=permisos.ADMIN_OWNERS)


@pytest.mark.parametrize(
    ("ace", "folder"),
    [
        (_ace(permisos.SID_EVERYONE, permisos.FILE_WRITE_DATA), False),
        (_ace(USERS, permisos.FILE_APPEND_DATA), False),
        (_ace(USER, permisos.DELETE), False),
        (_ace(USERS, permisos.WRITE_DAC), False),
        (_ace(USERS, permisos.WRITE_OWNER), True),
        (_ace(USERS, permisos.GENERIC_ALL), False),
        (_ace(USERS, permisos.GENERIC_WRITE, INHERIT_ONLY), True),  # para lo que se cree
        (_ace(USERS, permisos.FILE_ADD_FILE, 0x2), True),  # lo que hereda de ProgramData
        (_ace(USERS, permisos.FILE_ADD_SUBDIRECTORY), True),
        (_ace(USERS, permisos.FILE_DELETE_CHILD), True),
        (_ace(USERS, READ, 0, kind=0x5), False),  # tipo de ACE raro: se desconfía
    ],
)
def test_write_rights_for_anyone_else_are_rejected(ace, folder):
    from luzeira.sensores.windows_cpu import check_security

    security = _security(permisos.SID_ADMINISTRATORS, *GOOD, ace)
    with pytest.raises(SectionUntrusted):
        check_security(security, folder=folder, owners=permisos.ADMIN_OWNERS)


@pytest.mark.parametrize(
    "ace",
    [
        _ace(permisos.SID_TRUSTED_INSTALLER, FULL),
        _ace(permisos.SID_CREATOR_OWNER, permisos.GENERIC_ALL, INHERIT_ONLY),
        _ace(permisos.SID_EVERYONE, FULL, 0, kind=permisos.ACCESS_DENIED_ACE_TYPE),
        _ace(permisos.SID_EVERYONE, READ),  # leer, sí
    ],
)
def test_what_does_not_let_anyone_else_write_is_accepted(ace):
    from luzeira.sensores.windows_cpu import check_security

    security = _security(permisos.SID_SYSTEM, *GOOD, ace)
    check_security(security, folder=True, owners=permisos.ADMIN_OWNERS)


def test_no_dacl_means_anyone_can_write():
    from luzeira.sensores.windows_cpu import check_security

    with pytest.raises(SectionUntrusted):
        check_security(
            permisos.Security(permisos.SID_SYSTEM, None), folder=False, owners=permisos.ADMIN_OWNERS
        )


# --- el lector de verdad, con ficheros en una carpeta temporal --------------------------
# Nunca la carpeta real de %ProgramData%: se le pasa la ruta.

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="ficheros de Windows")


@functools.cache
def new_object_owner() -> str:
    """El propietario que Windows da a lo que crea este proceso: el usuario, o el grupo
    Administradores si el proceso está elevado (la CI de GitHub corre elevada)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "propietario"
        probe.write_bytes(b"")
        owner = permisos.path_security(probe).owner
    assert owner is not None
    return owner


def _set_dacl(path, sddl):
    """Pone a ``path`` la DACL de ``sddl`` (protegida si empieza por ``D:P``)."""
    import ctypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sd = ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None)
    present, defaulted, dacl = ctypes.c_int(), ctypes.c_int(), ctypes.c_void_p()
    adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 3
    assert adv.GetSecurityDescriptorDacl(
        sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
    )
    adv.SetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    protected = 0x80000000 if sddl.startswith("D:P") else 0x20000000  # PROTECTED / UNPROTECTED
    status = adv.SetNamedSecurityInfoW(str(path), 1, 0x4 | protected, None, None, dacl, None)
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree(sd)
    assert status == 0, status


@pytest.fixture
def module_dir(tmp_path):
    """Una carpeta como la del módulo, con su fichero; al acabar se le devuelve al
    usuario el control total para que pytest la pueda borrar."""
    folder = tmp_path / "Luzeira Sensores"
    folder.mkdir()
    (folder / modulo.FILE_NAME).write_bytes(build())
    yield folder
    me = permisos.current_user_sid()
    for path in (folder / modulo.FILE_NAME, folder):
        if path.exists():
            _set_dacl(path, f"D:P(A;OICI;FA;;;{me})")


def _lock_down(folder, extra=""):
    """La ACL que pondría el instalador, con el usuario de los tests en el papel de
    «Usuarios»: él solo lee."""
    me = permisos.current_user_sid()
    _set_dacl(folder / modulo.FILE_NAME, f"D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;{me}){extra}")
    _set_dacl(folder, f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;{me})")


def _reader(folder, **kwargs):
    from luzeira.sensores.windows_cpu import RealModuleFileReader

    return RealModuleFileReader(folder, modulo.FILE_NAME, modulo.SECTION_SIZE, **kwargs)


def _me_as_owner():
    # El dueño de lo que crea el test: el usuario, o Administradores si corre elevado.
    return frozenset({new_object_owner()})


@windows_only
def test_real_reader_without_the_module_finds_nothing(tmp_path, module_dir):
    reader = _reader(tmp_path / "no-existe")
    assert reader.read() is None
    reader.close()
    # La carpeta bien protegida, pero sin fichero (el servicio aún no lo ha creado).
    (module_dir / modulo.FILE_NAME).unlink()
    me = permisos.current_user_sid()
    _set_dacl(module_dir, f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;{me})")
    reader = _reader(module_dir, owners=_me_as_owner())
    assert reader.read() is None
    assert reader.closed


@windows_only
def test_real_reader_rejects_a_file_owned_by_a_normal_user(module_dir):
    if new_object_owner() != permisos.current_user_sid():
        pytest.skip("elevado (la CI): lo que crea es de Administradores, no del usuario")
    # Cualquiera puede crear una carpeta con ese nombre (en ProgramData, también): su
    # propietario es el usuario, no SYSTEM ni Administradores. Con los permisos bien
    # puestos (para que solo pueda fallar por el propietario).
    _lock_down(module_dir)
    reader = _reader(module_dir)
    with pytest.raises(SectionUntrusted, match="propietario"):
        reader.read()
    assert reader.closed


@windows_only
def test_real_reader_reads_a_well_protected_file_and_never_past_its_size(module_dir):
    _lock_down(module_dir)
    reader = _reader(module_dir, owners=_me_as_owner())
    try:
        data = reader.read()
        assert data == build()  # 112 bytes: ni uno más, aunque SECTION_SIZE sea mayor
        assert parse_section(data).values == {"cpu.temperature": 55.5}
    finally:
        reader.close()
    assert reader.closed


@windows_only
def test_real_reader_accepts_system_and_administrators_as_owners(module_dir):
    # Un fichero de verdad, con los permisos leídos de verdad pero el propietario
    # cambiado por el de SYSTEM o Administradores (en tmp_path no se puede poner).
    _lock_down(module_dir)
    for owner in (permisos.SID_SYSTEM, permisos.SID_ADMINISTRATORS):

        def security(handle, owner=owner):
            real = permisos.handle_security(handle)
            return permisos.Security(owner, real.dacl)

        reader = _reader(module_dir, security=security)
        try:
            assert reader.read() == build()
        finally:
            reader.close()


@windows_only
def test_real_reader_rejects_a_file_anyone_can_write(module_dir):
    _lock_down(module_dir, extra="(A;;FA;;;WD)")  # Todos: control total sobre el fichero
    reader = _reader(module_dir, owners=_me_as_owner())
    with pytest.raises(SectionUntrusted, match="permisos"):
        reader.read()
    assert reader.closed


@windows_only
def test_real_reader_rejects_a_folder_anyone_can_add_files_to(module_dir):
    _lock_down(module_dir)
    me = permisos.current_user_sid()
    _set_dacl(module_dir, f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;;0x1200a9;;;{me})(A;;0x2;;;WD)")
    reader = _reader(module_dir, owners=_me_as_owner())
    with pytest.raises(SectionUntrusted, match="permisos"):
        reader.read()


@windows_only
def test_real_reader_rejects_a_junction_instead_of_the_folder(module_dir, tmp_path):
    import _winapi

    _lock_down(module_dir)
    link = tmp_path / "enlace"
    _winapi.CreateJunction(str(module_dir), str(link))
    reader = _reader(link, owners=_me_as_owner())
    with pytest.raises(SectionUntrusted, match="enlace"):
        reader.read()
    assert reader.closed


@windows_only
def test_while_the_reader_holds_the_file_nobody_can_swap_it(module_dir, tmp_path):
    # El lector abre sin FILE_SHARE_DELETE: mientras lo tiene, ni el fichero ni la carpeta
    # se pueden renombrar, reemplazar ni borrar (si no, alguien cambiaría lo comprobado
    # por otra cosa). Los permisos reales dejan al usuario hacer de todo, así que el
    # único freno es el modo de compartir; la comprobación de permisos se inyecta limpia.
    clean = permisos.Security(
        permisos.SID_SYSTEM, tuple(permisos.Ace(kind, f, m, s) for s, m, f, kind in GOOD)
    )
    reader = _reader(module_dir, security=lambda _h: clean)
    target = module_dir / modulo.FILE_NAME
    other = tmp_path / "otro.bin"
    other.write_bytes(build(sequence=8))
    try:
        assert reader.read() == build()
        attempts = [
            lambda: os.replace(other, target),
            lambda: target.rename(module_dir / "movido.bin"),
            lambda: target.unlink(),
            lambda: module_dir.rename(tmp_path / "carpeta-movida"),
        ]
        for attempt in attempts:
            with pytest.raises(PermissionError) as caught:
                attempt()
            assert caught.value.winerror in (5, 32)  # acceso denegado / en uso
        assert reader.read() == build()
    finally:
        reader.close()
    target.unlink()  # ya suelto, sí se puede


@windows_only
def test_real_reader_rejects_a_file_smaller_than_the_header(module_dir):
    (module_dir / modulo.FILE_NAME).write_bytes(b"LUZSENS\0")
    _lock_down(module_dir)
    reader = _reader(module_dir, owners=_me_as_owner())
    with pytest.raises(OSError, match="pequeño"):
        reader.read()
    assert reader.closed


@windows_only
def test_programdata_comes_from_windows_not_from_the_environment(monkeypatch, tmp_path):
    import os

    from luzeira.sensores.windows_cpu import programdata_dir

    real = os.environ.get("PROGRAMDATA") or os.environ["ALLUSERSPROFILE"]
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setenv("ALLUSERSPROFILE", str(tmp_path))
    assert str(programdata_dir()).casefold() == real.casefold()


def test_the_default_opener_points_at_programdata(monkeypatch, tmp_path):
    from luzeira.sensores import windows_cpu

    seen = {}

    class Spy:
        def __init__(self, folder, name, size):
            seen.update(folder=folder, name=name, size=size)

    monkeypatch.setattr(windows_cpu, "RealModuleFileReader", Spy)
    monkeypatch.setattr(windows_cpu, "programdata_dir", lambda: tmp_path)
    modulo.real_reader()
    assert seen == {
        "folder": tmp_path / "Luzeira Sensores",
        "name": "sensores.bin",
        "size": modulo.SECTION_SIZE,
    }
