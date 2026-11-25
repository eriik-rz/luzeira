"""Icono en la bandeja de Windows: la cara de Luzeira mientras no exista el editor.

Hilos (y quién toca qué):
- Hilo principal: el icono (pystray), con su bucle de mensajes de Windows. **Es el único
  que toca el icono y su menú.** Los demás le piden cosas con ``_post``, que deja la
  función en una cola y le manda un mensaje a su ventana (revisor de la fase 4: el menú
  se reconstruía desde dos hilos a la vez).
- Hilo «pantalla»: ``DisplayService.run()`` (sensores + render + puerto serie).
- Avisos de energía y de apagado: llegan en hilos de Windows y solo llaman a métodos
  thread-safe del servicio.

Ningún fallo es silencioso (RQ-09): cada estado de problema muestra un aviso de
Windows en el idioma del usuario, el estado se ve siempre en el menú, y si una acción del
menú falla, se avisa.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import math
import os
import queue
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pystray
from PIL import Image

from luzeira import __version__, actualizar, idioma
from luzeira.config import (
    DEFAULT_THEME,
    AppConfig,
    DisplaySetting,
    clean_import_leftovers,
    config_dir,
    list_themes,
    load_config,
    migrate_old_name,
    save_config,
    theme_path,
    user_themes_dir,
)
from luzeira.editor.almacen import EditorError
from luzeira.editor.conexion import EditorLink
from luzeira.editor.ventana import EXIT_NO_WEBVIEW2
from luzeira.idioma import t
from luzeira.marca import STAR, draw_mark
from luzeira.pantallas.modelos import (
    ADVANCED,
    REV_C_SIZES,
    DisplayChoice,
    DisplayModel,
    PortInfo,
    compatible_models,
    identify,
    is_own_usb_only,
    list_ports,
    model_by_key,
    remembered_device,
)
from luzeira.sensores import modulo
from luzeira.servicio import DisplayService, Settings, Status
from luzeira.sistema import autoarranque, paquete, tarea_inicio
from luzeira.sistema.energia import PowerListener
from luzeira.sistema.instancia import APP_MUTEX, EditorRequests, SingleInstance
from luzeira.sistema.registro import default_log_dir, setup_logging
from luzeira.sistema.sesion import SessionWatcher
from luzeira.sistema.tarea_inicio import StartupState
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

# Textos para el usuario: estas constantes guardan la **clave** del catálogo (zona
# ``bandeja``), no el texto. Se traducen con ``t()`` al usarlas, nunca al importar: si no,
# se quedarían en el idioma del arranque aunque el usuario cambie de idioma.
MSG_NO_WEBVIEW2 = "bandeja.sin_webview2"

# --- elegir pantalla (C-44) ---------------------------------------------------------------
TITLE_BETA = "bandeja.beta_titulo"
# Para los modelos con saludo (la prueba puede decir «esa no es»).
MSG_BETA = "bandeja.beta_texto"
# Para los modelos sin saludo (Kipye): la prueba no puede fallar, así que no se promete.
MSG_BETA_UNCHECKED = "bandeja.beta_texto_sin_saludo"
# Para la rev C: además del saludo, hay que despertarla primero (hasta ~15 s abriendo su
# puente y unos segundos de saludos: ``pantallas.turing_rev_c``).
MSG_BETA_REV_C = "bandeja.beta_texto_rev_c"
TITLE_LOOKS_RIGHT = "bandeja.se_ve_bien_titulo"
MSG_LOOKS_RIGHT = "bandeja.se_ve_bien_texto"
MSG_TRIAL_REJECTED = "bandeja.prueba_rechazada"
TITLE_LOOKS_RIGHT_EDITOR = "bandeja.se_ve_bien_titulo_editor"
MSG_DIALOG_OPEN = "bandeja.dialogo_abierto"
TITLE_ADVANCED = "bandeja.avanzado_titulo"
MSG_ADVANCED = "bandeja.avanzado_texto"
MSG_CHOICE_SAVED = "bandeja.eleccion_guardada"
MSG_BACK_TO_AUTO = "bandeja.deteccion_sola"

# --- actualizaciones (ADR-006) -------------------------------------------------------------
MSG_UP_TO_DATE = "bandeja.actualizar_al_dia"
MSG_UPDATE_AVAILABLE = "bandeja.actualizar_disponible"
MSG_UPDATE_BUSY = "bandeja.actualizar_ocupado"
MSG_UPDATE_NONE = "bandeja.actualizar_ninguna"
MSG_STORE_UPDATES = "bandeja.actualizar_store"
MSG_AUTOSTART_BLOCKED = "bandeja.autoarranque_bloqueado"
MSG_AUTOSTART_FAILED = "bandeja.autoarranque_fallo"
MSG_STORE_SHORTCUT = "bandeja.acceso_store"
MSG_UPDATE_DOWNLOADING = "bandeja.actualizar_descargando"
MSG_UPDATE_INSTALLING = "bandeja.actualizar_instalando"
MSG_UPDATE_FAILED = "bandeja.actualizar_fallo"
# Módulo de sensores (ADR-007): solo se avisa y se abre su página; no se instala desde aquí.
MSG_MODULE_AVAILABLE = "bandeja.modulo_disponible"
MENU_MODULE_DOWNLOAD = "bandeja.menu_descargar_modulo"
_UPDATE_FIRST_DELAY = 60.0  # s tras arrancar: que la pantalla se encienda primero
_UPDATE_TICK = 3600.0  # s entre miradas al reloj
_UPDATE_EVERY = 24 * 3600.0  # s entre comprobaciones automáticas
_PORTS_TTL = 1.0  # s: reconstruir el menú no enumera los puertos varias veces seguidas
# Otra copia de Luzeira pide abrir el editor (acceso directo del escritorio): como mucho una
# petición cada este tiempo y una pendiente a la vez (R4-09). Cualquier proceso del usuario
# puede señalar el aviso en bucle; sin tope llenaba la cola y robaba el foco sin parar.
_EDITOR_REQUEST_GAP = 1.0  # s


def choice_from_setting(setting: DisplaySetting | None) -> DisplayChoice | None:
    return DisplayChoice(**setting.model_dump()) if setting is not None else None


def setting_from_choice(choice: DisplayChoice) -> DisplaySetting:
    return DisplaySetting(
        model=choice.model,
        device=choice.device,
        vid=choice.vid,
        pid=choice.pid,
        serial=choice.serial,
        advanced=choice.advanced,
    )


def model_label(key: str) -> str:
    model = model_by_key(key)
    return model.menu_label if model is not None else t("bandeja.pantalla_generica")


def clip(text: str, limit: int) -> str:
    """Recorta a ``limit`` caracteres con «…» si hace falta."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def beta_template(model: DisplayModel) -> str:
    """El aviso de «vas a probar un modelo en beta» que le toca a ``model``."""
    if model in REV_C_SIZES:
        return MSG_BETA_REV_C
    return MSG_BETA if model.has_hello else MSG_BETA_UNCHECKED


def unsupported_line(port: PortInfo) -> str | None:
    """La línea deshabilitada «todavía no compatible» de un puerto, o None.

    Solo si se reconoce un modelo sin driver **y** la lista cerrada no ofrece nada en ese
    puerto: una rev C o una WeAct de verdad ya salen con sus tamaños (fase 5). Queda, por
    ejemplo, para un aparato que anuncia el serie de una rev C con otro USB."""
    found = identify(port)
    if found is None or found.model is None or found.model.supported:
        return None
    if compatible_models(port):
        return None
    return t("bandeja.no_compatible", device=port.device, model=found.model.label)


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


class EditorSettings:
    """Los ajustes de la ventana «Ajustes» del editor (contrato en
    :mod:`luzeira.editor.ajustes`). Llega desde el hilo del servidor: lo que cambia la
    pantalla o la configuración se pasa al hilo del icono con ``_post``, como el menú.
    """

    def __init__(self, app: TrayApp) -> None:
        self._app = app

    def _options(self) -> tuple[list[dict], list[dict]]:
        """Lista cerrada y modo avanzado, con un ``id`` por opción (lo que el editor
        manda de vuelta) y su ``action`` interna (``(modelo, puerto, avanzado)``)."""
        app = self._app
        closed: list[dict] = []
        advanced: list[dict] = []
        for port in app._ports():
            for model in compatible_models(port):
                template = beta_template(model)
                closed.append(
                    {
                        "id": f"lista:{port.device}:{model.key}",
                        "label": f"{port.device}: {model.menu_label}",
                        "beta": model.beta,
                        "current": app._is_current(model, port),
                        "warning": (
                            t(template, device=port.device, model=model.label)
                            if model.beta
                            else None
                        ),
                        "action": (model, port, False),
                    }
                )
            unsupported = unsupported_line(port)
            if unsupported is not None:
                closed.append(
                    {
                        "id": None,
                        "label": unsupported,
                        "beta": True,
                        "current": False,
                        "warning": None,
                        "action": None,
                    }
                )
            if is_own_usb_only(port):
                continue  # 🔴 ni rev C (R5-02: órdenes que borran) ni WeAct (sin marco)
            for model in ADVANCED:  # sin rev C ni WeAct: solo casan con su propio USB
                advanced.append(
                    {
                        "id": f"avanzado:{port.device}:{model.key}",
                        "label": f"{port.label}: {model.menu_label}",
                        "beta": model.beta,
                        "current": False,
                        "warning": t(MSG_ADVANCED, device=port.device, model=model.menu_label),
                        "action": (model, port, True),
                    }
                )
        return closed, advanced

    def snapshot(self) -> dict:
        app = self._app
        closed, advanced = self._options()

        def public(options: list[dict]) -> list[dict]:
            return [{k: v for k, v in o.items() if k != "action"} for o in options]

        autostart = app._autostart_state()
        return {
            "updates": app.updates_snapshot(),
            "brightness": app.config.brightness,
            "brightness_levels": list(BRIGHTNESS_LEVELS),
            "flip": app.config.flip,
            "store": app._store,
            "autostart": autostart == StartupState.ON,
            "autostart_blocked": autostart == StartupState.BLOCKED,
            "language": app.config.language or "auto",
            "languages": [{"code": c, "name": n} for c, n in idioma.available().items()],
            "display_text": app._display_text(),
            "display_auto": app.config.display is None and app._trial is None,
            "choices": public(closed),
            "advanced": public(advanced),
        }

    def update(
        self,
        *,
        brightness: int | None,
        flip: bool | None,
        autostart: bool | None,
        updates: bool | None = None,
        language: str | None = None,
    ) -> None:
        app = self._app
        if language is not None and language != "auto" and language not in idioma.available():
            raise EditorError(400, idioma.t("idioma.no_valido"))
        if brightness is not None and brightness not in BRIGHTNESS_LEVELS:
            raise EditorError(400, t("bandeja.brillo_no_valido"))
        if autostart is not None:
            result = app._set_autostart(autostart)
            if result is None:
                if app._autostart_error:
                    raise EditorError(409, app._autostart_error)
                raise EditorError(500, t(MSG_AUTOSTART_FAILED))
            if autostart and result == StartupState.BLOCKED:
                raise EditorError(409, t(MSG_AUTOSTART_BLOCKED))
        changes: dict[str, object] = {}
        if brightness is not None:
            changes["brightness"] = brightness
        if flip is not None:
            changes["flip"] = flip
        if updates is not None:
            app._post(app._safe(lambda: app._set_updates(updates)))
        if language is not None:
            code = None if language == "auto" else language
            app._post(app._safe(lambda: app._set_language(code)))
        if changes:
            app._post(app._safe(lambda: app._change(**changes)))
        else:
            app._post(app.icon.update_menu)  # la marca de «Iniciar con Windows»

    def updates_question(self) -> bool:
        return self._app.config.updates is None and self._app._can_update()

    def update_available(self) -> str | None:
        app = self._app
        update = app._update
        return update.version if update is not None and not app._update_busy else None

    def check_updates(self) -> str:
        return self._app.check_updates(manual=True)

    def install_update(self) -> str:
        try:
            return self._app.install_update(delay=2.0)  # que la respuesta llegue al editor
        except actualizar.UpdateError as exc:
            raise EditorError(409, str(exc)) from None

    def choose_display(self, choice_id: str | None, *, confirmed: bool) -> None:
        app = self._app
        if app._dialog_open:
            raise EditorError(409, t(MSG_DIALOG_OPEN))
        if choice_id is None:
            app._post(app._safe(app._back_to_auto))
            return
        closed, advanced = self._options()
        option = next((o for o in closed + advanced if o["id"] == choice_id), None)
        if option is None or option["action"] is None:
            # El puerto ya no está (o nunca se ofreció): que el editor vuelva a leer.
            raise EditorError(409, t("bandeja.eleccion_ya_no_esta"))
        model, port, is_advanced = option["action"]
        if option["warning"] is not None and not confirmed:
            raise EditorError(400, t("bandeja.falta_confirmar"))
        choice = DisplayChoice.for_port(model, port, advanced=is_advanced)
        app._post(app._safe(lambda: app._try_choice(choice, from_editor=True)))

    def question(self) -> dict | None:
        choice = self._app.editor_question()
        if choice is None:
            return None
        text = t(MSG_LOOKS_RIGHT, model=model_label(choice.model), device=choice.device)
        return {"title": t(TITLE_LOOKS_RIGHT_EDITOR), "text": text}

    def answer(self, looks_right: bool) -> str:
        app = self._app
        choice = app.editor_question()
        if choice is None:
            raise EditorError(409, t("bandeja.pregunta_no_pendiente"))
        app._post(app._safe(lambda: app._answer_from_editor(choice, looks_right)))
        if looks_right:
            return t(MSG_CHOICE_SAVED, model=model_label(choice.model), device=choice.device)
        return t(MSG_TRIAL_REJECTED)

    def create_shortcut(self) -> str:
        from luzeira.sistema.accesos import ShortcutError, create_shortcut

        if self._app._store:
            raise EditorError(409, t(MSG_STORE_SHORTCUT))
        try:
            create_shortcut("Desktop")
        except ShortcutError as exc:
            raise EditorError(500, str(exc)) from None
        return t("bandeja.acceso_creado")

    def open_themes_folder(self) -> None:
        self._app._open_themes_folder()

    def open_startup_settings(self) -> None:
        self._app._open_startup_settings()

    def import_theme(self, zip_path: Path) -> str:
        from luzeira.temas import import_theme_zip

        dest = import_theme_zip(zip_path, user_themes_dir())
        self._app._post(self._app.icon.update_menu)  # que salga en «Tema»
        return dest.name


class TrayApp:
    def __init__(
        self, config: AppConfig, *, open_editor: bool = False, store: bool | None = None
    ) -> None:
        # open_editor: abrir también el editor al arrancar (la primera vez, desde el
        # instalador: así el usuario ve qué puede hacer sin buscar el icono del reloj).
        self._open_editor_on_start = open_editor
        # store: versión de Microsoft Store (paquete MSIX). Sin actualizador propio, sin
        # acceso del escritorio y con el inicio con Windows en Configuración (C-52).
        self._store = paquete.is_packaged() if store is None else store
        # Motivo del último fallo de «Iniciar con Windows» que el usuario puede arreglar.
        self._autostart_error: str | None = None
        self._pending_calls: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        # (estado, idioma.ident(mensaje)) del último estado: sin depender del idioma.
        self._last_notified: tuple[Status, object] | None = None
        self._importing = False
        self._startup_notice: str | None = None
        # Pantalla elegida a mano que se está probando: se guarda solo si funciona.
        self._trial: DisplayChoice | None = None
        # Diálogos Sí/No: uno cada vez. ``MessageBoxW`` sigue atendiendo los mensajes de la
        # ventana mientras está abierto, así que el menú y la cola pueden volver a entrar.
        self._dialog_open = False
        self._asking: DisplayChoice | None = None  # la de «¿se ve bien?», mientras se pregunta
        # True: esa pregunta está en la ventana del editor (se eligió desde allí), no en un
        # diálogo de Windows. Se contesta con ``EditorSettings.answer``.
        self._asking_in_editor = False
        self._trial_from_editor = False  # la elección que se prueba se hizo en el editor
        self._question_waiting: DisplayChoice | None = None  # esperando a otro diálogo
        self._ports_cache: tuple[float, list[PortInfo]] | None = None
        # Actualizaciones: la versión nueva ya comprobada (firma incluida) que se ofrece, y
        # si hay una comprobación o descarga en marcha (una cada vez).
        self._update: actualizar.Manifest | None = None
        self._update_lock = threading.Lock()
        self._update_busy = False
        self._update_notified: str | None = None
        self._update_wake = threading.Event()
        # Módulo de sensores: la versión nueva firmada que se ofrece (solo un enlace), la
        # última de la que se avisó, y cómo se lee la instalada (los tests la cambian).
        self._module: actualizar.ModuleManifest | None = None
        self._module_notified: str | None = None
        self._module_version: Callable[[], str | None] = modulo.installed_version
        self._closing = False
        # El idioma, lo primero: el aviso de abajo ya sale en el del usuario.
        idioma.set_language(config.language)
        if not is_theme_valid(config.theme):
            # Un tema que dejó de funcionar no puede dejar la pantalla apagada para siempre.
            self._startup_notice = t(
                "bandeja.tema_no_carga", theme=config.theme, default=DEFAULT_THEME
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
        # Peticiones de abrir el editor desde otra copia (R4-09): una pendiente a la vez y
        # como mucho una por segundo.
        self._open_request_lock = threading.Lock()
        self._open_request_pending = False
        self._open_request_at = -math.inf
        self.editor = EditorLink(
            apply_theme=self._apply_from_editor,
            status=lambda: (self.service.status.value, self.service.message),
            active_theme=self._active_theme,
            settings=EditorSettings(self),
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
        return DisplayService(
            self._settings(),
            choice=choice_from_setting(self.config.display),
            on_status=self._on_status,
        )

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
        """``message`` suele ser un :class:`~luzeira.idioma.Text`: se enseña en el idioma de
        ahora y se compara por su clave, así que el mismo aviso en otro idioma (tras cambiar
        de idioma) no vuelve a salir."""
        text = str(idioma.again(message))
        self.icon.icon = make_icon(_COLORS.get(status, _RED))
        self._show_tooltip(text)
        self._settle_trial(status)
        self.icon.update_menu()
        seen = (status, idioma.ident(message))
        if status in _NOTIFY and seen != self._last_notified:
            self._notify(text)
        self._last_notified = seen

    def _show_tooltip(self, message: str) -> None:
        self.icon.title = clip(f"Luzeira — {message}", _MAX_TIP)

    def _settle_trial(self, status: Status) -> None:
        """¿Ha funcionado la pantalla elegida a mano? Si se ve imagen, se recuerda; si
        no contestó como ese modelo, se olvida (el aviso ya lo da el servicio).

        Si su prueba no podía fallar (Kipye, o una rev A muda fuera de su USB), «se ve
        imagen» solo quiere decir que el puerto acepta datos: se pregunta al usuario antes
        de recordarla.

        Se compara con la elección con la que el servicio abrió la pantalla, no con el COM:
        si Windows la ha movido de puerto, sigue siendo la elegida (se guarda el COM nuevo).
        Un RUNNING de la pantalla anterior (llegado tarde) no la resuelve.
        """
        trial = self._trial
        if trial is None:
            return
        if status in (Status.NEEDS_CONFIRMATION, Status.ERROR, Status.STOPPED):
            self._trial = None
            return
        info = self.service.display_info
        if status is not Status.RUNNING or info is None or info.choice != trial:
            return
        self._trial = None
        from_editor = self._trial_from_editor
        found = replace(trial, device=remembered_device(trial, info.device))
        if info.verified:
            self._remember(found)
        else:
            self._post(lambda: self._ask_looks_right(found, in_editor=from_editor))

    def _remember(self, choice: DisplayChoice, *, notify: bool = True) -> None:
        self._save(self.config.model_copy(update={"display": setting_from_choice(choice)}))
        if notify:
            text = t(MSG_CHOICE_SAVED, model=model_label(choice.model), device=choice.device)
            self._notify(text)

    def _ask(self, title: str, text: str, **flags: bool) -> bool:
        """Un diálogo Sí/No (nunca dos a la vez: quien llame mira ``_dialog_open``)."""
        from luzeira.sistema.dialogos import confirm

        self._dialog_open = True
        try:
            return confirm(title, text, **flags)
        finally:
            self._dialog_open = False
            waiting, self._question_waiting = self._question_waiting, None
            if waiting is not None:
                self._post(lambda: self._ask_looks_right(waiting))

    def _ask_looks_right(self, choice: DisplayChoice, *, in_editor: bool = False) -> None:
        """«¿Se ve bien?» para una elección cuya prueba no podía fallar. Sí → se recuerda;
        No (o cerrar el diálogo) → se deja de usar y se vuelve a lo que hubiera guardado.

        ``in_editor``: se eligió en la ventana del editor; si sigue abierta, la pregunta
        sale allí (``editor_question``) en vez de en un diálogo de Windows."""
        if in_editor and self.editor.window.is_open:
            self._asking = choice
            self._asking_in_editor = True
            return
        if self._dialog_open:
            self._question_waiting = choice  # en cuanto se cierre el que hay
            return
        self._asking = choice
        self._asking_in_editor = False
        text = t(MSG_LOOKS_RIGHT, model=model_label(choice.model), device=choice.device)
        looks_right = self._ask(t(TITLE_LOOKS_RIGHT), text, default_no=True)
        if self._asking is not choice:
            return  # mientras se preguntaba, el usuario eligió otra cosa en el menú
        self._asking = None
        self._settle_answer(choice, looks_right)

    def _settle_answer(
        self, choice: DisplayChoice, looks_right: bool, *, notify: bool = True
    ) -> None:
        """Lo que pasa al contestar «¿se ve bien?». ``notify=False``: la respuesta llegó
        del editor, que ya se lo dice al usuario (sin repetirlo en un aviso de Windows)."""
        if looks_right:
            self._remember(choice, notify=notify)
            return
        self.service.choose_display(choice_from_setting(self.config.display))
        if notify:
            self._notify(t(MSG_TRIAL_REJECTED))

    def editor_question(self) -> DisplayChoice | None:
        """La pregunta «¿se ve bien?» que espera respuesta en el editor, si hay."""
        choice = self._asking
        return choice if choice is not None and self._asking_in_editor else None

    def _answer_from_editor(self, choice: DisplayChoice, looks_right: bool) -> None:
        if self._asking is not choice or not self._asking_in_editor:
            return  # ya se contestó, o se eligió otra cosa mientras tanto
        self._asking = None
        self._asking_in_editor = False
        self._settle_answer(choice, looks_right, notify=False)

    # --- acciones del menú (todas en el hilo del icono) -----------------------------

    def _safe(self, action: Callable[[], None]) -> Callable[[], None]:
        """Ninguna acción del menú falla en silencio: se registra y se avisa."""

        def run() -> None:
            try:
                action()
            except Exception:
                log.exception("Falló una acción del menú")
                self._notify(t("bandeja.algo_fallo"))
            self.icon.update_menu()

        return run

    def _change(self, *, reload: bool = False, **changes: object) -> None:
        """Cambia la configuración y la aplica. ``reload``: volver a leer el tema aunque
        sea el que ya está en pantalla (cuando lo pide el editor tras guardar cambios)."""
        new = self.config.model_copy(update=changes)
        if "theme" in changes and not is_theme_valid(new.theme):
            self._notify(t("bandeja.tema_con_error", theme=new.theme))
            return  # ni se guarda ni se aplica: el menú sigue diciendo la verdad
        self.service.apply(Settings(theme_path(new.theme), new.brightness, new.flip), reload=reload)
        self._save(new)

    def _set_language(self, code: str | None) -> None:
        """Cambia el idioma al momento: menú nuevo y el tema otra vez en la pantalla (la
        fecha y las etiquetas de los temas también cambian)."""
        idioma.set_language(code)
        self._change(reload=True, language=code)
        self.icon.menu = self._menu()
        self.icon.update_menu()
        # El estado de ahora, ya en el idioma nuevo (el servicio lo reescribe al pedirlo).
        self._show_tooltip(self.service.message)
        # La ventana del editor va en otro proceso: su título y su «¿cerrar sin guardar?».
        self.editor.window.set_language(idioma.current())

    def _save(self, new: AppConfig) -> None:
        self.config = new
        try:
            save_config(new)
        except OSError:
            log.exception("No se pudo guardar la configuración")
            self._notify(t("bandeja.config_no_guardada"))

    def _pending(self) -> str | None:
        return self.service.pending_device

    def _confirm_label(self) -> str:
        choice = self.service.pending_choice
        if choice is not None:
            return t(
                "bandeja.confirmar_modelo", model=model_label(choice.model), device=choice.device
            )
        return t("bandeja.confirmar_rev_a", device=self._pending())

    def _confirm(self) -> None:
        choice = self.service.pending_choice
        if choice is not None:
            self._try_choice(choice)  # una propuesta beta: se prueba y, si va, se recuerda
            return
        device = self._pending()
        if device is not None:
            self.service.confirm_port(device)

    # --- elegir pantalla (C-44) -------------------------------------------------------

    def _ports(self) -> list[PortInfo]:
        """Los puertos COM según Windows (sin abrir ninguno), con una caché corta."""
        now = time.monotonic()
        if self._ports_cache is None or now - self._ports_cache[0] > _PORTS_TTL:
            try:
                ports = list_ports()
            except Exception:
                log.exception("No se pudo leer la lista de puertos")
                ports = []
            self._ports_cache = (now, ports)
        return self._ports_cache[1]

    def _display_text(self) -> str:
        info = self.service.display_info
        if info is None:
            return t("bandeja.sin_pantalla")
        model = model_label(info.model_key)
        if info.device:
            return t("bandeja.conectada_en", model=model, device=info.device)
        return t("bandeja.conectada", model=model)

    def _is_current(self, model: DisplayModel, port: PortInfo) -> bool:
        info = self.service.display_info
        return info is not None and (info.model_key, info.device) == (model.key, port.device)

    def _try_choice(self, choice: DisplayChoice, *, from_editor: bool = False) -> None:
        self._trial = choice
        self._trial_from_editor = from_editor
        self._asking = self._question_waiting = None  # una pregunta pendiente ya no vale
        self._asking_in_editor = False
        self.service.choose_display(choice)

    def _pick(self, model: DisplayModel, port: PortInfo, *, advanced: bool) -> None:
        """El usuario ha elegido modelo y puerto en el menú. Beta → se explica y se
        pregunta; modo avanzado → advertencia y «No» por defecto."""
        if self._dialog_open:  # ya hay una pregunta abierta: no abrir otra encima
            self._notify(t(MSG_DIALOG_OPEN))
            return
        if advanced:
            text = t(MSG_ADVANCED, device=port.device, model=model.menu_label)
            if not self._ask(t(TITLE_ADVANCED), text, warning=True):
                return
        elif model.beta:
            text = t(beta_template(model), device=port.device, model=model.label)
            if not self._ask(t(TITLE_BETA), text):
                return
        self._try_choice(DisplayChoice.for_port(model, port, advanced=advanced))

    def _back_to_auto(self) -> None:
        self._trial = None
        self._asking = self._question_waiting = None
        self._asking_in_editor = False
        self._save(self.config.model_copy(update={"display": None}))
        self.service.choose_display(None)
        self._notify(t(MSG_BACK_TO_AUTO))

    def _autostart_state(self) -> StartupState:
        """Versión Store: la tarea de inicio del paquete (la clave Run de dentro del paquete
        no la ve Windows). Si no: la clave Run, y si el usuario la quitó en el
        Administrador de tareas, «bloqueado» (C-24)."""
        try:
            if self._store:
                return tarea_inicio.state()
            if not autoarranque.is_enabled():
                return StartupState.OFF
            if autoarranque.blocked_by_user():
                return StartupState.BLOCKED
            return StartupState.ON
        except Exception:
            log.exception("No se pudo leer el inicio con Windows")
            return StartupState.OFF

    def _autostart_enabled(self) -> bool:
        return self._autostart_state() == StartupState.ON

    def _set_autostart(self, on: bool) -> StartupState | None:
        """Lo activa o desactiva y devuelve cómo queda; ``None`` si falló. Si el fallo
        tiene un motivo que el usuario puede arreglar, queda en ``_autostart_error``."""
        self._autostart_error = None
        try:
            if self._store:
                return tarea_inicio.set_enabled(on)
            if on:
                autoarranque.enable(autoarranque.default_command())
            else:
                autoarranque.disable()
        except autoarranque.UnsafeAppFolder as exc:
            # R4-06: otro usuario del PC podría cambiar el programa que arrancaría. El
            # mensaje es para el usuario (dice qué hacer); al log no va la ruta.
            log.warning("Inicio con Windows rechazado: la carpeta de la app no es segura.")
            self._autostart_error = str(exc)
            return None
        except Exception:
            log.exception("No se pudo cambiar el inicio con Windows")
            return None
        return self._autostart_state()

    def _toggle_autostart(self) -> None:
        current = self._autostart_state()
        result = self._set_autostart(current != StartupState.ON)
        if result is None:
            self._notify(self._autostart_error or t(MSG_AUTOSTART_FAILED))
        elif result == StartupState.BLOCKED:
            self._notify(t(MSG_AUTOSTART_BLOCKED))
            self._open_startup_settings()

    def _open_startup_settings(self) -> None:
        os.startfile(paquete.STARTUP_SETTINGS_URI)  # noqa: S606 — página fija de Configuración

    def _create_shortcut(self) -> None:
        from luzeira.sistema.accesos import ShortcutError, create_shortcut

        try:
            create_shortcut("Desktop")
        except ShortcutError as exc:
            self._notify(str(exc))
            return
        self._notify(t("bandeja.acceso_creado"))

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
                self._notify(str(exc), t("bandeja.importar_fallo_titulo"))
                return
            self._notify(t("bandeja.importar_hecho", name=dest.name))
        finally:
            self._importing = False

    @staticmethod
    def _ask_zip() -> str | None:
        # Diálogo nativo de Windows (C-30: tkinter no viene en el Python embebible).
        from luzeira.sistema.dialogos import ask_open_file

        return ask_open_file(t("bandeja.importar_titulo"), t("bandeja.importar_filtro"), "*.zip")

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
            self._post(lambda: self._notify(t(MSG_NO_WEBVIEW2)))
        self._post(self._question_back_to_windows)

    def _question_back_to_windows(self) -> None:
        """Se ha cerrado el editor con «¿se ve bien?» sin contestar: se pregunta con un
        diálogo de Windows (sin respuesta, la pantalla no se recordaría nunca)."""
        choice = self.editor_question()
        if choice is None:
            return
        self._asking = None
        self._asking_in_editor = False
        self._ask_looks_right(choice)

    def _editor_requested(self, now: float | None = None) -> None:
        """Otra copia pide abrir el editor (llega desde el hilo del aviso). Se pasa al hilo
        del icono solo si no hay ya una petición pendiente y ha pasado
        ``_EDITOR_REQUEST_GAP`` desde la última (R4-09): una señal en bucle no llena la cola
        ni roba el foco más de una vez por segundo."""
        now = time.monotonic() if now is None else now
        with self._open_request_lock:
            if self._open_request_pending or now - self._open_request_at < _EDITOR_REQUEST_GAP:
                return
            self._open_request_pending = True
            self._open_request_at = now
        self._post(self._safe(self._open_requested_editor))

    def _open_requested_editor(self) -> None:
        with self._open_request_lock:
            self._open_request_pending = False
        self._open_editor()

    def _open_editor(self) -> None:
        if not EditorLink.is_built():
            self._notify(t("bandeja.editor_no_instalado"))
            return
        if self.editor.open_window():
            return
        # Ya estaba abierto: traerlo al frente en vez de solo avisar.
        if not self.editor.window.bring_to_front():
            self._notify(t("bandeja.editor_ya_abierto"))

    # --- actualizaciones (ADR-006) -------------------------------------------------------

    def _can_update(self) -> bool:
        """Esta copia busca versiones nuevas ella misma: lleva claves para comprobar la
        firma y no es la de Microsoft Store (esa la actualiza la Store)."""
        return bool(actualizar.PUBLIC_KEYS) and not self._store

    def updates_snapshot(self) -> dict:
        update = self._update
        return {
            "can_check": self._can_update(),
            "enabled": self.config.updates,
            "available": update.version if update is not None else None,
            "busy": self._update_busy,
            "version": __version__,
        }

    def _set_updates(self, enabled: bool) -> None:
        self._save(self.config.model_copy(update={"updates": enabled}))
        if enabled:
            self._update_wake.set()  # que mire ya si toca comprobar

    def _claim_update(self) -> bool:
        with self._update_lock:
            if self._update_busy:
                return False
            self._update_busy = True
            return True

    def _release_update(self) -> None:
        with self._update_lock:
            self._update_busy = False
        self._post(self.icon.update_menu)

    def check_updates(self, *, manual: bool) -> str:
        """Busca una versión nueva (bloquea: nunca desde el hilo del icono). Devuelve el
        texto para el usuario. ``manual``: lo ha pedido el usuario ahora mismo."""
        if not self._can_update():
            return t(MSG_STORE_UPDATES) if self._store else t(MSG_UPDATE_NONE)
        if not self._claim_update():
            return t(MSG_UPDATE_BUSY)
        checked_at = time.time()
        try:
            manifest = actualizar.check(max_seen=self.config.updates_max_seen)
            module_ok, module = self._check_module()  # nunca lanza
        except Exception as exc:
            if isinstance(exc, actualizar.UpdateError):
                log.warning("No se pudo comprobar si hay versión nueva: %s", exc)
                text = str(exc)
            else:
                log.exception("Fallo al comprobar si hay versión nueva")
                text = t(MSG_UPDATE_FAILED)
            # También cuenta como la comprobación del día: sin internet, o si GitHub da
            # error, no se reintenta cada hora (revisor, 2026-11-18).
            self._post(self._safe(lambda: self._save_last_check(checked_at)))
            return text
        finally:
            self._release_update()
        self._post(self._safe(lambda: self._update_checked(manifest, checked_at, manual)))
        if module_ok:
            self._post(self._safe(lambda: self._module_checked(module, manual)))
        if manifest is None:
            text = t(MSG_UP_TO_DATE, version=__version__)
        else:
            text = t(MSG_UPDATE_AVAILABLE, version=manifest.version)
        if manual and module is not None:
            # Una sola respuesta: dos avisos seguidos podrían taparse uno a otro.
            text += "\n" + t(MSG_MODULE_AVAILABLE, version=module.version)
        return text

    def _check_module(self) -> tuple[bool, actualizar.ModuleManifest | None]:
        """Después de la app, el módulo de sensores, si está instalado. ``(False, None)``
        si algo falla: se apunta en el registro y la comprobación de la app sigue igual
        (ni su texto ni su estado cambian). Nunca lanza."""
        try:
            installed = self._module_version()
            if installed is None:
                return True, None  # sin módulo (o sin saber su versión): nada que ofrecer
            return True, actualizar.check_module(installed)
        except actualizar.UpdateError as exc:
            log.warning("No se pudo comprobar si hay módulo de sensores nuevo: %s", exc)
        except Exception:
            log.exception("Fallo al comprobar si hay módulo de sensores nuevo")
        return False, None

    def _module_checked(self, module: actualizar.ModuleManifest | None, manual: bool) -> None:
        """En el hilo del icono: se ofrece (menú) y se avisa una sola vez por versión. Si lo
        pidió el usuario, el aviso ya va en la respuesta de ``check_updates``."""
        self._module = module
        if module is not None and module.version != self._module_notified:
            self._module_notified = module.version
            if not manual:
                self._notify(t(MSG_MODULE_AVAILABLE, version=module.version))

    def _open_module_page(self) -> None:
        """Abre en el navegador la release de la que salió ``modulo.json``. No se baja ni se
        instala nada desde aquí: el instalador del módulo pide administrador (ADR-007)."""
        webbrowser.open(actualizar.MODULE_PAGE_URL)

    def _module_label(self) -> str:
        module = self._module
        return t(MENU_MODULE_DOWNLOAD, version=module.version if module else "")

    def _save_last_check(self, checked_at: float) -> None:
        self._save(self.config.model_copy(update={"updates_last_check": checked_at}))

    def _update_checked(
        self, manifest: actualizar.Manifest | None, checked_at: float, manual: bool
    ) -> None:
        changes: dict[str, object] = {"updates_last_check": checked_at}
        if manifest is not None:
            changes["updates_max_seen"] = manifest.version
        self._save(self.config.model_copy(update=changes))
        self._update = manifest
        if manifest is not None and not manual and manifest.version != self._update_notified:
            self._update_notified = manifest.version
            self._notify(t(MSG_UPDATE_AVAILABLE, version=manifest.version))

    def install_update(self, *, delay: float = 0.0) -> str:
        """Descarga y comprueba la versión ofrecida (bloquea) y después, en el hilo del
        icono, lanza su instalador y cierra Luzeira. Lanza ``UpdateError``."""
        manifest = self._update
        if manifest is None:
            raise actualizar.UpdateError(t(MSG_UPDATE_NONE))
        if not self._claim_update():
            raise actualizar.UpdateError(t(MSG_UPDATE_BUSY))
        # El candado sigue puesto si va bien: hasta que se lanza el instalador (o falla al
        # lanzarlo) no se puede pedir otra descarga ni otro «Actualizar».
        try:
            installer = actualizar.download(manifest)
        except actualizar.UpdateError:
            self._release_update()
            raise
        except Exception:
            self._release_update()
            log.exception("Fallo al descargar la versión nueva")
            raise actualizar.UpdateError(t("bandeja.actualizar_fallo_descarga")) from None

        def launch() -> None:
            self._post(self._safe(lambda: self._launch_update(installer, manifest)))

        if delay > 0:
            threading.Timer(delay, launch).start()
        else:
            launch()
        return t(MSG_UPDATE_INSTALLING, version=manifest.version)

    def _launch_update(self, installer: Path, manifest: actualizar.Manifest) -> None:
        try:
            actualizar.launch(installer, manifest, default_log_dir() / "actualizacion.log")
        except actualizar.UpdateError as exc:
            log.warning("No se abre el instalador de la versión nueva: %s", exc)
            self._release_update()
            self._notify(str(exc))
            return
        except OSError:
            log.exception("No se pudo abrir el instalador de la versión nueva")
            self._release_update()
            self._notify(t("bandeja.actualizar_fallo_instalador"))
            return
        log.info("Instalando la versión %s: Luzeira se cierra.", manifest.version)
        self.quit()

    def _update_label(self) -> str:
        update = self._update
        if update:
            return t("bandeja.menu_actualizar_a", version=update.version)
        return t("bandeja.menu_actualizar")

    def _check_from_menu(self) -> None:
        def run() -> None:
            text = self.check_updates(manual=True)
            self._post(lambda: self._notify(text))

        threading.Thread(target=run, name="actualizar", daemon=True).start()

    def _install_from_menu(self) -> None:
        update = self._update
        if update is None:
            return
        self._notify(t(MSG_UPDATE_DOWNLOADING, version=update.version))

        def run() -> None:
            try:
                self.install_update()
            except actualizar.UpdateError as exc:
                text = str(exc)
                self._post(lambda: self._notify(text))

        threading.Thread(target=run, name="actualizar", daemon=True).start()

    def _update_due(self) -> bool:
        last = self.config.updates_last_check
        return last is None or not 0 <= time.time() - last < _UPDATE_EVERY

    def _updates_loop(self) -> None:
        """Hilo «actualizaciones»: con el «sí» del usuario, comprueba una vez al día."""
        delay = _UPDATE_FIRST_DELAY
        while True:
            self._update_wake.wait(delay)
            self._update_wake.clear()
            if self._closing:
                return
            if self.config.updates is True and self._can_update() and self._update_due():
                self.check_updates(manual=False)
            delay = _UPDATE_TICK

    def quit(self) -> None:
        self._closing = True
        self._update_wake.set()
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
                label = title if entry.builtin else t("bandeja.tema_tuyo", title=title)
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

        def pick(model, port, advanced):
            return self._safe(lambda: self._pick(model, port, advanced=advanced))

        def is_current(model, port):
            return lambda _item: self._is_current(model, port)

        def choice_items():
            # Lista cerrada: solo modelos con driver cuyo USB coincide con el del puerto.
            # Un puerto sin USB (Bluetooth, módem…) no sale aquí; solo en «Modo avanzado».
            offered = False
            for port in self._ports():
                for model in compatible_models(port):
                    offered = True
                    yield Item(
                        f"{port.device}: {model.menu_label}",
                        pick(model, port, False),
                        checked=is_current(model, port),
                    )
                unsupported = unsupported_line(port)
                if unsupported is not None:
                    offered = True
                    yield Item(unsupported, None, enabled=False)
            if not offered:
                yield Item(t("bandeja.menu_sin_compatibles"), None, enabled=False)

        def advanced_models(port):
            return Menu(*[Item(m.menu_label, pick(m, port, True)) for m in ADVANCED])

        def advanced_items():
            # 🔴 Una rev C no sale ni aquí (R5-02): es el único protocolo de la familia con
            # órdenes que borran, y un tema podría formarlas con sus píxeles si se le
            # mandara el protocolo de otro modelo. Ya sale, deshabilitada, en la lista.
            # Tampoco una WeAct: su protocolo no tiene marco (revisor de la fase 5).
            ports = [p for p in self._ports() if not is_own_usb_only(p)]
            if not ports:
                yield Item(t("bandeja.menu_sin_puertos"), None, enabled=False)
            for port in ports:
                yield Item(port.label, advanced_models(port))

        display_menu = Menu(
            Item(lambda _item: self._display_text(), None, enabled=False),
            Menu.SEPARATOR,
            Item(t("bandeja.menu_elegir"), Menu(choice_items)),
            Item(t("bandeja.menu_avanzado"), Menu(advanced_items)),
            Item(
                t("bandeja.menu_volver_auto"),
                self._safe(self._back_to_auto),
                visible=lambda _item: self.config.display is not None or self._trial is not None,
            ),
        )

        language_items = [
            Item(
                idioma.t("idioma.automatico"),
                self._safe(lambda: self._set_language(None)),
                checked=lambda _item: self.config.language is None,
                radio=True,
            ),
            *(
                Item(
                    name,
                    self._safe(lambda c=code: self._set_language(c)),
                    checked=lambda _item, c=code: self.config.language == c,
                    radio=True,
                )
                for code, name in idioma.available().items()
            ),
        ]

        return Menu(
            # default=True: también se abre con un clic en el icono.
            Item(t("bandeja.menu_abrir_editor"), self._safe(self._open_editor), default=True),
            Menu.SEPARATOR,
            Item(
                lambda _item: self.service.message or t("bandeja.menu_arrancando"),
                None,
                enabled=False,
            ),
            # Solo cuando hay una pantalla con el USB de la rev A pero sin su número de serie:
            # podría ser otro modelo, así que no se le envía nada hasta que el usuario diga.
            # Kipye: igual, pero el modelo propuesto viene en ``pending_choice``.
            Item(
                lambda _item: self._confirm_label(),
                self._safe(self._confirm),
                visible=lambda _item: self._pending() is not None,
            ),
            Item(
                t("bandeja.menu_reintentar"),
                self._safe(self._retry),
                visible=lambda _item: self.service.status is Status.ERROR,
            ),
            Menu.SEPARATOR,
            Item(t("bandeja.menu_pantalla"), display_menu),
            Item(t("bandeja.menu_tema"), Menu(theme_items)),
            Item(
                t("bandeja.menu_importar"),
                self._safe(self._import_theme),
                enabled=lambda _item: not self._importing,
            ),
            Item(t("bandeja.menu_brillo"), Menu(brightness_items)),
            Item(
                t("bandeja.menu_girar"),
                self._safe(lambda: self._change(flip=not self.config.flip)),
                checked=lambda _item: self.config.flip,
            ),
            Item(idioma.t("idioma.menu"), Menu(*language_items)),
            Menu.SEPARATOR,
            Item(
                t("bandeja.menu_iniciar_con_windows"),
                self._safe(self._toggle_autostart),
                checked=lambda _item: self._autostart_enabled(),
            ),
            Item(
                t("bandeja.menu_acceso_escritorio"),
                self._safe(self._create_shortcut),
                visible=lambda _item: not self._store,
            ),
            Item(t("bandeja.menu_carpeta_temas"), self._safe(self._open_themes_folder)),
            Menu.SEPARATOR,
            Item(
                lambda _item: self._update_label(),
                self._safe(self._install_from_menu),
                visible=lambda _item: self._update is not None,
                enabled=lambda _item: not self._update_busy,
            ),
            Item(
                lambda _item: self._module_label(),
                self._safe(self._open_module_page),
                visible=lambda _item: self._module is not None and self._can_update(),
            ),
            Item(
                t("bandeja.menu_buscar_actualizaciones"),
                self._safe(self._check_from_menu),
                visible=lambda _item: self._can_update(),
                enabled=lambda _item: not self._update_busy,
            ),
            Item(
                t("bandeja.menu_avisar_versiones"),
                self._safe(lambda: self._set_updates(self.config.updates is not True)),
                checked=lambda _item: self.config.updates is True,
                visible=lambda _item: self._can_update(),
            ),
            Menu.SEPARATOR,
            Item(f"Luzeira {__version__}", None, enabled=False),
            Item(t("bandeja.menu_salir"), lambda: self.quit()),
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
        if actualizar.is_elevated(unknown=False):
            # «Ejecutar como administrador» funciona, pero no hace falta y todo lo que
            # abra (el editor, los temas) correría con permisos de más (C-77.5).
            log.warning("Luzeira se está ejecutando como administrador.")
            self._notify(t("bandeja.aviso_elevada"))
        self._start_service()
        threading.Thread(target=self._updates_loop, name="actualizaciones", daemon=True).start()
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
        # El acceso directo del escritorio pide abrir el editor aunque Luzeira ya esté
        # abierta: otra copia señala el aviso y aquí se abre (en el hilo del icono).
        requests = EditorRequests()
        requests.listen(self._editor_requested)
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
            requests.stop()


def _already_running_message() -> None:
    """Avisar de que ya hay una Luzeira abierta (con doble clic no pasaba nada)."""
    # Esta copia no ha abierto bandeja: el idioma se lee aquí, para que salga en el suyo.
    with contextlib.suppress(Exception):
        idioma.set_language(load_config().language)
    with contextlib.suppress(AttributeError, OSError):  # fuera de Windows no hay MessageBox
        ctypes.windll.user32.MessageBoxW(
            None,
            t("bandeja.ya_abierta"),
            "Luzeira",
            0x40,  # MB_ICONINFORMATION
        )


def main(open_editor: bool = False) -> int:
    setup_logging(default_log_dir())
    with SingleInstance(APP_MUTEX) as instance:
        if not instance.acquired:
            # Otra Luzeira ya está en la bandeja: no abrir dos sobre el mismo puerto.
            log.info("Luzeira ya estaba abierta; esta instancia termina.")
            # Con --abrir-editor (acceso del escritorio) se le pide que abra el editor; si
            # no puede recibir el aviso, se dice que ya está abierta, como siempre.
            if not (open_editor and EditorRequests().request()):
                _already_running_message()
            return 0
        migrate_old_name()  # «Visoria» → «Luzeira» (D-14)
        config_dir().mkdir(parents=True, exist_ok=True)
        clean_import_leftovers()
        actualizar.clean_downloads()  # lo que quedó de una actualización ya instalada
        TrayApp(load_config(), open_editor=open_editor).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
