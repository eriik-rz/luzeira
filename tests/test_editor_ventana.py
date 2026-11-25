"""Ventana del editor: se prueba sin abrir ninguna ventana de verdad."""

import io
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from luzeira.editor import ventana


class FakeProcess:
    """``subprocess.Popen`` falso. Termina cuando se le dice (``finish``) o al matarlo;
    ``exits_on`` = línea de stdin que lo hace terminar solo (como la ventana con «cerrar»)."""

    pid = 4242

    def __init__(self, command, stdin=None, close_fds=None, exits_on=None):
        self.command = command
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None  # para poder leer lo escrito después
        self.exits_on = exits_on
        self.terminated = False
        self.code = None
        self._done = threading.Event()
        real_write = self.stdin.write

        def write(data):
            n = real_write(data)
            if exits_on is not None and data.strip() == exits_on.encode():
                self.finish(0)
            return n

        self.stdin.write = write

    def lines(self):
        return self.stdin.getvalue().decode().splitlines()

    def finish(self, code):
        self.code = code
        self._done.set()

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise ventana.subprocess.TimeoutExpired("luzeira", timeout)
        return self.code

    def terminate(self):
        self.terminated = True
        self.finish(1)


def _launcher(monkeypatch, **kwargs):
    launched = []
    monkeypatch.setattr(
        ventana.subprocess,
        "Popen",
        lambda *a, **k: launched.append(FakeProcess(*a, **k, **kwargs)) or launched[-1],
    )
    return launched


def _wait_for(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "no se cumplió a tiempo"
        time.sleep(0.005)


def test_token_goes_through_stdin_never_the_command_line(monkeypatch):
    launched = _launcher(monkeypatch)
    window = ventana.EditorWindow()
    assert window.open("http://127.0.0.1:5000/", "secreto-123")
    proc = launched[0]
    assert "secreto-123" not in " ".join(proc.command)
    assert proc.command[-1] == "editor-ventana"
    sent = json.loads(proc.lines()[0])
    # Con el idioma activo, para el título y los avisos de la ventana (C-62).
    assert sent == {"url": "http://127.0.0.1:5000/", "token": "secreto-123", "language": "es"}
    proc.finish(0)


def test_a_language_change_reaches_the_open_window(monkeypatch):
    launched = _launcher(monkeypatch)
    window = ventana.EditorWindow()
    assert not window.set_language("en")  # sin ventana abierta no hay a quién decírselo
    window.open("http://127.0.0.1:5000/", "t")
    assert window.set_language("en")
    assert launched[0].lines()[-1] == "idioma en"
    launched[0].finish(0)


def test_only_one_window_at_a_time(monkeypatch):
    launched = _launcher(monkeypatch)
    window = ventana.EditorWindow()
    assert window.open("http://127.0.0.1:5000/", "t")
    assert not window.open("http://127.0.0.1:5000/", "t")
    launched[0].finish(0)
    assert window.open("http://127.0.0.1:5000/", "t")
    assert len(launched) == 2
    launched[1].finish(0)


def test_close_asks_the_window_first_and_does_not_kill_it_if_it_closes(monkeypatch):
    launched = _launcher(monkeypatch, exits_on="cerrar")
    window = ventana.EditorWindow()
    window.open("http://127.0.0.1:5000/", "t")
    window.close(timeout=1.0)
    assert launched[0].lines()[1:] == ["cerrar"]
    assert not launched[0].terminated


def test_close_kills_the_window_only_if_it_does_not_answer(monkeypatch):
    launched = _launcher(monkeypatch)  # no hace caso a «cerrar»
    window = ventana.EditorWindow()
    window.open("http://127.0.0.1:5000/", "t")
    window.close(timeout=0.05)
    assert launched[0].lines()[1:] == ["cerrar"]
    assert launched[0].terminated


def test_bring_to_front_sends_mostrar(monkeypatch):
    launched = _launcher(monkeypatch)
    window = ventana.EditorWindow()
    assert not window.bring_to_front()  # sin ventana: nada que traer
    window.open("http://127.0.0.1:5000/", "t")
    assert window.bring_to_front()
    assert launched[0].lines()[1:] == ["mostrar"]
    launched[0].finish(0)
    assert not window.bring_to_front()


def test_exit_code_is_reported_only_for_the_current_window(monkeypatch):
    launched = _launcher(monkeypatch)
    codes = []
    window = ventana.EditorWindow(on_exit=codes.append)
    window.open("http://127.0.0.1:5000/", "t")
    launched[0].finish(ventana.EXIT_NO_WEBVIEW2)
    _wait_for(lambda: codes == [ventana.EXIT_NO_WEBVIEW2])


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1@evil.com/",  # usuario «127.0.0.1», contraseña «1», va a evil.com
        "http://127.0.0.1:5000@evil.com:80/",
        "https://127.0.0.1:5000/",
        "http://127.0.0.1/",  # sin puerto
        "http://127.0.0.1:abc/",
        "http://127.0.0.1:99999/",
        "http://localhost:5000/",
        "http://user@127.0.0.1:5000/",
        "file:///C:/Windows/",
    ],
)
def test_only_the_local_editor_url_is_accepted(url):
    assert not ventana.is_local_editor_url(url)


def test_local_editor_url_is_accepted():
    assert ventana.is_local_editor_url("http://127.0.0.1:5000/")


# --- proceso de la ventana (con un pywebview falso) -------------------------------------


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    """Ventana de pywebview falsa. ``destroy`` hace lo que WinForms: dispara ``closing``
    y solo cierra si ningún manejador devuelve False."""

    def __init__(self, flush_result=None, confirm=True, answer=True):
        self.events = types.SimpleNamespace(
            closing=FakeEvent(),
            minimized=FakeEvent(),
            restored=FakeEvent(),
            maximized=FakeEvent(),
            shown=FakeEvent(),
        )
        self.flush_result = flush_result
        self.answer = answer  # False: la página no contesta nunca
        self.confirm = confirm
        self.questions = []
        self.calls = []
        self.closed = False
        self.current_url = None  # lo que pywebview daría con get_current_url()

    def get_current_url(self):
        return self.current_url

    def evaluate_js(self, script, callback=None):
        self.calls.append(("js", script))
        if self.answer:
            threading.Thread(target=callback, args=(self.flush_result,)).start()

    def create_confirmation_dialog(self, title, message):
        self.questions.append(message)
        return self.confirm

    def destroy(self):
        self.calls.append(("destroy",))
        if all(h() is not False for h in self.events.closing.handlers):
            self.closed = True

    def show(self):
        self.calls.append(("show",))

    def set_title(self, title):
        self.calls.append(("title", title))

    def restore(self):
        self.calls.append(("restore",))


def _controller(window, **kwargs):
    controller = ventana._WindowController(window, **kwargs)
    window.events.closing += controller.on_closing
    return controller


def test_closing_saves_first_then_closes_when_nothing_is_pending():
    window = FakeWindow({"saved": True, "pending": "none"})
    _controller(window)
    window.destroy()  # el usuario pulsa la X
    _wait_for(lambda: window.closed)
    assert window.calls[1] == ("js", ventana.FLUSH_JS)
    assert window.questions == []


@pytest.mark.parametrize("pending", ["invalid", "builtin", "error"])
def test_closing_with_unsaved_changes_asks_and_no_keeps_the_window(pending):
    window = FakeWindow({"saved": False, "pending": pending}, confirm=False)
    controller = _controller(window)
    window.destroy()
    _wait_for(lambda: window.questions and not controller._checking)
    assert not window.closed
    assert "¿Cerrar igualmente?" in window.questions[0]
    # Si luego dice que sí, se cierra.
    window.confirm = True
    window.destroy()
    _wait_for(lambda: window.closed)


def test_closing_when_the_page_does_not_answer_asks_too():
    window = FakeWindow(answer=False, confirm=False)
    controller = _controller(window, flush_timeout=0.05)
    window.destroy()
    _wait_for(lambda: window.questions and not controller._checking)
    assert not window.closed
    assert "no he podido comprobar" in window.questions[0]


def test_flush_script_always_yields_a_promise():
    # pywebview 6.2.1 solo llama a la callback si el resultado es una promesa.
    assert ventana.FLUSH_JS.startswith("Promise.resolve(")
    assert "window.luzeiraEditor.flush()" in ventana.FLUSH_JS


def test_orders_from_the_tray_show_and_close_through_the_normal_path():
    window = FakeWindow({"pending": "none"})
    controller = _controller(window)
    controller.listen(io.StringIO("mostrar\n"))  # al acabar stdin, se cierra también
    assert window.calls[0] == ("show",)  # no minimizada: sin restore (no quita «maximizar»)
    _wait_for(lambda: window.closed)
    window = FakeWindow({"pending": "none"})
    controller = _controller(window)
    controller.on_minimized()
    controller.listen(io.StringIO("mostrar\ncerrar\n"))
    assert window.calls[:3] == [("restore",), ("show",), ("destroy",)]
    _wait_for(lambda: window.closed)


def test_the_window_follows_a_language_change_from_the_tray():
    # La orden «idioma» cambia el título y el aviso de cerrar sin guardar (son de Python).
    window = FakeWindow({"saved": False, "pending": "invalid"}, confirm=False)
    controller = _controller(window)
    controller.listen(io.StringIO("idioma en\n"))
    assert ("title", "Luzeira — Theme editor") in window.calls
    _wait_for(lambda: window.questions and not controller._checking)
    assert window.questions[0] == (
        "There are unsaved changes (there are errors marked in red). Close anyway?"
    )


def _fake_webview(monkeypatch, renderer="edgechromium"):
    created = {}

    def create_window(*a, **k):
        created.update(args=a, kwargs=k)
        created["window"] = FakeWindow({"pending": "none"})
        return created["window"]

    fake = types.SimpleNamespace(
        initialize=lambda gui=None: types.SimpleNamespace(renderer=renderer),
        create_window=create_window,
        start=lambda **k: created.update(start=k),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    return created


def test_window_process_only_shows_the_local_editor(monkeypatch):
    created = _fake_webview(monkeypatch)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"url": "http://127.0.0.1:1@evil.com/", "token": "t"}\n')
    )
    assert ventana.run_window_from_stdin() == ventana.EXIT_BAD_CONFIG
    assert created == {}


def test_window_process_gets_token_through_the_bridge_not_the_url(monkeypatch):
    created = _fake_webview(monkeypatch)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"url": "http://127.0.0.1:5000/", "token": "secreto"}\n')
    )
    assert ventana.run_window_from_stdin() == 0
    assert created["args"][0] == "Luzeira — Editor de temas"  # sin «language»: el de Windows
    # Con el idioma en la query, para los textos de antes de hablar con la API (C-62).
    assert created["args"][1] == "http://127.0.0.1:5000/?lang=es"
    # El puente solo da el token con el editor local cargado en la ventana (R1-02).
    created["window"].current_url = "http://127.0.0.1:5000/?lang=es"
    assert created["kwargs"]["js_api"].token() == "secreto"
    created["window"].current_url = "https://evil.example/"
    assert created["kwargs"]["js_api"].token() is None
    start = dict(created["start"])
    # El icono de Luzeira en la barra de tareas, no el de pythonw.exe.
    assert Path(start.pop("icon")).name == "luzeira.ico"
    assert start == {"gui": "edgechromium", "private_mode": True}
    assert len(created["window"].events.closing.handlers) == 1
    # Al mostrarse, la ventana dice a Windows cómo anclarla como «Luzeira».
    assert len(created["window"].events.shown.handlers) == 1


def test_window_process_takes_the_language_of_the_tray(monkeypatch):
    created = _fake_webview(monkeypatch)
    config = '{"url": "http://127.0.0.1:5000/", "token": "t", "language": "en"}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(config))
    assert ventana.run_window_from_stdin() == 0
    assert created["args"][0] == "Luzeira — Theme editor"
    assert created["args"][1] == "http://127.0.0.1:5000/?lang=en"


def test_window_url_carries_only_a_language_we_have(monkeypatch):
    created = _fake_webview(monkeypatch)
    config = '{"url": "http://127.0.0.1:5000/", "token": "t", "language": "x&token=1"}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(config))
    assert ventana.run_window_from_stdin() == 0
    assert created["args"][1] == "http://127.0.0.1:5000/?lang=es"  # el de «Windows»


def test_window_process_without_webview2_exits_with_its_own_code(monkeypatch):
    # pywebview 6.2.1 cae a MSHTML (Internet Explorer 11) en silencio sin WebView2.
    created = _fake_webview(monkeypatch, renderer="mshtml")
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"url": "http://127.0.0.1:5000/", "token": "t"}\n')
    )
    assert ventana.run_window_from_stdin() == ventana.EXIT_NO_WEBVIEW2
    assert created == {}


def test_window_process_rejects_garbage_config(monkeypatch):
    _fake_webview(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO("esto no es json\n"))
    assert ventana.run_window_from_stdin() == ventana.EXIT_BAD_CONFIG


# --- Revisión de seguridad R1 ---------------------------------------------------------


class _UrlWindow:
    def __init__(self, url):
        self.url = url

    def get_current_url(self):
        return self.url


@pytest.mark.parametrize(
    "current",
    [
        "https://evil.example/",
        "http://127.0.0.1:5001/",  # otro servidor local: otro origen
        "http://127.0.0.1:5000@evil.example/",
        "http://localhost:5000/",
        "file:///C:/x.html",
        "",
        None,
    ],
)
def test_bridge_gives_the_token_only_to_the_local_editor(current):
    """R1-02: pywebview inyecta el puente en cualquier página de la ventana; si la ventana
    acaba en otra web, esa web no se lleva el token."""
    bridge = ventana._Bridge("secreto", "http://127.0.0.1:5000/?lang=es")
    assert bridge.token() is None  # sin ventana todavía, tampoco
    bridge._attach(_UrlWindow(current))
    assert bridge.token() is None
    bridge._attach(_UrlWindow("http://127.0.0.1:5000/?lang=en#x"))  # control: así sí
    assert bridge.token() == "secreto"


def test_bridge_does_not_publish_its_window_to_the_page():
    """pywebview publica a la página todo lo que no empieza por «_»."""
    bridge = ventana._Bridge("secreto", "http://127.0.0.1:5000/")
    public = [name for name in dir(bridge) if not name.startswith("_")]
    assert public == ["token"]


@pytest.mark.parametrize(
    "url,opened",
    [
        ("https://github.com/eriik-rz/luzeira", True),
        ("http://example.com/", False),
        ("file:///C:/Windows/System32/calc.exe", False),
        ("ms-settings:privacy", False),
        ("javascript:alert(1)", False),
        ("https:///sin-host", False),
    ],
)
def test_external_links_only_https(url, opened):
    """R1-10: lo que la página abre con window.open va a ``webbrowser.open``: solo https."""
    calls = []
    module = types.SimpleNamespace(open=lambda u, *a, **k: calls.append(u) or True)
    ventana._guard_external_links(module)
    ventana._guard_external_links(module)  # dos veces no lo envuelve dos veces
    assert module.open(url) is opened
    assert calls == ([url] if opened else [])


def test_window_process_guards_webbrowser(monkeypatch):
    import webbrowser

    calls = []
    monkeypatch.setattr(webbrowser, "open", lambda u, *a, **k: calls.append(u) or True)
    _fake_webview(monkeypatch)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"url": "http://127.0.0.1:5000/", "token": "t"}\n')
    )
    assert ventana.run_window_from_stdin() == 0
    assert webbrowser.open("file:///C:/x") is False
    assert calls == []
