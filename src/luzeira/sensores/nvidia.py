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
import time
from collections.abc import Callable
from typing import Any

import pynvml

from luzeira.sensores.base import Reading, SensorInfo

log = logging.getLogger(__name__)

_GIB = 1024**3

NOT_SUPPORTED = "Esta tarjeta gráfica no informa de este dato."
NOT_READY = "La GPU aún no está lista."
NOT_FOUND = "No se encontró una tarjeta gráfica NVIDIA (o su controlador no responde)."

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
        self._sensors = self._build_sensors()
        self._try_start()

    def _try_start(self) -> None:
        """Intenta arrancar NVML y coger la GPU. Si falla, deja la fuente sin GPU."""
        try:
            pynvml.nvmlInit()
            self._initialized = True
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self._index)
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
        labels = {
            "temperature": "Temperatura de GPU",
            "usage": "Uso de GPU",
            "memory_used": "Memoria de GPU usada",
            "memory_total": "Memoria de GPU total",
            "memory_usage": "Uso de memoria de GPU",
            "power": "Consumo de GPU",
            "fan": "Ventilador de GPU",
            "clock": "Frecuencia de GPU",
        }
        numbers = [SensorInfo(self._id(n), labels[n], unit, "gpu") for n, unit in _UNITS.items()]
        # C-12: sensor de texto. Es el modelo de la tarjeta, no identifica a nadie.
        return [*numbers, SensorInfo(self._id("name"), "Nombre de la GPU", "", "gpu", "text")]

    def sensors(self) -> list[SensorInfo]:
        return list(self._sensors)

    def read_all(self) -> dict[str, Reading]:
        if self._handle is None:
            now = self._clock()
            # Una fuente cerrada no vuelve a abrir NVML.
            if not self._closed and now - self._last_attempt >= RETRY_SECONDS:
                self._last_attempt = now
                self._try_start()
            if self._handle is None:
                waited = now - self._started_at
                reason = NOT_READY if waited < GIVE_UP_SECONDS else NOT_FOUND
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
                else Reading(None, "%", reason=NOT_SUPPORTED)
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
        if self._initialized:
            self._initialized = False
            try:
                pynvml.nvmlShutdown()
            except Exception as exc:
                log.debug("Fallo al cerrar NVML (%s).", type(exc).__name__)


def _failure(exc: Exception) -> str:
    """Traduce un error de NVML a un motivo en español para el usuario."""
    if isinstance(exc, pynvml.NVMLError_NotSupported):
        return NOT_SUPPORTED
    return f"No se pudo leer ({type(exc).__name__})."


def _safe(unit: str, fn: Callable[[], float]) -> Reading:
    try:
        return Reading(float(fn()), unit)
    except Exception as exc:
        return Reading(None, unit, reason=_failure(exc))
