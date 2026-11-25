"""Sensores de tarjetas gráficas NVIDIA leídos con NVML (paquete ``nvidia-ml-py``).

NVML es la biblioteca del propio controlador de NVIDIA (la misma que usa ``nvidia-smi``)
y no pide permisos de administrador. La fuente nunca lanza excepciones.

Si NVML no arranca (sin tarjeta NVIDIA, o el controlador aún no ha cargado porque la
app se abrió con Windows), la fuente **declara igualmente sus sensores** y los da sin
valor con un motivo, y reintenta ``nvmlInit`` como mucho cada 30 s. Se declaran
siempre porque el hub pregunta ``sensors()`` una sola vez al crearse: si la lista
saliera vacía, la GPU no aparecería nunca aunque el controlador cargase un minuto
después.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import pynvml

from luzeira.idioma import Text, t
from luzeira.sensores.base import Reading, SensorInfo, gpu_label_key

log = logging.getLogger(__name__)

_GIB = 1024**3

# Claves del catálogo (zona ``sensores``): el motivo se traduce al crear cada lectura.
# «No lo da esta tarjeta» va como ``Text`` (recuerda su clave): ``gpu.GpuSource`` lo
# reconoce por la clave, no comparando textos traducidos.
NOT_SUPPORTED = "sensores.gpu_no_informa"
NOT_READY = "sensores.gpu_no_lista"
NOT_FOUND = "sensores.nvidia_no_encontrada"

# Cada cuánto se reintenta arrancar NVML mientras no funcione.
RETRY_SECONDS = 30.0
# Tras este tiempo sin conseguirlo se deja de decir «aún no está lista» (lo más
# probable es que no haya NVIDIA), aunque se sigue reintentando por si acaso.
GIVE_UP_SECONDS = 300.0

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


class NvidiaSource:
    """Una GPU NVIDIA concreta.

    En v1 solo se usa la GPU 0 con ids sin índice (``gpu.temperature``). Para varias
    GPUs bastará crear una fuente por índice con otro prefijo (``gpu1``, ``gpu2``...):
    los ids se construyen siempre a partir de ``prefix``.
    """

    def __init__(
        self, index: int = 0, prefix: str = "gpu", clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._index = index
        self._prefix = prefix
        # El reloj se inyecta para probar los reintentos sin esperar 30 s.
        self._clock = clock
        self._handle: Any = None
        self._initialized = False
        self._started_at = clock()
        self._last_attempt = self._started_at
        self._failures = 0
        self._closed = False
        self._name: str | None = None
        self._pci: tuple[int, int, int] | None = None
        self._sensors = self._build_sensors()
        self._try_start()

    def _try_start(self) -> None:
        """Intenta arrancar NVML y coger la GPU. Si falla, deja la fuente sin GPU."""
        try:
            pynvml.nvmlInit()
            self._initialized = True
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self._index)
            self._pci = _read_pci_address(self._handle)
        except Exception as exc:
            # Solo se registra el tipo de error: el mensaje puede incluir rutas del
            # equipo. A nivel info solo la primera vez, para no llenar el registro.
            self._failures += 1
            level = logging.INFO if self._failures == 1 else logging.DEBUG
            log.log(level, "NVML no disponible (%s); se reintentará.", type(exc).__name__)
            self._release()

    def _id(self, name: str) -> str:
        return f"{self._prefix}.{name}"

    def _build_sensors(self) -> list[SensorInfo]:
        # La etiqueta es una clave del catálogo (la misma en la fuente de Windows): se
        # traduce al servirla, no aquí, que solo se construye una vez.
        numbers = [
            SensorInfo(self._id(n), gpu_label_key(n), unit, "gpu") for n, unit in _UNITS.items()
        ]
        # C-12: sensor de texto. Es el modelo de la tarjeta, no identifica a nadie.
        return [*numbers, SensorInfo(self._id("name"), gpu_label_key("name"), "", "gpu", "text")]

    def sensors(self) -> list[SensorInfo]:
        return list(self._sensors)

    @property
    def has_gpu(self) -> bool:
        """Si NVML funciona y tiene la GPU (tras el último ``read_all``)."""
        return self._handle is not None

    @property
    def pci_address(self) -> tuple[int, int, int] | None:
        """Bus, dispositivo y función PCI de esta GPU; ``None`` si no hay GPU o NVML no
        lo dice. ``gpu.GpuSource`` lo compara con el de Windows."""
        return self._pci  # ``_release`` lo borra al soltar la GPU

    def read_all(self) -> dict[str, Reading]:
        if self._handle is None:
            now = self._clock()
            # Una fuente cerrada no vuelve a abrir NVML.
            if not self._closed and now - self._last_attempt >= RETRY_SECONDS:
                self._last_attempt = now
                self._try_start()
            if self._handle is None:
                waited = now - self._started_at
                reason = t(NOT_READY if waited < GIVE_UP_SECONDS else NOT_FOUND)
                out = {
                    self._id(name): Reading(None, unit, reason=reason)
                    for name, unit in _UNITS.items()
                }
                out[self._id("name")] = Reading(None, "", reason=reason)
                return out
        h = self._handle
        out: dict[str, Reading] = {}

        def put(name: str, unit: str, fn: Callable[[], float]) -> None:
            out[self._id(name)] = _safe(unit, fn)

        put(
            "temperature",
            "°C",
            lambda: pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
        )
        put("usage", "%", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        # La memoria se pide una vez y se reparte en tres sensores.
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        except Exception as exc:
            bad = _failure(exc)
            out[self._id("memory_used")] = Reading(None, "GB", reason=bad)
            out[self._id("memory_total")] = Reading(None, "GB", reason=bad)
            out[self._id("memory_usage")] = Reading(None, "%", reason=bad)
        else:
            out[self._id("memory_used")] = Reading(round(mem.used / _GIB, 2), "GB")
            out[self._id("memory_total")] = Reading(round(mem.total / _GIB, 2), "GB")
            pct = mem.used / mem.total * 100 if mem.total else None
            out[self._id("memory_usage")] = (
                Reading(round(pct, 1), "%")
                if pct is not None
                else Reading(None, "%", reason=Text(NOT_SUPPORTED))
            )

        # NVML da el consumo en milivatios.
        put("power", "W", lambda: round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1))
        # 0 % es un valor real: muchas tarjetas paran el ventilador en reposo.
        put("fan", "%", lambda: pynvml.nvmlDeviceGetFanSpeed(h))
        put("clock", "MHz", lambda: pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS))
        out[self._id("name")] = self._name_reading(h)
        return out

    def _name_reading(self, handle: Any) -> Reading:
        """El nombre no cambia: se pide una vez por GPU y se guarda."""
        if self._name is None:
            try:
                raw = pynvml.nvmlDeviceGetName(handle)
            except Exception as exc:
                return Reading(None, "", reason=_failure(exc))
            # Versiones antiguas de nvidia-ml-py devuelven bytes; las nuevas, str.
            self._name = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            self._name = self._name.removeprefix("NVIDIA ").strip()
        return Reading(None, "", text=self._name)

    def close(self) -> None:
        self._closed = True
        self._release()

    def _release(self) -> None:
        """Suelta la GPU y apaga NVML si estaba arrancado (sin marcar la fuente cerrada)."""
        self._handle = None
        self._name = None
        self._pci = None
        if self._initialized:
            self._initialized = False
            try:
                pynvml.nvmlShutdown()
            except Exception as exc:
                log.debug("Fallo al cerrar NVML (%s).", type(exc).__name__)


# «00000000:07:00.0»: dominio, bus, dispositivo y función, en hexadecimal.
_BUS_ID = re.compile(r"^([0-9a-fA-F]{1,8}):([0-9a-fA-F]{1,2}):([0-9a-fA-F]{1,2})\.([0-7])$")


def parse_pci_bus_id(raw: str | bytes) -> tuple[int, int, int] | None:
    """Bus, dispositivo y función del ``busId`` de NVML (``nvmlDeviceGetPciInfo``).

    Solo vale el dominio 0: Windows (D3DKMT) no da el dominio, así que con otro no se
    podría saber si es la misma tarjeta. En un PC de casa el dominio es siempre 0
    (medido: «00000000:07:00.0» en la RTX 4070 SUPER).
    """
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    m = _BUS_ID.match(text.strip())
    if m is None or int(m.group(1), 16) != 0:
        return None
    bus, device, function = (int(m.group(i), 16) for i in (2, 3, 4))
    return (bus, device, function) if device <= 31 else None


def _read_pci_address(handle: Any) -> tuple[int, int, int] | None:
    """Se pide una vez al arrancar. Si NVML no lo da, ``None`` (no es un fallo de la GPU)."""
    try:
        return parse_pci_bus_id(pynvml.nvmlDeviceGetPciInfo(handle).busId)
    except Exception as exc:
        log.debug("NVML no da la dirección PCI (%s).", type(exc).__name__)
        return None


def _failure(exc: Exception) -> str:
    """Traduce un error de NVML a un motivo en español para el usuario."""
    if isinstance(exc, pynvml.NVMLError_NotSupported):
        return Text(NOT_SUPPORTED)
    return t("sensores.no_se_pudo_leer", kind=type(exc).__name__)


def _safe(unit: str, fn: Callable[[], float]) -> Reading:
    try:
        return Reading(float(fn()), unit)
    except Exception as exc:
        return Reading(None, unit, reason=_failure(exc))
