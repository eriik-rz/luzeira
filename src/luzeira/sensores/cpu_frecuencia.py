"""Frecuencia real de la CPU en Windows, sin permisos de administrador.

psutil solo da la frecuencia nominal en Windows (3001 MHz en el equipo de pruebas aunque
la CPU vaya a 4 GHz). Windows publica dos contadores de rendimiento (PDH) que, juntos,
dan la real:

- ``Processor Frequency``: la nominal, en MHz;
- ``% Processor Performance``: a qué porcentaje de esa nominal va de verdad (pasa de 100
  cuando el procesador sube por encima, el «turbo»).

Real = nominal por porcentaje / 100. Medido el 2026-11-24 en el Ryzen 7 5700X3D: 3001 MHz
al 133 % ≈ 3990 MHz. Microsoft no documenta la fórmula: es la misma idea que el contador
``Processor Performance`` describe, comprobada a mano (ver la tanda 9 de investigación).

Abrir la consulta PDH tarda ~0,4 s (medido), así que se abre en un hilo aparte y
mientras tanto se da «Midiendo…». Cada lectura posterior cuesta menos de 1 ms.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from luzeira.idioma import t
from luzeira.sensores.base import Reading

log = logging.getLogger(__name__)

MEASURING = "sensores.midiendo"
CANNOT_READ = "sensores.frecuencia_no_informa"

#: Fuera de este rango el dato no es una frecuencia de CPU de verdad (contador roto).
MIN_MHZ = 100.0
MAX_MHZ = 10_000.0

#: Si abrir los contadores falla, se reintenta cada tanto, unas pocas veces.
RETRY_SECONDS = 30.0
MAX_ATTEMPTS = 5


class FrequencyCounters(Protocol):
    def sample(self) -> tuple[float, float] | None:
        """(nominal en MHz, porcentaje de rendimiento) o ``None`` si aún no hay dato."""
        ...

    def close(self) -> None: ...


def real_counters() -> FrequencyCounters:
    from luzeira.sensores.windows_cpu import RealFrequencyCounters

    return RealFrequencyCounters()


class CpuFrequency:
    """Lee la frecuencia real con los contadores de Windows, abiertos en segundo plano."""

    def __init__(
        self,
        opener: Callable[[], FrequencyCounters] = real_counters,
        clock: Callable[[], float] = time.monotonic,
        background: bool = True,
    ) -> None:
        self._opener = opener
        self._clock = clock
        self._background = background
        self._lock = threading.Lock()
        self._counters: FrequencyCounters | None = None
        self._thread: threading.Thread | None = None
        self._attempts = 0
        self._last_attempt: float | None = None
        self._failed = False
        self._closed = False

    def read(self) -> Reading:
        self._ensure_open()
        with self._lock:
            counters = self._counters
            failed = self._failed
        if counters is None:
            return Reading(None, "MHz", reason=t(CANNOT_READ if failed else MEASURING))
        sample = counters.sample()
        if sample is None:
            return Reading(None, "MHz", reason=t(MEASURING))
        nominal, percent = sample
        mhz = nominal * percent / 100.0
        if not (MIN_MHZ <= mhz <= MAX_MHZ):  # también descarta NaN
            return Reading(None, "MHz", reason=t(CANNOT_READ))
        return Reading(round(mhz), "MHz")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            counters, self._counters = self._counters, None
        if counters is not None:
            counters.close()

    def _ensure_open(self) -> None:
        with self._lock:
            if self._closed or self._counters is not None:
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
            if self._background:
                # «daemon»: si la app se cierra mientras abre, el hilo no la retiene.
                self._thread = threading.Thread(
                    target=self._open, name="luzeira-cpu-frecuencia", daemon=True
                )
                self._thread.start()
                return
        self._open()  # sin hilo (tests)

    def _open(self) -> None:
        try:
            counters = self._opener()
        except Exception as exc:
            # Solo el tipo de error: el mensaje podría llevar datos del equipo.
            log.info("Frecuencia de la CPU no disponible (%s).", type(exc).__name__)
            with self._lock:
                self._failed = True
            return
        with self._lock:
            if self._closed:
                counters.close()
                return
            self._counters = counters
            self._failed = False
