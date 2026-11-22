"""Tests de la tarea de inicio de la versión Store, con una tarea falsa (la real solo
existe dentro del paquete MSIX: se probó instalada, ver ``tarea_inicio``)."""

from __future__ import annotations

import pytest

from luzeira.sistema import tarea_inicio
from luzeira.sistema.tarea_inicio import StartupState, StartupTaskError


class Task:
    def __init__(self, raw: int, after: int | None = None) -> None:
        self.raw, self.after, self.calls = raw, after, []

    def state(self) -> int:
        return self.raw

    def enable(self) -> int:
        self.calls.append("enable")
        return self.after if self.after is not None else 2

    def disable(self) -> int:
        self.calls.append("disable")
        return self.after if self.after is not None else 0


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, StartupState.OFF),
        (1, StartupState.BLOCKED),  # el usuario la quitó: solo él la vuelve a poner
        (2, StartupState.ON),
        (3, StartupState.BLOCKED),  # directiva
        (4, StartupState.ON),  # activada por directiva
        (99, StartupState.BLOCKED),  # desconocido: no se promete lo que no se sabe
    ],
)
def test_windows_states_map_to_what_the_user_can_do(raw, expected):
    assert tarea_inicio.state(Task(raw)) == expected


def test_enable_and_disable_return_how_it_ends_up():
    task = Task(0)
    assert tarea_inicio.set_enabled(True, task) == StartupState.ON
    assert tarea_inicio.set_enabled(False, task) == StartupState.OFF
    assert task.calls == ["enable", "disable"]


def test_enabling_a_task_the_user_removed_stays_blocked():
    assert tarea_inicio.set_enabled(True, Task(1, after=1)) == StartupState.BLOCKED


def test_errors_from_windows_become_one_error():
    class Broken:
        def state(self) -> int:
            raise OSError("No se ha encontrado el elemento.")

        enable = disable = state

    with pytest.raises(StartupTaskError):
        tarea_inicio.state(Broken())
    with pytest.raises(StartupTaskError):
        tarea_inicio.set_enabled(True, Broken())


def test_the_task_id_is_the_one_in_the_manifest():
    from pathlib import Path

    manifest = Path(__file__).resolve().parent.parent / "instalador" / "AppxManifest.xml"
    assert f'TaskId="{tarea_inicio.TASK_ID}"' in manifest.read_text(encoding="utf-8")
