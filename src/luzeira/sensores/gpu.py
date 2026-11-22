"""Sensores ``gpu.*``: junta NVML (NVIDIA) y Windows (cualquier marca).

Reglas:

- **NVML manda.** Si hay una NVIDIA con NVML, todos los ``gpu.*`` salen de NVML y Windows
  ni se abre (en el equipo de pruebas, exactamente lo mismo que antes de existir esta
  fuente). PDH y NVML no dan las mismas cifras (~5 puntos de uso y ~270 MB de memoria de
  diferencia medida), así que no se alternan.
- Sin NVML (AMD, Intel, o NVIDIA sin NVML), todo sale de Windows.
- Si NVML arranca tarde (el controlador cargó después que la app), Windows lo da todo
  hasta entonces; en cuanto NVML responde, manda NVML y lo abierto de Windows se suelta
  (``WindowsGpuSource.release``). Ese cambio de fuente se ve una vez, al arrancar.
- Con NVML, Windows solo rellena los sensores que NVML dice que esta tarjeta no da
  (``NOT_SUPPORTED``), y solo si la tarjeta que eligió Windows es NVIDIA **y la única
  NVIDIA**: con dos, NVML lee la 0 y Windows la de más memoria, y podrían ser distintas.
  Un fallo pasajero de NVML no se rellena: se ve su motivo.
- La memoria va en bloque: la usada y la total vienen siempre de la misma fuente (si no,
  el porcentaje saldría mal). El consumo en vatios solo lo da NVML.
"""

from __future__ import annotations

from luzeira.sensores import nvidia
from luzeira.sensores.base import Reading, SensorInfo
from luzeira.sensores.nvidia import NvidiaSource
from luzeira.sensores.windows_gpu import NVIDIA_VENDOR_ID, WindowsGpuSource

_MEMORY = ("memory_used", "memory_total", "memory_usage")
# Lo que Windows puede rellenar sensor a sensor (la memoria va en bloque, en ``_MEMORY``).
# ``WindowsGpuSource.read_some`` solo toca PDH (el caro) si se le pide el uso o la memoria.
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
            # Por la clave del motivo, no por su texto: el texto depende del idioma (y
            # podría cambiar entre la lectura y esta comparación) y otro motivo podría
            # traducirse igual en algún idioma.
            key = getattr(r.reason, "key", None) if r is not None else None
            return r is not None and r.value is None and key == nvidia.NOT_SUPPORTED

        wanted = [self._id(n) for n in _SINGLE if missing(n)]
        memory_missing = missing("memory_used") and missing("memory_total")
        if memory_missing:
            wanted += [self._id(n) for n in _MEMORY]
        if not wanted:
            # NVML lo da todo: si Windows quedó abierto de antes de que NVML respondiera,
            # se suelta (sin nada abierto no hace nada).
            self._windows.release()
            return readings
        extra = self._windows.read_some(wanted)
        if self._windows.sole_vendor_id != NVIDIA_VENDOR_ID:
            # Windows aún no arrancó, eligió otra marca o hay más de una NVIDIA: no se
            # mezclan (podría ser el dato de otra tarjeta).
            return readings
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
