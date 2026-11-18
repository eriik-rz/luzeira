"""EditorLink: el servidor y su token viven lo que vive la ventana (sin red ni ventanas)."""

import itertools
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
    assert cli._editor(dev=True) == 0
    assert created["dev_origin"] == "http://127.0.0.1:5173"
    out = capsys.readouterr().out
    assert "http://127.0.0.1:5173/#token=token-" in out
    assert "localhost" not in out
    assert created["link"].closed
