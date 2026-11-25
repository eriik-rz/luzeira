"""Sensores de GPU de cualquier marca (AMD, Intel, NVIDIA) con lo que trae Windows.

Sirve cuando no hay NVML (tarjetas AMD e Intel). No pide administrador ni instala nada:
habla con tres bibliotecas que vienen con Windows usando ``ctypes`` (el módulo de Python
para llamar a funciones de una DLL escrita en C):

- **PDH** (``pdh.dll``), los contadores de rendimiento: el uso de cada motor de la GPU y
  la memoria usada (dedicada y compartida). Son los mismos datos que enseña el
  Administrador de tareas.
- **DXGI** (``dxgi.dll``): la lista de tarjetas con su nombre y su memoria total; y
  **DXCore** (``dxcore.dll``), solo para saber si cada una es integrada.
- **D3DKMT** (``gdi32.dll``): temperatura, ventilador, frecuencia y dirección PCI, **si
  el controlador los da**. Esta vía no está documentada para programas normales (ver
  ``_KMT_*``): puede fallar o cambiar con Windows, y entonces esos sensores salen sin
  valor y con su motivo.

En una gráfica integrada la memoria cuenta dedicada + compartida (``is_integrated``).

Abrir PDH tarda ~1 s (expande cientos de instancias), así que se hace en un hilo aparte la
primera vez que se necesita. Mientras, los sensores dicen «La GPU aún no está lista.».

Toda llamada a Windows pasa por ``WindowsGpuApi``: los tests le dan una falsa.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from luzeira.idioma import t
from luzeira.sensores.base import Reading, SensorInfo, gpu_label_key

log = logging.getLogger(__name__)

_GIB = 1024**3

# Claves del catálogo (zona ``sensores``): el motivo se traduce al crear cada lectura.
NOT_READY = "sensores.gpu_no_lista"
NOT_FOUND = "sensores.gpu_no_encontrada"
NOT_SUPPORTED = "sensores.gpu_no_informa"
NO_WATTS = "sensores.gpu_sin_vatios"
MEASURING = "sensores.midiendo"

# Cada cuánto se reintenta arrancar si falló, y cuántas veces como mucho. Un intento
# cuesta ~1 s de PDH: no se reintenta para siempre en un equipo sin GPU.
RETRY_SECONDS = 60.0
MAX_ATTEMPTS = 5
# Si la tarjeta elegida desaparece de PDH (el controlador se reinició y cambió su LUID),
# se vuelve a buscar como mucho con esta frecuencia.
RESELECT_SECONDS = 30.0

# Fabricante en DXGI_ADAPTER_DESC1.VendorId (identificador PCI).
NVIDIA_VENDOR_ID = 0x10DE

_UNITS = {
    "temperature": "°C",
    "usage": "%",
    "memory_used": "GB",
    "memory_total": "GB",
    "memory_usage": "%",
    "power": "W",
    "fan": "%",
    "clock": "MHz",
}
# Qué sensores salen de PDH (caros) y cuáles de D3DKMT (baratos). Medido en la RTX 4070
# SUPER, Windows 10 19045, con ``read_some`` de todo: 2,5 ms de reloj por lectura si se
# llama 20 veces seguidas (``perf_counter``); a 1 Hz, como en la app, 3,5 ms de reloj y
# 3,1 ms de CPU por lectura (``process_time`` total en 30 s / 30: por lectura suelta no
# vale, en Windows avanza a saltos de ~16 ms). Es ~0,3 % de un núcleo. Solo D3DKMT
# (temperatura): 0,01 ms. Crece con los procesos que usan la GPU (más instancias en PDH).
PDH_SENSORS = frozenset({"usage", "memory_used", "memory_usage"})
KMT_SENSORS = frozenset({"temperature", "fan", "clock"})

# Límites de cordura: el tipo de consulta de D3DKMT no está documentado, así que un valor
# fuera de rango se toma como «no informa» en lugar de enseñar un disparate.
_MAX_TEMPERATURE_DC = 1500  # 150 °C en décimas
_MAX_FAN_RPM = 20_000
_MAX_CLOCK_HZ = 10_000_000_000  # 10 GHz


# --- Datos que devuelve Windows (ya traducidos a Python) ------------------------------


@dataclass(frozen=True)
class Adapter:
    """Una tarjeta según DXGI. ``luid`` es su identificador en esta sesión de Windows
    (cambia en cada arranque: nunca se guarda en la configuración)."""

    luid: int
    name: str
    vendor_id: int
    dedicated_bytes: int
    software: bool  # «Microsoft Basic Render Driver» y similares: no es una GPU real
    # Memoria del sistema que la tarjeta puede usar (``SharedSystemMemory`` de DXGI).
    shared_bytes: int = 0
    # Si Windows dice que es una gráfica integrada (DXCore ``IsIntegrated``); ``None`` si
    # no lo pudo decir (Windows anterior a 2004, o la entrada repetida de DXGI).
    integrated: bool | None = None


@dataclass(frozen=True)
class PdhSample:
    """Una lectura de PDH: nombre de instancia → valor."""

    engines: dict[str, float]  # «GPU Engine(*)\\Utilization Percentage»
    dedicated: dict[str, float]  # «GPU Adapter Memory(*)\\Dedicated Usage», en bytes
    # «GPU Adapter Memory(*)\\Shared Usage», en bytes. Solo cuenta en las integradas.
    # ``field(default_factory=dict)``: un valor por defecto mutable se crea nuevo en cada
    # objeto (con ``= {}`` todos compartirían el mismo diccionario).
    shared: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PerfData:
    temperature_dc: int  # décimas de °C; 0 = sin sensor
    fan_rpm: int
    power_permille: int  # décimas de % del límite de potencia (no vatios)


@dataclass(frozen=True)
class PerfCaps:
    max_fan_rpm: int  # 0 = no informa del ventilador
    temperature_max_dc: int


class PdhCounters(Protocol):
    def collect(self) -> PdhSample | None:
        """Toma una muestra. ``None`` si aún no hay dos muestras (los porcentajes de uso
        se calculan entre dos lecturas)."""
        ...

    def close(self) -> None: ...


class KmtAdapter(Protocol):
    """Una tarjeta abierta con D3DKMT. Cada método lanza ``OSError`` si Windows dice que no."""

    def perf_data(self) -> PerfData: ...

    def perf_caps(self) -> PerfCaps: ...

    def clock_hz(self) -> int: ...

    def pci_address(self) -> tuple[int, int, int]:
        """Bus, dispositivo y función PCI de la tarjeta, tal como los da Windows (sin
        comprobar: eso lo hace ``valid_pci_address``)."""
        ...

    def close(self) -> None: ...


class WindowsGpuApi(Protocol):
    def list_adapters(self) -> list[Adapter]: ...

    def open_counters(self) -> PdhCounters: ...

    def open_adapter(self, luid: int) -> KmtAdapter: ...


# --- Cálculos sin Windows (los prueban los tests) --------------------------------------

# pid_1104_luid_0x00000000_0x00015ED9_phys_0_eng_9_engtype_Graphics_1
_ENGINE = re.compile(r"luid_0x([0-9a-fA-F]+)_0x([0-9a-fA-F]+)_phys_(\d+)_eng_(\d+)")
# luid_0x00000000_0x00015ED9_phys_0
_ADAPTER = re.compile(r"luid_0x([0-9a-fA-F]+)_0x([0-9a-fA-F]+)_phys_(\d+)$")


def _luid(high: str, low: str) -> int:
    return (int(high, 16) << 32) | int(low, 16)


def engine_usage(engines: dict[str, float]) -> dict[int, float]:
    """Uso de cada tarjeta, como lo calcula el Administrador de tareas: **el motor más
    ocupado** (no la suma ni la media). Cada instancia de PDH es «proceso y motor», así
    que primero se suman los procesos de cada motor (``luid + phys + eng_N``) y luego se
    coge el máximo. No se filtra por el tipo de motor (``3D``, ``Graphics_1``…): su
    nombre cambia de un controlador a otro."""
    per_engine: dict[tuple[int, int, int], float] = {}
    for name, value in engines.items():
        m = _ENGINE.search(name)
        if m is None:
            continue
        key = (_luid(m.group(1), m.group(2)), int(m.group(3)), int(m.group(4)))
        per_engine[key] = per_engine.get(key, 0.0) + value
    out: dict[int, float] = {}
    for (luid, _, _), value in per_engine.items():
        out[luid] = max(out.get(luid, 0.0), value)
    return {luid: min(max(v, 0.0), 100.0) for luid, v in out.items()}


def dedicated_usage(dedicated: dict[str, float]) -> dict[int, float]:
    """Memoria usada por tarjeta (bytes), sumando sus adaptadores físicos. Sirve igual
    para la dedicada y para la compartida: las instancias se llaman igual."""
    out: dict[int, float] = {}
    for name, value in dedicated.items():
        m = _ADAPTER.search(name)
        if m is not None:
            luid = _luid(m.group(1), m.group(2))
            out[luid] = out.get(luid, 0.0) + value
    return out


def choose_adapter(adapters: Iterable[Adapter], pdh_luids: set[int]) -> Adapter | None:
    """Elige la tarjeta que se enseña como ``gpu``.

    - Se descartan los adaptadores de software (dibujan con la CPU).
    - Solo valen los que PDH conoce: DXGI puede listar una tarjeta repetida con otro LUID
      sin datos en PDH (medido con una RTX 4070 SUPER). Si PDH no conoce ninguno (PDH no
      va), se usan todos los de hardware: al menos habrá nombre, total y temperatura.
    - Una dedicada gana siempre a una integrada (``is_integrated``), aunque la integrada
      declare más memoria: una APU con mucha memoria reservada en la BIOS no debe tapar
      a la tarjeta dedicada del mismo equipo.
    - Entre las del mismo tipo, la de más memoria dedicada. A igualdad, la primera de DXGI.
    """
    best: Adapter | None = None
    best_key: tuple[bool, int] | None = None
    for a in _candidates(adapters, pdh_luids):
        # Las tuplas se comparan elemento a elemento: primero «es dedicada» (True > False),
        # y solo si empatan, la memoria.
        key = (not is_integrated(a), a.dedicated_bytes)
        if best_key is None or key > best_key:
            best, best_key = a, key
    return best


def _candidates(adapters: Iterable[Adapter], pdh_luids: set[int]) -> list[Adapter]:
    """Las tarjetas entre las que elige ``choose_adapter`` (ver sus reglas)."""
    hardware = [a for a in adapters if not a.software]
    known = [a for a in hardware if a.luid in pdh_luids]
    return known or hardware


def same_vendor_count(adapters: Iterable[Adapter], pdh_luids: set[int], chosen: Adapter) -> int:
    """Cuántas tarjetas candidatas son del mismo fabricante que la elegida (ella incluida).

    Sirve para no mezclar fuentes con dos NVIDIA cuando no se sabe la dirección PCI de
    alguna de las dos (con ella, ``gpu.GpuSource`` compara direcciones): NVML lee la suya
    (la 0 de NVML) y Windows la de más memoria. Con una sola NVIDIA, son la misma seguro.
    La entrada repetida que lista DXGI sin datos en PDH no cuenta (no es candidata); si
    PDH no va, sí cuenta, y entonces no se mezcla: mejor «sin dato» que el dato de otra
    tarjeta.
    """
    return sum(1 for a in _candidates(adapters, pdh_luids) if a.vendor_id == chosen.vendor_id)


# Por debajo de esto, una tarjeta de la que Windows no dice si es integrada se toma como
# integrada (solo cuando DXCore no responde: ver ``is_integrated``).
INTEGRATED_GUESS_BYTES = _GIB


def is_integrated(adapter: Adapter) -> bool:
    """Si la tarjeta es una gráfica integrada (Intel UHD/Iris, AMD APU).

    Manda lo que dice Windows (DXCore ``IsIntegrated``, documentado en Microsoft Learn
    desde Windows 10 2004). Si Windows no lo dice, una regla prudente: menos de 1 GiB de
    memoria dedicada **y** memoria compartida declarada. El umbral de 1 GiB es una
    estimación **sin medir**: no hay ninguna integrada a mano para comprobar cuánta
    memoria dedicada declara. Equivocarse con una dedicada antigua de menos de 1 GiB solo
    haría que su total contara también la compartida, como el «Memoria de GPU» del
    Administrador de tareas.
    """
    if adapter.integrated is not None:
        return adapter.integrated
    return 0 < adapter.dedicated_bytes < INTEGRATED_GUESS_BYTES and adapter.shared_bytes > 0


def memory_total_bytes(adapter: Adapter, integrated: bool) -> int:
    """Memoria total que se enseña: en una integrada, dedicada + compartida (la dedicada
    sola es una reserva diminuta y el % saldría absurdo); en una dedicada, la dedicada."""
    if integrated:
        return adapter.dedicated_bytes + adapter.shared_bytes
    return adapter.dedicated_bytes


def valid_pci_address(raw: tuple[int, int, int]) -> tuple[int, int, int] | None:
    """La dirección PCI si es posible, ``None`` si no.

    Windows responde «bien» también para las tarjetas sin dirección (la entrada repetida
    de DXGI y el Basic Render Driver), pero con todo a unos: bus 0xFFFFFFFF y dispositivo
    y función 0xFFFF (medido en Windows 10 19045). PCI admite como mucho bus 255,
    dispositivo 31 y función 7.
    """
    bus, device, function = raw
    if 0 <= bus <= 255 and 0 <= device <= 31 and 0 <= function <= 7:
        return (bus, device, function)
    return None


def clean_name(raw: str) -> str:
    """Nombre corto: sin «NVIDIA » delante (como lo deja la fuente NVML) ni «(R)»/«(TM)»."""
    name = raw.replace("(R)", "").replace("(TM)", "").replace("(tm)", "")
    name = " ".join(name.split()).removeprefix("NVIDIA ")
    return name


# --- La fuente ----------------------------------------------------------------------


@dataclass
class _Started:
    """Lo que deja preparado el arranque (en el hilo)."""

    counters: PdhCounters | None
    adapter: Adapter
    kmt: KmtAdapter | None
    caps: PerfCaps | None
    same_vendor: int = 1  # ver ``same_vendor_count``
    pci: tuple[int, int, int] | None = None  # bus, dispositivo, función (si se sabe)
    integrated: bool = False  # ver ``is_integrated``


class WindowsGpuSource:
    """GPU elegida por ``choose_adapter``, con datos de PDH, DXGI y D3DKMT.

    Declara siempre los mismos sensores que ``NvidiaSource`` (el hub pregunta una sola
    vez). No lanza excepciones: lo que falle sale sin valor y con su motivo.
    """

    def __init__(
        self,
        prefix: str = "gpu",
        api: WindowsGpuApi | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        background: bool = True,
    ) -> None:
        self._prefix = prefix
        self._api = api
        self._clock = clock
        self._sleep = sleep
        # En los tests se arranca sin hilo para que todo pase en orden.
        self._background = background
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started: _Started | None = None
        self._failure: str | None = None  # clave del motivo del último arranque fallido
        self._attempts = 0
        self._last_attempt: float | None = None
        self._last_reselect = 0.0
        self._closed = False
        # Sube en cada ``release()``: un arranque que estaba en marcha en el hilo y termina
        # después de soltar la fuente ve que su «generación» ya no es la actual y cierra lo
        # que abrió en vez de guardarlo.
        self._generation = 0
        self._logged_kmt = False
        self._sensors = [
            *(SensorInfo(self._id(n), gpu_label_key(n), u, "gpu") for n, u in _UNITS.items()),
            SensorInfo(self._id("name"), gpu_label_key("name"), "", "gpu", "text"),
        ]

    def _id(self, name: str) -> str:
        return f"{self._prefix}.{name}"

    def sensors(self) -> list[SensorInfo]:
        return list(self._sensors)

    @property
    def sole_vendor_id(self) -> int | None:
        """Fabricante de la tarjeta elegida **si es la única de su marca** entre las
        candidatas; ``None`` si aún no hay tarjeta o si hay otra de la misma marca."""
        started = self._started
        if started is None or started.same_vendor != 1:
            return None
        return started.adapter.vendor_id

    @property
    def pci_address(self) -> tuple[int, int, int] | None:
        """Bus, dispositivo y función PCI de la tarjeta elegida; ``None`` si aún no hay
        tarjeta o Windows no la da. Sirve para saber si es la misma que lee NVML."""
        started = self._started
        return started.pci if started is not None else None

    # --- arranque ---

    def _ensure_started(self) -> None:
        """Arranca (o reintenta) si toca. Nunca espera al hilo."""
        with self._lock:
            if self._closed or self._started is not None:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            if self._attempts >= MAX_ATTEMPTS:
                return
            now = self._clock()
            if self._last_attempt is not None and now - self._last_attempt < RETRY_SECONDS:
                return
            self._last_attempt = now
            self._attempts += 1
            generation = self._generation
            if self._background:
                # Un hilo «daemon» no impide que la app se cierre si aún está arrancando.
                self._thread = threading.Thread(
                    target=self._start, args=(generation,), name="luzeira-gpu-windows", daemon=True
                )
                self._thread.start()
                return
        self._start(generation)  # sin hilo (tests): fuera del candado, porque _start lo coge

    def _start(self, generation: int) -> None:
        """Abre DXGI, PDH y D3DKMT. Corre en el hilo (o directamente en los tests)."""
        try:
            started = self._open()
        except Exception as exc:
            # Solo el tipo de error: el mensaje podría llevar rutas del equipo.
            level = logging.INFO if self._attempts == 1 else logging.DEBUG
            log.log(level, "GPU por Windows no disponible (%s).", type(exc).__name__)
            with self._lock:
                if generation == self._generation:
                    self._failure = NOT_FOUND
            return
        with self._lock:
            # Se cerró o se soltó mientras arrancaba: se suelta todo aquí.
            if self._closed or generation != self._generation:
                _close_started(started)
                return
            self._started = started
            self._failure = None

    def release(self) -> None:
        """Suelta PDH y D3DKMT sin cerrar la fuente: si vuelven a hacer falta, se abren
        otra vez (con sus reintentos desde cero).

        Para cuando NVML arranca tarde (el controlador cargó después que la app): hasta
        entonces Windows lo daba todo; después NVML manda y lo de Windows solo gastaría.
        Sin nada abierto, no hace nada (se puede llamar en cada ciclo).
        """
        with self._lock:
            if self._started is None and self._attempts == 0:
                return
            started, self._started = self._started, None
            self._generation += 1
            self._attempts = 0
            self._last_attempt = None
            self._failure = None
        log.debug("GPU por Windows soltada: NVML ya da todos los datos.")
        if started is not None:
            _close_started(started)

    def _open(self) -> _Started:
        api = self._api if self._api is not None else _real_api()
        counters: PdhCounters | None = None
        pdh_luids: set[int] = set()
        try:
            counters = api.open_counters()
            # PDH necesita dos muestras para el primer porcentaje; ~1 s entre ellas da
            # un valor fiable (con 0,1 s ya sale, pero muy ruidoso).
            counters.collect()
            self._sleep(1.0)
            sample = counters.collect()
            if sample is not None:
                pdh_luids = set(dedicated_usage(sample.dedicated)) | set(
                    engine_usage(sample.engines)
                )
        except Exception as exc:
            log.info("Contadores de GPU de Windows no disponibles (%s).", type(exc).__name__)
            if counters is not None:
                _quiet_close(counters)
            counters = None
        try:
            adapters = api.list_adapters()
            adapter = choose_adapter(adapters, pdh_luids)
        except Exception:
            if counters is not None:
                _quiet_close(counters)
            raise
        if adapter is None:
            if counters is not None:
                _quiet_close(counters)
            raise LookupError("sin tarjeta de hardware")
        return self._make_started(api, counters, adapters, pdh_luids, adapter)

    def _make_started(
        self,
        api: WindowsGpuApi,
        counters: PdhCounters | None,
        adapters: list[Adapter],
        pdh_luids: set[int],
        adapter: Adapter,
    ) -> _Started:
        """Abre D3DKMT para la tarjeta elegida y junta todo lo que se sabe de ella."""
        kmt, caps, pci = self._open_kmt(api, adapter)
        return _Started(
            counters,
            adapter,
            kmt,
            caps,
            same_vendor_count(adapters, pdh_luids, adapter),
            pci,
            is_integrated(adapter),
        )

    def _open_kmt(self, api: WindowsGpuApi, adapter: Adapter):
        try:
            kmt = api.open_adapter(adapter.luid)
        except Exception as exc:
            self._log_kmt(exc)
            return None, None, None
        try:
            caps = kmt.perf_caps()
        except Exception as exc:
            self._log_kmt(exc)
            caps = None
        try:
            # Se pregunta una vez: la dirección de una tarjeta no cambia mientras existe.
            pci = valid_pci_address(kmt.pci_address())
        except Exception as exc:
            log.debug("Windows no da la dirección PCI de la GPU (%s).", type(exc).__name__)
            pci = None
        return kmt, caps, pci

    def _log_kmt(self, exc: Exception) -> None:
        if not self._logged_kmt:
            self._logged_kmt = True
            log.info("Windows no da temperatura de esta GPU (%s).", type(exc).__name__)

    # --- lectura ---

    def read_all(self) -> dict[str, Reading]:
        return self.read_some(None)

    def read_some(self, wanted: Iterable[str] | None) -> dict[str, Reading]:
        """Lee solo los sensores pedidos (ids completos, p. ej. ``gpu.fan``), o todos con
        ``None``. Sirve para no gastar PDH cuando solo falta la temperatura."""
        names = (
            set(_UNITS) | {"name"}
            if wanted is None
            else {i.removeprefix(self._prefix + ".") for i in wanted}
        )
        self._ensure_started()
        started = self._started
        if started is None:
            reason = self._failure or NOT_READY
            if self._attempts >= MAX_ATTEMPTS and self._failure is None:
                reason = NOT_FOUND
            return self._all_without_value(names, t(reason))

        out: dict[str, Reading] = {}
        if names & PDH_SENSORS:
            self._read_pdh(started, out)
            started = self._started or started  # puede haber cambiado de tarjeta
        if names & {"memory_total", "memory_usage"}:
            self._put_total(started, out)
        if names & KMT_SENSORS:
            self._read_kmt(started, out)
        if "power" in names:
            out[self._id("power")] = Reading(None, "W", reason=t(NO_WATTS))
        if "name" in names:
            name = clean_name(started.adapter.name)
            out[self._id("name")] = (
                Reading(None, "", text=name) if name else Reading(None, "", reason=t(NOT_SUPPORTED))
            )
        return {self._id(n): out[self._id(n)] for n in self._ordered(names) if self._id(n) in out}

    def _ordered(self, names: set[str]) -> list[str]:
        return [n for n in [*_UNITS, "name"] if n in names]

    def _all_without_value(self, names: set[str], reason: str) -> dict[str, Reading]:
        units = {**_UNITS, "name": ""}
        return {self._id(n): Reading(None, units[n], reason=reason) for n in self._ordered(names)}

    def _read_pdh(self, started: _Started, out: dict[str, Reading]) -> None:
        used_id, usage_id, mem_pct_id = (
            self._id("memory_used"),
            self._id("usage"),
            self._id("memory_usage"),
        )
        if started.counters is None:
            for sid, unit in ((usage_id, "%"), (used_id, "GB"), (mem_pct_id, "%")):
                out[sid] = Reading(None, unit, reason=t(NOT_SUPPORTED))
            return
        try:
            sample = started.counters.collect()
        except Exception as exc:
            bad = t("sensores.no_se_pudo_leer", kind=type(exc).__name__)
            for sid, unit in ((usage_id, "%"), (used_id, "GB"), (mem_pct_id, "%")):
                out[sid] = Reading(None, unit, reason=bad)
            return
        if sample is None:
            for sid, unit in ((usage_id, "%"), (used_id, "GB"), (mem_pct_id, "%")):
                out[sid] = Reading(None, unit, reason=t(MEASURING))
            return
        usage = engine_usage(sample.engines)
        memory = dedicated_usage(sample.dedicated)
        luid = started.adapter.luid
        if luid not in memory and luid not in usage:
            # La tarjeta ya no está en PDH: el controlador se reinició y cambió el LUID.
            self._maybe_reselect(set(memory) | set(usage))
            for sid, unit in ((usage_id, "%"), (used_id, "GB"), (mem_pct_id, "%")):
                out[sid] = Reading(None, unit, reason=t(NOT_READY))
            return
        out[usage_id] = (
            Reading(round(usage[luid], 1), "%")
            if luid in usage
            else Reading(None, "%", reason=t(NOT_SUPPORTED))
        )
        used = memory.get(luid)
        if used is not None and started.integrated:
            # Integrada: como el «Memoria de GPU» del Administrador de tareas, dedicada +
            # compartida. Sin la compartida no se enseña nada: la usada y la total tienen
            # que contar lo mismo.
            shared = dedicated_usage(sample.shared).get(luid)
            used = used + shared if shared is not None else None
        if used is not None:
            total = memory_total_bytes(started.adapter, started.integrated)
            # La usada nunca pasa de la total. En una integrada podría: según Learn,
            # ``SharedSystemMemory`` no cuenta la memoria «incidental» del controlador, y
            # PDH sí. En una dedicada no se ha visto nunca (el % ya tenía este tope).
            if total > 0:
                used = min(used, float(total))
            out[used_id] = Reading(round(used / _GIB, 2), "GB")
            out[mem_pct_id] = (
                Reading(round(used / total * 100, 1), "%")
                if total > 0
                else Reading(None, "%", reason=t(NOT_SUPPORTED))
            )
        else:
            out[used_id] = Reading(None, "GB", reason=t(NOT_SUPPORTED))
            out[mem_pct_id] = Reading(None, "%", reason=t(NOT_SUPPORTED))

    def _put_total(self, started: _Started, out: dict[str, Reading]) -> None:
        total = memory_total_bytes(started.adapter, started.integrated)
        out[self._id("memory_total")] = (
            Reading(round(total / _GIB, 2), "GB")
            if total > 0
            else Reading(None, "GB", reason=t(NOT_SUPPORTED))
        )
        # Sin PDH no hay «usada», así que tampoco porcentaje (nunca se mezcla la usada de
        # una fuente con la total de otra).
        out.setdefault(self._id("memory_usage"), Reading(None, "%", reason=t(NOT_SUPPORTED)))

    def _maybe_reselect(self, pdh_luids: set[int]) -> None:
        now = self._clock()
        if now - self._last_reselect < RESELECT_SECONDS:
            return
        self._last_reselect = now
        api = self._api if self._api is not None else _real_api()
        try:
            adapters = api.list_adapters()
            adapter = choose_adapter(adapters, pdh_luids)
        except Exception as exc:
            log.debug("No se pudo volver a buscar la GPU (%s).", type(exc).__name__)
            return
        old = self._started
        if adapter is None or old is None or adapter.luid == old.adapter.luid:
            return
        new = self._make_started(api, old.counters, adapters, pdh_luids, adapter)
        kmt = new.kmt
        # El cambio se hace con el candado y solo si nadie cerró ni soltó la fuente
        # entretanto; si no, el handle nuevo no lo cerraría nadie. El viejo se cierra después
        # del cambio, para que ``close()`` no lo cierre dos veces.
        with self._lock:
            if self._closed or self._started is not old:
                discard = kmt
            else:
                self._started = new
                discard = old.kmt
        if discard is not None:
            _quiet_close(discard)

    def _read_kmt(self, started: _Started, out: dict[str, Reading]) -> None:
        temp_id, fan_id, clock_id = self._id("temperature"), self._id("fan"), self._id("clock")
        kmt = started.kmt
        if kmt is None:
            for sid, unit in ((temp_id, "°C"), (fan_id, "%"), (clock_id, "MHz")):
                out[sid] = Reading(None, unit, reason=t(NOT_SUPPORTED))
            return
        try:
            perf = kmt.perf_data()
        except Exception as exc:
            self._log_kmt(exc)
            perf = None
        # Temperatura en décimas de grado. 0 es «no hay sensor», no 0 °C.
        if perf is not None and 0 < perf.temperature_dc <= _MAX_TEMPERATURE_DC:
            out[temp_id] = Reading(round(perf.temperature_dc / 10, 1), "°C")
        else:
            out[temp_id] = Reading(None, "°C", reason=t(NOT_SUPPORTED))
        # Ventilador: Windows da vueltas por minuto; el sensor es en % del máximo que
        # declara la tarjeta. Si no declara máximo, no informa del ventilador. Con máximo,
        # 0 RPM es real: muchas tarjetas paran el ventilador en reposo.
        caps = started.caps
        if (
            perf is not None
            and caps is not None
            and 0 < caps.max_fan_rpm <= _MAX_FAN_RPM
            and 0 <= perf.fan_rpm <= _MAX_FAN_RPM
        ):
            pct = min(perf.fan_rpm / caps.max_fan_rpm * 100, 100.0)
            out[fan_id] = Reading(float(round(pct)), "%")
        else:
            out[fan_id] = Reading(None, "%", reason=t(NOT_SUPPORTED))
        try:
            hz = kmt.clock_hz()
        except Exception as exc:
            self._log_kmt(exc)
            hz = 0
        out[clock_id] = (
            Reading(float(round(hz / 1_000_000)), "MHz")
            if 0 < hz <= _MAX_CLOCK_HZ
            else Reading(None, "MHz", reason=t(NOT_SUPPORTED))
        )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            started, self._started = self._started, None
        if started is not None:
            _close_started(started)


def _close_started(started: _Started) -> None:
    if started.counters is not None:
        _quiet_close(started.counters)
    if started.kmt is not None:
        _quiet_close(started.kmt)


def _quiet_close(thing: PdhCounters | KmtAdapter) -> None:
    try:
        thing.close()
    except Exception as exc:
        log.debug("Fallo al cerrar (%s).", type(exc).__name__)


def _real_api() -> WindowsGpuApi:
    # Se importa aquí: el módulo con ctypes de Windows no se carga en otros sistemas.
    from luzeira.sensores import windows_api

    return windows_api.RealWindowsGpuApi()
