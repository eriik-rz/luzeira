"""Agregador de fuentes de sensores.

El hub es lo único que ve el resto de la app: una vez por ciclo se llama a ``poll()``
(lee todas las fuentes y guarda una instantánea) y después el render pide cada valor
con ``read(id)``, que solo consulta la instantánea y no toca el hardware.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from luzeira.idioma import t
from luzeira.sensores.base import Reading, SensorInfo, SensorSource

log = logging.getLogger(__name__)

# Claves del catálogo (zona ``sensores``): el motivo se traduce al crear cada lectura.
NOT_READ_YET = "sensores.sin_lectura"
SOURCE_FAILED = "sensores.fuente_fallo"


class SensorHub:
    def __init__(self, sources: Sequence[SensorSource]) -> None:
        self._sources = list(sources)
        # Qué sensores ofrece cada fuente: se pregunta una sola vez al crear el hub.
        self._by_source: list[tuple[SensorSource, list[SensorInfo]]] = []
        for source in self._sources:
            try:
                infos = list(source.sensors())
            except Exception as exc:
                _log_failure("sensors", source, exc)
                infos = []
            self._by_source.append((source, infos))
        self._info: dict[str, SensorInfo] = {}
        for _, infos in self._by_source:
            for info in infos:
                self._info.setdefault(info.id, info)  # si se repite un id, gana la primera
        self._snapshot: dict[str, Reading] = {}

    def available(self) -> list[SensorInfo]:
        return list(self._info.values())

    def poll(self) -> None:
        """Lee todas las fuentes. Una fuente que falla no tumba al resto."""
        snapshot: dict[str, Reading] = {}
        for source, infos in self._by_source:
            try:
                readings = source.read_all()
            except Exception as exc:
                _log_failure("read_all", source, exc)
                readings = {i.id: Reading(None, i.unit, reason=t(SOURCE_FAILED)) for i in infos}
            for sensor_id, reading in readings.items():
                snapshot.setdefault(sensor_id, reading)
        self._snapshot = snapshot

    def read(self, sensor_id: str) -> Reading:
        reading = self._snapshot.get(sensor_id)
        if reading is not None:
            return reading
        info = self._info.get(sensor_id)
        if info is None:
            return Reading(None, "", reason=t("sensores.desconocido", id=sensor_id))
        return Reading(None, info.unit, reason=t(NOT_READ_YET))

    def close(self) -> None:
        for source in self._sources:
            try:
                source.close()
            except Exception as exc:
                _log_failure("close", source, exc)


def _log_failure(step: str, source: SensorSource, exc: Exception) -> None:
    # Solo nombre de clase y tipo de error: el mensaje y la traza pueden llevar rutas
    # con el nombre de usuario de Windows, y los registros se adjuntan en incidencias.
    log.warning("La fuente %s falló en %s (%s).", type(source).__name__, step, type(exc).__name__)


def default_hub() -> SensorHub:
    """Hub con las fuentes: sistema (psutil), módulo opcional de sensores (temperatura de
    la CPU; sin él, «—») y GPU (NVML si hay NVIDIA; si no, lo que da Windows para
    cualquier marca)."""
    from luzeira.sensores.gpu import GpuSource
    from luzeira.sensores.modulo import ModuleSource
    from luzeira.sensores.nvidia import NvidiaSource
    from luzeira.sensores.sistema import SystemSource
    from luzeira.sensores.windows_gpu import WindowsGpuSource

    return SensorHub(
        [SystemSource(), ModuleSource(), GpuSource(NvidiaSource(), WindowsGpuSource())]
    )
