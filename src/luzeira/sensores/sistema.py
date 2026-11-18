"""Sensores del sistema leídos con psutil (CPU, memoria, red, disco, tiempo encendido).

Todo se lee sin permisos de administrador. Las velocidades (red, disco) y el uso de CPU
se calculan como diferencia entre dos lecturas dividida por el tiempo real transcurrido,
así que la primera lectura no tiene valor («Midiendo…»).

Los contadores de red y disco se comparan **por adaptador y por disco**, nunca como
suma: si aparece un adaptador nuevo (p. ej. una VPN que se conecta con sus contadores
ya acumulados), restar sumas daría un pico falso de miles de MB/s.

Red con VPN (C-11): en Windows solo se suman las tarjetas de verdad (Ethernet, Wi-Fi),
no los adaptadores virtuales ni los túneles, por los que pasa el mismo tráfico otra vez
(ver ``windows_red``).
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

import psutil

from luzeira.sensores.base import Reading, SensorInfo

log = logging.getLogger(__name__)

# Capacidades (memoria) en GiB: así 32 GiB de RAM se ven como «32 GB», igual que en el
# Administrador de tareas. Velocidades en MB/s decimales (10^6 bytes), como las de red.
_GIB = 1024**3
_MB = 1_000_000

MEASURING = "Midiendo…"
CPU_TEMPERATURE_REASON = (
    "La temperatura de la CPU necesita el módulo de sensores avanzados "
    "(llegará en una versión futura)."
)
CPU_FREQUENCY_REASON = (
    "Windows solo informa de la frecuencia nominal de la CPU; la real necesita el módulo "
    "de sensores avanzados (llegará en una versión futura)."
)

# Bucle local: no es tráfico real hacia fuera. Se compara el nombre exacto «lo» (Linux)
# o el prefijo de Windows; un prefijo corto como «lo» excluiría también «Local Area
# Connection», que es la tarjeta principal en un Windows en inglés.
_LOOPBACK_EXACT = "lo"
_LOOPBACK_PREFIX = "loopback pseudo-interface"

# Intervalo válido entre dos lecturas para calcular velocidades. Fuera de él (equipo
# que vuelve de suspensión tras horas, o dos lecturas casi seguidas) la media no
# significa nada: se da «Midiendo…» y la lectura actual pasa a ser la nueva base.
_MIN_INTERVAL = 0.2
_MAX_INTERVAL = 10.0

# Contadores acumulados (enviados/leídos, recibidos/escritos) por adaptador o disco.
Counters = dict[str, tuple[int, int]]

# Cada cuánto se vuelve a mirar qué tarjetas hay (se puede enchufar un Wi-Fi USB).
_PHYSICAL_TTL = 30.0


def _windows_physical_names() -> frozenset[str]:
    from luzeira.sensores.windows_red import list_interfaces, physical_names

    return physical_names(list_interfaces())


class _PhysicalNics:
    """Nombres de las tarjetas de red de verdad, con caché. ``None``: no filtrar (fuera
    de Windows, o si Windows no deja leerlo: mejor contar de más que dejar la red a 0)."""

    def __init__(self, lister: Callable[[], frozenset[str]] | None, clock) -> None:
        self._lister = lister
        self._clock = clock
        self._names: frozenset[str] | None = None
        self._at: float | None = None

    def names(self) -> frozenset[str] | None:
        if self._lister is None:
            return None
        now = self._clock()
        if self._at is None or now - self._at >= _PHYSICAL_TTL:
            self._at = now
            try:
                self._names = self._lister() or None
            except OSError:
                log.warning("No se pudo leer qué tarjetas de red son físicas; cuento todas.")
                self._names = None
        return self._names


def _fixed_drives() -> list[tuple[str, str]]:
    """Devuelve (letra en minúscula, punto de montaje) de las unidades fijas.

    En Windows psutil marca las unidades locales con «fixed» en ``opts``; así se
    descartan lectores de CD vacíos y unidades de red, que pueden tardar en responder.
    """
    drives: list[tuple[str, str]] = []
    for part in psutil.disk_partitions(all=False):
        if "fixed" not in part.opts.split(","):
            continue
        letter = part.mountpoint.rstrip(":\\/").lower()
        if len(letter) == 1 and letter.isalpha():
            drives.append((letter, part.mountpoint))
    return drives


def _is_loopback(name: str) -> bool:
    lowered = name.lower()
    return lowered == _LOOPBACK_EXACT or lowered.startswith(_LOOPBACK_PREFIX)


def _nic_counters(physical: frozenset[str] | None = None) -> Counters:
    """Bytes (enviados, recibidos) de cada interfaz, salvo el bucle local.

    Con ``physical``, solo esas. Si ninguna coincide (nombres que no cuadran), todas: una
    red a 0 sería peor que una con VPN contada dos veces."""
    every = {
        name: (c.bytes_sent, c.bytes_recv)
        for name, c in psutil.net_io_counters(pernic=True).items()
        if not _is_loopback(name)
    }
    if physical is None:
        return every
    return {name: c for name, c in every.items() if name in physical} or every


class SystemSource:
    """Fuente de sensores basada en psutil.

    Coste medido el 2026-11-17 en el equipo de pruebas (Windows 10, Ryzen 7 5700X3D):
    un ``read_all`` tarda 13-29 ms; casi todo es ``virtual_memory`` (8-18 ms) y
    ``net_io_counters(pernic=True)`` (~5 ms). Con una lectura por segundo es < 3 % de
    un núcleo durante la llamada, pero no es «menos de 1 ms».
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        physical_nics: Callable[[], frozenset[str]] | None = (
            _windows_physical_names if sys.platform == "win32" else None
        ),
    ) -> None:
        # El reloj se inyecta para poder probar el cálculo de velocidades sin esperar.
        self._clock = clock
        self._physical = _PhysicalNics(physical_nics, clock)
        self._drives = _fixed_drives()
        # Estado de la lectura anterior para calcular diferencias: (instante, valores).
        self._prev_time: float | None = None
        self._prev_cpu: tuple[float, float] | None = None  # (ocupado, total) en segundos
        self._prev_net: Counters | None = None
        self._prev_disk: Counters | None = None
        self._sensors = self._build_sensors()
        self._units = {info.id: info.unit for info in self._sensors}

    def _build_sensors(self) -> list[SensorInfo]:
        sensors = [
            SensorInfo("cpu.usage", "Uso de CPU", "%", "cpu"),
            SensorInfo("cpu.frequency", "Frecuencia de CPU", "MHz", "cpu"),
            SensorInfo("cpu.temperature", "Temperatura de CPU", "°C", "cpu"),
            SensorInfo("memory.usage", "Uso de memoria", "%", "memory"),
            SensorInfo("memory.used", "Memoria usada", "GB", "memory"),
            SensorInfo("memory.total", "Memoria total", "GB", "memory"),
            SensorInfo("network.upload", "Subida de red", "MB/s", "network"),
            SensorInfo("network.download", "Bajada de red", "MB/s", "network"),
        ]
        sensors += [
            SensorInfo(f"disk.usage.{letter}", f"Uso del disco {letter.upper()}:", "%", "disk")
            for letter, _ in self._drives
        ]
        sensors += [
            SensorInfo("disk.read", "Lectura de disco", "MB/s", "disk"),
            SensorInfo("disk.write", "Escritura de disco", "MB/s", "disk"),
            SensorInfo("system.uptime", "Tiempo encendido", "h", "system"),
            # C-12: sensor de texto. Es el modelo del procesador, no identifica a nadie.
            SensorInfo("cpu.name", "Nombre del procesador", "", "cpu", "text"),
        ]
        return sensors

    def sensors(self) -> list[SensorInfo]:
        return list(self._sensors)

    def read_all(self) -> dict[str, Reading]:
        now = self._clock()
        elapsed = None if self._prev_time is None else now - self._prev_time
        if elapsed is not None and not (_MIN_INTERVAL <= elapsed <= _MAX_INTERVAL):
            # Sin intervalo fiable no hay velocidad; esta lectura se guarda como base.
            elapsed = None
        out: dict[str, Reading] = {}

        # Cada bloque va protegido por separado: si falla uno, los demás siguen.
        self._guard(out, ("cpu.usage",), lambda: self._read_cpu(out, elapsed))
        self._guard(out, ("cpu.frequency",), lambda: self._read_freq(out))
        out["cpu.temperature"] = Reading(None, "°C", reason=CPU_TEMPERATURE_REASON)
        self._guard(
            out, ("memory.usage", "memory.used", "memory.total"), lambda: self._read_memory(out)
        )
        self._guard(
            out, ("network.upload", "network.download"), lambda: self._read_network(out, elapsed)
        )
        for letter, mountpoint in self._drives:
            sid = f"disk.usage.{letter}"
            self._guard(out, (sid,), lambda s=sid, m=mountpoint: self._read_drive(out, s, m))
        self._guard(out, ("disk.read", "disk.write"), lambda: self._read_disk(out, elapsed))
        self._guard(out, ("system.uptime",), lambda: self._read_uptime(out))

        out["cpu.name"] = _CPU_NAME_READING
        self._prev_time = now
        return out

    def close(self) -> None:
        """psutil no mantiene recursos abiertos: no hay nada que cerrar."""

    # --- lectores por bloque -------------------------------------------------------

    def _guard(self, out: dict[str, Reading], ids: tuple[str, ...], fn: Callable[[], None]) -> None:
        """Ejecuta un lector y, si lanza, marca sus sensores sin valor (nunca a 0)."""
        try:
            fn()
        except Exception as exc:
            reason = f"No se pudo leer ({type(exc).__name__})."
            for sid in ids:
                out[sid] = Reading(None, self._units[sid], reason=reason)

    def _read_cpu(self, out: dict[str, Reading], elapsed: float | None) -> None:
        # Se calcula con cpu_times() propios en vez de cpu_percent(interval=None):
        # cpu_percent guarda su estado en el módulo psutil y lo comparte con cualquier
        # otra parte del programa que lo llame, y dos llamadas muy seguidas dan basura.
        times = psutil.cpu_times()
        idle = times.idle
        total = sum(times)
        busy = total - idle
        prev = self._prev_cpu
        self._prev_cpu = (busy, total)
        if prev is None or elapsed is None:
            out["cpu.usage"] = Reading(None, "%", reason=MEASURING)
            return
        d_total = total - prev[1]
        if d_total <= 0:
            out["cpu.usage"] = Reading(None, "%", reason=MEASURING)
            return
        pct = (busy - prev[0]) / d_total * 100.0
        out["cpu.usage"] = Reading(round(min(max(pct, 0.0), 100.0), 1), "%")

    def _read_freq(self, out: dict[str, Reading]) -> None:
        if sys.platform == "win32":
            # Medido en el equipo de pruebas: psutil devuelve siempre 3001 MHz (la nominal)
            # aunque la CPU esté a plena carga. Un valor fijo y falso es peor que ninguno.
            out["cpu.frequency"] = Reading(None, "MHz", reason=CPU_FREQUENCY_REASON)
            return
        freq = psutil.cpu_freq()
        if freq is None or not freq.current:
            out["cpu.frequency"] = Reading(None, "MHz", reason="Este sistema no la informa.")
            return
        out["cpu.frequency"] = Reading(round(freq.current), "MHz")

    def _read_memory(self, out: dict[str, Reading]) -> None:
        vm = psutil.virtual_memory()
        out["memory.usage"] = Reading(round(vm.percent, 1), "%")
        out["memory.used"] = Reading(round((vm.total - vm.available) / _GIB, 2), "GB")
        out["memory.total"] = Reading(round(vm.total / _GIB, 2), "GB")

    def _read_network(self, out: dict[str, Reading], elapsed: float | None) -> None:
        current = _nic_counters(self._physical.names())
        prev = self._prev_net
        self._prev_net = current
        up, down = _rates(prev, current, elapsed)
        out["network.upload"] = up
        out["network.download"] = down

    def _read_drive(self, out: dict[str, Reading], sensor_id: str, mountpoint: str) -> None:
        out[sensor_id] = Reading(round(psutil.disk_usage(mountpoint).percent, 1), "%")

    def _read_disk(self, out: dict[str, Reading], elapsed: float | None) -> None:
        # Por disco (como la red): conectar un disco USB no debe dar un pico falso.
        io = psutil.disk_io_counters(perdisk=True)
        if not io:
            reason = "Este sistema no informa de la actividad del disco."
            out["disk.read"] = Reading(None, "MB/s", reason=reason)
            out["disk.write"] = Reading(None, "MB/s", reason=reason)
            return
        current = {name: (c.read_bytes, c.write_bytes) for name, c in io.items()}
        prev = self._prev_disk
        self._prev_disk = current
        read, write = _rates(prev, current, elapsed)
        out["disk.read"] = read
        out["disk.write"] = write

    def _read_uptime(self, out: dict[str, Reading]) -> None:
        hours = (time.time() - psutil.boot_time()) / 3600
        out["system.uptime"] = Reading(round(hours, 2), "h")


def _read_cpu_name() -> Reading:
    """Modelo del procesador, leído una vez. En Windows está en el registro (solo lectura,
    sin admin); en otros sistemas, lo que diga ``platform``."""
    name = ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0])
    except (ImportError, OSError):
        import platform

        name = platform.processor()
    name = " ".join(name.split())  # el registro trae espacios de relleno
    if not name:
        return Reading(None, "", reason="No se pudo saber el modelo del procesador.")
    return Reading(None, "", text=name)


_CPU_NAME_READING = _read_cpu_name()


def _rates(
    prev: Counters | None, current: Counters, elapsed: float | None
) -> tuple[Reading, Reading]:
    """Velocidad en MB/s de dos juegos de contadores acumulados (por adaptador o disco).

    Solo cuentan los adaptadores presentes en las dos lecturas: uno que aparece o
    desaparece entre medias no aporta nada en ese ciclo. Si el contador de uno
    retrocede (adaptador reiniciado), se descarta ese adaptador. Si no queda ninguno
    válido, o no hay lectura anterior, el valor es «Midiendo…».
    """
    if prev is None or elapsed is None or elapsed <= 0:
        none = Reading(None, "MB/s", reason=MEASURING)
        return none, none
    readings = []
    for pos in (0, 1):
        total = 0
        valid = 0
        for name, after in current.items():
            before = prev.get(name)
            if before is None:
                continue
            delta = after[pos] - before[pos]
            if delta < 0:
                continue
            total += delta
            valid += 1
        if valid == 0:
            readings.append(Reading(None, "MB/s", reason=MEASURING))
        else:
            readings.append(Reading(round(total / elapsed / _MB, 3), "MB/s"))
    return readings[0], readings[1]
