"""Tests del servicio de la pantalla con dobles (sin puerto serie real ni sensores)."""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path

import pytest

import luzeira
from luzeira.pantallas import (
    ChosenDisplayMissing,
    ConfirmationNeeded,
    DisplayBusy,
    DisplayChoice,
    DisplayError,
    ModelToConfirm,
    NoAnswer,
    Orientation,
    UnsupportedModel,
    WrongModel,
)
from luzeira.pantallas.comun import open_error
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
    # El error tal como lo crean los drivers: su mensaje ya dice «lo sigo intentando»
    # (``says_retrying``), así que el servicio no lo repite. Antes se buscaba la palabra
    # «intentando» en el texto, lo que en otro idioma no funcionaría.
    from luzeira.pantallas.comun import open_error

    failed = open_error("COM3", OSError("no abre"))
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
    # Fuera de pintar: un fallo de draw_image ya no sube (se reintenta, ver
    # test_driver_failing_to_draw_does_not_kill_the_service).
    class Boom(FakeDisplay):
        def set_brightness(self, percent):
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


# --- elegir modelo y puerto (C-44) ----------------------------------------------------

REV_B_CHOICE = DisplayChoice("xuanfang-rev-b", "COM6", 0x1A86, 0x5722, "2017-2-25")


class ChoiceOpener:
    """``open_choice`` falso: ``plan`` como en ``Opener``; luego, pantallas falsas."""

    def __init__(self, log: list, plan=()) -> None:
        self.plan = list(plan)
        self.calls: list[DisplayChoice] = []
        self.log = log
        self.displays: list[FakeDisplay] = []

    def __call__(self, choice: DisplayChoice) -> FakeDisplay:
        self.calls.append(choice)
        if self.plan:
            outcome = self.plan.pop(0)
            if outcome is not None:
                raise outcome
        display = FakeDisplay(self.log)
        display.model_key, display.device = choice.model, choice.device
        self.displays.append(display)
        return display


def test_saved_choice_is_used_and_auto_detection_is_not():
    opener = Opener()
    chooser = ChoiceOpener(opener.log)
    with Harness(opener=opener, choice=REV_B_CHOICE, open_choice=chooser) as h:
        h.wait_status(Status.RUNNING)
        assert h.service.display_info.model_key == "xuanfang-rev-b"
        assert h.service.display_info.device == "COM6"
    assert chooser.calls == [REV_B_CHOICE] and opener.calls == []
    assert h.service.display_info is None  # al parar ya no hay nada abierto


def test_saved_choice_that_is_not_connected_falls_back_to_auto():
    opener = Opener()
    chooser = ChoiceOpener(opener.log, plan=[ChosenDisplayMissing()])
    with Harness(opener=opener, choice=REV_B_CHOICE, open_choice=chooser) as h:
        h.wait_status(Status.RUNNING)
    assert chooser.calls == [REV_B_CHOICE] and opener.calls == [None]


def test_wrong_model_stops_asks_and_never_tries_another_model():
    opener = Opener()
    wrong = WrongModel("COM6", 'XuanFang 3,5"')
    chooser = ChoiceOpener(opener.log, plan=[wrong])
    with Harness(opener=opener, choice=REV_B_CHOICE, open_choice=chooser) as h:
        h.wait_status(Status.NEEDS_CONFIRMATION)
        assert "no parece ser ese modelo" in h.service.message
        assert h.service.pending_device is None  # nada que confirmar: elegir otro
        time.sleep(0.2)
        assert chooser.calls == [REV_B_CHOICE] and opener.calls == []  # ni reintento ni otro
        # Con la siguiente orden se vuelve a la detección sola (la elegida se olvida).
        h.service.apply(Settings(THEME, brightness=30))
        h.wait_status(Status.RUNNING)
    assert chooser.calls == [REV_B_CHOICE] and opener.calls == [None]


def test_choose_display_releases_the_current_one_and_probes_the_new_one():
    opener = Opener()
    chooser = ChoiceOpener(opener.log)
    with Harness(opener=opener, open_choice=chooser) as h:
        h.wait_status(Status.RUNNING)
        h.service.choose_display(REV_B_CHOICE)
        h.wait_for(lambda: chooser.displays)
        h.wait_status(Status.RUNNING)
        first = opener.calls_of(opener.displays[0])
        assert first[-2:] == [("off",), ("close",)]  # la anterior, apagada y soltada
        assert h.service.display_info.model_key == "xuanfang-rev-b"
        # Volver a la detección sola.
        h.service.choose_display(None)
        h.wait_for(lambda: len(opener.displays) == 2)
        h.wait_status(Status.RUNNING)
    assert chooser.calls == [REV_B_CHOICE]


def test_proposed_beta_model_is_offered_and_confirming_uses_its_driver():
    kipye = DisplayChoice("kipye-rev-d", "COM8", 0x454D, 0x4E41)
    # Muchas: mientras se espera el «sí», el servicio sigue buscando (remate C-44).
    opener = Opener(plan=[ModelToConfirm(kipye, 'Kipye Qiye 3,5"')] * 10_000)
    chooser = ChoiceOpener(opener.log)
    with Harness(opener=opener, open_choice=chooser) as h:
        h.wait_status(Status.NEEDS_CONFIRMATION)
        assert h.service.pending_device == "COM8"
        assert h.service.pending_choice == kipye
        assert "beta" in h.service.message
        h.service.confirm_port("COM8")
        h.wait_status(Status.RUNNING)
        assert h.service.pending_choice is None
        assert h.service.display_info.choice == kipye
    assert chooser.calls == [kipye]


def test_plain_rev_a_confirmation_has_no_proposed_model():
    opener = Opener(plan=[ConfirmationNeeded("COM7")])
    with Harness(opener=opener) as h:
        h.wait_status(Status.NEEDS_CONFIRMATION)
        assert h.service.pending_choice is None


# --- remate C-44 -----------------------------------------------------------------------


def test_unconfirmed_kipye_does_not_stop_the_search_for_a_rev_a():
    # Una Kipye propuesta (sin «sí» todavía) no deja de buscar: si luego aparece una rev A
    # con su serie, la detección sola la abre (aquí: el plan se acaba y da pantalla).
    kipye = DisplayChoice("kipye-rev-d", "COM8", 0x454D, 0x4E41)
    opener = Opener(plan=[ModelToConfirm(kipye, 'Kipye Qiye 3,5"')] * 3)
    with Harness(opener=opener) as h:
        h.wait_status(Status.NEEDS_CONFIRMATION)
        h.wait_status(Status.RUNNING)
        assert h.service.pending_choice is None
        assert h.service.display_info.choice is None  # la de la detección sola
    assert opener.calls == [None] * 4
    notified = [m for s, m in h.statuses if s == Status.NEEDS_CONFIRMATION]
    assert len(notified) == 1  # la propuesta se avisa una vez, no en cada búsqueda


def test_unsupported_model_message_does_not_promise_retries_but_keeps_searching():
    unsupported = UnsupportedModel("COM11", 'Turing de 2,1", 5" u 8,8" (revisión C)')
    opener = Opener(plan=[unsupported] * 3)
    with Harness(opener=opener) as h:
        h.wait_status(Status.NO_DISPLAY)
        assert h.service.message == str(unsupported)
        assert "intentando" not in h.service.message
        h.wait_status(Status.RUNNING)  # sigue buscando: aparece otra y se usa
    assert len(opener.calls) == 4


def test_chosen_display_that_does_not_answer_yet_is_retried_and_kept():
    # Remate C-44: no contestar nada no es «otro modelo»: se reintenta la misma elección.
    opener = Opener()
    chooser = ChoiceOpener(opener.log, plan=[NoAnswer("COM6"), NoAnswer("COM6")])
    with Harness(opener=opener, choice=REV_B_CHOICE, open_choice=chooser) as h:
        h.wait_status(Status.NO_DISPLAY)
        assert h.service.message == "La pantalla de COM6 no contesta todavía. Lo sigo intentando."
        h.wait_status(Status.RUNNING)
        assert h.service.display_info.choice == REV_B_CHOICE
    assert chooser.calls == [REV_B_CHOICE] * 3 and opener.calls == []


def test_display_info_says_whether_the_first_probe_proved_the_model():
    opener = Opener()

    class Unverified(ChoiceOpener):
        def __call__(self, choice):
            display = super().__call__(choice)
            display.verified = False  # p. ej. la Kipye
            return display

    chooser = Unverified(opener.log)
    with Harness(opener=opener, choice=REV_B_CHOICE, open_choice=chooser) as h:
        h.wait_status(Status.RUNNING)
        assert h.service.display_info.verified is False
        h.service.choose_display(None)
        h.wait_for(lambda: opener.displays)
        h.wait_status(Status.RUNNING)
        info = h.service.display_info
        assert info.verified is True and info.choice is None


# --- C-62: cambiar de idioma con un problema a la vista -------------------------------------


class FreshErrors:
    """``open_display`` falso que crea el error en cada intento, como los drivers de verdad
    (así sale en el idioma de ese momento)."""

    def __init__(self, make) -> None:
        self.make = make
        self.calls: list[str | None] = []

    def __call__(self, port):
        self.calls.append(port)
        raise self.make()


@pytest.mark.parametrize(
    ("make", "status"),
    [
        (lambda: ConfirmationNeeded("COM7"), Status.NEEDS_CONFIRMATION),
        (lambda: open_error("COM3", OSError("x")), Status.NO_DISPLAY),
    ],
)
def test_changing_language_rewrites_the_message_but_does_not_notify_again(make, status):
    from luzeira import idioma

    with Harness(opener=FreshErrors(make)) as h:
        h.wait_status(status)
        spanish = h.service.message
        idioma.set_language("en")
        # Sin orden nueva: el mensaje guardado ya se lee en inglés.
        english = h.service.message
        assert english != spanish and "display" in english
        h.service.apply(Settings(THEME), reload=True)  # lo que hace la bandeja
        h.wait_for(lambda: len(h.opener.calls) >= 2)  # ha vuelto a intentarlo
        time.sleep(0.05)
        assert h.service.message == english
    # El mismo aviso en otro idioma no es un aviso nuevo (no se repite en Windows).
    assert [s for s, _ in h.statuses].count(status) == 1


def test_a_different_problem_is_still_notified_after_changing_language():
    from luzeira import idioma

    # Mientras no se cambia de idioma, la pantalla está ocupada; después, pide confirmar.
    changed = threading.Event()

    def make():
        return ConfirmationNeeded("COM7") if changed.is_set() else open_error("COM3", OSError())

    with Harness(opener=FreshErrors(make)) as h:
        h.wait_status(Status.NO_DISPLAY)
        idioma.set_language("en")
        changed.set()
        h.service.apply(Settings(THEME), reload=True)
        h.wait_status(Status.NEEDS_CONFIRMATION)
    assert [s for s, _ in h.statuses][:2] == [Status.NO_DISPLAY, Status.NEEDS_CONFIRMATION]


# --- R5-09 / R5-03: no abrir sin fin una pantalla que falla -----------------------------

REPLUG = "Desenchúfala y vuelve a enchufarla"


class Ports:
    """``list_ports`` falso: lo que Windows diría que hay conectado ahora."""

    def __init__(self, *devices: str) -> None:
        self.devices = list(devices)
        self.calls = 0

    def __call__(self):
        from luzeira.pantallas.modelos import PortInfo

        self.calls += 1
        return [PortInfo(d, 0x1A86, 0x5722, "") for d in self.devices]


def test_many_failures_in_a_row_ask_to_replug_and_wait_longer():
    # R5-09: un puerto que abre pero falla siempre ya no se reabre cada pocos segundos sin
    # fin (cada apertura reinicia la rev A y le manda el saludo).
    failing = open_error("COM3", OSError("no abre"))
    opener = Opener(plan=[failing] * 1000)
    timings = Timings(
        tick=0.02,
        warmup=0.02,
        backoff_start=0.01,
        backoff_max=0.02,
        failures_before_long=4,
        backoff_long=0.4,
    )
    ports = Ports("COM3")
    with Harness(opener=opener, timings=timings, list_ports=ports) as h:
        h.wait_for(lambda: REPLUG in h.service.message)
        assert h.service.status == Status.NO_DISPLAY
        assert len(opener.calls) == 4
        h.wait_for(lambda: len(opener.calls) == 5, timeout=2)
    gaps = [b - a for a, b in itertools.pairwise(opener.times)]
    assert max(gaps[:3]) < 0.15, gaps  # las primeras, deprisa
    assert gaps[3] >= 0.35, gaps  # tras la cuarta, la espera larga
    assert ports.calls >= 2  # mientras, solo se mira la lista de puertos


def test_replugging_during_the_long_wait_retries_at_once():
    failing = open_error("COM3", OSError("no abre"))
    opener = Opener(plan=[failing] * 3)  # luego, la pantalla funciona
    timings = Timings(
        tick=0.02,
        warmup=0.02,
        backoff_start=0.01,
        backoff_max=0.02,
        failures_before_long=3,
        backoff_long=60.0,
    )
    ports = Ports("COM3")
    with Harness(opener=opener, timings=timings, list_ports=ports) as h:
        h.wait_for(lambda: REPLUG in h.service.message)
        time.sleep(0.1)
        assert len(opener.calls) == 3  # esperando: no la abre
        ports.devices = []  # la desenchufan…
        h.wait_for(lambda: len(opener.calls) == 4)  # …y se intenta al momento
        h.wait_status(Status.RUNNING)


def test_not_found_is_not_counted_as_a_failure():
    # «No la encuentro» no le manda nada: se sigue mirando cada pocos segundos (RQ-08: al
    # enchufarla tiene que volver en < 10 s).
    opener = Opener(plan=[DisplayError("No encuentro la pantalla.")] * 30)
    timings = Timings(tick=0.02, warmup=0.02, backoff_start=0.01, backoff_max=0.01)
    timings = Timings(**{**timings.__dict__, "failures_before_long": 3})
    with Harness(opener=opener, timings=timings, list_ports=Ports()) as h:
        h.wait_status(Status.RUNNING, timeout=3)
    assert len(opener.calls) == 31
    assert not any(REPLUG in str(m) for _s, m in h.statuses)


def test_cut_image_on_a_beta_display_waits_for_a_replug():
    # R5-03: una rev B o Kipye con una imagen a medias puede estar esperando el resto; el
    # saludo de reabrir se tomaría por píxeles. No se reabre hasta que cambien los puertos.
    from luzeira.pantallas.comun import ImageCut

    class CutScreen(FakeDisplay):
        def draw_image(self, image, x=0, y=0):
            raise ImageCut()

    opener = Opener(make=lambda i, log, fail: CutScreen(log) if i == 0 else FakeDisplay(log))
    timings = Timings(tick=0.02, warmup=0.02, backoff_start=0.01, backoff_max=0.02)
    ports = Ports("COM6")
    with Harness(opener=opener, timings=timings, list_ports=ports) as h:
        h.wait_for(lambda: REPLUG in h.service.message)
        assert "sigo intentando" not in h.service.message
        time.sleep(0.15)
        assert len(opener.calls) == 1  # ni un saludo a ciegas
        ports.devices = []
        h.wait_status(Status.RUNNING)
    assert len(opener.calls) == 2


def test_user_action_retries_at_once_even_while_waiting_for_a_replug():
    failing = open_error("COM3", OSError("no abre"))
    opener = Opener(plan=[failing] * 2)
    timings = Timings(
        tick=0.02,
        warmup=0.02,
        backoff_start=0.01,
        backoff_max=0.02,
        failures_before_long=2,
        backoff_long=60.0,
    )
    with Harness(opener=opener, timings=timings, list_ports=Ports("COM3")) as h:
        h.wait_for(lambda: REPLUG in h.service.message)
        h.service.apply(Settings(THEME))  # p. ej. «Reintentar» o cambiar algo en el menú
        h.wait_status(Status.RUNNING)


# --- R2-02: un fallo del render no tumba el servicio ----------------------------------


class Bomb(Exception):
    """Lo que lanzaría Pillow con una imagen o fuente trampa (texto con una ruta)."""


@pytest.fixture
def flaky_renderer(monkeypatch):
    from luzeira import servicio
    from luzeira.render import Renderer

    fails = {"full_frame": 0, "changed": 0}

    class Flaky(Renderer):
        def full_frame(self):
            if fails["full_frame"]:
                fails["full_frame"] -= 1
                raise Bomb("C:/Users/alguien/tema/fuente.ttf")
            return super().full_frame()

        def changed(self):
            if fails["changed"]:
                fails["changed"] -= 1
                raise Bomb("C:/Users/alguien/tema/fuente.ttf")
            return super().changed()

    monkeypatch.setattr(servicio, "Renderer", Flaky)
    return fails


def test_render_failure_in_full_frame_keeps_the_service_alive(flaky_renderer, caplog):
    flaky_renderer["full_frame"] = 3
    with Harness() as h:
        h.wait_status(Status.THEME_ERROR)
        assert "Bomb" in h.service.message and "alguien" not in h.service.message
        h.wait_status(Status.RUNNING)
        assert h.thread.is_alive()
        display = h.opener.displays[0]
        assert ("close",) not in h.opener.calls_of(display)  # la pantalla sigue abierta
    assert len(h.opener.displays) == 1
    assert "alguien" not in caplog.text and "Bomb" in caplog.text
    assert caplog.text.count("Fallo al preparar la imagen") == 1  # no uno por intento


def test_render_failure_in_changed_repaints_whole_frame_later(flaky_renderer):
    offset = [0.0]
    with Harness(monotonic=lambda: time.monotonic() + offset[0]) as h:
        h.wait_status(Status.RUNNING)
        display = h.opener.displays[0]
        flaky_renderer["changed"] = 1
        offset[0] += 10.0  # que los widgets caduquen y se pida ``changed`` ya
        h.wait_status(Status.THEME_ERROR)
        fulls = h.opener.calls_of(display).count(("full",))
        h.wait_status(Status.RUNNING)
        h.wait_for(lambda: h.opener.calls_of(display).count(("full",)) == fulls + 1)
        assert h.thread.is_alive()


def test_render_retries_back_off_instead_of_spinning(flaky_renderer):
    flaky_renderer["full_frame"] = 1000
    timings = Timings(tick=0.01, warmup=0.0, backoff_start=0.02, backoff_max=0.05)
    timings = Timings(**{**timings.__dict__, "render_retry_max": 0.08})
    with Harness(timings=timings) as h:
        h.wait_status(Status.THEME_ERROR)
        time.sleep(0.5)
        left = flaky_renderer["full_frame"]
    # 0,01 + 0,02 + 0,04 + 0,08 + 0,08…: unos 8 intentos en 0,5 s, no miles.
    assert 1000 - left <= 12, 1000 - left


def test_confirmed_rev_a_from_the_real_open_path_checks_its_hello(monkeypatch):
    # R5-06: «Sí, es una rev A» abre con ``open_once(puerto)``, que también tiene que
    # rechazar un eco. Camino completo, con pyserial y la lista de puertos falsos.
    from types import SimpleNamespace

    import serial
    from serial.tools import list_ports

    from luzeira import servicio

    class Echo:
        def __init__(self, *_a, **_k) -> None:
            self.writes: list[bytes] = []
            self.closed = False

        def write(self, data):
            self.writes.append(bytes(data))
            return len(data)

        def read(self, size=1):
            return b"\x45" * size

        def reset_input_buffer(self):
            pass

        def close(self):
            self.closed = True

    opened: list[Echo] = []
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: opened.append(Echo()) or opened[-1])
    port = SimpleNamespace(device="COM9", vid=0x1A86, pid=0x5722, serial_number=None)
    monkeypatch.setattr(list_ports, "comports", lambda: [port])
    with pytest.raises(WrongModel):
        servicio.open_once("COM9")
    assert len(opened) == 1 and opened[0].closed


def test_sensor_failure_right_after_connecting_does_not_kill_the_service():
    class FirstPollFails(FakeHub):
        def poll(self) -> None:
            super().poll()
            if self.polls == 1:
                raise KeyError("sensor")

    h = Harness()
    h.hub = FirstPollFails()
    with h:
        h.wait_status(Status.RUNNING)
        assert h.thread.is_alive()


def test_display_that_opens_but_always_fails_ends_up_asking_to_replug():
    # Remate R5-09 (m24): el puerto abre bien pero la pantalla falla al encender, una y
    # otra vez. Sin contar estos fallos se reabriría (y reiniciaría la rev A) cada 3 s.
    timings = Timings(
        tick=0.02,
        warmup=0.02,
        backoff_start=0.01,
        backoff_max=0.02,
        failures_before_long=4,
        backoff_long=60.0,
    )
    opener = Opener(make=lambda index, log, fail: DeadScreen(log))
    with Harness(opener=opener, timings=timings, list_ports=Ports("COM3")) as h:
        h.wait_for(lambda: REPLUG in h.service.message)
        time.sleep(0.1)
        assert len(opener.displays) == 4  # y ahí se queda esperando


def test_reconnections_that_reach_an_image_do_not_ask_to_replug():
    # Remate R5-09 (m34): perder la pantalla y recuperarla con imagen 10 veces seguidas no
    # es «falla siempre»: cada imagen completa pone la cuenta a cero.
    timings = Timings(
        tick=1.0,
        warmup=0.0,
        backoff_start=0.5,
        backoff_max=0.5,
        failures_before_long=4,
        backoff_long=1e9,
    )
    opener = Opener(fail_on_draw={i: 2 for i in range(10)})  # imagen entera y luego se pierde
    # Reloj que salta 2 s en cada mirada: los widgets caducan ya y cada tick trae un
    # parche (el que falla), sin esperas reales.
    clock = itertools.count(0.0, 2.0).__next__
    with Harness(opener=opener, timings=timings, list_ports=Ports("COM3"), monotonic=clock) as h:
        h.wait_for(lambda: len(opener.displays) == 11, timeout=10)
        h.wait_status(Status.RUNNING)
    assert all(d.failed_at is not None for d in opener.displays[:10])
    assert not any(REPLUG in str(m) for _s, m in h.statuses)


# --- pantallas de otro tamaño (lienzo) ------------------------------------------------


class SizedDisplay(FakeDisplay):
    """Pantalla de 5" (480x800 en vertical) que dice su tamaño, como los drivers reales."""

    native = (480, 800)

    def __init__(self, log: list, fail_on_draw: int | None = None) -> None:
        super().__init__(log, fail_on_draw)
        self.orientation = Orientation.PORTRAIT
        self.sizes: list[tuple[int, int, tuple[int, int]]] = []

    @property
    def width(self) -> int:
        return self.native[1] if self.orientation.is_landscape else self.native[0]

    @property
    def height(self) -> int:
        return self.native[0] if self.orientation.is_landscape else self.native[1]

    def set_orientation(self, orientation: Orientation) -> None:
        self.orientation = orientation
        super().set_orientation(orientation)

    def draw_image(self, image, x: int = 0, y: int = 0) -> None:
        self.sizes.append((x, y, image.size))
        super().draw_image(image, x, y)


def test_full_frame_is_drawn_at_the_display_size_and_follows_the_flip():
    # El tema Esencial es de 480x320: en la 5" girada (800x480) se pinta escalado a su
    # tamaño, nunca en la esquina. Al girarlo 180° sigue siendo de 800x480.
    opener = Opener(make=lambda index, log, fail: SizedDisplay(log, fail))
    with Harness(opener=opener) as h:
        h.wait_for(lambda: opener.displays and opener.displays[0].sizes)
        display = opener.displays[0]
        assert display.sizes[0] == (0, 0, (800, 480))
        h.service.apply(Settings(THEME, flip=True))
        h.wait_for(
            lambda: ("orientation", Orientation.REVERSE_LANDSCAPE) in opener.calls_of(display)
        )
        h.wait_for(lambda: display.sizes.count((0, 0, (800, 480))) >= 2)
        # Ningún parche se sale de la pantalla.
        for x, y, (w, hh) in display.sizes:
            assert x + w <= 800 and y + hh <= 480


def test_display_without_size_gets_the_theme_size():
    # Un driver que no dice su tamaño (o dice algo raro): al tamaño del tema, como antes.
    from luzeira.servicio import display_size

    class Odd:
        width, height = 480.0, 800

    class Huge:
        width, height = 480, 4000

    assert display_size(FakeDisplay([])) is None
    assert display_size(Odd()) is None
    assert display_size(Huge()) is None
    assert display_size(SizedDisplay([])) == (480, 800)


def test_stop_while_a_slow_display_is_opening_ends_quickly(monkeypatch):
    # Despertar una rev C son hasta ~20 s de esperas dentro de ``open_chosen``. Con el
    # ``open_choice`` de verdad, esas esperas atienden a stop(): termina en < 2 s y el
    # driver ha cerrado lo que abrió (``comun.open_probed``).
    from luzeira import servicio

    started = threading.Event()
    waited: list[float] = []

    def slow_open_chosen(choice, *, sleep):
        started.set()
        for _ in range(30):  # 30 s si nadie la corta
            waited.append(1.0)
            sleep(1.0)
        raise AssertionError("la espera no se ha cortado")

    monkeypatch.setattr(servicio, "open_chosen", slow_open_chosen)
    h = Harness(choice=REV_B_CHOICE)
    h.thread.start()
    assert started.wait(1)
    start = time.monotonic()
    h.service.stop()
    h.thread.join(2)
    assert not h.thread.is_alive()
    assert time.monotonic() - start < 2
    assert len(waited) == 1 and h.hub.closed


def test_new_theme_on_an_open_display_is_drawn_at_the_display_size():
    # Cambiar de tema con la 5" abierta: el motor nuevo también va al tamaño de la
    # pantalla (no al del tema), desde su primera imagen.
    opener = Opener(make=lambda index, log, fail: SizedDisplay(log, fail))
    with Harness(opener=opener) as h:
        h.wait_for(lambda: opener.displays and opener.displays[0].sizes)
        display = opener.displays[0]
        before = len(display.sizes)
        h.service.apply(Settings(THEME.parent / "ambar"))
        h.wait_for(lambda: len(display.sizes) > before and h.service.status == Status.RUNNING)
        h.wait_for(lambda: "mbar" in h.service.message)
        fulls = [s for x, y, s in display.sizes if (x, y) == (0, 0)]
        assert fulls and set(fulls) == {(800, 480)}


@pytest.mark.parametrize(
    "error",
    [ValueError("(900, 0) está fuera de la pantalla 800x480"), RuntimeError("bug del driver")],
    ids=["fuera", "bug"],
)
def test_driver_failing_to_draw_does_not_kill_the_service(error):
    # Un parche fuera de la pantalla (ValueError) o un bug del driver: se trata como un
    # fallo del render (aviso y reintento), no tumba el hilo.
    class Picky(SizedDisplay):
        def draw_image(self, image, x: int = 0, y: int = 0) -> None:
            if self.draws == 0:
                self.draws += 1
                raise error
            super().draw_image(image, x, y)

    opener = Opener(make=lambda index, log, fail: Picky(log, fail))
    with Harness(opener=opener) as h:
        h.wait_status(Status.THEME_ERROR)
        h.wait_status(Status.RUNNING, timeout=3)
        assert h.thread.is_alive()
        assert opener.displays[0].sizes[0][2] == (800, 480)
