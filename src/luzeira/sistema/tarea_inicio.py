"""«Iniciar con Windows» en la versión de Microsoft Store: la tarea de inicio del paquete.

Concepto: una app empaquetada no puede usar la clave ``Run`` (queda aislada, ver
:mod:`luzeira.sistema.paquete`). Arranca con Windows por la ``windows.startupTask`` de su
manifiesto (``TaskId="LuzeiraInicio"``), y la API WinRT ``StartupTask`` deja leerla y
cambiarla desde la app. Solo funciona con identidad de paquete: fuera de él, Windows
contesta «No se ha encontrado el elemento».

Un caso importa: si el usuario la desactiva en el Administrador de tareas o en
Configuración, el estado pasa a ``DisabledByUser`` y **la app ya no puede reactivarla**
(Windows lo reserva al usuario). Lo mismo si la bloquea una directiva. Para la app eso es
«bloqueado»: se le explica al usuario y se le abre Configuración.

Medido el 2026-11-22 en el paquete de prueba (Windows 10 22H2): leer, ``Disable`` y
``RequestEnableAsync`` cambian el estado y Configuración lo refleja al momento.

Fuente: «StartupTask Class» y «StartupTaskState Enum», Microsoft Learn (consultadas
2026-11-22), https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.startuptask
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Protocol

log = logging.getLogger(__name__)

#: El ``TaskId`` de ``instalador/AppxManifest.xml``.
TASK_ID = "LuzeiraInicio"

#: ``StartupTaskState``: 0 desactivada, 1 desactivada por el usuario, 2 activada,
#: 3 desactivada por directiva, 4 activada por directiva.
_DISABLED, _DISABLED_BY_USER, _ENABLED, _DISABLED_BY_POLICY, _ENABLED_BY_POLICY = range(5)


class StartupState(StrEnum):
    ON = "on"
    OFF = "off"
    BLOCKED = "blocked"  # solo el usuario (o el administrador) puede cambiarlo, en Windows


class StartupTaskError(Exception):
    """No se pudo leer o cambiar la tarea de inicio."""


class Task(Protocol):
    """Lo que se usa de ``StartupTask`` (los tests ponen uno falso)."""

    def state(self) -> int: ...

    def enable(self) -> int: ...

    def disable(self) -> int: ...


class _WinRTTask:
    """La tarea real. Cada llamada es corta: se espera con ``asyncio.run``."""

    @staticmethod
    async def _get():
        from winrt.windows.applicationmodel import StartupTask

        return await StartupTask.get_async(TASK_ID)

    def state(self) -> int:
        return int(asyncio.run(self._get()).state)

    def enable(self) -> int:
        async def run() -> int:
            return int(await (await self._get()).request_enable_async())

        return asyncio.run(run())

    def disable(self) -> int:
        async def run() -> int:
            task = await self._get()
            task.disable()
            return int(task.state)

        return asyncio.run(run())


def _to_state(raw: int) -> StartupState:
    if raw in (_ENABLED, _ENABLED_BY_POLICY):
        return StartupState.ON
    if raw == _DISABLED:
        return StartupState.OFF
    return StartupState.BLOCKED  # por el usuario, por directiva o un valor desconocido


def state(task: Task | None = None) -> StartupState:
    try:
        return _to_state((task or _WinRTTask()).state())
    except Exception as exc:  # WinRT da OSError, ImportError fuera de Windows…
        raise StartupTaskError(f"No se pudo leer la tarea de inicio: {exc}") from exc


def set_enabled(on: bool, task: Task | None = None) -> StartupState:
    """Activa o desactiva la tarea y devuelve cómo queda (puede quedar ``BLOCKED``)."""
    task = task or _WinRTTask()
    try:
        result = _to_state(task.enable() if on else task.disable())
    except Exception as exc:
        raise StartupTaskError(f"No se pudo cambiar la tarea de inicio: {exc}") from exc
    log.info("Tarea de inicio: %s", result)
    return result
