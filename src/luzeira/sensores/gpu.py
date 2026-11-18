"""Sensores ``gpu.*``: junta NVML (NVIDIA) y Windows (cualquier marca).

Reglas:

- **NVML manda.** Si hay una NVIDIA con NVML, todos los ``gpu.*`` salen de NVML y Windows
  ni se abre (en el equipo de pruebas, exactamente lo mismo que antes de existir esta
  fuente). PDH y NVML no dan las mismas cifras (~5 puntos de uso y ~270 MB de memoria de
  diferencia medida), así que no se alternan.
- Sin NVML (AMD, Intel, o NVIDIA sin NVML), todo sale de Windows.
- Con NVML, Windows solo rellena los sensores que NVML dice que esta tarjeta no da
  (``NOT_SUPPORTED``), y solo si la tarjeta que eligió Windows también es NVIDIA. Un fallo
  pasajero de NVML no se rellena: se ve su motivo.
- La memoria va en bloque: la usada y la total vienen siempre de la misma fuente (si no,
  el porcentaje saldría mal). El consumo en vatios solo lo da NVML.
"""

from __future__ import annotations

from luzeira.sensores import nvidia
from luzeira.sensores.base import Reading, SensorInfo
from luzeira.sensores.nvidia import NvidiaSource
from luzeira.sensores.windows_gpu import NVIDIA_VENDOR_ID, WindowsGpuSource

_MEMORY = ("memory_used", "memory_total", "memory_usage")
# Lo que Windows puede dar sensor a sensor. El uso se rellena aparte (es PDH, más caro).
_SINGLE = ("usage", "temperature", "fan", "clock")


class GpuSource:
    def __init__(self, nvml: NvidiaSource, windows: WindowsGpuSource, prefix: str = "gpu") -> None:
        self._nvml = nvml
        self._windows = windows
        self._prefix = prefix

    def _id(self, name: str) -> str:
        return f"{self._prefix}.{name}"

    def sensors(self) -> list[SensorInfo]:
        # Los mismos ids en las dos fuentes; las etiquetas y unidades, las de NVML.
        return self._nvml.sensors()

    def read_all(self) -> dict[str, Reading]:
        readings = self._nvml.read_all()
        if not self._nvml.has_gpu:
            return self._windows.read_all()

        def missing(name: str) -> bool:
            r = readings.get(self._id(name))
            return r is not None and r.value is None and r.reason == nvidia.NOT_SUPPORTED

        wanted = [self._id(n) for n in _SINGLE if missing(n)]
        memory_missing = missing("memory_used") and missing("memory_total")
        if memory_missing:
            wanted += [self._id(n) for n in _MEMORY]
        if not wanted:
            return readings
        extra = self._windows.read_some(wanted)
        if self._windows.vendor_id != NVIDIA_VENDOR_ID:
            return readings  # Windows aún no arrancó o eligió otra tarjeta: no se mezclan
        for name in _SINGLE:
            reading = extra.get(self._id(name))
            if reading is not None and reading.value is not None:
                readings[self._id(name)] = reading
        if memory_missing and all(
            (r := extra.get(self._id(n))) is not None and r.value is not None
            for n in ("memory_used", "memory_total")
        ):
            for name in _MEMORY:
                readings[self._id(name)] = extra[self._id(name)]
        return readings

    def close(self) -> None:
        try:
            self._nvml.close()
        finally:
            self._windows.close()
