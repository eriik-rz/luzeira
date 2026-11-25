"""EditorLink: el servidor y su token viven lo que vive la ventana (sin red ni ventanas)."""

import contextlib
import itertools
import socket
import threading
import time
from typing import ClassVar

from test_editor_ventana import FakeProcess

from luzeira.editor import conexion, ventana

_tokens = itertools.count(1)


class FakeServer:
    instances: ClassVar[list["FakeServer"]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.token = f"token-{next(_tokens)}"
        self.url = "http://127.0.0.1:5000/"
        self.running = False
        FakeServer.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class FakeSensors:
    def __init__(self):
        self.closed = False

    def available(self):
        return []

    def read(self, sensor_id):
        raise KeyError(sensor_id)

    def close(self):
        self.closed = True


def _link(monkeypatch, tmp_path):
    FakeServer.instances = []
    launched = []
    monkeypatch.setattr(conexion, "EditorServer", FakeServer)
    monkeypatch.setattr(conexion, "_LiveSensors", FakeSensors)
    monkeypatch.setattr(conexion, "user_themes_dir", lambda: tmp_path / "temas")
    monkeypatch.setattr(conexion, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        ventana.subprocess,
        "Popen",
        lambda *a, **k: launched.append(FakeProcess(*a, **k)) or launched[-1],
    )
    codes = []
    link = conexion.EditorLink(
        apply_theme=lambda name: None,
        status=lambda: ("running", ""),
        active_theme=lambda: None,
        on_window_exit=codes.append,
    )
    return link, launched, codes


def _wait_for(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "no se cumplió a tiempo"
        time.sleep(0.005)


def test_server_and_sensors_stop_when_the_window_ends_and_token_is_new_each_time(
    monkeypatch, tmp_path
):
    link, launched, codes = _link(monkeypatch, tmp_path)
    assert link.open_window()
    first = FakeServer.instances[0]
    sensors = link._sensors
    assert first.running
    launched[0].finish(ventana.EXIT_OK)
    _wait_for(lambda: codes == [ventana.EXIT_OK])
    assert not first.running and sensors.closed
    assert link._server is None

    assert link.open_window()
    second = FakeServer.instances[1]
    assert second.running and second.token != first.token
    launched[1].finish(ventana.EXIT_OK)
    _wait_for(lambda: len(codes) == 2)


def test_reopening_before_the_exit_notice_still_gets_a_fresh_server(monkeypatch, tmp_path):
    link, launched, _codes = _link(monkeypatch, tmp_path)
    link.open_window()
    first = FakeServer.instances[0]
    launched[0].code = 0  # ha terminado, pero su vigilante aún no ha avisado
    assert link.open_window()
    assert not first.running
    assert FakeServer.instances[1].running
    launched[0].finish(0)  # llega el aviso tarde: no debe parar el servidor nuevo
    time.sleep(0.05)
    assert FakeServer.instances[1].running
    launched[1].finish(0)


def test_dev_mode_uses_127_0_0_1_for_vite_and_prints_it(monkeypatch, capsys):
    # El contrato fija dev_origin = http://127.0.0.1:5173; con «localhost» el navegador
    # manda otro Origin y el servidor lo rechaza con 403.
    import luzeira.__main__ as cli

    created = {}

    class FakeLink:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.closed = False
            created["link"] = self

        def server(self):
            return FakeServer()

        def close(self):
            self.closed = True

    def stop(_seconds):
        raise KeyboardInterrupt  # Ctrl+C a la primera espera

    monkeypatch.setattr(conexion, "EditorLink", FakeLink)
    monkeypatch.setattr(cli.time, "sleep", stop)
    # Sin mirar el 5173 de verdad (suele tenerlo otro proyecto): «libre».
    monkeypatch.setattr(cli, "_vite_port_state", lambda origin: "free")
    assert cli._editor(dev=True) == 0
    assert created["dev_origin"] == "http://127.0.0.1:5173"
    out = capsys.readouterr().out
    assert "http://127.0.0.1:5173/#token=token-" in out
    assert "localhost" not in out
    assert created["link"].closed


# --- Revisión de seguridad R1-07: el token de --desarrollo ------------------------------


class _TinyHttp:
    """Un servidor HTTP de prueba en un puerto libre: responde siempre ``page``."""

    def __init__(self, page: bytes) -> None:
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.page = page
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                with contextlib.suppress(OSError):
                    conn.recv(65536)
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
                        + f"Content-Length: {len(self.page)}\r\n\r\n".encode()
                        + self.page
                    )

    def close(self) -> None:
        self.sock.close()


def test_vite_port_state_tells_free_ours_and_other():
    import luzeira.__main__ as cli

    other = _TinyHttp(b"<!doctype html><title>Forja de heroes</title>")
    ours = _TinyHttp(b"<!doctype html><title>Luzeira \xc2\xb7 Editor de temas</title>")
    try:
        assert cli._vite_port_state(f"http://127.0.0.1:{other.port}") == "other"
        assert cli._vite_port_state(f"http://127.0.0.1:{ours.port}") == "ours"
    finally:
        other.close()
        ours.close()
    # Ya cerrado: libre.
    assert cli._vite_port_state(f"http://127.0.0.1:{other.port}") == "free"


def test_dev_mode_does_not_print_the_token_if_another_program_has_the_port(monkeypatch, capsys):
    """R1-07: en el 5173 suele estar el Vite de otro proyecto. La dirección con el token
    se abriría en su página, que lo leería del fragmento: ni se arranca el servidor ni se
    imprime el token."""
    import luzeira.__main__ as cli

    busy = _TinyHttp(b"<!doctype html><title>Otro proyecto</title>")
    started = []

    class FakeLink:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def server(self):
            return FakeServer()

        def close(self):
            pass

    monkeypatch.setattr(conexion, "EditorLink", FakeLink)
    monkeypatch.setattr(cli, "DEV_ORIGIN", f"http://127.0.0.1:{busy.port}")
    monkeypatch.setattr(cli.time, "sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        assert cli._editor(dev=True) == 1
    finally:
        busy.close()
    out = capsys.readouterr().out
    assert "token-" not in out
    assert "#token=" not in out
    assert f"127.0.0.1:{busy.port}" in out  # dice dónde está el problema
    assert started == []


def test_without_dev_mode_there_is_no_dev_origin(monkeypatch):
    """Sin --desarrollo no hay origen de Vite: ni en «luzeira editor» ni en la bandeja."""
    import ast
    from pathlib import Path

    import luzeira.__main__ as cli
    from luzeira import bandeja

    created = {}

    class FakeLink:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.window = type("W", (), {"is_open": False})()

        def server(self):
            return FakeServer()

        @staticmethod
        def is_built():
            return False  # sale enseguida, sin ventana

        def close(self):
            pass

    monkeypatch.setattr(conexion, "EditorLink", FakeLink)
    assert cli._editor(dev=False) == 1
    assert created["dev_origin"] is None
    # La bandeja crea su EditorLink sin dev_origin (por defecto None).
    tree = ast.parse(Path(bandeja.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "EditorLink"
    ]
    assert calls, "la bandeja ya no crea un EditorLink: revisa este test"
    assert all(kw.arg != "dev_origin" for call in calls for kw in call.keywords)
