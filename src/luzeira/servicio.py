"""Servicio de la pantalla: tema + sensores + render + pantalla, con reconexión y reposo.

Sustituye al bucle provisional de ``app.py`` (fase 3). Cumple:

- RQ-07: apaga la pantalla (SCREEN_OFF) al suspender y al salir; al volver, repinta.
- RQ-08: si la pantalla se desenchufa o se cuelga, reintenta con espera creciente
  (1, 2, 4… hasta un tope) **sin bucles que gasten CPU**: toda espera es un
  ``Condition.wait`` con tiempo, que despierta al momento si llega una orden.
- RQ-09: ningún fallo silencioso. Cada problema cambia el estado y avisa por
  ``on_status`` con un mensaje para el usuario, en su idioma.

🔑 Un solo hilo toca la pantalla y los sensores: el que ejecuta :meth:`DisplayService.run`.
Los demás métodos (``stop``, ``suspend``, ``resume``, ``apply``, ``confirm_port``,
``choose_display``) solo dejan una orden y despiertan a ese hilo. Así nunca hay dos
hilos escribiendo en el puerto a la vez (p. ej. la callback de energía cerrándolo en
mitad de un ``draw_image``).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from PIL import Image

from luzeira.idioma import t
from luzeira.pantallas import (
    ChosenDisplayMissing,
    ConfirmationNeeded,
    DisplayBusy,
    DisplayChoice,
    DisplayError,
    ModelToConfirm,
    Orientation,
    TuringRevA,
    UnsupportedModel,
    WrongModel,
)
from luzeira.pantallas.apertura import open_auto, open_chosen
from luzeira.render import Renderer
from luzeira.sensores import default_hub
from luzeira.sensores.base import Reading
from luzeira.temas import ThemeError, load_theme, resolve_font

log = logging.getLogger(__name__)

ORIENTATIONS = {
    "portrait": Orientation.PORTRAIT,
    "reverse-portrait": Orientation.REVERSE_PORTRAIT,
    "landscape": Orientation.LANDSCAPE,
    "reverse-landscape": Orientation.REVERSE_LANDSCAPE,
}
# Girar 180°: el mismo tamaño, del revés (para pantallas montadas al contrario).
FLIPPED = {
    Orientation.PORTRAIT: Orientation.REVERSE_PORTRAIT,
    Orientation.REVERSE_PORTRAIT: Orientation.PORTRAIT,
    Orientation.LANDSCAPE: Orientation.REVERSE_LANDSCAPE,
    Orientation.REVERSE_LANDSCAPE: Orientation.LANDSCAPE,
}


class Status(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    NO_DISPLAY = "no_display"
    NEEDS_CONFIRMATION = "needs_confirmation"
    SUSPENDED = "suspended"
    THEME_ERROR = "theme_error"
    ERROR = "error"  # fallo inesperado (un bug): el servicio se ha parado
    STOPPED = "stopped"


@dataclass(frozen=True)
class Settings:
    theme_dir: Path
    brightness: int = 60
    flip: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.brightness <= 100:
            raise ValueError(f"brillo {self.brightness} fuera de rango (0-100)")


@dataclass(frozen=True)
class Timings:
    """Tiempos del servicio, en segundos. Los tests los acortan.

    ``backoff_max`` es 3 s y no 5: «vuelve en < 10 s» se mide desde que la pantalla
    vuelve a estar disponible, y el peor caso es tope + abrir (~0,2 s) + ``warmup`` (1 s)
    + imagen completa (1,34 s medidos). Con 5 s quedaban ~7,5 s; con 3 s, ~5,5 s, y un
    intento cada 3 s (una enumeración de puertos) no se nota en la CPU.
    """

    tick: float = 1.0
    warmup: float = 1.0  # espera antes de la primera imagen: velocidades con intervalo real
    backoff_start: float = 1.0
    backoff_max: float = 3.0


class Display(Protocol):
    """Lo que el servicio usa de una pantalla (``TuringRevA`` lo cumple)."""

    def screen_on(self) -> None: ...
    def screen_off(self) -> None: ...
    def set_brightness(self, percent: int) -> None: ...
    def set_orientation(self, orientation: Orientation) -> None: ...
    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None: ...
    def close(self) -> None: ...


class Hub(Protocol):
    def poll(self) -> None: ...
    def read(self, sensor_id: str) -> Reading: ...
    def close(self) -> None: ...


OpenDisplay = Callable[[str | None], Display]
OpenChoice = Callable[[DisplayChoice], Display]
StatusCallback = Callable[[Status, str], None]


@dataclass(frozen=True)
class DisplayInfo:
    """Qué pantalla está abierta ahora (para el menú «Pantalla» de la bandeja)."""

    model_key: str  # clave de ``pantallas.modelos`` ("" si el driver no la dice)
    device: str | None
    # La elección con la que se abrió (None: detección sola). Con ella sabe la bandeja si
    # lo que se ve es lo que el usuario eligió, aunque Windows le haya cambiado el COM.
    choice: DisplayChoice | None = None
    # False: la primera prueba no demuestra que ahí esté ese modelo (Kipye, o una rev A
    # muda fuera de su USB). La bandeja pregunta al usuario antes de recordarla.
    verified: bool = True


def open_once(port: str | None) -> Display:
    """Un solo intento de abrir la pantalla.

    ``TuringRevA.open`` reintenta 10 veces con ``time.sleep(1)``: ~10 s bloqueado sin
    poder atender a ``stop()`` ni a ``suspend()``. Aquí los reintentos los hace el
    servicio con esperas interrumpibles.

    ``port`` = el usuario ha confirmado que ahí hay una rev A. Sin él, detección
    (``pantallas.apertura.open_auto``): se decide con lo que Windows ya sabe del USB, sin
    enviar bytes, y solo se abre (y se le manda algo) a una pantalla que su número de serie
    identifica sin duda: la rev A, igual que siempre, o la rev B.
    """
    if port is not None:
        return TuringRevA.open(port, retries=1)
    return open_auto()


# --- mensajes para el usuario -----------------------------------------------------------
# Claves del catálogo (zona ``pantallas``), no textos: se traducen con ``t()`` al cambiar
# de estado, en el idioma de ese momento. Tras cambiar de idioma, la bandeja recarga el
# tema y el estado vuelve a escribirse en el nuevo.

MSG_STARTING = "pantallas.estado_arrancando"
MSG_CONNECTING = "pantallas.estado_conectando"
MSG_SUSPENDED = "pantallas.estado_en_reposo"
MSG_STOPPED = "pantallas.estado_parada"
MSG_RETRYING = "pantallas.estado_reintentando"
MSG_ERROR = "pantallas.estado_error"


def _with_retrying(text: str) -> str:
    return f"{text} {t(MSG_RETRYING)}"


def _no_display_message(exc: DisplayError) -> str:
    """El mensaje del driver ya es para el usuario (la causa técnica va en ``__cause__``).
    «Ocupada» (``DisplayBusy``) dice qué hacer; se añade que se sigue intentando."""
    text = str(exc)
    if isinstance(exc, UnsupportedModel):
        return text  # ya dice qué pasa: no se le manda nada, se sigue buscando otra
    if isinstance(exc, DisplayBusy) or not exc.says_retrying:
        text = _with_retrying(text)
    return text


def _theme_message(theme_dir: Path, exc: Exception) -> str:
    if isinstance(exc, ThemeError):
        return t("pantallas.estado_tema_con_error", theme=theme_dir.name, error=exc)
    # Un fallo que no es ThemeError es un bug nuestro: no se enseña su texto (puede
    # llevar rutas con el nombre de usuario), solo el tipo.
    return t("pantallas.estado_tema_error_interno", theme=theme_dir.name, kind=type(exc).__name__)


class DisplayService:
    """Mantiene la pantalla mostrando el tema. Se usa una vez: ``run()`` en un hilo
    aparte y, desde cualquier otro hilo, ``stop()``, ``suspend()``, ``resume()``,
    ``apply()`` y ``confirm_port()``."""

    def __init__(
        self,
        settings: Settings,
        *,
        open_display: OpenDisplay = open_once,
        choice: DisplayChoice | None = None,
        open_choice: OpenChoice = open_chosen,
        make_hub: Callable[[], Hub] = default_hub,
        monotonic: Callable[[], float] = time.monotonic,
        on_status: StatusCallback = lambda status, message: None,
        timings: Timings = Timings(),
    ) -> None:
        self._open_display = open_display
        self._open_choice = open_choice
        self._make_hub = make_hub
        self._monotonic = monotonic
        self._on_status = on_status
        self._t = timings

        # --- compartido entre hilos (siempre bajo _cond) ---
        self._cond = threading.Condition()
        self._version = 0  # sube con cada orden
        self._seen = 0  # última versión que el hilo del servicio ha recogido
        self._stop_requested = False
        self._want_suspend = False
        self._suspend_ack = -1  # versión con la que el hilo ya ha soltado el puerto
        self._pending: Settings | None = None
        self._pending_reload = False  # apply(reload=True): recargar aunque sea la misma carpeta
        self._kick = False  # «inténtalo ya» (resume / apply / confirm_port)
        self._resumed = False  # resume(): reconectar desde cero aunque el puerto siga abierto
        self._confirmed: set[str] = set()
        self._started = False
        self._running = False
        self._status = Status.STARTING
        self._message = t(MSG_STARTING)
        self._pending_device: str | None = None
        self._pending_choice: DisplayChoice | None = None  # propuesta que espera un «sí»
        self._new_choice: tuple[DisplayChoice | None] | None = None  # de choose_display()
        self._info: DisplayInfo | None = None

        # --- solo del hilo del servicio ---
        self._settings = settings
        self._choice = choice  # pantalla elegida por el usuario (None: detección sola)
        self._hub: Hub | None = None
        self._display: Display | None = None
        self._renderer: Renderer | None = None
        self._theme_name = ""
        self._loaded_dir: Path | None = None
        self._orientation = Orientation.PORTRAIT
        self._theme_error: str | None = None  # error del último tema que se intentó
        self._needs_full = False
        self._full_at = 0.0
        self._next_tick = 0.0
        self._retry_at = 0.0
        self._backoff = timings.backoff_start

    # --- API thread-safe -------------------------------------------------------------

    @property
    def status(self) -> Status:
        with self._cond:
            return self._status

    @property
    def message(self) -> str:
        with self._cond:
            return self._message

    @property
    def pending_device(self) -> str | None:
        """Puerto que espera que el usuario confirme el modelo (NEEDS_CONFIRMATION);
        None en cualquier otro estado. Se confirma con :meth:`confirm_port`."""
        with self._cond:
            return self._pending_device

    @property
    def pending_choice(self) -> DisplayChoice | None:
        """Modelo y puerto propuestos que esperan un «sí» del usuario (una pantalla beta
        reconocida solo por su USB). Se aceptan con :meth:`choose_display`."""
        with self._cond:
            return self._pending_choice

    @property
    def display_info(self) -> DisplayInfo | None:
        """La pantalla abierta ahora, o None."""
        with self._cond:
            return self._info

    def stop(self) -> None:
        """Pide parar. ``run()`` apaga la pantalla, suelta puerto y sensores y termina
        (< 2 s salvo que el USB esté colgado en mitad de una escritura: entonces hasta
        el ``WRITE_TIMEOUT`` del driver, 5 s)."""
        with self._cond:
            self._stop_requested = True
            self._bump()

    def suspend(self, timeout: float = 1.5) -> bool:
        """El equipo se va a dormir: SCREEN_OFF y soltar el puerto.

        Espera (hasta ``timeout``) a que el hilo del servicio lo haya hecho, porque la
        callback de energía tiene que terminar antes de que Windows suspenda. Windows da
        unos 2 s a PBT_APMSUSPEND: 1,5 s deja margen para el resto de la callback.
        Devuelve False si no le ha dado tiempo (p. ej. estaba enviando una imagen
        completa); la suspensión queda pedida y se hará en cuanto el hilo quede libre.
        """
        with self._cond:
            self._want_suspend = True
            request = self._bump()
            if not self._running:
                return True  # nada abierto que apagar
            done = self._cond.wait_for(
                lambda: self._suspend_ack >= request or not self._running, timeout
            )
        if not done:
            log.warning("La pantalla no se apagó a tiempo antes del reposo (%.1f s).", timeout)
        return done

    def resume(self) -> None:
        """El equipo ha vuelto: reabrir y repintar entero.

        Siempre se reconecta desde cero, aunque el hilo no llegara a procesar la
        suspensión (p. ej. ``suspend()`` agotó su tiempo en mitad de un envío): la
        pantalla pudo quedarse sin corriente y haber perdido imagen, brillo y giro.
        """
        with self._cond:
            self._want_suspend = False
            self._resumed = True
            self._kick = True
            self._bump()

    def apply(self, settings: Settings, *, reload: bool = False) -> None:
        """Cambia tema, brillo o giro en caliente. También vale como «reinténtalo ya».

        Si la carpeta del tema es la misma que ya está en pantalla, normalmente no se vuelve
        a leer (cambiar solo el brillo no debe repintar). ``reload=True`` obliga a leerla
        otra vez: el editor guarda cambios en el tema que ya se ve y al «Aplicar» tienen que
        aparecer (remate 2 de la fase 5). Si llegan varias órdenes antes de que el hilo las
        recoja, basta con que una pida recargar.
        """
        with self._cond:
            self._pending = settings
            self._pending_reload = self._pending_reload or reload
            self._kick = True
            self._bump()

    def confirm_port(self, device: str) -> None:
        """El usuario confirma que la pantalla de ``device`` es una revisión A."""
        with self._cond:
            self._confirmed.add(device)
            self._kick = True
            self._bump()

    def choose_display(self, choice: DisplayChoice | None) -> None:
        """Usar esta pantalla (modelo + puerto) en vez de la que haya; ``None``: volver a
        la detección sola. Se suelta la pantalla actual y se prueba la elegida con la
        primera prueba de su modelo (el saludo; la Kipye no tiene y su prueba es un brillo);
        si contesta otra cosa, el estado pasa a NEEDS_CONFIRMATION con el aviso y no se
        prueba ningún otro modelo."""
        with self._cond:
            self._new_choice = (choice,)  # en tupla: «elegir None» también es una orden
            self._kick = True
            self._bump()

    def _bump(self) -> int:
        """(Bajo _cond) registra una orden y despierta a quien espere."""
        self._version += 1
        self._cond.notify_all()
        return self._version

    # --- bucle ---------------------------------------------------------------------

    def run(self) -> None:
        """Bucle bloqueante. Termina tras ``stop()`` (o por un fallo inesperado, que
        deja el estado ERROR y se vuelve a lanzar)."""
        with self._cond:
            if self._started:
                raise RuntimeError("DisplayService.run() solo se puede llamar una vez")
            self._started = self._running = True
        failed = False
        try:
            self._hub = self._make_hub()
            self._load_theme(self._settings)
            self._loop()
        except BaseException as exc:
            failed = True
            # Aquí solo el tipo. La traza completa no se pierde: el ``raise`` de abajo la
            # lleva a ``threading.excepthook`` (o ``sys.excepthook`` si run() va en el
            # hilo principal), que ``sistema.registro`` manda al log pasando por su
            # filtro de privacidad (borra rutas de perfil, usuario y equipo).
            log.error("El servicio de la pantalla ha fallado (%s).", type(exc).__name__)
            self._set(Status.ERROR, t(MSG_ERROR, kind=type(exc).__name__))
            raise
        finally:
            self._disconnect(screen_off=True)
            if self._hub is not None:
                try:
                    self._hub.close()
                except Exception as exc:
                    log.warning("No se pudieron cerrar los sensores (%s).", type(exc).__name__)
            if not failed:
                self._set(Status.STOPPED, t(MSG_STOPPED))
            with self._cond:
                self._running = False
                self._cond.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cond:
                self._seen = self._version
                stop = self._stop_requested
                want_suspend = self._want_suspend
                pending, self._pending = self._pending, None
                reload, self._pending_reload = self._pending_reload, False
                kick, self._kick = self._kick, False
                resumed, self._resumed = self._resumed, False
                new_choice, self._new_choice = self._new_choice, None
            if stop:
                return
            if new_choice is not None:
                self._disconnect(screen_off=True)  # soltar la que hubiera antes de probar
                self._choice = new_choice[0]
            if resumed and not want_suspend:
                # Sin SCREEN_OFF: la pantalla puede no responder tras el reposo.
                self._disconnect(screen_off=False)
            if pending is not None:
                self._apply_now(pending, reload=reload)
            if want_suspend:
                self._disconnect(screen_off=True)
                with self._cond:
                    self._suspend_ack = self._seen
                    self._cond.notify_all()
                self._set(Status.SUSPENDED, t(MSG_SUSPENDED))
                self._wait(None)
                continue
            if kick:
                self._retry_at = 0.0
                self._backoff = self._t.backoff_start
            if self._renderer is None:
                error = self._theme_error or t("pantallas.estado_tema_no_vale")
                self._set(Status.THEME_ERROR, error)
                self._wait(None)  # hasta que apply() traiga un tema (o stop/suspend)
                continue
            if self._display is None:
                self._try_connect()
                continue
            self._step()

    def _wait(self, timeout: float | None) -> None:
        """Duerme hasta ``timeout`` o hasta que llegue una orden nueva. Nunca gira en
        vacío: si no hay orden pendiente, el hilo queda bloqueado en el sistema."""
        if timeout is not None:
            if timeout <= 0:
                return
            if math.isinf(timeout):
                timeout = None
        with self._cond:
            self._cond.wait_for(lambda: self._version != self._seen, timeout)

    def _try_connect(self) -> None:
        now = self._monotonic()
        if now < self._retry_at:
            self._wait(self._retry_at - now)
            return
        try:
            display, opened_with = self._open()
        except ModelToConfirm as exc:
            # Una beta reconocida solo por su USB (Kipye): no se le manda nada, pero se sigue
            # buscando (enumerar puertos no manda nada). Si mientras tanto aparece una rev A
            # con su serie, se abre sola; la propuesta sigue en pie con el mismo aviso.
            self._schedule_retry()
            self._set(Status.NEEDS_CONFIRMATION, str(exc), device=exc.device, choice=exc.choice)
            return
        except ConfirmationNeeded as exc:
            # Podría ser otro modelo: no se reintenta hasta que el usuario responda.
            self._retry_at = math.inf
            self._set(Status.NEEDS_CONFIRMATION, str(exc), device=exc.device, choice=exc.choice)
            return
        except WrongModel as exc:
            # La elegida ha contestado otra cosa que ese modelo y el driver ya cerró el
            # puerto. Ni se reintenta ni se prueba otro modelo. Con la próxima orden se
            # vuelve a la detección sola, que solo abre lo que su serie identifica sin duda.
            # (Que no conteste nada no llega aquí: es un DisplayError y se reintenta.)
            self._choice = None
            with self._cond:
                self._confirmed.discard(exc.device)  # un «sí» que resultó no ser: se olvida
            self._retry_at = math.inf
            self._set(Status.NEEDS_CONFIRMATION, str(exc))
            return
        except DisplayError as exc:
            self._schedule_retry()
            self._set(Status.NO_DISPLAY, _no_display_message(exc))
            return
        self._display = display
        with self._cond:
            self._info = DisplayInfo(
                getattr(display, "model_key", ""),
                getattr(display, "device", None),
                choice=opened_with,
                verified=bool(getattr(display, "verified", True)),
            )
        try:
            display.screen_on()
            display.set_brightness(self._settings.brightness)
            display.set_orientation(self._orientation)
        except DisplayError as exc:
            self._lost(exc)
            return
        self._hub_poll()
        self._needs_full = True
        self._full_at = self._monotonic() + self._t.warmup
        self._set(Status.STARTING, t(MSG_CONNECTING))

    def _open(self) -> tuple[Display, DisplayChoice | None]:
        """Abre una pantalla. Devuelve también la elección con la que se abrió (None si
        es la de la detección sola o una rev A confirmada)."""
        if self._choice is not None:
            try:
                return self._open_choice(self._choice), self._choice
            except ChosenDisplayMissing:
                pass  # la elegida no está (o su USB ya no coincide): detección sola
        try:
            return self._open_display(None), None
        except ConfirmationNeeded as exc:
            with self._cond:
                confirmed = exc.device in self._confirmed
            if not confirmed:
                raise
            if exc.choice is not None:  # una propuesta beta confirmada: con su driver
                return self._open_choice(exc.choice), exc.choice
            return self._open_display(exc.device), None

    def _schedule_retry(self) -> None:
        self._retry_at = self._monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, self._t.backoff_max)

    def _step(self) -> None:
        """Con la pantalla abierta: imagen completa pendiente o un tick normal."""
        display, renderer = self._display, self._renderer
        if display is None or renderer is None:
            return
        now = self._monotonic()
        target = self._full_at if self._needs_full else self._next_tick
        if now < target:
            self._wait(target - now)
            return
        try:
            self._hub_poll()
            if self._needs_full:
                display.draw_image(renderer.full_frame())
                self._needs_full = False
                # Solo una conexión que llega a mostrar imagen reinicia la espera: si el
                # puerto abre pero la pantalla falla siempre, la espera sigue creciendo.
                self._backoff = self._t.backoff_start
                self._next_tick = self._monotonic() + self._t.tick
                self._set(Status.RUNNING, self._running_message())
                return
            self._next_tick += self._t.tick
            if self._next_tick < now:
                # Nos hemos quedado atrás (PC suspendido o envío lento): no hacer una
                # ráfaga de ticks para «ponerse al día», seguir desde ahora.
                self._next_tick = now + self._t.tick
            for patch in renderer.changed():
                display.draw_image(patch.image, patch.x, patch.y)
        except DisplayError as exc:
            self._lost(exc)

    def _hub_poll(self) -> None:
        if self._hub is not None:
            self._hub.poll()

    def _lost(self, exc: DisplayError) -> None:
        """Se ha perdido la pantalla: cerrar sin intentar SCREEN_OFF (con el USB colgado,
        cada escritura esperaría el WRITE_TIMEOUT) y reintentar con espera creciente."""
        self._disconnect(screen_off=False)
        self._schedule_retry()
        self._set(Status.NO_DISPLAY, _with_retrying(str(exc)))

    def _disconnect(self, *, screen_off: bool) -> None:
        display, self._display = self._display, None
        self._needs_full = False
        with self._cond:
            self._info = None
        if display is None:
            return
        try:
            if screen_off:
                display.screen_off()
        except DisplayError:
            log.warning("No se pudo apagar la pantalla (¿USB desenchufado?).")
        finally:
            try:
                display.close()
            except Exception as exc:
                log.warning("No se pudo cerrar el puerto de la pantalla (%s).", type(exc).__name__)

    # --- tema y ajustes ------------------------------------------------------------

    def _load_theme(self, settings: Settings) -> bool:
        """Carga el tema de ``settings``. Si falla, se queda con el anterior (si lo hay)
        y guarda el mensaje en ``_theme_error``."""
        try:
            loaded = load_theme(settings.theme_dir)
            renderer = Renderer(
                loaded.theme,
                font_for=lambda style: resolve_font(style, loaded.files),
                read_sensor=self._read_sensor,
                files=loaded.files,
                clock=self._monotonic,
            )
        except Exception as exc:
            if not isinstance(exc, ThemeError):
                log.warning("Fallo inesperado al preparar un tema (%s).", type(exc).__name__)
            self._theme_error = _theme_message(settings.theme_dir, exc)
            return False
        self._renderer = renderer
        self._theme_name = loaded.theme.name
        self._loaded_dir = settings.theme_dir
        self._theme_error = None
        self._orientation = self._orientation_for(settings)
        return True

    def _read_sensor(self, sensor_id: str) -> Reading:
        if self._hub is None:  # no pasa: el hub se crea antes de cargar el tema
            return Reading(None, "", reason=t("sensores.sin_lectura"))
        return self._hub.read(sensor_id)

    def _orientation_for(self, settings: Settings) -> Orientation:
        if self._renderer is None:
            return self._orientation
        orientation = ORIENTATIONS[self._renderer.theme.orientation]
        return FLIPPED[orientation] if settings.flip else orientation

    def _running_message(self) -> str:
        if self._theme_error:
            return t(
                "pantallas.estado_mostrando_con_error",
                theme=self._theme_name,
                error=self._theme_error,
            )
        return t("pantallas.estado_mostrando", theme=self._theme_name)

    def _apply_now(self, new: Settings, *, reload: bool = False) -> None:
        old, self._settings = self._settings, new
        display = self._display
        if reload or new.theme_dir != self._loaded_dir or self._renderer is None:
            had_renderer = self._renderer is not None
            if self._load_theme(new):
                if display is not None:
                    self._safely(display.set_orientation, self._orientation)
                    self._safely(display.set_brightness, new.brightness)
                    self._schedule_full()
                return
            # El tema nuevo no vale: se queda el anterior con los demás ajustes nuevos.
            self._settings = replace(new, theme_dir=self._loaded_dir or new.theme_dir)
            if had_renderer and self.status == Status.RUNNING:
                self._set(Status.RUNNING, self._running_message())
            if not had_renderer:
                return
            new = self._settings
        if display is None:
            if self._renderer is not None:
                self._orientation = self._orientation_for(new)
            return
        orientation = self._orientation_for(new)
        if orientation != self._orientation:
            self._orientation = orientation
            self._safely(display.set_orientation, orientation)  # borra la copia: repintar
            self._schedule_full()
        if new.brightness != old.brightness:
            self._safely(display.set_brightness, new.brightness)

    def _schedule_full(self) -> None:
        if self._display is not None:
            self._needs_full = True
            self._full_at = self._monotonic()  # los sensores ya tienen intervalo

    def _safely(self, action: Callable[..., None], *args: object) -> None:
        """Una orden a la pantalla; si se ha perdido, pasa a reconectar."""
        if self._display is None:
            return
        try:
            action(*args)
        except DisplayError as exc:
            self._lost(exc)

    # --- estado --------------------------------------------------------------------

    def _set(
        self,
        status: Status,
        message: str,
        *,
        device: str | None = None,
        choice: DisplayChoice | None = None,
    ) -> None:
        """Cambia el estado y avisa solo si ha cambiado algo (nada de avisos en bucle).
        La callback se llama fuera del candado para que pueda llamar al servicio.
        ``device``: el puerto que espera confirmación (solo con NEEDS_CONFIRMATION);
        ``choice``: el modelo propuesto para él, si no es la rev A."""
        with self._cond:
            # Antes de avisar: quien reciba on_status puede leer pending_device ya.
            asking = status == Status.NEEDS_CONFIRMATION
            self._pending_device = device if asking else None
            self._pending_choice = choice if asking else None
            if (status, message) == (self._status, self._message):
                return
            self._status, self._message = status, message
            self._cond.notify_all()
        try:
            self._on_status(status, message)
        except Exception as exc:
            log.warning("La callback de estado falló (%s).", type(exc).__name__)
