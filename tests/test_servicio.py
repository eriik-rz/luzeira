"""Tests del servicio de la pantalla con dobles (sin puerto serie real ni sensores)."""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path

import pytest

import luzeira
from luzeira.pantallas import ConfirmationNeeded, DisplayBusy, DisplayError, Orientation
from luzeira.sensores.base import Reading
from luzeira.servicio import DisplayService, Settings, Status, Timings

THEME = Path(luzeira.__file__).parent / "recursos" / "temas" / "esencial"
FULL_SIZE = (480, 320)  # el tema Esencial es horizontal
FAST = Timings(tick=0.02, warmup=0.02, backoff_start=0.02, backoff_max=0.05)


class FakeDisplay:
    """Pantalla falsa: apunta cada llamada; puede fallar en la N-ésima escritura."""

    def __init__(self, log: list, fail_on_draw: int | None = None) -> None:
        self.log = log
        self.fail_on_draw = fail_on_draw
        self.draws = 0
        self.failed_at: float | None = None  # cuándo falló la escritura programada

    def _rec(self, *call) -> None:
        self.log.append((self, *call))

    def screen_on(self) -> None:
        self._rec("on")

    def screen_off(self) -> None:
        self._rec("off")

    def set_brightness(self, percent: int) -> None:
        self._rec("brightness", percent)

    def set_orientation(self, orientation: Orientation) -> None:
        self._rec("orientation", orientation)

    def draw_image(self, image, x: int = 0, y: int = 0) -> None:
        self.draws += 1
        if self.fail_on_draw is not None and self.draws >= self.fail_on_draw:
            self.failed_at = time.monotonic()
            raise DisplayError(
                "Se ha perdido la conexión con la pantalla. ¿Se ha desenchufado el USB?"
            )
        full = (x, y) == (0, 0) and image.size == FULL_SIZE
        self._rec("full" if full else "patch")

    def close(self) -> None:
        self._rec("close")


class FakeHub:
    def __init__(self) -> None:
        self.polls = 0
        self.closed = False

    def poll(self) -> None:
        self.polls += 1

    def read(self, sensor_id: str) -> Reading:
        return Reading(float(self.polls % 100), "%")

    def close(self) -> None:
        self.closed = True


class Opener:
    """``open_display`` falso. ``plan`` = lo que pasa en cada intento (una excepción o
    None para dar una pantalla); cuando se acaba, siempre da pantalla."""

    def __init__(self, plan=(), fail_on_draw: dict[int, int] | None = None, make=None) -> None:
        self.plan = list(plan)
        self.calls: list[str | None] = []
        self.times: list[float] = []  # cuándo se intentó cada apertura
        self.log: list = []
        self.displays: list[FakeDisplay] = []
        self.fail_on_draw = fail_on_draw or {}
        # make(index, log, fail_on_draw) -> pantalla; por defecto, FakeDisplay.
        self.make = make or (lambda index, log, fail: FakeDisplay(log, fail))

    def __call__(self, port: str | None) -> FakeDisplay:
        self.calls.append(port)
        self.times.append(time.monotonic())
        if self.plan:
            outcome = self.plan.pop(0)
            if outcome is not None:
                raise outcome
        index = len(self.displays)
        display = self.make(index, self.log, self.fail_on_draw.get(index))
        self.displays.append(display)
        return display

    def calls_of(self, display: FakeDisplay) -> list:
        return [c[1:] for c in self.log if c[0] is display]


class Harness:
    def __init__(self, settings=None, timings=FAST, **kwargs) -> None:
        self.opener = kwargs.pop("opener", Opener())
        self.hub = FakeHub()
        self.statuses: list[tuple[Status, str]] = []
        self.service = DisplayService(
            settings or Settings(THEME),
            open_display=self.opener,
            make_hub=lambda: self.hub,
            on_status=lambda s, m: self.statuses.append((s, m)),
            timings=timings,
            **kwargs,
        )
        self.thread = threading.Thread(target=self.service.run, daemon=True)

    def __enter__(self) -> Harness:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.service.stop()
        self.thread.join(2)
        assert not self.thread.is_alive(), "run() no ha terminado tras stop()"

    def wait_for(self, predicate, timeout: float = 1.0) -> None:
        end = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > end:
                raise AssertionError(f"no se cumplió a tiempo; estados: {self.statuses}")
            time.sleep(0.005)

    def wait_status(self, status: Status, timeout: float = 1.0) -> None:
        self.wait_for(lambda: self.service.status == status, timeout)


def assert_off_then_closed(calls: list) -> None:
    """SCREEN_OFF, después un único close y ya nada más (RQ-07)."""
    assert calls.count(("close",)) == 1
    assert calls[-2:] == [("off",), ("close",)]


def test_normal_start_configures_then_waits_and_draws_full_frame():
    with Harness(Settings(THEME, brightness=40)) as h:
        h.wait_status(Status.RUNNING)
        calls = h.opener.calls_of(h.opener.displays[0])
        assert calls[:4] == [
            ("on",),
            ("brightness", 40),
            ("orientation", Orientation.LANDSCAPE),
            ("full",),
        ]
        assert h.hub.polls >= 2  # uno al conectar y otro tras la espera
    assert h.opener.calls == [None]
    assert [s for s, _ in h.statuses][:2] == [Status.STARTING, Status.RUNNING]


def test_first_full_frame_waits_for_warmup():
    slow_warmup = Timings(tick=0.02, warmup=0.3, backoff_start=0.02, backoff_max=0.05)
    with Harness(timings=slow_warmup) as h:
        h.wait_status(Status.STARTING)
        h.wait_for(lambda: h.opener.displays)
        time.sleep(0.1)
        assert ("full",) not in h.opener.calls_of(h.opener.displays[0])
        h.wait_status(Status.RUNNING)


def test_stop_turns_screen_off_before_closing_and_releases_sensors():
    with Harness() as h:
        h.wait_status(Status.RUNNING)
    calls = h.opener.calls_of(h.opener.displays[0])
    assert_off_then_closed(calls)
    assert h.hub.closed
    assert h.service.status == Status.STOPPED


def test_unplug_mid_run_reconnects_and_repaints_whole_frame():
    # La primera pantalla falla en su 2.ª imagen (desenchufe ya en marcha). El giro
    # fuerza esa 2.ª imagen al momento, sin esperar a que cambie un sensor.
    opener = Opener(plan=[None, DisplayError("No encuentro la pantalla. ¿Está conectada?")])
    opener.fail_on_draw = {0: 2}
    with Harness(opener=opener) as h:
        h.wait_status(Status.RUNNING)
        h.service.apply(Settings(THEME, flip=True))
        h.wait_for(lambda: len(opener.displays) == 2)
        h.wait_for(lambda: ("full",) in opener.calls_of(opener.displays[1]))
        h.wait_status(Status.RUNNING)
        first = opener.calls_of(opener.displays[0])
        assert first[-1] == ("close",)
        assert ("off",) not in first  # con el USB perdido no se espera a apagarla
    seen = [s for s, _ in h.statuses]
    assert Status.NO_DISPLAY in seen
    assert seen.index(Status.NO_DISPLAY) < len(seen) - 1 - seen[::-1].index(Status.RUNNING)


def test_busy_display_shows_clear_message_and_retries_with_backoff():
    opener = Opener(plan=[DisplayBusy("COM3")] * 1000)
    with Harness(opener=opener) as h:
        h.wait_status(Status.NO_DISPLAY)
        time.sleep(0.35)
        assert "en uso por otro programa" in h.service.message
        assert "sigo intentando" in h.service.message
        # Espera creciente con tope de 0,05 s: en 0,35 s, unos 8 intentos, no miles.
        assert 5 <= len(opener.calls) <= 14
        # El tope se respeta: sin él, la espera llegaría a 0,16 s en este rato.
        gaps = [b - a for a, b in itertools.pairwise(opener.times)]
        assert max(gaps) < 0.1, gaps
    # El mismo estado con el mismo mensaje no se avisa una y otra vez.
    assert [s for s, _ in h.statuses].count(Status.NO_DISPLAY) == 1


def test_open_failure_message_is_not_repeated():
    failed = DisplayError("No puedo abrir la pantalla (COM3). Lo sigo intentando.")
    opener = Opener(plan=[failed] * 1000)
    with Harness(opener=opener) as h:
        h.wait_status(Status.NO_DISPLAY)
        assert h.service.message == "No puedo abrir la pantalla (COM3). Lo sigo intentando."


def test_confirmation_needed_does_not_loop_until_user_confirms():
    opener = Opener(plan=[ConfirmationNeeded("COM7"), ConfirmationNeeded("COM7")])
    with Harness(opener=opener) as h:
        assert h.service.pending_device is None
        h.wait_status(Status.NEEDS_CONFIRMATION)
        assert "COM7" in h.service.message
        assert h.service.pending_device == "COM7"
        time.sleep(0.2)
        assert opener.calls == [None]  # ni un reintento solo
        h.service.confirm_port("COM7")
        h.wait_status(Status.RUNNING)
        assert h.service.pending_device is None
    assert opener.calls == [None, None, "COM7"]


def test_broken_theme_never_touches_display_until_a_good_one_arrives(tmp_path):
    with Harness(Settings(tmp_path / "no-existe")) as h:
        h.wait_status(Status.THEME_ERROR)
        assert "no-existe" in h.service.message
        time.sleep(0.1)
        assert h.opener.calls == []
        h.service.apply(Settings(THEME))
        h.wait_status(Status.RUNNING)


def test_broken_theme_applied_while_running_keeps_previous_one(tmp_path):
    with Harness() as h:
        h.wait_status(Status.RUNNING)
        h.service.apply(Settings(tmp_path / "roto"))
        h.wait_for(lambda: "roto" in h.service.message)
        assert h.service.status == Status.RUNNING
        assert "Esencial" in h.service.message


class SlowOffDisplay(FakeDisplay):
    """SCREEN_OFF tarda (como el USB real): suspend() debe esperar a que acabe."""

    def screen_off(self) -> None:
        time.sleep(0.15)
        super().screen_off()


def test_suspend_turns_off_before_closing_and_resume_repaints():
    opener = Opener(
        make=lambda index, log, fail: SlowOffDisplay(log, fail) if index == 0 else FakeDisplay(log)
    )
    with Harness(opener=opener) as h:
        h.wait_status(Status.RUNNING)
        assert h.service.suspend(timeout=1.0)
        # suspend() no vuelve hasta que la pantalla está apagada y el puerto suelto.
        first = h.opener.calls_of(h.opener.displays[0])
        assert_off_then_closed(first)
        assert h.service.status == Status.SUSPENDED
        time.sleep(0.1)
        assert len(h.opener.displays) == 1  # dormido: no reabre
        h.service.resume()
        h.wait_for(lambda: len(h.opener.displays) == 2)
        h.wait_status(Status.RUNNING)
        assert ("full",) in h.opener.calls_of(h.opener.displays[1])


class GatedDisplay(FakeDisplay):
    """La imagen completa se queda «enviándose» hasta que el test abre la compuerta."""

    def __init__(self, log: list, fail_on_draw: int | None = None) -> None:
        super().__init__(log, fail_on_draw)
        self.sending = threading.Event()
        self.gate = threading.Event()

    def draw_image(self, image, x: int = 0, y: int = 0) -> None:
        if image.size == FULL_SIZE:
            self.sending.set()
            assert self.gate.wait(2), "el test no abrió la compuerta"
        super().draw_image(image, x, y)


def test_resume_before_suspend_was_handled_still_reconnects_from_scratch(caplog):
    # suspend() agota su tiempo mientras se envía la imagen completa y resume() llega
    # antes de que el hilo vea la suspensión: la pantalla pudo perder la corriente, así
    # que hay que reabrir y mandar todo (encendido, brillo, giro e imagen completa).
    opener = Opener(
        make=lambda index, log, fail: GatedDisplay(log, fail) if index == 0 else FakeDisplay(log)
    )
    with Harness(Settings(THEME, brightness=40), opener=opener) as h:
        h.wait_for(lambda: opener.displays)
        first = opener.displays[0]
        assert first.sending.wait(1)
        with caplog.at_level("WARNING", logger="luzeira.servicio"):
            assert h.service.suspend(timeout=0.05) is False
        assert "no se apagó a tiempo" in caplog.text
        h.service.resume()
        first.gate.set()
        h.wait_for(lambda: len(opener.displays) == 2)
        h.wait_for(lambda: ("full",) in opener.calls_of(opener.displays[1]))
        h.wait_status(Status.RUNNING)
        old = opener.calls_of(first)
        assert old[-1] == ("close",)
        assert ("off",) not in old  # tras el reposo no se espera a que responda
        assert opener.calls_of(opener.displays[1])[:4] == [
            ("on",),
            ("brightness", 40),
            ("orientation", Orientation.LANDSCAPE),
            ("full",),
        ]


class DeadScreen(FakeDisplay):
    """El puerto abre, pero la pantalla no acepta ni el encendido."""

    def screen_on(self) -> None:
        raise DisplayError("La pantalla no responde.")


def test_backoff_keeps_growing_until_a_connection_reaches_running():
    # Las 4 primeras aperturas abren el puerto pero fallan al encender; la 5.ª llega a
    # RUNNING y se pierde en su primer parche; la 6.ª va bien.
    timings = Timings(tick=0.02, warmup=0.02, backoff_start=0.02, backoff_max=0.5)

    def make(index, log, fail):
        return DeadScreen(log) if index < 4 else FakeDisplay(log, 2 if index == 4 else None)

    # Reloj adelantable: así los widgets «caducan» y el primer parche sale ya, sin
    # esperar su intervalo real (1 s). La espera se mide en tiempo real.
    offset = [0.0]
    opener = Opener(make=make)
    with Harness(
        opener=opener, timings=timings, monotonic=lambda: time.monotonic() + offset[0]
    ) as h:
        h.wait_for(lambda: len(opener.displays) == 5)
        h.wait_status(Status.RUNNING)
        offset[0] += 10.0
        h.wait_for(lambda: opener.displays[4].failed_at is not None)
        h.wait_for(lambda: len(opener.displays) == 6)
        h.wait_status(Status.RUNNING)
    t = opener.times
    gaps = [b - a for a, b in itertools.pairwise(t)]
    # Esperas 0,02 → 0,04 → 0,08 → 0,16: no vuelve a 0,02 por abrir el puerto.
    assert t[4] - t[3] >= 0.1, gaps
    # Tras llegar a RUNNING sí se reinicia: la 6.ª va a la primera espera (0,02 s), no
    # a la siguiente de la serie (0,32 s).
    assert t[5] - opener.displays[4].failed_at < 0.15, gaps


def test_stop_during_reconnect_wait_ends_quickly():
    slow = Timings(tick=0.02, warmup=0.02, backoff_start=30, backoff_max=30)
    opener = Opener(plan=[DisplayError("No encuentro la pantalla.")] * 10)
    h = Harness(opener=opener, timings=slow)
    h.thread.start()
    h.wait_status(Status.NO_DISPLAY)
    start = time.monotonic()
    h.service.stop()
    h.thread.join(1)
    assert not h.thread.is_alive()
    assert time.monotonic() - start < 0.5
    assert h.hub.closed
    assert opener.calls == [None]


def test_brightness_only_change_does_not_repaint():
    with Harness() as h:
        h.wait_status(Status.RUNNING)
        h.service.apply(Settings(THEME, brightness=80))
        h.wait_for(lambda: ("brightness", 80) in h.opener.calls_of(h.opener.displays[0]))
        time.sleep(0.1)
        calls = h.opener.calls_of(h.opener.displays[0])
        assert calls.count(("full",)) == 1
        assert calls[2] == ("orientation", Orientation.LANDSCAPE)
        assert sum(c[0] == "orientation" for c in calls) == 1


def test_flip_change_reorients_and_repaints():
    with Harness() as h:
        h.wait_status(Status.RUNNING)
        h.service.apply(Settings(THEME, flip=True))
        h.wait_for(lambda: h.opener.calls_of(h.opener.displays[0]).count(("full",)) == 2)
        calls = h.opener.calls_of(h.opener.displays[0])
        assert ("orientation", Orientation.REVERSE_LANDSCAPE) in calls


def test_clock_jump_after_sleep_does_not_burst_ticks():
    offset = [0.0]

    def clock() -> float:
        return time.monotonic() + offset[0]

    with Harness(monotonic=clock) as h:
        h.wait_status(Status.RUNNING)
        offset[0] += 100.0  # el reloj salta como tras una suspensión
        before = h.hub.polls
        time.sleep(0.1)
        # Con ticks de 0,02 s: unos 5 sondeos, no los 5000 que «faltan».
        assert h.hub.polls - before <= 10


def test_status_callback_errors_do_not_kill_the_loop():
    def bad(status, message):
        raise RuntimeError("bandeja rota")

    h = Harness()
    h.service._on_status = bad
    with h:
        h.wait_status(Status.RUNNING)


def test_invalid_brightness_is_rejected():
    with pytest.raises(ValueError):
        Settings(THEME, brightness=101)


def test_unexpected_error_sets_error_status_and_releases_everything():
    class Boom(FakeDisplay):
        def draw_image(self, image, x=0, y=0):
            raise ZeroDivisionError

    hub = FakeHub()
    statuses = []
    service = DisplayService(
        Settings(THEME),
        open_display=lambda port: Boom([]),
        make_hub=lambda: hub,
        on_status=lambda s, m: statuses.append(s),
        timings=FAST,
    )
    with pytest.raises(ZeroDivisionError):
        service.run()
    assert service.status == Status.ERROR
    assert statuses[-1] == Status.ERROR
    assert hub.closed


class PixelDisplay(FakeDisplay):
    """Además apunta el color de un píxel de fondo (esquina inferior derecha) de cada
    imagen completa: así se ve qué tema se ha pintado de verdad."""

    def __init__(self, log: list, fail_on_draw: int | None = None) -> None:
        super().__init__(log, fail_on_draw)
        self.full_pixels: list[tuple[int, int, int]] = []

    def draw_image(self, image, x: int = 0, y: int = 0) -> None:
        if (x, y) == (0, 0) and image.size == FULL_SIZE:
            self.full_pixels.append(image.convert("RGB").getpixel((479, 319)))
        super().draw_image(image, x, y)


def test_reapplying_the_same_folder_with_reload_repaints_the_new_theme(tmp_path):
    # Revisor del remate 2 (fase 5): el editor guarda en el tema que ya está en pantalla y
    # «Aplicar» no hacía nada, porque la carpeta era la misma.
    import json
    import shutil

    folder = tmp_path / "mio"
    shutil.copytree(THEME, folder)
    data = json.loads((folder / "theme.json").read_text(encoding="utf-8"))
    data["widgets"] = []  # solo fondo: el píxel de la esquina es el color del fondo
    data["background"] = {"color": "#000000"}
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")

    opener = Opener(make=lambda index, log, fail: PixelDisplay(log, fail))
    with Harness(Settings(folder), opener=opener) as h:
        h.wait_status(Status.RUNNING)
        display = opener.displays[0]
        assert display.full_pixels == [(0, 0, 0)]
        data["background"] = {"color": "#ff0000"}
        (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        h.service.apply(Settings(folder))  # sin recargar: nada cambia (solo ajustes)
        time.sleep(0.1)
        assert display.full_pixels == [(0, 0, 0)]
        h.service.apply(Settings(folder), reload=True)
        h.wait_for(lambda: len(display.full_pixels) == 2)
        assert display.full_pixels[-1] == (255, 0, 0)
