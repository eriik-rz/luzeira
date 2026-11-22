"""Tests de la GPU por Windows (PDH, DXGI, D3DKMT) y de cómo se junta con NVML.

Casi todos con una API de Windows falsa: no tocan el hardware. Los últimos leen la GPU
real si la hay (solo lectura, sin admin)."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from luzeira.idioma import t
from luzeira.sensores import nvidia, windows_gpu
from luzeira.sensores.base import Reading, SensorInfo
from luzeira.sensores.gpu import GpuSource
from luzeira.sensores.windows_gpu import (
    Adapter,
    PdhSample,
    PerfCaps,
    PerfData,
    WindowsGpuSource,
    choose_adapter,
    dedicated_usage,
    engine_usage,
)

GIB = 1024**3
LUID = 0x15ED9  # alto 0, bajo 0x15ED9: «luid_0x00000000_0x00015ED9»
DUP = 0x209CD  # la tarjeta repetida que lista DXGI sin datos en PDH
SOFT = 0x17485  # Microsoft Basic Render Driver


def eng(pid: int, luid: int, n: int, kind: str = "3D") -> str:
    return (
        f"pid_{pid}_luid_0x{luid >> 32:08X}_0x{luid & 0xFFFFFFFF:08X}_phys_0_eng_{n}_engtype_{kind}"
    )


def mem(luid: int) -> str:
    return f"luid_0x{luid >> 32:08X}_0x{luid & 0xFFFFFFFF:08X}_phys_0"


RTX = Adapter(LUID, "NVIDIA GeForce RTX 4070 SUPER", 0x10DE, 12 * GIB, False)
RTX_DUP = Adapter(DUP, "NVIDIA GeForce RTX 4070 SUPER", 0x10DE, 12 * GIB, False)
BASIC = Adapter(SOFT, "Microsoft Basic Render Driver", 0x1414, 0, True)
RADEON = Adapter(0x9999, "AMD Radeon(TM) RX 7800 XT", 0x1002, 16 * GIB, False)


def sample(luid: int = LUID, used: float = 3 * GIB) -> PdhSample:
    return PdhSample(
        engines={
            eng(10, luid, 0): 20.0,
            eng(11, luid, 0): 15.0,  # mismo motor, otro proceso: se suman → 35
            eng(10, luid, 9, "Graphics_1"): 30.0,
            eng(12, SOFT, 0): 0.0,
        },
        dedicated={mem(luid): used, mem(SOFT): 0.0},
    )


# --- API falsa ----------------------------------------------------------------------


class FakeCounters:
    def __init__(self, samples: list[PdhSample | None]) -> None:
        self.samples = list(samples)
        self.closed = False
        self.fail = False
        self.collects = 0

    def collect(self) -> PdhSample | None:
        self.collects += 1
        if self.fail:
            raise OSError("PdhCollectQueryData devolvió 0xC0000BBA")
        if len(self.samples) > 1:
            return self.samples.pop(0)
        return self.samples[0]

    def close(self) -> None:
        self.closed = True


class FakeKmt:
    # Por defecto, lo medido en la RTX 4070 SUPER: 56,0 °C, 1346 de 3200 RPM.
    def __init__(self, perf=None, caps=None, hz=2_610_000_000):
        self.perf = perf or PerfData(560, 1346, 513)
        self.caps = caps or PerfCaps(3200, 900)
        self.hz = hz
        self.closed = False
        self.fail_perf = False

    def perf_data(self) -> PerfData:
        if self.fail_perf:
            raise OSError("ADAPTERPERFDATA devolvió 0xC000000D")
        return self.perf

    def perf_caps(self) -> PerfCaps:
        if self.caps is None:
            raise OSError("ADAPTERPERFDATA_CAPS devolvió 0xC000000D")
        return self.caps

    def clock_hz(self) -> int:
        return self.hz

    def close(self) -> None:
        self.closed = True


class FakeApi:
    def __init__(self, adapters=None, samples=None, kmt: FakeKmt | None = None) -> None:
        self.adapters = [RTX, RTX_DUP, BASIC] if adapters is None else adapters
        # Primera muestra None (PDH aún no tiene dos), y luego datos.
        self.counters = FakeCounters([None, sample()] if samples is None else samples)
        self.kmt = FakeKmt() if kmt is None else kmt
        self.fail_dxgi = False
        self.fail_pdh = False
        self.fail_kmt = False
        self.opened_luids: list[int] = []
        self.calls = 0

    def list_adapters(self) -> list[Adapter]:
        self.calls += 1
        if self.fail_dxgi:
            raise OSError("CreateDXGIFactory1 devolvió 0x887A0004")
        return list(self.adapters)

    def open_counters(self) -> FakeCounters:
        self.calls += 1
        if self.fail_pdh:
            raise OSError("PdhAddEnglishCounterW devolvió 0xC0000BB8")
        return self.counters

    def open_adapter(self, luid: int) -> FakeKmt:
        self.calls += 1
        self.opened_luids.append(luid)
        if self.fail_kmt:
            raise OSError("OpenAdapterFromLuid devolvió 0xC000000D")
        return self.kmt


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(api: FakeApi, clock: Clock | None = None) -> WindowsGpuSource:
    return WindowsGpuSource(api=api, clock=clock or Clock(), sleep=lambda s: None, background=False)


# --- cálculos -----------------------------------------------------------------------


def test_usage_is_busiest_engine_summing_processes():
    usage = engine_usage(sample().engines)
    # Motor 0: 20 + 15 = 35; motor 9: 30. El más ocupado, no la suma (65) ni el «3D».
    assert usage[LUID] == 35.0
    assert usage[SOFT] == 0.0


def test_usage_is_clamped_to_100():
    engines = {eng(1, LUID, 0): 80.0, eng(2, LUID, 0): 70.0}
    assert engine_usage(engines)[LUID] == 100.0


def test_usage_ignores_unknown_instance_names():
    assert engine_usage({"_Total": 50.0, "otra cosa": 1.0}) == {}


def test_dedicated_memory_by_luid():
    assert dedicated_usage(sample().dedicated) == {LUID: 3 * GIB, SOFT: 0.0}


def test_luid_high_part_is_parsed():
    big = (0x1 << 32) | 0x2A
    assert engine_usage({eng(1, big, 0): 10.0}) == {big: 10.0}


def test_choose_skips_software_and_duplicate_without_pdh():
    assert choose_adapter([RTX_DUP, BASIC, RTX], {LUID, SOFT}) == RTX
    assert choose_adapter([BASIC], {SOFT}) is None


def test_choose_falls_back_to_hardware_when_pdh_knows_none():
    assert choose_adapter([BASIC, RTX], set()) == RTX


def test_choose_prefers_most_dedicated_memory():
    igpu = Adapter(0x1, "Intel(R) UHD Graphics 770", 0x8086, 128 * 1024**2, False)
    assert choose_adapter([igpu, RADEON], {0x1, RADEON.luid}) == RADEON


def test_clean_name():
    assert windows_gpu.clean_name("NVIDIA GeForce RTX 4070 SUPER") == "GeForce RTX 4070 SUPER"
    assert windows_gpu.clean_name("Intel(R)  UHD Graphics 770") == "Intel UHD Graphics 770"
    assert windows_gpu.clean_name("AMD Radeon(TM) RX 7800 XT") == "AMD Radeon RX 7800 XT"


# --- la fuente ----------------------------------------------------------------------


def test_reads_every_sensor_from_windows():
    api = FakeApi()
    src = make(api)
    r = src.read_all()
    assert set(r) == {s.id for s in src.sensors()}
    assert r["gpu.usage"] == Reading(35.0, "%")
    assert r["gpu.memory_used"] == Reading(3.0, "GB")
    assert r["gpu.memory_total"] == Reading(12.0, "GB")
    assert r["gpu.memory_usage"] == Reading(25.0, "%")
    assert r["gpu.temperature"] == Reading(56.0, "°C")
    assert r["gpu.fan"] == Reading(42.0, "%")  # 1346 de 3200 RPM
    assert r["gpu.clock"] == Reading(2610.0, "MHz")
    assert r["gpu.name"] == Reading(None, "", text="GeForce RTX 4070 SUPER")
    assert r["gpu.power"].value is None
    assert r["gpu.power"].reason == t(windows_gpu.NO_WATTS)
    assert api.opened_luids == [LUID]  # la repetida de DXGI no se usa
    src.close()
    assert api.counters.closed
    assert api.kmt.closed


def test_declares_same_sensors_as_nvml(monkeypatch):
    def no_nvml():
        raise RuntimeError("sin NVIDIA")

    fake = SimpleNamespace(nvmlInit=no_nvml, nvmlShutdown=lambda: None)
    monkeypatch.setattr(nvidia, "pynvml", fake)
    win = [(s.id, s.label, s.unit, s.kind) for s in make(FakeApi()).sensors()]
    nv = [(s.id, s.label, s.unit, s.kind) for s in nvidia.NvidiaSource().sensors()]
    assert win == nv


def test_zero_means_no_sensor_not_zero_degrees():
    api = FakeApi(kmt=FakeKmt(perf=PerfData(0, 0, 0), caps=PerfCaps(0, 0), hz=0))
    r = make(api).read_all()
    for sid in ("gpu.temperature", "gpu.fan", "gpu.clock"):
        assert r[sid].value is None, sid
        assert r[sid].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.usage"].value == 35.0


def test_stopped_fan_is_zero_percent_when_card_reports_fan():
    api = FakeApi(kmt=FakeKmt(perf=PerfData(400, 0, 0), caps=PerfCaps(3200, 900)))
    assert make(api).read_all()["gpu.fan"] == Reading(0.0, "%")


def test_absurd_values_are_discarded():
    api = FakeApi(kmt=FakeKmt(perf=PerfData(65535, 99999, 0), hz=50_000_000_000))
    r = make(api).read_all()
    assert r["gpu.temperature"].value is None
    assert r["gpu.fan"].value is None
    assert r["gpu.clock"].value is None


def test_kmt_failures_keep_pdh_values():
    api = FakeApi()
    api.fail_kmt = True
    r = make(api).read_all()
    assert r["gpu.temperature"].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.fan"].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.usage"].value == 35.0
    assert r["gpu.name"].text == "GeForce RTX 4070 SUPER"


def test_caps_failing_hides_fan_but_keeps_temperature():
    kmt = FakeKmt()
    kmt.caps = None  # Windows antiguo sin ADAPTERPERFDATA_CAPS: sin máximo, sin %
    r = make(FakeApi(kmt=kmt)).read_all()
    assert r["gpu.fan"].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.temperature"] == Reading(56.0, "°C")


def test_perfdata_query_failing_gives_reason():
    api = FakeApi()
    src = make(api)
    api.kmt.fail_perf = True  # p. ej. otro Windows donde el tipo 62 ya no responde
    r = src.read_all()
    assert r["gpu.temperature"].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.clock"].value == 2610.0


def test_pdh_unavailable_keeps_name_total_and_temperature():
    api = FakeApi()
    api.fail_pdh = True
    r = make(api).read_all()
    assert r["gpu.usage"].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.memory_used"].reason == t(windows_gpu.NOT_SUPPORTED)
    assert r["gpu.memory_usage"].value is None  # nunca se mezcla usada y total
    assert r["gpu.memory_total"] == Reading(12.0, "GB")
    assert r["gpu.temperature"].value == 56.0
    assert api.opened_luids == [LUID]  # sin PDH, la primera de hardware


def test_first_pdh_sample_says_measuring():
    api = FakeApi(samples=[None])
    r = make(api).read_all()
    assert r["gpu.usage"] == Reading(None, "%", reason=t(windows_gpu.MEASURING))
    assert r["gpu.memory_total"].value == 12.0


def test_pdh_read_error_has_reason():
    api = FakeApi()
    src = make(api)
    src.read_all()
    api.counters.fail = True
    r = src.read_all()
    assert r["gpu.usage"].value is None
    assert "OSError" in r["gpu.usage"].reason


def test_no_hardware_adapter_says_not_found_and_retries_limited():
    api = FakeApi(adapters=[BASIC])
    clock = Clock()
    src = make(api, clock)
    r = src.read_all()
    assert all(x.value is None and x.reason == t(windows_gpu.NOT_FOUND) for x in r.values())
    calls = api.calls
    clock.now += windows_gpu.RETRY_SECONDS - 1
    src.read_all()
    assert api.calls == calls  # antes de tiempo no se reintenta
    for _ in range(windows_gpu.MAX_ATTEMPTS + 3):
        clock.now += windows_gpu.RETRY_SECONDS
        src.read_all()
    assert api.calls == calls * windows_gpu.MAX_ATTEMPTS  # y no para siempre


def test_dll_failure_is_reported_and_recovers_on_retry():
    api = FakeApi()
    api.fail_dxgi = True
    clock = Clock()
    src = make(api, clock)
    assert src.read_all()["gpu.name"].reason == t(windows_gpu.NOT_FOUND)
    api.fail_dxgi = False
    clock.now += windows_gpu.RETRY_SECONDS
    assert src.read_all()["gpu.name"].text == "GeForce RTX 4070 SUPER"


def test_reselects_when_luid_disappears():
    new = Adapter(0x777, "NVIDIA GeForce RTX 4070 SUPER", 0x10DE, 12 * GIB, False)
    api = FakeApi()
    clock = Clock()
    src = make(api, clock)
    assert src.read_all()["gpu.usage"].value == 35.0
    # El controlador se reinicia: la tarjeta vuelve con otro LUID.
    api.adapters = [new, BASIC]
    api.counters.samples = [sample(luid=0x777, used=6 * GIB)]
    clock.now += windows_gpu.RESELECT_SECONDS
    assert src.read_all()["gpu.usage"].reason == t(windows_gpu.NOT_READY)
    r = src.read_all()
    assert r["gpu.memory_used"] == Reading(6.0, "GB")
    assert api.opened_luids == [LUID, 0x777]


def test_reselect_does_not_leak_handle_if_closed_meanwhile():
    # Otro hilo cierra la fuente justo mientras se abre la tarjeta nueva: el handle nuevo
    # se cierra y no se guarda (antes quedaba abierto en una fuente ya cerrada).
    new = Adapter(0x777, "NVIDIA GeForce RTX 4070 SUPER", 0x10DE, 12 * GIB, False)
    api = FakeApi()
    clock = Clock()
    src = make(api, clock)
    src.read_all()
    new_kmt = FakeKmt()

    def open_adapter(luid):
        api.opened_luids.append(luid)
        src.close()
        return new_kmt

    api.open_adapter = open_adapter
    api.adapters = [new, BASIC]
    api.counters.samples = [sample(luid=0x777)]
    clock.now += windows_gpu.RESELECT_SECONDS
    src.read_all()
    assert api.opened_luids == [LUID, 0x777]
    assert new_kmt.closed
    assert src._started is None


def test_same_vendor_count_ignores_duplicate_without_pdh():
    other = Adapter(0x888, "NVIDIA GeForce RTX 3060", 0x10DE, 12 * GIB, False)
    adapters = [RTX, RTX_DUP, other, RADEON, BASIC]
    assert windows_gpu.same_vendor_count(adapters, {LUID, RADEON.luid}, RTX) == 1
    assert windows_gpu.same_vendor_count(adapters, {LUID, 0x888, RADEON.luid}, RTX) == 2
    # Sin PDH no se sabe cuál es la repetida: cuentan las dos.
    assert windows_gpu.same_vendor_count(adapters, set(), RTX) == 3


def test_release_closes_and_allows_restart():
    api = FakeApi()
    src = make(api)
    src.read_all()
    src.release()
    assert api.counters.closed and api.kmt.closed
    assert src._started is None
    calls = api.calls
    src.release()  # sin nada abierto, no hace nada
    assert api.calls == calls
    assert src.read_some(["gpu.temperature"])["gpu.temperature"].value == 56.0
    assert api.opened_luids == [LUID, LUID]  # se volvió a abrir


def test_release_during_background_start_discards_result():
    started = threading.Event()
    release = threading.Event()
    api = FakeApi()

    class SlowApi(FakeApi):
        def open_counters(self):
            started.set()
            release.wait(5)
            return api.counters

    slow = SlowApi()
    slow.counters = api.counters
    slow.kmt = api.kmt
    src = WindowsGpuSource(api=slow, sleep=lambda s: None)
    src.read_all()
    assert started.wait(5)
    src.release()  # NVML respondió mientras Windows aún arrancaba
    release.set()
    src._thread.join(5)
    assert slow.kmt.closed and slow.counters.closed
    assert src._started is None
    src.close()


def test_read_some_skips_pdh():
    api = FakeApi()
    src = make(api)
    src.read_all()
    before = api.counters.collects
    r = src.read_some(["gpu.temperature"])
    assert r == {"gpu.temperature": Reading(56.0, "°C")}
    assert api.counters.collects == before  # PDH (~2 ms) no se toca


def test_background_start_does_not_block_and_close_releases():
    started = threading.Event()
    release = threading.Event()
    api = FakeApi()

    class SlowApi(FakeApi):
        def open_counters(self):
            started.set()
            release.wait(5)
            return api.counters

    slow = SlowApi()
    slow.counters = api.counters
    slow.kmt = api.kmt
    src = WindowsGpuSource(api=slow, sleep=lambda s: None)
    r = src.read_all()  # vuelve enseguida, con el hilo aún abriendo PDH
    assert started.wait(5)
    assert r["gpu.usage"].reason == t(windows_gpu.NOT_READY)
    src.close()  # se cierra mientras arranca
    release.set()
    src._thread.join(5)
    assert slow.kmt.closed  # el hilo suelta lo que abrió
    assert src.read_all()["gpu.usage"].reason == t(windows_gpu.NOT_READY)


# --- combinación con NVML -------------------------------------------------------------


class FakeNvml:
    """Lo mínimo de NvidiaSource que usa GpuSource."""

    def __init__(self, has_gpu: bool, unsupported: tuple[str, ...] = ()) -> None:
        self.has_gpu = has_gpu
        self.unsupported = unsupported
        self.closed = False

    def sensors(self):
        return [SensorInfo("gpu.usage", "Uso de GPU", "%", "gpu")]

    def read_all(self):
        if not self.has_gpu:
            return {"gpu.usage": Reading(None, "%", reason=t(nvidia.NOT_FOUND))}
        out = {
            "gpu.temperature": Reading(55.0, "°C"),
            "gpu.usage": Reading(40.0, "%"),
            "gpu.memory_used": Reading(3.0, "GB"),
            "gpu.memory_total": Reading(12.0, "GB"),
            "gpu.memory_usage": Reading(25.0, "%"),
            "gpu.power": Reading(150.5, "W"),
            "gpu.fan": Reading(30.0, "%"),
            "gpu.clock": Reading(2610.0, "MHz"),
            "gpu.name": Reading(None, "", text="GeForce RTX 4070 SUPER"),
        }
        for name in self.unsupported:
            out[f"gpu.{name}"] = Reading(
                None, out[f"gpu.{name}"].unit, reason=t(nvidia.NOT_SUPPORTED)
            )
        return out

    def close(self):
        self.closed = True


def test_nvml_rules_and_windows_is_never_opened():
    api = FakeApi()
    src = GpuSource(FakeNvml(has_gpu=True), make(api))
    r = src.read_all()
    assert r["gpu.usage"] == Reading(40.0, "%")
    assert r["gpu.memory_total"] == Reading(12.0, "GB")
    assert api.calls == 0


def test_without_nvml_everything_comes_from_windows():
    radeon_sample = sample(luid=RADEON.luid, used=4 * GIB)
    api = FakeApi(adapters=[RADEON], samples=[None, radeon_sample])
    src = GpuSource(FakeNvml(has_gpu=False), make(api))
    r = src.read_all()
    assert r["gpu.name"].text == "AMD Radeon RX 7800 XT"
    assert r["gpu.memory_used"] == Reading(4.0, "GB")
    assert r["gpu.memory_total"] == Reading(16.0, "GB")
    assert r["gpu.temperature"] == Reading(56.0, "°C")


def test_windows_fills_only_what_nvml_does_not_support():
    api = FakeApi()
    src = GpuSource(FakeNvml(has_gpu=True, unsupported=("fan",)), make(api))
    r = src.read_all()
    assert r["gpu.fan"] == Reading(42.0, "%")
    assert r["gpu.usage"] == Reading(40.0, "%")  # el resto sigue siendo de NVML
    assert r["gpu.temperature"] == Reading(55.0, "°C")


def test_fill_needs_the_same_vendor():
    api = FakeApi(adapters=[RADEON], samples=[None, sample(luid=RADEON.luid)])
    src = GpuSource(FakeNvml(has_gpu=True, unsupported=("fan",)), make(api))
    assert src.read_all()["gpu.fan"].reason == t(nvidia.NOT_SUPPORTED)


def test_fill_refused_with_two_nvidia_cards():
    # Dos NVIDIA con datos en PDH: la de Windows podría no ser la 0 de NVML.
    other = Adapter(0x888, "NVIDIA GeForce RTX 3060", 0x10DE, 12 * GIB, False)
    both = PdhSample(
        engines={**sample().engines, eng(20, 0x888, 0): 5.0},
        dedicated={**sample().dedicated, mem(0x888): GIB},
    )
    api = FakeApi(adapters=[RTX, other, BASIC], samples=[None, both])
    src = GpuSource(FakeNvml(has_gpu=True, unsupported=("fan",)), make(api))
    assert src.read_all()["gpu.fan"].reason == t(nvidia.NOT_SUPPORTED)


def test_windows_released_when_nvml_arrives_late():
    nv = FakeNvml(has_gpu=False)
    api = FakeApi()
    src = GpuSource(nv, make(api))
    assert src.read_all()["gpu.usage"] == Reading(35.0, "%")  # de Windows
    nv.has_gpu = True  # el controlador de NVIDIA terminó de cargar
    r = src.read_all()
    assert r["gpu.usage"] == Reading(40.0, "%")  # de NVML, sin mezclar
    assert api.counters.closed and api.kmt.closed
    # Si luego NVML dejara de dar algo, Windows vuelve a abrirse para eso.
    nv.unsupported = ("fan",)
    assert src.read_all()["gpu.fan"] == Reading(42.0, "%")
    assert api.opened_luids == [LUID, LUID]


def test_memory_is_never_mixed_between_sources():
    api = FakeApi()
    # NVML da la usada pero no la total: no se completa con la total de Windows.
    src = GpuSource(FakeNvml(has_gpu=True, unsupported=("memory_total",)), make(api))
    r = src.read_all()
    assert r["gpu.memory_used"] == Reading(3.0, "GB")
    assert r["gpu.memory_total"].value is None
    # Si NVML no da ninguna de las dos, va el bloque entero de Windows.
    src = GpuSource(
        FakeNvml(has_gpu=True, unsupported=("memory_used", "memory_total", "memory_usage")),
        make(FakeApi()),
    )
    r = src.read_all()
    assert r["gpu.memory_used"] == Reading(3.0, "GB")
    assert r["gpu.memory_total"] == Reading(12.0, "GB")
    assert r["gpu.memory_usage"] == Reading(25.0, "%")


def test_close_closes_both():
    nv = FakeNvml(has_gpu=False)
    api = FakeApi()
    src = GpuSource(nv, make(api))
    src.read_all()
    src.close()
    assert nv.closed and api.kmt.closed


# --- hardware real ------------------------------------------------------------------


def _real_source_values() -> dict[str, Reading] | None:
    if sys.platform != "win32":
        return None
    src = WindowsGpuSource(background=False, sleep=lambda s: __import__("time").sleep(0.3))
    try:
        r = src.read_all()
    finally:
        src.close()
    return r if r["gpu.name"].text else None


@pytest.mark.skipif(sys.platform != "win32", reason="Solo en Windows")
def test_real_windows_gpu_values_are_plausible():
    r = _real_source_values()
    if r is None:
        pytest.skip("Windows no da datos de GPU en este equipo")
    assert r["gpu.name"].text
    if r["gpu.usage"].value is not None:
        assert 0 <= r["gpu.usage"].value <= 100
    if r["gpu.memory_total"].value is not None:
        assert 0 < r["gpu.memory_total"].value < 200
    if r["gpu.temperature"].value is not None:
        assert 10 <= r["gpu.temperature"].value <= 110


@pytest.mark.skipif(sys.platform != "win32", reason="Solo en Windows")
def test_real_windows_matches_nvml_on_nvidia():
    # En el equipo de pruebas (NVIDIA): si el tipo 62 de D3DKMT o las estructuras dejan
    # de cuadrar con un Windows nuevo, este test cae en vez de dar números raros.
    src = nvidia.NvidiaSource()
    try:
        nv = src.read_all()
    finally:
        src.close()
    if nv["gpu.temperature"].value is None:
        pytest.skip("No hay GPU NVIDIA con NVML en este equipo")
    win = _real_source_values()
    assert win is not None
    assert win["gpu.name"].text == nv["gpu.name"].text
    assert abs(win["gpu.temperature"].value - nv["gpu.temperature"].value) <= 5
    assert abs(win["gpu.memory_total"].value - nv["gpu.memory_total"].value) < 1
    assert win["gpu.usage"].value is not None
    assert win["gpu.clock"].value is not None
    assert win["gpu.fan"].value is not None
    _assert_clock_query_is_the_engine_one()


def _assert_clock_query_is_the_engine_one():
    """La frecuencia **máxima** del motor por D3DKMT debe ser la de NVML.

    La actual no sirve para comprobarlo: en reposo el motor va a 210 MHz y la memoria a
    405 MHz, y bajo carga cambia entre una lectura y otra. Con el tipo de consulta
    equivocado (62 en vez de 61) sale la máxima de la memoria (10 501 MHz, medido) y no
    la del motor (3105 MHz): este test cae.
    """
    import pynvml

    from luzeira.sensores import windows_api

    pynvml.nvmlInit()
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        nv_max_mhz = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
    finally:
        pynvml.nvmlShutdown()
    src = WindowsGpuSource(background=False, sleep=lambda s: __import__("time").sleep(0.3))
    try:
        src.read_all()
        luid = src._started.adapter.luid
    finally:
        src.close()
    kmt = windows_api.RealKmtAdapter(luid)
    try:
        hz, max_hz = kmt.node_clocks()
    finally:
        kmt.close()
    assert abs(max_hz / 1_000_000 - nv_max_mhz) <= nv_max_mhz * 0.03
    assert 0 < hz <= max_hz


def test_default_hub_with_nvidia_does_not_open_windows():
    from luzeira.sensores import default_hub

    hub = default_hub()
    try:
        hub.poll()
        gpu = next(s for s in hub._sources if isinstance(s, GpuSource))
        if not gpu._nvml.has_gpu:
            pytest.skip("No hay GPU NVIDIA con NVML en este equipo")
        assert gpu._windows._attempts == 0
    finally:
        hub.close()
