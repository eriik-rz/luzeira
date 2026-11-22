"""Tests de los sensores. Casi todos sin hardware: psutil y NVML se sustituyen por
objetos falsos con ``monkeypatch``. Solo el último lee la GPU real si existe."""

from __future__ import annotations

import logging
import sys
from collections import namedtuple
from types import SimpleNamespace

import pytest

from luzeira.idioma import t
from luzeira.sensores import nvidia, sistema
from luzeira.sensores.base import SENSOR_ID, Reading, SensorInfo
from luzeira.sensores.hub import SensorHub

CpuTimes = namedtuple("CpuTimes", "user system idle")
NicIO = namedtuple("NicIO", "bytes_sent bytes_recv")
DiskIO = namedtuple("DiskIO", "read_bytes write_bytes")
Part = namedtuple("Part", "device mountpoint fstype opts")
VMem = namedtuple("VMem", "total available percent")
Usage = namedtuple("Usage", "percent")

GIB = 1024**3


# --- psutil falso -------------------------------------------------------------------


class FakePsutil:
    """Contadores que el test avanza a mano, con un reloj también manual."""

    def __init__(self) -> None:
        self.now = 100.0
        self.cpu = CpuTimes(user=10.0, system=10.0, idle=80.0)
        self.nics = {
            "Ethernet": NicIO(1_000_000, 5_000_000),
            "Loopback Pseudo-Interface 1": NicIO(0, 0),
        }
        self.disks = {"PhysicalDrive0": DiskIO(0, 0)}
        self.fail_memory = False

    # API de psutil que usa SystemSource
    def cpu_times(self):
        return self.cpu

    def cpu_freq(self):
        return SimpleNamespace(current=3001.0)

    def virtual_memory(self):
        if self.fail_memory:
            raise OSError("fallo simulado")
        return VMem(total=32 * GIB, available=8 * GIB, percent=75.0)

    def net_io_counters(self, pernic=False):
        return dict(self.nics)

    def disk_partitions(self, all=False):
        return [
            Part("C:\\", "C:\\", "NTFS", "rw,fixed"),
            Part("D:\\", "D:\\", "", "cdrom"),
        ]

    def disk_usage(self, path):
        return Usage(42.5)

    def disk_io_counters(self, perdisk=False):
        if perdisk:
            return dict(self.disks)
        return DiskIO(
            sum(d.read_bytes for d in self.disks.values()),
            sum(d.write_bytes for d in self.disks.values()),
        )

    def boot_time(self):
        return 0.0


@pytest.fixture
def fake(monkeypatch):
    fp = FakePsutil()
    monkeypatch.setattr(sistema, "psutil", fp)
    return fp


def make_source(fp: FakePsutil, physical_nics=None) -> sistema.SystemSource:
    # Sin ``physical_nics`` se cuentan todas: los nombres falsos no son de este equipo.
    return sistema.SystemSource(
        clock=lambda: fp.now, physical_nics=physical_nics, memory=sistema._psutil_memory
    )


def test_system_sensor_ids_follow_contract(fake):
    src = make_source(fake)
    ids = [s.id for s in src.sensors()]
    assert all(SENSOR_ID.match(i) for i in ids)
    assert "disk.usage.c" in ids
    assert "disk.usage.d" not in ids  # el lector de CD no es unidad fija
    assert "cpu.temperature" in ids
    assert len(ids) == len(set(ids))


def test_first_read_has_no_rates_and_says_measuring(fake):
    src = make_source(fake)
    r = src.read_all()
    for sid in ("network.upload", "network.download", "disk.read", "disk.write", "cpu.usage"):
        assert r[sid].value is None, sid
        assert r[sid].reason == "Midiendo…"


def test_rates_use_real_elapsed_interval(fake):
    src = make_source(fake)
    src.read_all()
    # 2,5 s después: 5 MB subidos, 10 MB bajados, 25 MB leídos y 50 MB escritos.
    fake.now += 2.5
    fake.nics["Ethernet"] = NicIO(1_000_000 + 5_000_000, 5_000_000 + 10_000_000)
    fake.nics["Loopback Pseudo-Interface 1"] = NicIO(999_000_000, 999_000_000)  # se ignora
    fake.disks["PhysicalDrive0"] = DiskIO(25_000_000, 50_000_000)
    r = src.read_all()
    assert r["network.upload"] == Reading(2.0, "MB/s")
    assert r["network.download"] == Reading(4.0, "MB/s")
    assert r["disk.read"] == Reading(10.0, "MB/s")
    assert r["disk.write"] == Reading(20.0, "MB/s")


def test_counter_going_backwards_gives_no_value(fake):
    src = make_source(fake)
    src.read_all()
    fake.now += 1
    fake.nics["Ethernet"] = NicIO(0, 0)  # adaptador reiniciado
    r = src.read_all()
    assert r["network.upload"].value is None
    assert r["network.download"].value is None


def test_new_nic_with_accumulated_counters_gives_no_spike(fake):
    src = make_source(fake)
    src.read_all()
    fake.now += 1
    fake.nics["Ethernet"] = NicIO(1_000_000 + 1_000_000, 5_000_000 + 2_000_000)
    # Tailscale se conecta con 8 GB ya acumulados: no es tráfico de este segundo.
    fake.nics["Tailscale"] = NicIO(8_000_000_000, 8_000_000_000)
    r = src.read_all()
    assert r["network.upload"] == Reading(1.0, "MB/s")
    assert r["network.download"] == Reading(2.0, "MB/s")
    # Y si desaparece, tampoco da un valor negativo ni raro.
    fake.now += 1
    del fake.nics["Tailscale"]
    r = src.read_all()
    assert r["network.upload"] == Reading(0.0, "MB/s")


def test_one_nic_reset_does_not_hide_the_others(fake):
    fake.nics["Wi-Fi"] = NicIO(0, 0)
    src = make_source(fake)
    src.read_all()
    fake.now += 1
    fake.nics["Ethernet"] = NicIO(0, 0)  # reiniciada: se descarta solo esta
    fake.nics["Wi-Fi"] = NicIO(3_000_000, 4_000_000)
    r = src.read_all()
    assert r["network.upload"] == Reading(3.0, "MB/s")
    assert r["network.download"] == Reading(4.0, "MB/s")


def test_new_disk_with_accumulated_counters_gives_no_spike(fake):
    src = make_source(fake)
    src.read_all()
    fake.now += 1
    fake.disks["PhysicalDrive0"] = DiskIO(5_000_000, 6_000_000)
    fake.disks["PhysicalDrive1"] = DiskIO(900_000_000_000, 900_000_000_000)  # USB nuevo
    r = src.read_all()
    assert r["disk.read"] == Reading(5.0, "MB/s")
    assert r["disk.write"] == Reading(6.0, "MB/s")


def test_local_area_connection_is_not_loopback(fake):
    # «Local Area Connection» es la tarjeta principal en un Windows en inglés.
    fake.nics = {
        "Local Area Connection": NicIO(0, 0),
        "Loopback Pseudo-Interface 1": NicIO(0, 0),
        "lo": NicIO(0, 0),
    }
    src = make_source(fake)
    src.read_all()
    fake.now += 1
    fake.nics["Local Area Connection"] = NicIO(2_000_000, 3_000_000)
    fake.nics["Loopback Pseudo-Interface 1"] = NicIO(500_000_000, 500_000_000)
    fake.nics["lo"] = NicIO(700_000_000, 700_000_000)
    r = src.read_all()
    assert r["network.upload"] == Reading(2.0, "MB/s")
    assert r["network.download"] == Reading(3.0, "MB/s")


@pytest.mark.parametrize("gap", [3600.0, 10.5, 0.1, -1.0])
def test_unreliable_interval_says_measuring_and_resets_base(fake, gap):
    rate_ids = ("network.upload", "network.download", "disk.read", "disk.write", "cpu.usage")
    src = make_source(fake)
    src.read_all()
    # Tras suspender (o en ráfaga) los contadores han avanzado lo que sea.
    fake.now += gap
    fake.nics["Ethernet"] = NicIO(900_000_000, 900_000_000)
    fake.disks["PhysicalDrive0"] = DiskIO(900_000_000, 900_000_000)
    fake.cpu = CpuTimes(user=500.0, system=500.0, idle=900.0)
    r = src.read_all()
    for sid in rate_ids:
        assert r[sid] == Reading(None, r[sid].unit, reason="Midiendo…"), sid
    # La lectura anterior es la nueva base: 1 s después ya hay valores normales.
    fake.now += 1
    fake.nics["Ethernet"] = NicIO(901_000_000, 902_000_000)
    fake.disks["PhysicalDrive0"] = DiskIO(903_000_000, 904_000_000)
    fake.cpu = CpuTimes(user=501.0, system=500.0, idle=901.0)
    r = src.read_all()
    assert r["network.upload"] == Reading(1.0, "MB/s")
    assert r["network.download"] == Reading(2.0, "MB/s")
    assert r["disk.read"] == Reading(3.0, "MB/s")
    assert r["disk.write"] == Reading(4.0, "MB/s")
    assert r["cpu.usage"] == Reading(50.0, "%")


@pytest.mark.parametrize("gap", [0.2, 10.0])
def test_interval_limits_are_inclusive(fake, gap):
    src = make_source(fake)
    src.read_all()
    fake.now += gap
    assert src.read_all()["network.upload"].value == 0.0


def test_cpu_usage_from_time_deltas(fake):
    src = make_source(fake)
    src.read_all()
    fake.now += 1
    # +3 s ocupada y +1 s inactiva → 75 %
    fake.cpu = CpuTimes(user=12.0, system=11.0, idle=81.0)
    assert src.read_all()["cpu.usage"] == Reading(75.0, "%")


def test_static_values_and_cpu_temperature_reason(fake, monkeypatch):
    monkeypatch.setattr(sistema.time, "time", lambda: 7200.0)
    r = make_source(fake).read_all()
    assert r["memory.total"] == Reading(32.0, "GB")
    assert r["memory.used"] == Reading(24.0, "GB")
    assert r["memory.usage"] == Reading(75.0, "%")
    assert r["disk.usage.c"] == Reading(42.5, "%")
    assert r["system.uptime"] == Reading(2.0, "h")
    assert r["cpu.temperature"].value is None
    assert "sensores avanzados" in r["cpu.temperature"].reason


def test_cpu_frequency_is_withheld_on_windows(fake, monkeypatch):
    monkeypatch.setattr(sistema.sys, "platform", "win32")
    r = make_source(fake).read_all()
    assert r["cpu.frequency"].value is None
    assert "nominal" in r["cpu.frequency"].reason
    monkeypatch.setattr(sistema.sys, "platform", "linux")
    assert make_source(fake).read_all()["cpu.frequency"] == Reading(3001, "MHz")


def test_failing_block_does_not_break_the_others(fake):
    fake.fail_memory = True
    r = make_source(fake).read_all()
    assert r["memory.usage"].value is None
    assert r["memory.used"].unit == "GB"
    assert "OSError" in r["memory.usage"].reason
    assert r["disk.usage.c"].value == 42.5


# --- NVML falso ---------------------------------------------------------------------


class FakeNotSupported(Exception):
    pass


def make_fake_nvml(*, init_error: Exception | None = None, fan_supported: bool = True):
    # ``init_error`` se puede cambiar después con calls["init_error"] = None.
    calls = {"shutdown": 0, "init": 0, "init_error": init_error}

    def init():
        calls["init"] += 1
        if calls["init_error"]:
            raise calls["init_error"]

    def fan(h):
        if not fan_supported:
            raise FakeNotSupported()
        return 30

    def shutdown():
        calls["shutdown"] += 1

    mod = SimpleNamespace(
        NVMLError_NotSupported=FakeNotSupported,
        NVML_TEMPERATURE_GPU=0,
        NVML_CLOCK_GRAPHICS=0,
        nvmlInit=init,
        nvmlShutdown=shutdown,
        nvmlDeviceGetHandleByIndex=lambda i: object(),
        nvmlDeviceGetTemperature=lambda h, s: 55,
        nvmlDeviceGetUtilizationRates=lambda h: SimpleNamespace(gpu=40, memory=10),
        nvmlDeviceGetMemoryInfo=lambda h: SimpleNamespace(total=12 * GIB, used=3 * GIB),
        nvmlDeviceGetPowerUsage=lambda h: 150_500,
        nvmlDeviceGetFanSpeed=fan,
        nvmlDeviceGetClockInfo=lambda h, c: 2610,
    )
    return mod, calls


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_nvml_unavailable_declares_sensors_without_value(monkeypatch):
    mod, _ = make_fake_nvml(init_error=RuntimeError("sin controlador"))
    monkeypatch.setattr(nvidia, "pynvml", mod)
    src = nvidia.NvidiaSource(clock=Clock())
    ids = {s.id for s in src.sensors()}
    assert "gpu.temperature" in ids
    r = src.read_all()
    assert set(r) == ids
    assert all(x.value is None and x.reason == t(nvidia.NOT_READY) for x in r.values())
    assert r["gpu.power"].unit == "W"
    src.close()


def test_nvml_retries_every_30_s_until_it_starts(monkeypatch):
    mod, calls = make_fake_nvml(init_error=RuntimeError("controlador aún cargando"))
    monkeypatch.setattr(nvidia, "pynvml", mod)
    clock = Clock()
    src = nvidia.NvidiaSource(clock=clock)
    assert calls["init"] == 1
    clock.now += 29
    src.read_all()
    assert calls["init"] == 1  # antes de 30 s no se reintenta
    calls["init_error"] = None  # el controlador ya ha cargado
    clock.now += 1
    r = src.read_all()
    assert calls["init"] == 2
    assert r["gpu.temperature"] == Reading(55.0, "°C")
    clock.now += 100
    src.read_all()
    assert calls["init"] == 2  # funcionando, no se vuelve a llamar
    src.close()


def test_nvml_gives_up_wording_after_5_minutes_but_keeps_retrying(monkeypatch):
    mod, calls = make_fake_nvml(init_error=RuntimeError("sin NVIDIA"))
    monkeypatch.setattr(nvidia, "pynvml", mod)
    clock = Clock()
    src = nvidia.NvidiaSource(clock=clock)
    clock.now += nvidia.GIVE_UP_SECONDS
    r = src.read_all()
    assert r["gpu.usage"].reason == t(nvidia.NOT_FOUND)
    assert calls["init"] == 2
    src.close()
    clock.now += 60
    src.read_all()
    assert calls["init"] == 2  # una fuente cerrada no reabre NVML


def test_nvml_reads_and_converts_units(monkeypatch):
    mod, calls = make_fake_nvml()
    monkeypatch.setattr(nvidia, "pynvml", mod)
    src = nvidia.NvidiaSource()
    r = src.read_all()
    assert {s.id for s in src.sensors()} == set(r)
    assert r["gpu.temperature"] == Reading(55.0, "°C")
    assert r["gpu.usage"] == Reading(40.0, "%")
    assert r["gpu.memory_total"] == Reading(12.0, "GB")
    assert r["gpu.memory_used"] == Reading(3.0, "GB")
    assert r["gpu.memory_usage"] == Reading(25.0, "%")
    assert r["gpu.power"] == Reading(150.5, "W")
    assert r["gpu.fan"] == Reading(30.0, "%")
    assert r["gpu.clock"] == Reading(2610.0, "MHz")
    src.close()
    src.close()  # cerrar dos veces no debe llamar dos veces a nvmlShutdown
    assert calls["shutdown"] == 1


def test_nvml_unsupported_sensor_has_reason(monkeypatch):
    mod, _ = make_fake_nvml(fan_supported=False)
    monkeypatch.setattr(nvidia, "pynvml", mod)
    r = nvidia.NvidiaSource().read_all()
    assert r["gpu.fan"].value is None
    assert r["gpu.fan"].unit == "%"
    assert r["gpu.fan"].reason == t(nvidia.NOT_SUPPORTED)
    assert r["gpu.temperature"].value == 55.0


def test_nvml_prefix_allows_several_gpus(monkeypatch):
    mod, _ = make_fake_nvml()
    monkeypatch.setattr(nvidia, "pynvml", mod)
    ids = {s.id for s in nvidia.NvidiaSource(index=1, prefix="gpu1").sensors()}
    assert "gpu1.temperature" in ids


# --- hub ----------------------------------------------------------------------------


class StaticSource:
    def __init__(self) -> None:
        self.closed = False

    def sensors(self):
        return [SensorInfo("test.value", "Valor", "W", "system")]

    def read_all(self):
        return {"test.value": Reading(5.0, "W")}

    def close(self):
        self.closed = True


class BrokenSource:
    def sensors(self):
        return [SensorInfo("broken.value", "Roto", "°C", "system")]

    def read_all(self):
        raise RuntimeError("C:\\Users\\alguien\\secreto")

    def close(self):
        raise RuntimeError("tampoco cierra")


def test_hub_survives_a_failing_source(caplog):
    good = StaticSource()
    hub = SensorHub([BrokenSource(), good])
    assert [s.id for s in hub.available()] == ["broken.value", "test.value"]
    with caplog.at_level(logging.WARNING):
        hub.poll()
    assert hub.read("test.value") == Reading(5.0, "W")
    broken = hub.read("broken.value")
    assert broken.value is None
    assert broken.unit == "°C"
    assert broken.reason
    assert "BrokenSource" in caplog.text
    assert "alguien" not in caplog.text  # el mensaje con la ruta no se registra
    hub.close()
    assert good.closed  # el fallo al cerrar la rota no impide cerrar la buena


def test_hub_unknown_and_not_yet_read():
    hub = SensorHub([StaticSource()])
    assert hub.read("nope.x") == Reading(None, "", reason="Sensor desconocido: nope.x")
    before = hub.read("test.value")
    assert before.value is None
    assert before.unit == "W"
    hub.poll()
    assert hub.read("test.value").value == 5.0


# --- hardware real ------------------------------------------------------------------


def _has_nvidia() -> bool:
    # sensors() se declara siempre; lo que dice si hay GPU es que haya valor.
    src = nvidia.NvidiaSource()
    try:
        return src.read_all()["gpu.temperature"].value is not None
    finally:
        src.close()


@pytest.mark.skipif(not _has_nvidia(), reason="No hay GPU NVIDIA con NVML en este equipo")
def test_real_nvidia_values_are_plausible():
    src = nvidia.NvidiaSource()
    try:
        r = src.read_all()
    finally:
        src.close()
    assert 10 <= r["gpu.temperature"].value <= 110
    assert 0 <= r["gpu.usage"].value <= 100
    assert 0 < r["gpu.memory_total"].value < 200
    assert 0 <= r["gpu.memory_usage"].value <= 100


def test_text_sensors_are_declared_as_text_and_read_real_names():
    # C-12: nombre del procesador y de la GPU, como texto (nunca nombre de usuario ni de
    # equipo). En el equipo de pruebas: «AMD Ryzen 7 5700X3D…» y «GeForce RTX 4070 SUPER».
    from luzeira.sensores import default_hub

    hub = default_hub()
    try:
        hub.poll()
        kinds = {info.id: info.kind for info in hub.available()}
        assert kinds["cpu.name"] == "text" and kinds["cpu.usage"] == "number"
        cpu = hub.read("cpu.name")
        assert cpu.value is None and cpu.text and "  " not in cpu.text
        if kinds.get("gpu.name") == "text" and hub.read("gpu.temperature").value is not None:
            gpu = hub.read("gpu.name")
            assert gpu.text and not gpu.text.startswith("NVIDIA ")
    finally:
        hub.close()


# --- C-11: la VPN no cuenta dos veces ----------------------------------------------------


def test_vpn_traffic_is_not_counted_twice(fake):
    src = make_source(fake, physical_nics=lambda: frozenset({"Ethernet"}))
    fake.nics["Tailscale"] = NicIO(0, 0)
    src.read_all()
    fake.now += 1
    # 1 MB sale cifrado por la tarjeta y el mismo 1 MB pasa en claro por la VPN.
    fake.nics["Ethernet"] = NicIO(1_000_000 + 1_000_000, 5_000_000)
    fake.nics["Tailscale"] = NicIO(1_000_000, 0)
    assert src.read_all()["network.upload"] == Reading(1.0, "MB/s")


def test_physical_list_that_matches_nothing_counts_everything(fake):
    src = make_source(fake, physical_nics=lambda: frozenset({"Otra"}))
    src.read_all()
    fake.now += 1
    fake.nics["Ethernet"] = NicIO(1_000_000 + 2_000_000, 5_000_000)
    assert src.read_all()["network.upload"] == Reading(2.0, "MB/s")


def test_physical_list_failing_counts_everything_and_is_retried(fake):
    calls = []

    def lister():
        calls.append(fake.now)
        raise OSError("sin permiso")

    src = make_source(fake, physical_nics=lister)
    src.read_all()
    fake.now += 1
    fake.nics["Ethernet"] = NicIO(1_000_000 + 2_000_000, 5_000_000)
    assert src.read_all()["network.upload"] == Reading(2.0, "MB/s")
    fake.now += sistema._PHYSICAL_TTL
    src.read_all()
    assert len(calls) == 2  # una vez al empezar y otra pasados 30 s, no en cada lectura


@pytest.mark.skipif(sys.platform != "win32", reason="GetIfTable2 solo existe en Windows")
def test_real_windows_interfaces_are_read():
    from luzeira.sensores import windows_red

    interfaces = windows_red.list_interfaces()
    assert interfaces and all(isinstance(i.name, str) and i.name for i in interfaces)
    # El bucle local nunca es una tarjeta de verdad.
    assert not any(i.physical for i in interfaces if "loopback" in i.name.lower())


# --- C-20: memoria con GlobalMemoryStatusEx ---------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="GlobalMemoryStatusEx solo existe en Windows")
def test_windows_memory_matches_psutil():
    import psutil

    total, available = sistema._windows_memory()
    vm = psutil.virtual_memory()
    assert total == vm.total
    assert abs(available - vm.available) < 256 * 1024**2  # cambia entre las dos lecturas


def test_memory_reading_from_total_and_available(fake):
    src = sistema.SystemSource(
        clock=lambda: fake.now, physical_nics=None, memory=lambda: (32 * GIB, 8 * GIB)
    )
    r = src.read_all()
    assert r["memory.usage"] == Reading(75.0, "%")
    assert r["memory.used"] == Reading(24.0, "GB")
    assert r["memory.total"] == Reading(32.0, "GB")
