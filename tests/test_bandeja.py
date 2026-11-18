"""Bandeja: lo que se puede probar sin abrir el icono ni la pantalla."""

import pystray
import pytest

from luzeira import bandeja
from luzeira.bandeja import WM_QUERYENDSESSION, allow_shutdown, clip, make_icon
from luzeira.config import DEFAULT_THEME, AppConfig
from luzeira.servicio import Status


@pytest.fixture(autouse=True)
def free_tray_window_classes(monkeypatch):
    """pystray registra una clase de ventana llamada con ``id(icono)`` y solo la libera al
    parar un icono que se llegó a ejecutar. En los tests nunca se ejecutan: si Python reusa
    la dirección de un icono muerto para el siguiente, el nombre choca («Esta clase ya
    existe», intermitente). Se guardan vivos durante el test y se libera su clase al final.
    En la app no pasa: hay un solo icono."""
    created = []
    real_init = pystray.Icon.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(pystray.Icon, "__init__", init)
    yield
    for icon in created:
        if getattr(icon, "_atom", None):
            icon._unregister_class(icon._atom)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(bandeja, "save_config", lambda _c: None)
    monkeypatch.setattr(bandeja.autoarranque, "is_enabled", lambda: False)
    tray = bandeja.TrayApp(AppConfig())
    notes = []
    monkeypatch.setattr(tray, "_notify", lambda text, title="Luzeira": notes.append(text))
    tray.notes = notes
    return tray


def walk(menu):
    for item in menu.items:
        _ = item.text, item.checked, item.visible, item.enabled
        if item.submenu:
            yield from walk(item.submenu)
        yield item


def test_pystray_private_handlers_still_exist():
    # Si pystray cambia y desaparece ``_message_handlers``, su ventana oculta volvería a
    # bloquear el apagado de Windows. Este test avisa antes de que pase en casa de alguien.
    icon = pystray.Icon("luzeira-test", make_icon())
    assert isinstance(getattr(icon, "_message_handlers", None), dict)


def test_allow_shutdown_answers_true_to_query_end_session():
    icon = pystray.Icon("luzeira-test", make_icon())
    allow_shutdown(icon)
    assert icon._message_handlers[WM_QUERYENDSESSION](0, 0) == 1


def test_tray_app_wires_shutdown_handler(app):
    # Revisor fase 4: quitar allow_shutdown del constructor dejaba todo en verde.
    assert app.icon._message_handlers[WM_QUERYENDSESSION](0, 0) == 1


def test_whole_menu_builds_and_every_item_can_be_evaluated(app, monkeypatch):
    # El primer arranque real falló al construir el menú (pystray rechaza funciones con
    # más de 2 argumentos). Este test recorre el menú entero como lo haría Windows.
    items = list(walk(app.icon.menu))
    labels = [i.text for i in items]
    assert "Tema" in labels and "Brillo" in labels and "Salir" in labels
    applied = []
    monkeypatch.setattr(app.service, "apply", lambda s, reload=False: applied.append(s))
    [fifty] = [i for i in items if i.text == "50 %"]
    fifty(app.icon)
    assert app.config.brightness == 50 and applied[-1].brightness == 50
    [fifty_again] = [i for i in walk(app.icon.menu) if i.text == "50 %"]
    assert fifty_again.checked


def test_long_messages_are_clipped_to_windows_limits():
    # Revisor fase 4: pystray lanza ValueError con más de 256 caracteres y el aviso no sale.
    assert len(clip("x" * 300, 255)) == 255
    assert clip("corto", 255) == "corto"


def test_notify_never_passes_windows_limit(monkeypatch):
    monkeypatch.setattr(bandeja, "save_config", lambda _c: None)
    tray = bandeja.TrayApp(AppConfig())
    sent = []
    monkeypatch.setattr(tray.icon, "notify", lambda text, title: sent.append((text, title)))
    tray._notify("e" * 400, "t" * 100)
    assert len(sent[0][0]) <= 255 and len(sent[0][1]) <= 63


@pytest.mark.parametrize(
    "status", [Status.NO_DISPLAY, Status.NEEDS_CONFIRMATION, Status.THEME_ERROR, Status.ERROR]
)
def test_problem_states_are_notified_once(app, status):
    app._show_status(status, "algo pasa")
    app._show_status(status, "algo pasa")
    assert app.notes == ["algo pasa"]


def test_normal_states_are_not_notified(app):
    app._show_status(Status.RUNNING, "Mostrando «esencial»")
    assert app.notes == []


def test_status_from_other_thread_goes_through_the_icon_thread(app):
    # Revisor fase 4: el menú se reconstruía desde el hilo de la pantalla.
    shown = []
    app._show_status = lambda s, m: shown.append(s)
    app._on_status(Status.RUNNING, "ok")
    assert shown == []  # todavía no: está en la cola del hilo del icono
    app._drain()
    assert shown == [Status.RUNNING]


def test_broken_theme_is_not_saved_nor_applied(app, monkeypatch):
    monkeypatch.setattr(bandeja, "is_theme_valid", lambda name: name != "roto")
    applied = []
    monkeypatch.setattr(app.service, "apply", lambda s, reload=False: applied.append(s))
    app._change(theme="roto")
    assert app.config.theme == DEFAULT_THEME and applied == []
    assert "roto" in app.notes[0]


def test_broken_saved_theme_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(bandeja, "is_theme_valid", lambda name: name == DEFAULT_THEME)
    tray = bandeja.TrayApp(AppConfig(theme="se-rompio"))
    assert tray.config.theme == DEFAULT_THEME
    assert "se-rompio" in tray._startup_notice


def test_retry_is_offered_only_after_an_error(app, monkeypatch):
    def retry_visible():
        return next(i for i in walk(app.icon.menu) if i.text == "Reintentar").visible

    assert not retry_visible()
    monkeypatch.setattr(type(app.service), "status", property(lambda _s: Status.ERROR))
    assert retry_visible()


def test_run_wires_power_and_session_to_the_current_service(app, monkeypatch):
    # Revisor fase 4: intercambiar suspend/resume o vaciar _on_end_session dejaba todo verde.
    captured = {}

    class FakePower:
        def __init__(self, on_suspend, on_resume):
            captured["power"] = (on_suspend, on_resume)

        def start(self):
            return True

        def stop(self):
            pass

    class FakeSession:
        def __init__(self, on_end_session):
            captured["session"] = on_end_session

        start, stop = FakePower.start, FakePower.stop

    calls = []

    class FakeService:
        status, message, pending_device = Status.RUNNING, "", None

        def suspend(self, timeout=1.5):
            calls.append("suspend")
            return True

        def resume(self):
            calls.append("resume")

        def stop(self):
            calls.append("stop")

    monkeypatch.setattr(bandeja, "PowerListener", FakePower)
    monkeypatch.setattr(bandeja, "SessionWatcher", FakeSession)
    monkeypatch.setattr(app.icon, "run", lambda setup=None: None)
    app.run()
    app.service = FakeService()  # como tras «Reintentar»: un servicio nuevo
    on_suspend, on_resume = captured["power"]
    on_suspend()
    on_resume()
    captured["session"]()
    assert calls == ["suspend", "resume", "suspend"]


def test_icon_changes_color_with_status():
    orange, red = make_icon((255, 140, 0)), make_icon((200, 50, 50))
    assert orange.getpixel((10, 32)) != red.getpixel((10, 32))


# --- editor (remate 2 de la fase 5) ---------------------------------------------------


def test_apply_from_editor_always_reloads_even_the_theme_on_screen(app, monkeypatch):
    applied = []
    monkeypatch.setattr(
        app.service, "apply", lambda s, reload=False: applied.append((s.theme_dir.name, reload))
    )
    app._apply_from_editor(DEFAULT_THEME)  # el mismo que ya está en pantalla
    app._drain()
    assert applied == [(DEFAULT_THEME, True)]
    # Desde el menú, el mismo tema no obliga a releerlo.
    app._change(theme=DEFAULT_THEME)
    assert applied[-1] == (DEFAULT_THEME, False)


def test_theme_applied_from_editor_counts_as_active_before_the_tray_processes_it(app, monkeypatch):
    # Carrera aplicar → borrar: el servidor no debe dejar borrar el tema recién aplicado.
    monkeypatch.setattr(app.service, "apply", lambda s, reload=False: None)
    monkeypatch.setattr(bandeja, "is_theme_valid", lambda name: True)
    app._apply_from_editor("mio")
    assert app.config.theme == DEFAULT_THEME  # la bandeja aún no lo ha procesado
    assert app._active_theme() == "mio"
    app._drain()
    assert app.config.theme == "mio" and app._active_theme() == "mio"


def test_rejected_editor_theme_stops_counting_as_active(app, monkeypatch):
    monkeypatch.setattr(app.service, "apply", lambda s, reload=False: None)
    monkeypatch.setattr(bandeja, "is_theme_valid", lambda name: name != "roto")
    app._apply_from_editor("roto")
    assert app._active_theme() == "roto"
    app._drain()
    assert app._active_theme() == DEFAULT_THEME


def test_missing_webview2_is_explained_to_the_user(app):
    from luzeira.editor.ventana import EXIT_NO_WEBVIEW2

    app._on_editor_exit(0)
    app._on_editor_exit(EXIT_NO_WEBVIEW2)
    assert app.notes == []  # llega por el hilo del icono
    app._drain()
    assert app.notes == [bandeja.MSG_NO_WEBVIEW2]
    assert "WebView2" in app.notes[0]


def test_editor_link_reports_window_exit_to_the_tray(app):
    # Que el aviso de WebView2 esté conectado de verdad (no solo el método suelto).
    assert app.editor._on_window_exit == app._on_editor_exit


def test_clicking_the_icon_with_the_editor_open_brings_it_to_front(app, monkeypatch):
    monkeypatch.setattr(bandeja.EditorLink, "is_built", staticmethod(lambda: True))
    monkeypatch.setattr(app.editor, "open_window", lambda: False)  # ya estaba abierto
    fronted = []
    monkeypatch.setattr(app.editor.window, "bring_to_front", lambda: fronted.append(1) or True)
    app._open_editor()
    assert fronted == [1] and app.notes == []
    monkeypatch.setattr(app.editor.window, "bring_to_front", lambda: False)
    app._open_editor()
    assert app.notes == ["El editor ya está abierto."]


@pytest.mark.parametrize("open_editor", [True, False])
def test_editor_opens_on_start_only_when_asked(monkeypatch, open_editor):
    # El instalador, al terminar, arranca con --abrir-editor; el autoarranque, no.
    monkeypatch.setattr(bandeja.autoarranque, "is_enabled", lambda: False)
    tray = bandeja.TrayApp(AppConfig(), open_editor=open_editor)
    opened = []
    monkeypatch.setattr(tray, "_start_service", lambda: None)
    monkeypatch.setattr(tray, "_open_editor", lambda: opened.append(True))
    # El icono real se queda (soltarlo lo destruye a destiempo y su clase de ventana choca
    # con la del test siguiente: «Esta clase ya existe», intermitente). Solo se calla.
    monkeypatch.setattr(tray.icon, "update_menu", lambda: None)
    tray._setup(type("FakeIcon", (), {"visible": False})())
    assert opened == ([True] if open_editor else [])
