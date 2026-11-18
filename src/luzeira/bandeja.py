"""Icono en la bandeja de Windows: la cara de Luzeira mientras no exista el editor.

Hilos (y quién toca qué):
- Hilo principal: el icono (pystray), con su bucle de mensajes de Windows. **Es el único
  que toca el icono y su menú.** Los demás le piden cosas con ``_post``, que deja la
  función en una cola y le manda un mensaje a su ventana (revisor de la fase 4: el menú
  se reconstruía desde dos hilos a la vez).
- Hilo «pantalla»: ``DisplayService.run()`` (sensores + render + puerto serie).
- Avisos de energía y de apagado: llegan en hilos de Windows y solo llaman a métodos
  thread-safe del servicio.

Ningún fallo es silencioso (RQ-09): cada estado de problema muestra un aviso de Windows
en español, el estado se ve siempre en el menú, y si una acción del menú falla, se avisa.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pystray
from PIL import Image

from luzeira import __version__
from luzeira.config import (
    DEFAULT_THEME,
    AppConfig,
    clean_import_leftovers,
    config_dir,
    list_themes,
    load_config,
    migrate_old_name,
    save_config,
    theme_path,
    user_themes_dir,
)
from luzeira.editor.conexion import EditorLink
from luzeira.editor.ventana import EXIT_NO_WEBVIEW2
from luzeira.marca import STAR, draw_mark
from luzeira.servicio import DisplayService, Settings, Status
from luzeira.sistema import autoarranque
from luzeira.sistema.energia import PowerListener
from luzeira.sistema.instancia import APP_MUTEX, SingleInstance
from luzeira.sistema.registro import default_log_dir, setup_logging
from luzeira.sistema.sesion import SessionWatcher
from luzeira.temas import ThemeError, load_theme

log = logging.getLogger(__name__)

BRIGHTNESS_LEVELS = (25, 50, 75, 100)
_FONTS = Path(__file__).parent / "recursos" / "fuentes"
# Estados que merecen un aviso de Windows (el resto solo se ve en el menú).
_NOTIFY = {Status.NO_DISPLAY, Status.NEEDS_CONFIRMATION, Status.THEME_ERROR, Status.ERROR}
_COLORS = {
    Status.STARTING: STAR,
    Status.RUNNING: STAR,
    Status.SUSPENDED: (120, 120, 120),
    Status.STOPPED: (120, 120, 120),
}
_RED = (200, 50, 50)

WM_QUERYENDSESSION = 0x0011
WM_APP_RUN = 0x8000 + 0x56  # WM_APP + algo nuestro: «ejecuta lo que hay en la cola»

# Límites de Windows para los avisos del área de notificación (NOTIFYICONDATAW):
# szInfo = 256 y szInfoTitle = 64 caracteres, contando el terminador. pystray lanza
# ValueError si se pasan y el aviso no sale (revisor de la fase 4, medido).
_MAX_INFO = 255
_MAX_INFO_TITLE = 63
_MAX_TIP = 127

MSG_NO_WEBVIEW2 = (
    "Para el editor hace falta Microsoft Edge WebView2 Runtime (gratuito de Microsoft). "
    "Instálalo y vuelve a abrir el editor."
)


def clip(text: str, limit: int) -> str:
    """Recorta a ``limit`` caracteres con «…» si hace falta."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def make_icon(color: tuple[int, int, int] = STAR) -> Image.Image:
    """Icono de 64x64: el lucero de Luzeira con el color del estado."""
    return draw_mark(64, color)


def allow_shutdown(icon: pystray.Icon) -> None:
    """Que la ventana oculta de pystray no bloquee el apagado de Windows.

    pystray 0.19.5 devuelve 0 a los mensajes que no maneja, y 0 a WM_QUERYENDSESSION
    significa «no se puede cerrar»: Windows mataría Luzeira antes de que nuestra ventana
    (``SessionWatcher``) apague la pantalla (fase 4, agente de sistema). Se responde 1.
    ⚠ ``_message_handlers`` es API privada de pystray: versión fijada en pyproject.toml y
    un test que falla si desaparece.
    """
    handlers = getattr(icon, "_message_handlers", None)
    if isinstance(handlers, dict):
        handlers[WM_QUERYENDSESSION] = lambda _wparam, _lparam: 1
    else:
        log.warning("pystray ha cambiado: no puedo asegurar que no bloquee el apagado.")


def is_theme_valid(name: str) -> bool:
    try:
        load_theme(theme_path(name))
    except ThemeError:
        return False
    return True


class TrayApp:
    def __init__(self, config: AppConfig, *, open_editor: bool = False) -> None:
        # open_editor: abrir también el editor al arrancar (la primera vez, desde el
        # instalador: así el usuario ve qué puede hacer sin buscar el icono del reloj).
        self._open_editor_on_start = open_editor
        self._pending_calls: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self._last_notified: tuple[Status, str] | None = None
        self._importing = False
        self._startup_notice: str | None = None
        if not is_theme_valid(config.theme):
            # Un tema que dejó de funcionar no puede dejar la pantalla apagada para siempre.
            self._startup_notice = (
                f"El tema «{config.theme}» no se puede cargar. Uso «{DEFAULT_THEME}»."
            )
            config = config.model_copy(update={"theme": DEFAULT_THEME})
        self.config = config
        self.service = self._new_service()
        self._worker: threading.Thread | None = None
        # Tema que el editor acaba de pedir aplicar y la bandeja aún no ha procesado: el
        # servidor lo da ya por «en pantalla» para que no se pueda borrar en ese hueco.
        self._editor_lock = threading.Lock()
        self._editor_request: tuple[int, str] | None = None
        self._editor_seq = 0
        self.editor = EditorLink(
            apply_theme=self._apply_from_editor,
            status=lambda: (self.service.status.value, self.service.message),
            active_theme=self._active_theme,
            on_window_exit=self._on_editor_exit,
        )
        self.icon = pystray.Icon(
            "luzeira", make_icon(), f"Luzeira {__version__}", menu=self._menu()
        )
        allow_shutdown(self.icon)
        handlers = getattr(self.icon, "_message_handlers", None)
        if isinstance(handlers, dict):
            handlers[WM_APP_RUN] = lambda _w, _l: self._drain()

    # --- hilo del icono ------------------------------------------------------------

    def _post(self, fn: Callable[[], None]) -> None:
        """Ejecuta ``fn`` en el hilo del icono. Seguro desde cualquier hilo."""
        self._pending_calls.put(fn)
        hwnd = getattr(self.icon, "_hwnd", None)
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_APP_RUN, 0, 0)
        # Sin ventana aún: se ejecutará al arrancar el icono (``_setup`` vacía la cola).

    def _drain(self) -> int:
        while True:
            try:
                fn = self._pending_calls.get_nowait()
            except queue.Empty:
                return 0
            try:
                fn()
            except Exception:
                log.exception("Fallo en una tarea de la bandeja")

    def _notify(self, text: str, title: str = "Luzeira") -> None:
        try:
            self.icon.notify(clip(text, _MAX_INFO), clip(title, _MAX_INFO_TITLE))
        except Exception:
            log.exception("No se pudo mostrar un aviso")

    # --- servicio --------------------------------------------------------------------

    def _settings(self) -> Settings:
        return Settings(
            theme_dir=theme_path(self.config.theme),
            brightness=self.config.brightness,
            flip=self.config.flip,
        )

    def _new_service(self) -> DisplayService:
        return DisplayService(self._settings(), on_status=self._on_status)

    def _start_service(self) -> None:
        self._worker = threading.Thread(target=self._run_service, name="pantalla", daemon=True)
        self._worker.start()

    def _run_service(self) -> None:
        try:
            self.service.run()
        except Exception:
            # El servicio ya ha pasado a ERROR y lo ha avisado; aquí solo se registra.
            log.exception("El servicio de la pantalla se ha detenido por un error")

    def _retry(self) -> None:
        """Tras un error inesperado: un servicio nuevo desde cero."""
        self.service.stop()
        if self._worker is not None:
            self._worker.join(timeout=6)
        self.service = self._new_service()
        self._start_service()

    def _on_status(self, status: Status, message: str) -> None:
        """Llamado desde el hilo de la pantalla: se pasa al hilo del icono."""
        log.info("Estado: %s", status.value)
        self._post(lambda: self._show_status(status, message))

    def _show_status(self, status: Status, message: str) -> None:
        self.icon.icon = make_icon(_COLORS.get(status, _RED))
        self.icon.title = clip(f"Luzeira — {message}", _MAX_TIP)
        self.icon.update_menu()
        if status in _NOTIFY and (status, message) != self._last_notified:
            self._notify(message)
        self._last_notified = (status, message)

    # --- acciones del menú (todas en el hilo del icono) -----------------------------

    def _safe(self, action: Callable[[], None]) -> Callable[[], None]:
        """Ninguna acción del menú falla en silencio: se registra y se avisa."""

        def run() -> None:
            try:
                action()
            except Exception:
                log.exception("Falló una acción del menú")
                self._notify("Algo ha fallado. Los detalles están en el registro de Luzeira.")
            self.icon.update_menu()

        return run

    def _change(self, *, reload: bool = False, **changes: object) -> None:
        """Cambia la configuración y la aplica. ``reload``: volver a leer el tema aunque
        sea el que ya está en pantalla (cuando lo pide el editor tras guardar cambios)."""
        new = self.config.model_copy(update=changes)
        if "theme" in changes and not is_theme_valid(new.theme):
            self._notify(f"El tema «{new.theme}» tiene un error y no se puede usar.")
            return  # ni se guarda ni se aplica: el menú sigue diciendo la verdad
        self.service.apply(Settings(theme_path(new.theme), new.brightness, new.flip), reload=reload)
        self.config = new
        try:
            save_config(new)
        except OSError:
            log.exception("No se pudo guardar la configuración")
            self._notify("No he podido guardar la configuración: se perderá al cerrar.")

    def _pending(self) -> str | None:
        return self.service.pending_device

    def _confirm(self) -> None:
        device = self._pending()
        if device is not None:
            self.service.confirm_port(device)

    def _autostart_enabled(self) -> bool:
        try:
            return autoarranque.is_enabled()
        except Exception:
            log.exception("No se pudo leer el inicio con Windows")
            return False

    def _toggle_autostart(self) -> None:
        try:
            if autoarranque.is_enabled():
                autoarranque.disable()
            else:
                autoarranque.enable(autoarranque.default_command())
        except (autoarranque.AutostartError, ValueError, OSError):
            log.exception("No se pudo cambiar el inicio con Windows")
            self._notify("No he podido cambiar el inicio con Windows.")

    def _create_shortcut(self) -> None:
        from luzeira.sistema.accesos import ShortcutError, create_shortcut

        try:
            create_shortcut("Desktop")
        except ShortcutError as exc:
            self._notify(str(exc))
            return
        self._notify("Listo: tienes Luzeira en el escritorio.")

    def _open_themes_folder(self) -> None:
        folder = user_themes_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # noqa: S606 — abre el Explorador en una carpeta nuestra

    def _import_theme(self) -> None:
        if self._importing:  # el diálogo ya está abierto: no abrir otro encima
            return
        self._importing = True
        try:
            chosen = self._ask_zip()
            if not chosen:
                return
            from luzeira.temas import import_theme_zip

            try:
                dest = import_theme_zip(Path(chosen), user_themes_dir())
            except ThemeError as exc:
                self._notify(str(exc), "Luzeira: no se pudo importar")
                return
            self._notify(f"Tema «{dest.name}» importado. Elígelo en «Tema».")
        finally:
            self._importing = False

    @staticmethod
    def _ask_zip() -> str | None:
        # Diálogo nativo de Windows (C-30: tkinter no viene en el Python embebible).
        from luzeira.sistema.dialogos import ask_open_file

        return ask_open_file("Importar tema de Luzeira", "Temas de Luzeira (*.zip)", "*.zip")

    # --- editor ---------------------------------------------------------------------

    def _apply_from_editor(self, name: str) -> None:
        """«Aplicar a la pantalla» del editor. Llega desde el hilo del servidor: se apunta
        ya como tema pedido y el cambio de verdad se pasa al hilo del icono."""
        with self._editor_lock:
            self._editor_seq += 1
            seq = self._editor_seq
            self._editor_request = (seq, name)

        def run() -> None:
            try:
                self._change(theme=name, reload=True)  # recarga siempre (remate 2)
            finally:
                with self._editor_lock:
                    if self._editor_request is not None and self._editor_request[0] == seq:
                        self._editor_request = None

        self._post(self._safe(run))

    def _active_theme(self) -> str:
        """Tema en pantalla para el servidor del editor (no deja borrarlo)."""
        with self._editor_lock:
            request = self._editor_request
        return request[1] if request is not None else self.config.theme

    def _on_editor_exit(self, code: int) -> None:
        """La ventana del editor ha terminado (llega desde un hilo vigilante)."""
        if code == EXIT_NO_WEBVIEW2:
            self._post(lambda: self._notify(MSG_NO_WEBVIEW2))

    def _open_editor(self) -> None:
        if not EditorLink.is_built():
            self._notify("El editor no está instalado en esta copia de Luzeira.")
            return
        if self.editor.open_window():
            return
        # Ya estaba abierto: traerlo al frente en vez de solo avisar.
        if not self.editor.window.bring_to_front():
            self._notify("El editor ya está abierto.")

    def quit(self) -> None:
        self.editor.close()
        self.service.stop()
        self.icon.stop()

    # --- menú --------------------------------------------------------------------

    def _menu(self) -> pystray.Menu:
        Item, Menu = pystray.MenuItem, pystray.Menu

        # ⚠ pystray cuenta los argumentos de cada función del menú y solo admite 0, 1 o 2:
        # nada de lambdas con valores por defecto (``name=entry.name`` cuenta como 3). Para
        # «recordar» el tema o el brillo de cada opción se usan estas pequeñas fábricas.
        def choose(**changes):
            return self._safe(lambda: self._change(**changes))

        def is_theme(name):
            return lambda _item: self.config.theme == name

        def is_brightness(level):
            return lambda _item: self.config.brightness == level

        def theme_items():
            for entry in list_themes():
                title = entry.title or entry.name
                label = title if entry.builtin else f"{title} (tuyo)"
                yield Item(
                    label, choose(theme=entry.name), checked=is_theme(entry.name), radio=True
                )

        def brightness_items():
            for level in BRIGHTNESS_LEVELS:
                yield Item(
                    f"{level} %",
                    choose(brightness=level),
                    checked=is_brightness(level),
                    radio=True,
                )

        return Menu(
            # default=True: también se abre con un clic en el icono.
            Item("Abrir editor", self._safe(self._open_editor), default=True),
            Menu.SEPARATOR,
            Item(lambda _item: self.service.message or "Arrancando…", None, enabled=False),
            # Solo cuando hay una pantalla con el USB de la rev A pero sin su número de serie:
            # podría ser otro modelo, así que no se le envía nada hasta que el usuario diga.
            Item(
                lambda _item: f"Sí, es mi pantalla Turing / UsbPCMonitor ({self._pending()})",
                self._safe(self._confirm),
                visible=lambda _item: self._pending() is not None,
            ),
            Item(
                "Reintentar",
                self._safe(self._retry),
                visible=lambda _item: self.service.status is Status.ERROR,
            ),
            Menu.SEPARATOR,
            Item("Tema", Menu(theme_items)),
            Item(
                "Importar tema (.zip)…",
                self._safe(self._import_theme),
                enabled=lambda _item: not self._importing,
            ),
            Item("Brillo", Menu(brightness_items)),
            Item(
                "Girar 180°",
                self._safe(lambda: self._change(flip=not self.config.flip)),
                checked=lambda _item: self.config.flip,
            ),
            Menu.SEPARATOR,
            Item(
                "Iniciar con Windows",
                self._safe(self._toggle_autostart),
                checked=lambda _item: self._autostart_enabled(),
            ),
            Item(
                "Crear acceso directo en el escritorio",
                self._safe(self._create_shortcut),
            ),
            Item("Abrir carpeta de temas", self._safe(self._open_themes_folder)),
            Menu.SEPARATOR,
            Item("Salir", lambda: self.quit()),
        )

    # --- arranque ------------------------------------------------------------------

    def _on_end_session(self) -> None:
        """Windows se apaga o cierra la sesión: apagar la pantalla YA (RQ-07). ``suspend``
        espera a que esté apagada; tras volver de aquí el proceso puede morir."""
        if not self.service.suspend(timeout=3.0):
            log.warning("No dio tiempo a apagar la pantalla antes del apagado.")

    def _setup(self, icon: pystray.Icon) -> None:
        """Ya hay icono visible: ahora sí se arranca el servicio (si arrancara antes, el
        primer aviso se perdería sin ventana a la que mandarlo; revisor fase 4)."""
        icon.visible = True
        if self._startup_notice:
            self._notify(self._startup_notice)
        self._start_service()
        if self._open_editor_on_start:
            self._safe(self._open_editor)()
        self._drain()

    def run(self) -> None:
        # Lambdas, no métodos ligados: tras «Reintentar» ``self.service`` es otro objeto y
        # los avisos tienen que llegar al servicio actual, no al que murió.
        power = PowerListener(
            on_suspend=lambda: self.service.suspend(), on_resume=lambda: self.service.resume()
        )
        session = SessionWatcher(self._on_end_session)
        if not power.start():
            log.warning("Sin avisos de suspensión: la pantalla no se apagará al suspender.")
        if not session.start():
            log.warning("Sin aviso de apagado: la pantalla podría quedarse encendida.")
        try:
            self.icon.run(setup=self._setup)  # bloquea hasta «Salir»
        finally:
            self.editor.close()
            self.service.stop()
            if self._worker is not None:
                self._worker.join(timeout=6)
            session.stop()
            power.stop()


def _already_running_message() -> None:
    """Avisar de que ya hay una Luzeira abierta (con doble clic no pasaba nada)."""
    with contextlib.suppress(AttributeError, OSError):  # fuera de Windows no hay MessageBox
        ctypes.windll.user32.MessageBoxW(
            None,
            "Luzeira ya está abierta. Búscala en la bandeja, junto al reloj.",
            "Luzeira",
            0x40,  # MB_ICONINFORMATION
        )


def main(open_editor: bool = False) -> int:
    setup_logging(default_log_dir())
    with SingleInstance(APP_MUTEX) as instance:
        if not instance.acquired:
            # Otra Luzeira ya está en la bandeja: no abrir dos sobre el mismo puerto.
            log.info("Luzeira ya estaba abierta; esta instancia termina.")
            _already_running_message()
            return 0
        migrate_old_name()  # «Visoria» → «Luzeira» (D-14)
        config_dir().mkdir(parents=True, exist_ok=True)
        clean_import_leftovers()
        TrayApp(load_config(), open_editor=open_editor).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
