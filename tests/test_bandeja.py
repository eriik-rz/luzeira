"""Bandeja: lo que se puede probar sin abrir el icono ni la pantalla."""

from types import SimpleNamespace

import pystray
import pytest

from luzeira import bandeja
from luzeira.bandeja import WM_QUERYENDSESSION, allow_shutdown, clip, make_icon
from luzeira.config import DEFAULT_THEME, AppConfig, DisplaySetting
from luzeira.pantallas.modelos import DisplayChoice, PortInfo
from luzeira.servicio import DisplayInfo, Status


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


# Puertos falsos para el menú «Pantalla» (nunca se lee el equipo de verdad en los tests).
REV_A_PORT = PortInfo("COM3", 0x1A86, 0x5722, "USB35INCHIPSV2", "Dispositivo serie USB (COM3)")
BT_PORT = PortInfo("COM4", description="Serie estándar sobre el vínculo Bluetooth (COM4)")
KIPYE_PORT = PortInfo("COM8", 0x454D, 0x4E41, "", "Dispositivo serie USB (COM8)")
REV_C_PORT = PortInfo("COM11", 0x1A86, 0xCA21, "CT21INCH", "")
UNKNOWN_CH552 = PortInfo("COM9", 0x1A86, 0x5722, "", "Dispositivo serie USB (COM9)")
PORTS = [REV_A_PORT, BT_PORT, KIPYE_PORT, REV_C_PORT]


@pytest.fixture
def app(monkeypatch):
    saved = []
    monkeypatch.setattr(bandeja, "save_config", saved.append)
    monkeypatch.setattr(bandeja.autoarranque, "is_enabled", lambda: False)
    monkeypatch.setattr(bandeja.autoarranque, "blocked_by_user", lambda: False)
    monkeypatch.setattr(bandeja, "list_ports", lambda: list(PORTS))
    tray = bandeja.TrayApp(AppConfig())
    notes = []
    monkeypatch.setattr(tray, "_notify", lambda text, title="Luzeira": notes.append(text))
    tray.notes = notes
    tray.saved = saved
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


# --- menú «Pantalla» (C-44) -----------------------------------------------------------


def submenu(app, *path):
    menu = app.icon.menu
    for title in path:
        menu = next(i for i in menu.items if i.text == title).submenu
    return list(menu.items)


@pytest.fixture
def chosen(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.service, "choose_display", calls.append)
    return calls


@pytest.fixture
def answers(monkeypatch):
    """Respuestas del diálogo Sí/No; apunta cada pregunta (sin abrir nada de verdad)."""
    from luzeira.sistema import dialogos

    asked = []
    replies = []

    def confirm(title, text, *, warning=False, default_no=False):
        asked.append((title, text, warning))
        return replies.pop(0) if replies else False

    monkeypatch.setattr(dialogos, "confirm", confirm)
    return SimpleNamespace(asked=asked, replies=replies)


def set_info(monkeypatch, app, info):
    monkeypatch.setattr(type(app.service), "display_info", property(lambda _s: info))


def test_display_menu_lists_only_compatible_models_per_port(app):
    labels = [i.text for i in submenu(app, "Pantalla", "Elegir modelo y puerto")]
    assert labels == [
        'COM3: Turing / UsbPCMonitor 3,5"',  # su serie la identifica: solo ese modelo
        'COM8: Kipye Qiye 3,5" (beta)',
        'COM11: Turing de 2,1", 5" u 8,8" (revisión C) — todavía no compatible',
    ]  # el Bluetooth (COM4) no sale: no tiene USB
    rev_c = submenu(app, "Pantalla", "Elegir modelo y puerto")[-1]
    assert not rev_c.enabled


def test_display_menu_offers_both_models_only_when_the_serial_does_not_decide(app, monkeypatch):
    monkeypatch.setattr(bandeja, "list_ports", lambda: [REV_A_PORT, UNKNOWN_CH552])
    app._ports_cache = None
    labels = [i.text for i in submenu(app, "Pantalla", "Elegir modelo y puerto")]
    assert labels == [
        'COM3: Turing / UsbPCMonitor 3,5"',
        'COM9: Turing / UsbPCMonitor 3,5"',
        'COM9: XuanFang 3,5" (también «flagship») (beta)',
    ]


def test_advanced_mode_offers_every_port_and_every_model(app):
    ports = submenu(app, "Pantalla", "Modo avanzado (cualquier puerto)")
    assert [p.text for p in ports] == [
        "Dispositivo serie USB (COM3)",
        "Serie estándar sobre el vínculo Bluetooth (COM4)",
        "Dispositivo serie USB (COM8)",
        "COM11",
    ]
    models = [m.text for m in ports[1].submenu.items]
    assert len(models) == 3 and all("revisión C" not in m for m in models)


def test_display_menu_says_what_is_connected(app, monkeypatch):
    [status] = [i.text for i in submenu(app, "Pantalla")[:1]]
    assert status == "Ninguna pantalla conectada"
    set_info(monkeypatch, app, DisplayInfo("xuanfang-rev-b", "COM6"))
    assert (
        submenu(app, "Pantalla")[0].text
        == 'Conectada: XuanFang 3,5" (también «flagship») (beta) en COM6'
    )


def test_rev_a_from_the_list_is_tried_without_a_dialog(app, chosen, answers):
    rev_a = submenu(app, "Pantalla", "Elegir modelo y puerto")[0]
    rev_a(app.icon)
    assert answers.asked == []
    assert chosen == [DisplayChoice("turing-rev-a", "COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")]


def test_beta_model_asks_first_and_no_means_nothing(app, chosen, answers, monkeypatch):
    monkeypatch.setattr(bandeja, "list_ports", lambda: [UNKNOWN_CH552])
    app._ports_cache = None
    rev_b = submenu(app, "Pantalla", "Elegir modelo y puerto")[1]
    rev_b(app.icon)  # responde «No»
    assert chosen == [] and len(answers.asked) == 1
    title, text, warning = answers.asked[0]
    assert "beta" in title and "beta" in text and "COM9" in text and not warning
    assert "saludo" in text  # la rev B sí tiene saludo: la promesa es cierta
    answers.replies.append(True)
    rev_b(app.icon)
    assert [c.model for c in chosen] == ["xuanfang-rev-b"] and not chosen[0].advanced


def test_beta_model_without_hello_does_not_promise_a_check(app, chosen, answers):
    # Remate C-44: la Kipye no tiene saludo; su prueba (un brillo) no puede fallar.
    kipye = submenu(app, "Pantalla", "Elegir modelo y puerto")[1]
    kipye(app.icon)
    _title, text, _warning = answers.asked[0]
    assert "saludo. Si no contesta" not in text and "cerrará el puerto" not in text
    assert "no puede comprobar" in text and "Volver a detectarla sola" in text
    assert "rayas" in text


def test_advanced_mode_warns_and_marks_the_choice(app, chosen, answers):
    bt_rev_a = submenu(app, "Pantalla", "Modo avanzado (cualquier puerto)")[1].submenu.items[0]
    bt_rev_a(app.icon)  # «No» (también es lo que pasa si se pulsa Intro)
    assert chosen == []
    title, text, warning = answers.asked[0]
    assert warning and "avanzado" in title.lower() and "COM4" in text and "Cuidado" in text
    answers.replies.append(True)
    bt_rev_a(app.icon)
    assert chosen == [DisplayChoice("turing-rev-a", "COM4", None, None, "", advanced=True)]


def test_choice_is_remembered_only_once_it_shows_the_theme(app, chosen, monkeypatch):
    choice = DisplayChoice("xuanfang-rev-b", "COM6", 0x1A86, 0x5722, "2017-2-25")
    app._try_choice(choice)
    set_info(monkeypatch, app, None)
    app._show_status(Status.STARTING, "Pantalla encontrada…")
    assert app.saved == []
    set_info(monkeypatch, app, DisplayInfo("xuanfang-rev-b", "COM6", choice=choice))
    app._show_status(Status.RUNNING, "Mostrando el tema «Esencial».")
    assert app.config.display == DisplaySetting(
        model="xuanfang-rev-b", device="COM6", vid=0x1A86, pid=0x5722, serial="2017-2-25"
    )
    assert app.saved[-1].display == app.config.display
    assert any("Lo recordaré" in n for n in app.notes)
    app._show_status(Status.RUNNING, "otra vez")
    assert len(app.saved) == 1  # se guarda una vez, no en cada estado


def test_failed_choice_is_not_remembered(app, chosen, monkeypatch):
    app._try_choice(DisplayChoice("xuanfang-rev-b", "COM3", 0x1A86, 0x5722, "x"))
    app._show_status(Status.NEEDS_CONFIRMATION, "no parece ser ese modelo")
    set_info(monkeypatch, app, DisplayInfo("xuanfang-rev-b", "COM3"))
    app._show_status(Status.RUNNING, "Mostrando…")  # p. ej. otra cosa más tarde
    assert app.saved == [] and app.config.display is None


def test_running_with_another_display_does_not_save_the_trial(app, chosen, monkeypatch):
    app._try_choice(DisplayChoice("xuanfang-rev-b", "COM3", 0x1A86, 0x5722, "x"))
    set_info(monkeypatch, app, DisplayInfo("turing-rev-a", "COM3"))  # aún la de antes
    app._show_status(Status.RUNNING, "Mostrando…")
    assert app.saved == []


def test_proposed_beta_model_is_confirmed_with_its_own_label(app, chosen, monkeypatch):
    kipye = DisplayChoice("kipye-rev-d", "COM8", 0x454D, 0x4E41)
    monkeypatch.setattr(type(app.service), "pending_device", property(lambda _s: "COM8"))
    monkeypatch.setattr(type(app.service), "pending_choice", property(lambda _s: kipye))
    [item] = [i for i in walk(app.icon.menu) if i.text.startswith("Sí, es mi")]
    assert item.text == 'Sí, es mi Kipye Qiye 3,5" (beta) (COM8): probarla'
    item(app.icon)
    assert chosen == [kipye] and app._trial == kipye


def test_plain_confirmation_still_confirms_rev_a(app, monkeypatch):
    confirmed = []
    monkeypatch.setattr(type(app.service), "pending_device", property(lambda _s: "COM9"))
    monkeypatch.setattr(app.service, "confirm_port", confirmed.append)
    [item] = [i for i in walk(app.icon.menu) if i.text.startswith("Sí, es mi")]
    assert item.text == "Sí, es mi pantalla Turing / UsbPCMonitor (COM9)"
    item(app.icon)
    assert confirmed == ["COM9"]


def test_saved_choice_reaches_the_service(monkeypatch):
    monkeypatch.setattr(bandeja.autoarranque, "is_enabled", lambda: False)
    monkeypatch.setattr(bandeja, "list_ports", lambda: list(PORTS))
    setting = DisplaySetting(
        model="xuanfang-rev-b", device="COM6", vid=0x1A86, pid=0x5722, serial="2017-2-25"
    )
    tray = bandeja.TrayApp(AppConfig(display=setting))
    assert tray.service._choice == DisplayChoice(
        "xuanfang-rev-b", "COM6", 0x1A86, 0x5722, "2017-2-25"
    )


def test_back_to_auto_forgets_the_choice(app, chosen):
    back = next(i for i in submenu(app, "Pantalla") if i.text == "Volver a detectarla sola")
    assert not back.visible
    app.config = app.config.model_copy(
        update={"display": DisplaySetting(model="kipye-rev-d", device="COM8")}
    )
    assert back.visible
    back(app.icon)
    assert chosen == [None] and app.config.display is None and app.saved[-1].display is None


# --- remate C-44: elecciones cuya prueba no puede fallar ----------------------------------

KIPYE_CHOICE = DisplayChoice("kipye-rev-d", "COM8", 0x454D, 0x4E41)


def run_unverified_trial(app, monkeypatch, choice=KIPYE_CHOICE):
    app._try_choice(choice)
    set_info(monkeypatch, app, DisplayInfo(choice.model, choice.device, choice, verified=False))
    app._show_status(Status.RUNNING, "Mostrando el tema «Esencial».")


def test_unverified_choice_is_not_saved_without_asking(app, chosen, answers, monkeypatch):
    run_unverified_trial(app, monkeypatch)
    assert app.saved == [] and app.config.display is None
    assert answers.asked == []  # la pregunta va por la cola del icono, no a mitad del estado
    answers.replies.append(True)
    app._drain()
    [(title, text, _warning)] = answers.asked
    assert "se ve bien" in title.lower() and "¿Se ve bien el tema en la pantalla?" in text
    assert app.config.display == DisplaySetting(
        model="kipye-rev-d", device="COM8", vid=0x454D, pid=0x4E41
    )
    assert any("Lo recordaré" in n for n in app.notes)


def test_unverified_choice_that_looks_wrong_is_dropped(app, chosen, answers, monkeypatch):
    saved = DisplaySetting(model="turing-rev-a", device="COM3", vid=0x1A86, pid=0x5722)
    app.config = app.config.model_copy(update={"display": saved})
    run_unverified_trial(app, monkeypatch)
    app._drain()  # «No» (también al cerrar el diálogo o pulsar Intro)
    assert app.saved == [] and app.config.display == saved
    # Se deja de usar y se vuelve a lo que estaba guardado.
    assert chosen == [KIPYE_CHOICE, bandeja.choice_from_setting(saved)]
    assert bandeja.MSG_TRIAL_REJECTED in app.notes


def test_looks_right_question_defaults_to_no(app, monkeypatch):
    from luzeira.sistema import dialogos

    flags = []
    monkeypatch.setattr(dialogos, "confirm", lambda title, text, **kw: flags.append(kw) or False)
    monkeypatch.setattr(app.service, "choose_display", lambda _c: None)
    run_unverified_trial(app, monkeypatch)
    app._drain()
    assert flags == [{"default_no": True}]


def test_silent_rev_a_in_advanced_mode_is_asked_too(app, chosen, answers, monkeypatch):
    mute = DisplayChoice("turing-rev-a", "COM4", None, None, "", advanced=True)
    run_unverified_trial(app, monkeypatch, mute)
    app._drain()
    assert len(answers.asked) == 1 and app.saved == []


def test_answer_after_choosing_something_else_is_ignored(app, chosen, answers, monkeypatch):
    run_unverified_trial(app, monkeypatch)
    rev_a = DisplayChoice("turing-rev-a", "COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")

    def confirm(title, text, **_kw):
        app._try_choice(rev_a)  # mientras el diálogo está abierto, elige otra en el menú
        return True

    from luzeira.sistema import dialogos

    monkeypatch.setattr(dialogos, "confirm", confirm)
    app._drain()
    assert app.saved == [] and app._trial == rev_a


def test_choice_found_on_another_com_is_saved_with_the_new_one(app, chosen, monkeypatch):
    # Remate C-44: Windows ha movido la elegida de COM6 a COM10 (match_choice sigue su USB).
    choice = DisplayChoice("xuanfang-rev-b", "COM6", 0x1A86, 0x5722, "2017-2-25")
    app._try_choice(choice)
    set_info(monkeypatch, app, DisplayInfo("xuanfang-rev-b", "COM10", choice=choice))
    app._show_status(Status.RUNNING, "Mostrando…")
    assert app._trial is None
    assert app.config.display.device == "COM10" and app.config.display.serial == "2017-2-25"


def test_running_auto_display_does_not_resolve_the_trial(app, chosen, monkeypatch):
    # La misma pantalla y el mismo COM, pero abierta por la detección sola (choice=None):
    # no es la prueba de la elección; esa sigue pendiente.
    choice = DisplayChoice("turing-rev-a", "COM3", 0x1A86, 0x5722, "USB35INCHIPSV2")
    app._try_choice(choice)
    set_info(monkeypatch, app, DisplayInfo("turing-rev-a", "COM3"))
    app._show_status(Status.RUNNING, "Mostrando…")
    assert app.saved == [] and app._trial == choice


def test_menu_dialog_cannot_open_on_top_of_another(app, chosen, answers, monkeypatch):
    # Remate C-44: MessageBoxW sigue atendiendo el menú; un segundo clic no abre otro.
    from luzeira.sistema import dialogos

    kipye = submenu(app, "Pantalla", "Elegir modelo y puerto")[1]
    opened = []

    def confirm(title, text, **_kw):
        opened.append(title)
        kipye(app.icon)  # el usuario vuelve a pulsar mientras el primero está abierto
        return False

    monkeypatch.setattr(dialogos, "confirm", confirm)
    kipye(app.icon)
    assert len(opened) == 1 and chosen == []
    assert bandeja.MSG_DIALOG_OPEN in app.notes
    assert not app._dialog_open
    kipye(app.icon)  # cerrado el primero, se puede volver a abrir
    assert len(opened) == 2


def test_looks_right_question_waits_for_an_open_dialog(app, chosen, answers, monkeypatch):
    from luzeira.sistema import dialogos

    asked = []

    def confirm(title, text, **_kw):
        asked.append(title)
        if len(asked) == 1:
            app._ask_looks_right(KIPYE_CHOICE)  # llega mientras hay otro abierto
            assert asked == [bandeja.TITLE_ADVANCED]  # no se abre encima
        return False

    monkeypatch.setattr(dialogos, "confirm", confirm)
    advanced_kipye = submenu(app, "Pantalla", "Modo avanzado (cualquier puerto)")[0]
    advanced_kipye.submenu.items[2](app.icon)
    app._drain()
    assert asked == [bandeja.TITLE_ADVANCED, bandeja.TITLE_LOOKS_RIGHT]


@pytest.mark.parametrize(
    ("open_editor", "delivered", "message_shown"),
    [(True, True, False), (True, False, True), (False, False, True)],
)
def test_second_copy_asks_the_open_one_for_the_editor(
    monkeypatch, open_editor, delivered, message_shown
):
    # El acceso del escritorio (--abrir-editor) con Luzeira ya abierta: se le pide a la
    # abierta que abra el editor y no sale el aviso «ya está abierta». Sin la opción, o si
    # el aviso no llega, sale el de siempre.
    class Busy:
        acquired = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    asked, shown = [], []
    monkeypatch.setattr(bandeja, "setup_logging", lambda _d: None)
    monkeypatch.setattr(bandeja, "SingleInstance", lambda _n: Busy())
    monkeypatch.setattr(
        bandeja.EditorRequests, "request", lambda self: asked.append(True) or delivered
    )
    monkeypatch.setattr(bandeja, "_already_running_message", lambda: shown.append(True))
    assert bandeja.main(open_editor=open_editor) == 0
    assert bool(asked) == open_editor
    assert bool(shown) == message_shown


# --- ventana «Ajustes» del editor ----------------------------------------------------------


@pytest.fixture
def settings(app):
    return bandeja.EditorSettings(app)


def test_settings_snapshot_offers_the_same_closed_list_as_the_menu(app, settings):
    snap = settings.snapshot()
    menu = [i.text for i in submenu(app, "Pantalla", "Elegir modelo y puerto")]
    assert [c["label"] for c in snap["choices"]] == menu
    assert snap["display_auto"] and snap["display_text"] == "Ninguna pantalla conectada"
    assert snap["brightness_levels"] == [25, 50, 75, 100] and snap["autostart"] is False
    rev_a, kipye, rev_c = snap["choices"]
    assert rev_a["warning"] is None and not rev_a["beta"]
    assert "rayas" in kipye["warning"]  # el texto de la Kipye, sin saludo
    assert rev_c["id"] is None  # se ve, pero no se puede elegir
    assert len(snap["advanced"]) == 4 * 3  # 4 puertos x 3 modelos con driver
    assert all("Cuidado" in o["warning"] for o in snap["advanced"])
    assert all("action" not in o for o in snap["choices"] + snap["advanced"])


def test_settings_brightness_and_flip_go_through_the_icon_thread(app, settings, monkeypatch):
    applied = []
    monkeypatch.setattr(app.service, "apply", lambda s, reload=False: applied.append(s))
    settings.update(brightness=75, flip=True, autostart=None)
    assert applied == []  # todavía en la cola del icono
    app._drain()
    assert (applied[-1].brightness, applied[-1].flip) == (75, True)
    assert (app.saved[-1].brightness, app.saved[-1].flip) == (75, True)


def test_settings_rejects_a_brightness_outside_the_levels(app, settings):
    with pytest.raises(bandeja.EditorError) as exc:
        settings.update(brightness=60, flip=None, autostart=None)
    assert exc.value.status == 400


def test_settings_sets_autostart_to_the_asked_value(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(bandeja.autoarranque, "enable", lambda cmd: calls.append("on"))
    monkeypatch.setattr(bandeja.autoarranque, "disable", lambda: calls.append("off"))
    settings.update(brightness=None, flip=None, autostart=True)
    settings.update(brightness=None, flip=None, autostart=False)
    assert calls == ["on", "off"]


def test_settings_beta_choice_needs_confirmation(app, settings, chosen, answers):
    kipye = settings.snapshot()["choices"][1]
    with pytest.raises(bandeja.EditorError) as exc:
        settings.choose_display(kipye["id"], confirmed=False)
    assert exc.value.status == 400
    settings.choose_display(kipye["id"], confirmed=True)
    app._drain()
    assert [c.model for c in chosen] == ["kipye-rev-d"] and answers.asked == []


def test_settings_advanced_choice_is_marked_advanced(app, settings, chosen):
    bt = next(o for o in settings.snapshot()["advanced"] if o["id"].startswith("avanzado:COM4:"))
    settings.choose_display(bt["id"], confirmed=True)
    app._drain()
    assert chosen == [DisplayChoice("turing-rev-a", "COM4", None, None, "", advanced=True)]


@pytest.mark.parametrize("choice_id", ["lista:COM4:turing-rev-a", "lista:COM3:kipye-rev-d", "x"])
def test_settings_choice_not_offered_is_refused(app, settings, chosen, choice_id):
    # COM4 (Bluetooth) y la Kipye en COM3 no están en la lista cerrada.
    with pytest.raises(bandeja.EditorError):
        settings.choose_display(choice_id, confirmed=True)
    app._drain()
    assert chosen == []


def test_settings_choice_waits_for_an_open_dialog(app, settings, chosen):
    app._dialog_open = True
    with pytest.raises(bandeja.EditorError) as exc:
        settings.choose_display(None, confirmed=False)
    assert exc.value.status == 409


def test_settings_back_to_auto(app, settings, chosen):
    settings.choose_display(None, confirmed=False)
    app._drain()
    assert chosen == [None]


def test_settings_import_goes_to_the_user_themes(settings, monkeypatch, tmp_path):
    from luzeira import temas

    seen = []
    monkeypatch.setattr(bandeja, "user_themes_dir", lambda: tmp_path)
    monkeypatch.setattr(temas, "import_theme_zip", lambda z, d: seen.append(d) or d / "nuevo")
    assert settings.import_theme(tmp_path / "x.zip") == "nuevo"
    assert seen == [tmp_path]


def test_tray_passes_its_settings_to_the_editor(app):
    assert isinstance(app.editor._settings, bandeja.EditorSettings)


# --- «¿se ve bien?» dentro del editor ------------------------------------------------------


def editor_open(monkeypatch, app, value=True):
    monkeypatch.setattr(type(app.editor.window), "is_open", property(lambda _s: value))


def editor_trial(app, monkeypatch, settings):
    kipye = settings.snapshot()["choices"][1]
    settings.choose_display(kipye["id"], confirmed=True)
    app._drain()  # se prueba
    set_info(monkeypatch, app, DisplayInfo("kipye-rev-d", "COM8", KIPYE_CHOICE, verified=False))
    app._show_status(Status.RUNNING, "Mostrando el tema «Esencial».")
    app._drain()  # llega la pregunta


def test_question_from_the_editor_is_asked_in_the_editor(
    app, settings, chosen, answers, monkeypatch
):
    editor_open(monkeypatch, app)
    editor_trial(app, monkeypatch, settings)
    assert answers.asked == []  # ningún diálogo de Windows
    question = settings.question()
    assert question is not None and "¿Se ve bien el tema en la pantalla?" in question["text"]
    message = settings.answer(True)
    app._drain()
    assert "Lo recordaré" in message and settings.question() is None
    assert app.config.display is not None and app.config.display.model == "kipye-rev-d"
    assert app.notes == []  # el editor ya lo dice: sin aviso de Windows repetido


def test_question_from_the_editor_answered_no(app, settings, chosen, answers, monkeypatch):
    editor_open(monkeypatch, app)
    editor_trial(app, monkeypatch, settings)
    assert settings.answer(False) == bandeja.MSG_TRIAL_REJECTED
    app._drain()
    assert app.saved == [] and chosen[-1] is None and app.notes == []
    with pytest.raises(bandeja.EditorError):
        settings.answer(True)  # ya contestada


def test_question_goes_to_windows_if_the_editor_closes(app, settings, chosen, answers, monkeypatch):
    editor_open(monkeypatch, app)
    editor_trial(app, monkeypatch, settings)
    editor_open(monkeypatch, app, False)
    answers.replies.append(True)
    app._on_editor_exit(0)
    app._drain()
    assert len(answers.asked) == 1 and "se ve bien" in answers.asked[0][0].lower()
    assert app.config.display is not None and settings.question() is None


def test_choice_from_the_menu_still_asks_with_windows(app, chosen, answers, monkeypatch):
    editor_open(monkeypatch, app)  # aunque el editor esté abierto
    run_unverified_trial(app, monkeypatch)
    app._drain()
    assert len(answers.asked) == 1 and app.editor_question() is None


def test_question_from_the_editor_with_the_editor_closed_uses_windows(
    app, settings, chosen, answers, monkeypatch
):
    editor_open(monkeypatch, app, False)
    editor_trial(app, monkeypatch, settings)
    assert len(answers.asked) == 1 and settings.question() is None


# --- actualizaciones (ADR-006) ------------------------------------------------------------


def a_manifest(version="0.9.0"):
    return bandeja.actualizar.Manifest(
        app="luzeira",
        version=version,
        file=f"Luzeira-{version}-instalador.exe",
        size=1,
        sha256="0" * 64,
    )


@pytest.fixture
def with_keys(monkeypatch):
    monkeypatch.setattr(bandeja.actualizar, "PUBLIC_KEYS", ("clave",))


def menu_texts(app):
    return [i.text for i in walk(app.icon.menu) if i.visible]


def test_without_keys_there_is_no_update_menu_nor_question(app, settings, monkeypatch):
    monkeypatch.setattr(bandeja.actualizar, "PUBLIC_KEYS", ())
    assert not any("actualiza" in t.lower() for t in menu_texts(app))
    assert settings.updates_question() is False
    assert settings.snapshot()["updates"]["can_check"] is False


def test_the_question_is_asked_only_until_answered(app, settings, with_keys):
    assert app.config.updates is None and settings.updates_question() is True
    settings.update(brightness=None, flip=None, autostart=None, updates=False)
    app._drain()
    assert app.saved[-1].updates is False and settings.updates_question() is False


def one_loop_turn(app, monkeypatch) -> list:
    """Una vuelta del hilo «actualizaciones» (sin esperar de verdad) y fuera."""
    calls = []
    monkeypatch.setattr(bandeja.actualizar, "check", lambda **kw: calls.append(kw))
    waits = []

    def wait(timeout=None):
        waits.append(timeout)
        app._closing = len(waits) > 1
        return True

    monkeypatch.setattr(app._update_wake, "wait", wait)
    app._updates_loop()
    app._drain()
    return calls


@pytest.mark.parametrize("answer", [None, False])
def test_no_automatic_check_without_a_yes(app, with_keys, monkeypatch, answer):
    app.config = app.config.model_copy(update={"updates": answer})
    assert one_loop_turn(app, monkeypatch) == []


def test_automatic_check_with_a_yes_only_when_due(app, with_keys, monkeypatch):
    app.config = app.config.model_copy(update={"updates": True})
    assert len(one_loop_turn(app, monkeypatch)) == 1
    app.config = app.config.model_copy(update={"updates_last_check": bandeja.time.time()})
    assert one_loop_turn(app, monkeypatch) == []  # ya se miró hoy


def test_automatic_check_with_a_yes_notifies_once(app, with_keys, monkeypatch):
    app.config = app.config.model_copy(update={"updates": True})
    monkeypatch.setattr(bandeja.actualizar, "check", lambda **kw: a_manifest())
    assert "0.9.0" in app.check_updates(manual=False)
    app._drain()
    app.check_updates(manual=False)
    app._drain()
    assert app.notes == [bandeja.MSG_UPDATE_AVAILABLE.format(version="0.9.0")]
    assert app.saved[-1].updates_max_seen == "0.9.0" and app.saved[-1].updates_last_check
    assert "Actualizar a la versión 0.9.0" in menu_texts(app)
    assert app.updates_snapshot()["available"] == "0.9.0"


def test_check_passes_the_highest_version_seen(app, with_keys, monkeypatch):
    seen = []
    app.config = app.config.model_copy(update={"updates_max_seen": "0.5.0"})
    monkeypatch.setattr(bandeja.actualizar, "check", lambda **kw: seen.append(kw) or None)
    assert app.check_updates(manual=True) == bandeja.MSG_UP_TO_DATE.format(
        version=bandeja.__version__
    )
    assert seen == [{"max_seen": "0.5.0"}]


@pytest.mark.parametrize(
    "error", [bandeja.actualizar.UpdateError("No he podido conectar con GitHub."), ValueError()]
)
def test_a_failed_check_says_why_and_waits_a_day(app, with_keys, monkeypatch, error):
    def fail(**kw):
        raise error

    monkeypatch.setattr(bandeja.actualizar, "check", fail)
    text = app.check_updates(manual=True)
    assert text == (str(error) or bandeja.MSG_UPDATE_FAILED)
    app._drain()
    assert app._update is None and not app._update_busy
    # Cuenta como la comprobación del día: sin internet no se reintenta cada hora.
    assert app.saved[-1].updates_last_check and not app._update_due()
    assert app.saved[-1].updates_max_seen is None


def test_only_one_check_or_download_at_a_time(app, with_keys):
    assert app._claim_update()
    assert app.check_updates(manual=True) == bandeja.MSG_UPDATE_BUSY
    app._update = a_manifest()
    with pytest.raises(bandeja.actualizar.UpdateError, match="Espera"):
        app.install_update()


def test_install_downloads_then_launches_and_quits(app, with_keys, monkeypatch, tmp_path):
    app._update = a_manifest()
    order = []
    exe = tmp_path / "Luzeira-0.9.0-instalador.exe"
    monkeypatch.setattr(
        bandeja.actualizar, "download", lambda m: order.append(("download", m.version)) or exe
    )
    monkeypatch.setattr(
        bandeja.actualizar, "launch", lambda path, m, log: order.append(("launch", path))
    )
    monkeypatch.setattr(app, "quit", lambda: order.append(("quit",)))
    text = app.install_update()
    assert "0.9.0" in text and order == [("download", "0.9.0")]  # lanzar va por la cola
    app._drain()
    assert order == [("download", "0.9.0"), ("launch", exe), ("quit",)]


def test_install_that_fails_to_verify_does_not_quit(app, with_keys, monkeypatch, tmp_path):
    app._update = a_manifest()
    monkeypatch.setattr(bandeja.actualizar, "download", lambda m: tmp_path / "x.exe")

    def bad(path, m, log):
        raise bandeja.actualizar.UpdateError("El instalador descargado no coincide.")

    monkeypatch.setattr(bandeja.actualizar, "launch", bad)
    quits = []
    monkeypatch.setattr(app, "quit", lambda: quits.append(1))
    app.install_update()
    app._drain()
    assert quits == [] and app.notes == ["El instalador descargado no coincide."]


def test_editor_install_without_an_update_is_409(settings, with_keys):
    with pytest.raises(bandeja.EditorError) as exc:
        settings.install_update()
    assert exc.value.status == 409


def test_a_failed_download_frees_the_lock(app, with_keys, monkeypatch):
    app._update = a_manifest()

    def fail(m):
        raise bandeja.actualizar.UpdateError("La descarga es más grande de lo esperado.")

    monkeypatch.setattr(bandeja.actualizar, "download", fail)
    with pytest.raises(bandeja.actualizar.UpdateError):
        app.install_update()
    assert not app._update_busy


def test_after_a_good_download_nothing_else_until_launch(app, with_keys, monkeypatch, tmp_path):
    app._update = a_manifest()
    monkeypatch.setattr(bandeja.actualizar, "download", lambda m: tmp_path / "x.exe")

    def bad(path, m, log):
        raise bandeja.actualizar.UpdateError("No coincide.")

    monkeypatch.setattr(bandeja.actualizar, "launch", bad)
    app.install_update()
    assert app._update_busy  # ni otra descarga ni otro «Actualizar» mientras tanto
    assert app.check_updates(manual=True) == bandeja.MSG_UPDATE_BUSY
    app._drain()  # al lanzar falla: se suelta
    assert not app._update_busy


def test_the_editor_offers_an_update_only_when_ready(app, settings, with_keys):
    assert settings.update_available() is None
    app._update = a_manifest()
    assert settings.update_available() == "0.9.0"
    assert app._claim_update()  # descargando: no se ofrece otra vez
    assert settings.update_available() is None


def test_the_menu_shows_the_installed_version(app):
    assert f"Luzeira {bandeja.__version__}" in menu_texts(app)


def test_the_notice_points_to_the_editor():
    assert "editor" in bandeja.MSG_UPDATE_AVAILABLE.format(version="0.9.0")


# --- versión de Microsoft Store (paquete MSIX, C-52) ------------------------------------


@pytest.fixture
def store_app(app):
    app._store = True
    return app


def test_store_has_no_updater_even_with_keys(store_app, settings, with_keys, monkeypatch):
    assert not any("actualiza" in t.lower() for t in menu_texts(store_app))
    assert settings.updates_question() is False
    assert settings.snapshot()["updates"]["can_check"] is False
    assert settings.check_updates() == bandeja.MSG_STORE_UPDATES
    store_app.config = store_app.config.model_copy(update={"updates": True})
    assert one_loop_turn(store_app, monkeypatch) == []  # nunca se conecta


class FakeStartupTask:
    """La tarea de inicio del paquete (StartupTask) con su estado en un número."""

    def __init__(self, raw: int = 0, *, user_blocks: bool = False) -> None:
        self.raw, self.user_blocks = raw, user_blocks

    def state(self) -> int:
        return self.raw

    def enable(self) -> int:
        self.raw = 1 if self.user_blocks else 2
        return self.raw

    def disable(self) -> int:
        self.raw = 1 if self.user_blocks else 0
        return self.raw


@pytest.fixture
def startup_task(monkeypatch):
    task = FakeStartupTask()
    monkeypatch.setattr(bandeja.tarea_inicio, "_WinRTTask", lambda: task)
    return task


def test_store_autostart_uses_the_package_startup_task(
    store_app, settings, startup_task, monkeypatch
):
    monkeypatch.setattr(bandeja.autoarranque, "enable", lambda cmd: pytest.fail("clave Run"))
    item = next(i for i in walk(store_app.icon.menu) if i.text == "Iniciar con Windows")
    assert not item.checked
    item(store_app.icon)
    assert startup_task.raw == 2 and item.checked
    snap = settings.snapshot()
    assert snap["store"] is True and snap["autostart"] is True
    assert snap["autostart_blocked"] is False
    settings.update(brightness=None, flip=None, autostart=False)
    assert startup_task.raw == 0 and settings.snapshot()["autostart"] is False


def test_store_autostart_removed_by_the_user_can_only_come_back_in_windows(
    store_app, settings, startup_task, monkeypatch
):
    opened = []
    monkeypatch.setattr(bandeja.os, "startfile", opened.append, raising=False)
    startup_task.raw, startup_task.user_blocks = 1, True  # DisabledByUser
    snap = settings.snapshot()
    assert snap["autostart"] is False and snap["autostart_blocked"] is True
    with pytest.raises(bandeja.EditorError) as exc:
        settings.update(brightness=None, flip=None, autostart=True)
    assert exc.value.status == 409 and "Configuración" in str(exc.value)
    item = next(i for i in walk(store_app.icon.menu) if i.text == "Iniciar con Windows")
    item(store_app.icon)
    assert store_app.notes[-1] == bandeja.MSG_AUTOSTART_BLOCKED
    assert opened == ["ms-settings:startupapps"]
    settings.open_startup_settings()
    assert opened == ["ms-settings:startupapps"] * 2


def test_store_autostart_that_cannot_be_read_shows_off_and_fails_softly(
    store_app, settings, monkeypatch
):
    def broken():
        raise OSError("No se ha encontrado el elemento.")

    monkeypatch.setattr(bandeja.tarea_inicio, "_WinRTTask", broken)
    assert settings.snapshot()["autostart"] is False
    with pytest.raises(bandeja.EditorError) as exc:
        settings.update(brightness=None, flip=None, autostart=True)
    assert exc.value.status == 500


def test_autostart_removed_in_the_task_manager_is_shown_as_blocked(app, settings, monkeypatch):
    # C-24: el valor de Run sigue ahí, pero Windows no lo lanza.
    monkeypatch.setattr(bandeja.autoarranque, "is_enabled", lambda: True)
    monkeypatch.setattr(bandeja.autoarranque, "blocked_by_user", lambda: True)
    monkeypatch.setattr(bandeja.autoarranque, "enable", lambda cmd: None)
    monkeypatch.setattr(bandeja.os, "startfile", lambda uri: None, raising=False)
    snap = settings.snapshot()
    assert snap["autostart"] is False and snap["autostart_blocked"] is True
    with pytest.raises(bandeja.EditorError) as exc:
        settings.update(brightness=None, flip=None, autostart=True)
    assert exc.value.status == 409


def test_store_has_no_desktop_shortcut(store_app, settings, monkeypatch):
    import luzeira.sistema.accesos as accesos

    monkeypatch.setattr(accesos, "create_shortcut", lambda folder: pytest.fail("acceso"))
    assert "Crear acceso directo en el escritorio" not in menu_texts(store_app)
    with pytest.raises(bandeja.EditorError) as exc:
        settings.create_shortcut()
    assert exc.value.status == 409


def test_the_normal_version_keeps_its_menu(app, settings, with_keys):
    texts = menu_texts(app)
    assert "Iniciar con Windows" in texts
    assert "Crear acceso directo en el escritorio" in texts
    assert "Buscar actualizaciones ahora" in texts
    assert settings.snapshot()["store"] is False


# --- C-62: idioma ---------------------------------------------------------------------------


def test_settings_offer_the_languages_and_change_it_on_the_icon_thread(app, settings, monkeypatch):
    applied = []
    monkeypatch.setattr(app.service, "apply", lambda s, reload=False: applied.append(reload))
    snap = settings.snapshot()
    assert snap["language"] == "auto"
    assert {"code": "en", "name": "English"} in snap["languages"]
    assert "Idioma" in menu_texts(app)
    settings.update(brightness=None, flip=None, autostart=None, language="en")
    app._drain()
    assert idioma_current() == "en" and app.saved[-1].language == "en"
    assert applied == [True]  # el tema otra vez: fecha y etiquetas en el idioma nuevo
    assert "Language" in menu_texts(app) and "Idioma" not in menu_texts(app)
    settings.update(brightness=None, flip=None, autostart=None, language="auto")
    app._drain()
    assert app.saved[-1].language is None and idioma_current() == "es"  # el de «Windows»


def test_settings_refuse_a_language_that_is_not_there(settings):
    with pytest.raises(bandeja.EditorError) as exc:
        settings.update(brightness=None, flip=None, autostart=None, language="xx")
    assert exc.value.status == 400


def idioma_current() -> str:
    from luzeira import idioma

    return idioma.current()
