"""Contrato de los sensores (fijado por el coordinador en la fase 3).

Un sensor tiene un identificador estable en inglés (lo usan los temas, p. ej.
``"gpu.temperature"``), una etiqueta en español para la interfaz y una unidad.
Una lectura sin valor **nunca es 0**: lleva ``value=None`` y ``reason`` con el porqué
en español (RQ-05).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

SENSOR_ID = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


@dataclass(frozen=True)
class SensorInfo:
    id: str  # "cpu.usage", "gpu.temperature", "disk.usage.c"...
    label: str  # en español: "Uso de CPU"
    unit: str  # "%", "°C", "W", "MB/s", "GB", "RPM", ""
    category: str  # "cpu", "gpu", "memory", "network", "disk", "system"
    # 🆕 C-12 (2026-11-17): «number» (lo normal) o «text» (nombre de la GPU, canción…).
    # El editor lo usa para ofrecer solo los sensores que encajan con cada widget.
    kind: str = "number"


@dataclass(frozen=True)
class Reading:
    value: float | None
    unit: str
    reason: str | None = None  # por qué no hay valor (en español), si value es None
    # 🆕 C-12: valor de los sensores de texto. En ellos ``value`` es None y ``text`` lleva
    # el texto; si tampoco hay texto, ``reason`` dice por qué. Nunca datos que identifiquen
    # al usuario ni al equipo (nombre de usuario, de equipo, IP…): solo hardware y medios.
    text: str | None = None


class SensorSource(Protocol):
    """Una familia de sensores (psutil, NVML...). Debe ser barata de consultar:
    la app la llama cada segundo y el objetivo es < 1 % de CPU (RQ-04)."""

    def sensors(self) -> list[SensorInfo]:
        """Sensores que ofrece en este equipo (puede ser lista vacía)."""
        ...

    def read_all(self) -> dict[str, Reading]:
        """Lee todos sus sensores de una vez. No lanza excepciones: si algo falla,
        devuelve Reading(None, unit, reason) para ese sensor."""
        ...

    def close(self) -> None: ...
