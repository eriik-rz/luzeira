"""Tests del servidor local del editor (fase 5, RQ-14).

Se arranca el servidor de verdad (127.0.0.1, puerto 0, en su hilo) y se le habla con
``http.client``, que deja poner a mano ``Host`` y ``Origin`` como haría un atacante.
Todas las carpetas (temas del usuario, papelera) van en ``tmp_path``.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from luzeira import config as luzeira_config
from luzeira.editor import EditorServer
from luzeira.editor.almacen import EditorError
from luzeira.sensores.base import Reading, SensorInfo
from luzeira.temas import cargar

FIXTURES = Path(__file__).parent / "fixtures" / "temas"

SENSORS = [
    SensorInfo("cpu.usage", "Uso de CPU", "%", "cpu"),
    SensorInfo("gpu.name", "Nombre de la GPU", "", "gpu", kind="text"),
]
READINGS = {
    "cpu.usage": Reading(100.0, "%"),
    "gpu.name": Reading(None, "", text="RTX de prueba"),
}


def png_bytes(size=(16, 16), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def theme_dict(name="Mío", orientation="landscape", widgets=None, **extra) -> dict:
    out = {
        "format": "luzeira-theme/1",
        "name": name,
        "orientation": orientation,
        "background": {"color": "#000000"},
        "widgets": widgets or [],
    }
    out.update(extra)
    return out


class Client:
    """Cliente de pruebas: por defecto manda Host y token buenos."""

    def __init__(self, server: EditorServer) -> None:
        self.server = server

    def request(
        self,
        method: str,
        path: str,
        body: bytes | dict | list | None = None,
        *,
        token: str | bool | None = True,
        host: str | None = None,
        origin: str | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{self.server.port}")
        if token is True:
            conn.putheader("X-Luzeira-Token", self.server.token)
        elif isinstance(token, str):
            conn.putheader("X-Luzeira-Token", token)
        if origin is not None:
            conn.putheader("Origin", origin)
        if isinstance(body, dict | list):
            body = json.dumps(body).encode("utf-8")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        if body is not None and "Content-Length" not in (headers or {}):
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body is not None:
            # el servidor puede cortar antes de leer (413)
            with contextlib.suppress(ConnectionError, OSError):
                conn.send(body)
        resp = conn.getresponse()
        data = resp.read()
        result = resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        conn.close()
        return result

    def json(self, method: str, path: str, body=None, **kw) -> tuple[int, object]:
        status, _, data = self.request(method, path, body, **kw)
        return status, json.loads(data) if data else None


def upload(client: Client, theme: str, encoded_name: str | None, data: bytes, query: str = ""):
    """Sube ``data`` con el nombre (ya codificado en URL) en ``X-Luzeira-Filename``."""
    headers = {} if encoded_name is None else {"X-Luzeira-Filename": encoded_name}
    return client.json("POST", f"/api/temas/{theme}/recursos{query}", data, headers=headers)


REAL_BUILTIN = Path(luzeira_config.BUILTIN_THEMES)


def builtin_theme() -> dict:
    """theme.json del tema incluido de mentira (el fixture ``builtin`` ya lo ha puesto)."""
    path = luzeira_config.BUILTIN_THEMES / "esencial" / "theme.json"
    return json.loads(path.read_text("utf-8"))


def _tree_state(root: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
    )


@pytest.fixture(autouse=True)
def builtin(tmp_path, monkeypatch):
    """Temas incluidos **de mentira** en ``tmp_path``, y alarma si se toca la carpeta real.

    Un test que rompe la protección de solo lectura (al validar al revés) escribiría en
    ``src/luzeira/recursos/temas``; pasó una vez con la carpeta real. Ahora no puede.
    """
    before = _tree_state(REAL_BUILTIN)
    fake = tmp_path / "incluidos"
    shutil.copytree(FIXTURES / "ejemplo-completo", fake / "esencial")
    data = json.loads((fake / "esencial" / "theme.json").read_text("utf-8"))
    data["name"] = "Esencial"
    (fake / "esencial" / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(luzeira_config, "BUILTIN_THEMES", fake)
    monkeypatch.setattr(cargar, "_BUILTIN_THEMES", fake)
    yield fake
    assert _tree_state(REAL_BUILTIN) == before, "un test ha tocado los temas incluidos reales"


@pytest.fixture
def env(tmp_path, builtin):
    user = tmp_path / "temas"
    user.mkdir()
    shutil.copytree(FIXTURES / "ejemplo-completo", user / "completo")
    (user / "mio").mkdir()
    (user / "mio" / "theme.json").write_text(json.dumps(theme_dict()), encoding="utf-8")
    applied: list[str] = []
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>Editor</title>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secreto.txt").write_text("no", encoding="utf-8")
    server = EditorServer(
        sensors=lambda: SENSORS,
        read_sensor=lambda i: READINGS.get(i, Reading(None, "", reason="desconocido")),
        apply_theme=applied.append,
        status=lambda: ("running", "Mostrando «Esencial»."),
        user_themes=user,
        trash=tmp_path / "papelera",
        active_theme=lambda: "completo",
        web_root=web,
    )
    server.start()
    try:
        yield server, Client(server), user, tmp_path, applied
    finally:
        server.stop()


# --- Filtros de seguridad --------------------------------------------------------------


def test_listens_only_on_loopback_and_token_is_long(env):
    server, *_ = env
    assert server.url.startswith("http://127.0.0.1:")
    assert server._httpd.server_address[0] == "127.0.0.1"
    assert len(server.token) >= 40


@pytest.mark.parametrize("token", [None, "", "malo", "x" * 43])
def test_api_without_good_token_is_403(env, token):
    _, client, *_ = env
    status, body = client.json("GET", "/api/temas", token=token)
    assert status == 403
    assert body == {"error": "Acceso denegado."}


def test_token_in_url_is_not_accepted(env):
    server, client, *_ = env
    status, _ = client.json("GET", f"/api/temas?token={server.token}", token=None)
    assert status == 403


@pytest.mark.parametrize("host", ["evil.com", "localhost:{port}", "127.0.0.1", "127.0.0.1:1"])
def test_bad_host_is_403_even_with_token(env, host):
    server, client, *_ = env
    status, _ = client.json("GET", "/api/temas", host=host.format(port=server.port))
    assert status == 403
    # También para los ficheros estáticos (DNS rebinding leería la página)
    status, _, _ = client.request("GET", "/", host=host.format(port=server.port), token=None)
    assert status == 403


@pytest.mark.parametrize(
    "origin", ["http://evil.com", "null", "http://localhost:{port}", "https://127.0.0.1:{port}"]
)
def test_foreign_origin_is_403(env, origin):
    server, client, *_ = env
    status, _ = client.json("GET", "/api/temas", origin=origin.format(port=server.port))
    assert status == 403


def test_own_origin_is_accepted_and_no_cors_headers(env):
    server, client, *_ = env
    status, headers, _ = client.request("GET", "/api/temas", origin=server.origin)
    assert status == 200
    assert "access-control-allow-origin" not in headers


def test_preflight_without_dev_mode_is_403(env):
    _, client, *_ = env
    status, _, _ = client.request("OPTIONS", "/api/temas", token=None, origin="http://evil.com")
    assert status == 403


def test_dev_origin_only_in_dev_mode(tmp_path):
    server = EditorServer(
        sensors=list,
        read_sensor=lambda i: Reading(None, ""),
        apply_theme=lambda n: None,
        status=lambda: ("running", ""),
        user_themes=tmp_path / "t",
        trash=tmp_path / "p",
        dev_origin="http://127.0.0.1:5173",
    )
    server.start()
    try:
        client = Client(server)
        status, headers, _ = client.request(
            "OPTIONS", "/api/temas", token=None, origin="http://127.0.0.1:5173"
        )
        assert status == 204
        assert headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "X-Luzeira-Token" in headers["access-control-allow-headers"]
        assert "X-Luzeira-Filename" in headers["access-control-allow-headers"]
        # «localhost» no es el mismo origen que 127.0.0.1
        assert client.request("GET", "/api/temas", origin="http://localhost:5173")[0] == 403
        status, headers, _ = client.request("GET", "/api/temas", origin="http://127.0.0.1:5173")
        assert status == 200
        assert headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        # Otro origen sigue fuera, y el token sigue haciendo falta
        assert client.request("GET", "/api/temas", origin="http://evil.com")[0] == 403
        assert (
            client.request("GET", "/api/temas", token=None, origin="http://127.0.0.1:5173")[0]
            == 403
        )
    finally:
        server.stop()


def test_body_over_1mb_is_413(env):
    _, client, *_ = env
    big = b"{" + b" " * (1024 * 1024) + b"}"
    status, _, data = client.request("PUT", "/api/temas/mio", big)
    assert status == 413
    assert "demasiado grandes" in json.loads(data)["error"]


def test_upload_over_10mb_is_413(env):
    _, client, *_ = env
    status, _, _ = client.request(
        "POST",
        "/api/temas/mio/recursos",
        b"x" * (10 * 1024 * 1024 + 1),
        headers={"X-Luzeira-Filename": "a.png"},
    )
    assert status == 413


def test_missing_content_length_is_411(env):
    _, client, *_ = env
    status, _, _ = client.request("PUT", "/api/temas/mio", headers={"Transfer-Encoding": "chunked"})
    assert status == 411


def test_deeply_nested_json_is_400_not_500(env):
    _, client, *_ = env
    status, _ = client.json("PUT", "/api/temas/mio", b"[" * 200_000 + b"]" * 200_000)
    assert status == 400


def test_nan_and_infinity_are_rejected(env):
    """El esquema acepta NaN e infinito en min/max de una barra (pydantic los deja
    pasar); el editor no debe poder guardarlos."""
    _, client, *_ = env
    bar = {"type": "bar", "sensor": "cpu.usage", "box": {"x": 0, "y": 0, "width": 9, "height": 9}}
    good = json.dumps(theme_dict(widgets=[{**bar, "min": 0, "max": 100}]))
    assert client.json("PUT", "/api/temas/mio", good.encode())[0] == 200
    for bad in ("NaN", "1e400", "-Infinity"):
        raw = good.replace('"max": 100', f'"max": {bad}').encode()
        assert bad in raw.decode()
        status, body = client.json("PUT", "/api/temas/mio", raw)
        assert status == 400, bad
        assert body["error"] == "Los datos enviados están mal formados."


def test_server_header_does_not_leak_python_version(env):
    _, client, *_ = env
    _, headers, _ = client.request("GET", "/api/info")
    assert "python" not in headers.get("server", "").lower()
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"


# --- Estáticos -------------------------------------------------------------------------


def test_static_index_and_assets_without_token(env):
    _, client, *_ = env
    status, headers, data = client.request("GET", "/", token=None)
    assert status == 200 and b"Editor" in data
    assert headers["content-type"].startswith("text/html")
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    status, headers, _ = client.request("GET", "/assets/app.js", token=None)
    assert status == 200 and headers["content-type"].startswith("text/javascript")


@pytest.mark.parametrize(
    "path",
    ["/../secreto.txt", "/%2e%2e/secreto.txt", "/assets/..%5c..%5csecreto.txt", "/nada.html"],
)
def test_static_traversal_is_404(env, path):
    _, client, *_ = env
    assert client.request("GET", path, token=None)[0] == 404


# --- Info y sensores -------------------------------------------------------------------


def test_info(env):
    _, client, *_ = env
    status, body = client.json("GET", "/api/info")
    assert status == 200
    assert body["status"] == "running" and body["message"] == "Mostrando «Esencial»."
    assert body["version"]
    assert body["sizes"]["portrait"] == {"width": 320, "height": 480}


def test_sensors_have_kind_and_value(env):
    _, client, *_ = env
    status, body = client.json("GET", "/api/sensores")
    assert status == 200
    by_id = {row["id"]: row for row in body}
    assert by_id["cpu.usage"]["kind"] == "number" and by_id["cpu.usage"]["value"] == 100.0
    assert by_id["gpu.name"]["kind"] == "text" and by_id["gpu.name"]["text"] == "RTX de prueba"
    assert set(by_id["cpu.usage"]) == {
        "id",
        "label",
        "unit",
        "category",
        "kind",
        "value",
        "text",
        "reason",
    }


# --- Temas: leer, guardar --------------------------------------------------------------


def test_list_themes_marks_builtin(env):
    _, client, *_ = env
    status, body = client.json("GET", "/api/temas")
    assert status == 200
    by_name = {t["name"]: t for t in body}
    assert by_name["esencial"] == {"name": "esencial", "title": "Esencial", "builtin": True}
    assert by_name["mio"]["builtin"] is False and by_name["mio"]["title"] == "Mío"


def test_get_theme_lists_files(env):
    _, client, *_ = env
    status, body = client.json("GET", "/api/temas/completo")
    assert status == 200
    assert body["builtin"] is False
    assert set(body["files"]) == {"fondo.png", "img/anim.gif", "img/logo.jpg"}
    assert body["theme"]["format"] == "luzeira-theme/1"


def test_get_unknown_theme_is_404(env):
    _, client, *_ = env
    assert client.json("GET", "/api/temas/no-existe")[0] == 404
    assert client.json("GET", "/api/temas/..%2F..%2Fetc")[0] == 404


def test_put_builtin_is_409_not_403(env):
    _, client, *_ = env
    status, body = client.json("PUT", "/api/temas/esencial", theme_dict())
    assert status == 409
    assert "Guardar como" in body["error"]


def test_put_valid_saves_and_reads_back(env):
    _, client, user, *_ = env
    theme = theme_dict(
        name="Cambiado",
        widgets=[
            {"type": "text", "text": "Hola ñ", "box": {"x": 1, "y": 2, "width": 50, "height": 20}}
        ],
    )
    status, body = client.json("PUT", "/api/temas/mio", theme)
    assert (status, body) == (200, {"ok": True})
    status, got = client.json("GET", "/api/temas/mio")
    assert got["theme"]["name"] == "Cambiado"
    assert got["theme"]["widgets"][0]["text"] == "Hola ñ"
    # Lo guardado se vuelve a guardar idéntico (relee igual)
    assert client.json("PUT", "/api/temas/mio", got["theme"])[0] == 200
    assert client.json("GET", "/api/temas/mio")[1]["theme"] == got["theme"]
    # Sin temporales a medias
    assert sorted(p.name for p in (user / "mio").iterdir()) == ["theme.json"]


def test_put_invalid_is_400_with_details(env):
    _, client, user, *_ = env
    before = (user / "mio" / "theme.json").read_bytes()
    theme = theme_dict(
        widgets=[
            {"type": "text", "text": "a", "box": {"x": 0, "y": 0, "width": 10, "height": 10}},
            {
                "type": "sensor",
                "sensor": "cpu.usage",
                "box": {"x": -5, "y": 0, "width": 10, "height": 10},
            },
        ]
    )
    status, body = client.json("PUT", "/api/temas/mio", theme)
    assert status == 400
    assert body["details"] == [{"path": "widgets.1.box.x", "message": "número demasiado pequeño"}]
    assert (user / "mio" / "theme.json").read_bytes() == before


def test_put_missing_file_is_400_with_field(env):
    _, client, *_ = env
    theme = theme_dict(background={"color": "#000000", "image": "no/esta.png"})
    status, body = client.json("PUT", "/api/temas/mio", theme)
    assert status == 400
    assert body["details"][0]["path"] == "background.image"
    assert "no existe" in body["details"][0]["message"]


# --- Copiar, borrar, aplicar -----------------------------------------------------------


def test_copy_builtin_creates_user_theme(env):
    _, client, user, *_ = env
    status, body = client.json("POST", "/api/temas/esencial/copiar", {"title": "Mi Esencial Ñ"})
    assert status == 200
    assert body == {"name": "mi-esencial-n"}
    got = client.json("GET", "/api/temas/mi-esencial-n")[1]
    assert got["builtin"] is False and got["theme"]["name"] == "Mi Esencial Ñ"
    assert not any(p.name.startswith(".") for p in user.iterdir())


def test_copy_copies_resources_and_avoids_collisions(env):
    _, client, user, *_ = env
    (user / "completo" / "malo.exe").write_bytes(b"MZ")
    names = [
        client.json("POST", "/api/temas/completo/copiar", {"title": "Copia"})[1]["name"]
        for _ in range(2)
    ]
    assert names == ["copia", "copia-2"]
    assert (user / "copia" / "img" / "logo.jpg").is_file()
    assert not (user / "copia" / "malo.exe").exists()


def test_copy_with_bad_title_is_400(env):
    _, client, *_ = env
    for title in ("", "   ", "x" * 61, 5):
        status, body = client.json("POST", "/api/temas/mio/copiar", {"title": title})
        assert status == 400 and body["details"][0]["path"] == "title"


def test_delete_moves_to_trash(env):
    _, client, user, tmp_path, _ = env
    status, body = client.json("DELETE", "/api/temas/mio")
    assert (status, body) == (200, {"ok": True})
    assert not (user / "mio").exists()
    trashed = list((tmp_path / "papelera").iterdir())
    assert len(trashed) == 1 and trashed[0].name.startswith("mio-")
    assert (trashed[0] / "theme.json").is_file()


def test_delete_builtin_and_active_are_409(env):
    _, client, *_ = env
    status, body = client.json("DELETE", "/api/temas/esencial")
    assert status == 409 and "incluidos" in body["error"]
    assert client.json("DELETE", "/api/temas/completo")[0] == 409


def test_apply_calls_callback(env):
    *_, applied = env
    _, client, *_ = env
    assert client.json("POST", "/api/temas/mio/aplicar", b"")[0] == 200
    assert client.json("POST", "/api/temas/esencial/aplicar", b"")[0] == 200
    assert applied == ["mio", "esencial"]
    assert client.json("POST", "/api/temas/no-existe/aplicar", b"")[0] == 404
    assert applied == ["mio", "esencial"]


def test_apply_broken_theme_is_400_and_no_callback(env):
    _, client, user, _, applied = env
    (user / "mio" / "theme.json").write_text(
        json.dumps(theme_dict(background={"color": "#000000", "image": "x.png"})), encoding="utf-8"
    )
    assert client.json("POST", "/api/temas/mio/aplicar", b"")[0] == 400
    assert applied == []


# --- Vista previa ----------------------------------------------------------------------


def _preview(client, name, theme):
    status, headers, data = client.request(
        "POST", "/api/vista-previa", {"name": name, "theme": theme}
    )
    return status, headers, data


@pytest.mark.parametrize("orientation,size", [("landscape", (480, 320)), ("portrait", (320, 480))])
def test_preview_png_has_screen_size(env, orientation, size):
    _, client, *_ = env
    status, headers, data = _preview(client, "mio", theme_dict(orientation=orientation))
    assert status == 200 and headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(data)).size == size


def test_preview_reflects_unsaved_change_and_live_sensor(env):
    _, client, user, *_ = env
    saved = (user / "mio" / "theme.json").read_bytes()
    bar = {
        "type": "bar",
        "sensor": "cpu.usage",
        "box": {"x": 0, "y": 0, "width": 40, "height": 10},
        "fill": "#00ff00",
    }
    theme = theme_dict(background={"color": "#112233"}, widgets=[bar])
    status, _, data = _preview(client, "mio", theme)
    assert status == 200
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert img.getpixel((300, 300)) == (0x11, 0x22, 0x33)  # fondo sin guardar
    assert img.getpixel((20, 5)) == (0, 255, 0)  # barra al 100 % (lectura actual)
    assert (user / "mio" / "theme.json").read_bytes() == saved  # no se ha guardado


def test_preview_uses_files_of_folder_even_if_not_saved_yet(env):
    _, client, user, *_ = env
    (user / "mio" / "img").mkdir()
    (user / "mio" / "img" / "rojo.png").write_bytes(png_bytes((8, 8), (255, 0, 0)))
    theme = theme_dict(background={"color": "#000000", "image": "img/rojo.png"})
    status, _, data = _preview(client, "mio", theme)
    assert status == 200
    assert Image.open(io.BytesIO(data)).convert("RGB").getpixel((10, 10)) == (255, 0, 0)


def test_preview_invalid_is_400_with_details(env):
    _, client, *_ = env
    status, _, data = _preview(client, "mio", theme_dict(orientation="diagonal"))
    assert status == 400
    assert json.loads(data)["details"][0]["path"] == "orientation"
    status, _, data = _preview(
        client, "mio", theme_dict(background={"color": "#000000", "image": "../x.png"})
    )
    assert status == 400


def test_preview_of_builtin_theme(env):
    _, client, *_ = env
    status, _, data = _preview(client, "esencial", builtin_theme())
    assert status == 200 and Image.open(io.BytesIO(data)).size == (480, 320)


# --- Recursos --------------------------------------------------------------------------


def test_upload_valid_png_and_serve_it(env):
    _, client, user, *_ = env
    status, body = upload(client, "mio", "Mi%20Logo.PNG", png_bytes())
    assert status == 200
    assert body == {"path": "img/mi-logo.png"}
    assert (user / "mio" / "img" / "mi-logo.png").read_bytes() == png_bytes()
    # Segunda subida con el mismo nombre: no pisa
    assert upload(client, "mio", "Mi%20Logo.PNG", png_bytes())[1] == {"path": "img/mi-logo-2.png"}
    status, headers, data = client.request("GET", "/api/temas/mio/recursos/img/mi-logo.png")
    assert status == 200 and headers["content-type"] == "image/png" and data == png_bytes()
    assert not any(p.name.startswith(".") for p in user.iterdir())


@pytest.mark.parametrize(
    "nombre,data",
    [
        ("dibujo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("programa.exe", b"MZ\x90\x00"),
        ("falso.png", b"esto no es un png"),
        ("../../fuera.png", None),
        ("..png", None),
        ("a/b.png", None),
        ("sinextension", None),
    ],
)
def test_upload_rejected(env, nombre, data):
    _, client, user, tmp_path, _ = env
    from urllib.parse import quote

    status, body = upload(client, "mio", quote(nombre, safe=""), data or png_bytes())
    assert status == 400, body
    assert sorted(p.name for p in (user / "mio").iterdir()) == ["theme.json"]
    assert not (tmp_path / "fuera.png").exists() and not (user / "fuera.png").exists()
    assert not any(p.name.startswith(".") for p in user.iterdir())


def test_upload_to_builtin_is_409(env):
    _, client, *_ = env
    status, body = upload(client, "esencial", "a.png", png_bytes())
    assert status == 409 and "Guardar como" in body["error"]


@pytest.mark.parametrize(
    "path", ["../mio/theme.json", "theme.json", "img/..%2F..%2Fsecreto.txt", "nada.png"]
)
def test_resource_get_rejects_bad_paths(env, path):
    _, client, *_ = env
    assert client.json("GET", f"/api/temas/completo/recursos/{path}")[0] == 404


def test_resource_get_rejects_fake_image(env):
    _, client, user, *_ = env
    (user / "mio" / "falso.png").write_bytes(b"no soy png")
    assert client.json("GET", "/api/temas/mio/recursos/falso.png")[0] == 400


# --- Coste de la vista previa ----------------------------------------------------------


def test_preview_cost_is_bounded(env, capsys):
    """Mide la vista previa del tema incluido y de uno con fondo grande (4096x4096).

    No es una prueba de rendimiento estricta: el umbral es holgado para no fallar en
    un PC cargado, pero avisa si algo se dispara (p. ej. sin la caché de validación).
    """
    _, client, user, *_ = env
    theme = builtin_theme()
    _preview(client, "esencial", theme)  # calienta fuentes
    t0 = time.perf_counter()
    for _ in range(5):
        assert _preview(client, "esencial", theme)[0] == 200
    small = (time.perf_counter() - t0) / 5 * 1000

    big = Image.linear_gradient("L").resize((4096, 4096)).convert("RGB")
    big.save(user / "mio" / "grande.png")
    assert (user / "mio" / "grande.png").stat().st_size < 10 * 1024 * 1024
    big_theme = theme_dict(background={"color": "#000000", "image": "grande.png"})
    t0 = time.perf_counter()
    assert _preview(client, "mio", big_theme)[0] == 200
    first = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    assert _preview(client, "mio", big_theme)[0] == 200
    second = (time.perf_counter() - t0) * 1000

    # Tema pesado (remate 2): 64 imágenes sacadas de la de 4096² + el fondo. Sin la caché
    # de imágenes escaladas cada vista previa decodificaba y escalaba 65 veces (medido
    # con 8 JPEG de 4096²: 14 s cada una). Ahora la 2.ª solo compone.
    heavy = dict(
        big_theme,
        widgets=[
            {
                "type": "image",
                "file": "grande.png",
                "box": {"x": (n % 8) * 60, "y": (n // 8) * 40, "width": 60, "height": 40},
            }
            for n in range(64)
        ],
    )
    t0 = time.perf_counter()
    assert _preview(client, "mio", heavy)[0] == 200
    heavy_first = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    assert _preview(client, "mio", heavy)[0] == 200
    heavy_second = (time.perf_counter() - t0) * 1000
    with capsys.disabled():
        print(
            f"\n[vista previa] esencial {small:.0f} ms · "
            f"fondo 4096² 1.ª {first:.0f} ms, 2.ª {second:.0f} ms · "
            f"64 imágenes 1.ª {heavy_first:.0f} ms, 2.ª {heavy_second:.0f} ms"
        )
    assert small < 1000 and second < 3000
    assert heavy_second < 1500


def test_preview_validation_is_cached_until_file_changes(env, monkeypatch):
    from luzeira.editor import almacen

    calls: list[str] = []
    real = almacen._check_file

    def counting(directory, rel, *args):
        calls.append(rel)
        return real(directory, rel, *args)

    monkeypatch.setattr(almacen, "_check_file", counting)
    _, client, user, *_ = env
    (user / "mio" / "a.png").write_bytes(png_bytes((8, 8), (1, 2, 3)))
    theme = theme_dict(background={"color": "#000000", "image": "a.png"})
    for _ in range(3):
        assert _preview(client, "mio", theme)[0] == 200
    assert calls == ["a.png"]
    # Si el fichero cambia (aquí, por uno falso), se vuelve a validar y se rechaza
    (user / "mio" / "a.png").write_bytes(b"ya no soy un png, pero mido otra cosa")
    assert _preview(client, "mio", theme)[0] == 400
    assert calls == ["a.png", "a.png"]


# --- Ronda de remate (contrato 2026-11-17) ----------------------------------------------

CONTRACT_CSP = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'"


@pytest.mark.parametrize("path", ["/", "/assets/app.js"])
def test_static_has_contract_csp_and_nosniff(env, path):
    _, client, *_ = env
    status, headers, _ = client.request("GET", path, token=None)
    assert status == 200
    assert headers["content-security-policy"].startswith(CONTRACT_CSP)
    assert headers["x-content-type-options"] == "nosniff"


def test_info_has_active_theme(env, tmp_path):
    _, client, *_ = env
    assert client.json("GET", "/api/info")[1]["active_theme"] == "completo"
    # Sin la función inyectada: null (no falla)
    server = EditorServer(
        sensors=list,
        read_sensor=lambda i: Reading(None, ""),
        apply_theme=lambda n: None,
        status=lambda: ("running", ""),
        user_themes=tmp_path / "t2",
        trash=tmp_path / "p2",
    )
    server.start()
    try:
        status, body = Client(server).json("GET", "/api/info")
        assert status == 200 and body["active_theme"] is None
    finally:
        server.stop()


def test_widget_off_screen_error_points_to_its_box(env):
    _, client, *_ = env
    theme = theme_dict(
        widgets=[
            {"type": "text", "text": "a", "box": {"x": 0, "y": 0, "width": 10, "height": 10}},
            {"type": "text", "text": "b", "box": {"x": 470, "y": 0, "width": 20, "height": 10}},
        ]
    )
    status, body = client.json("PUT", "/api/temas/mio", theme)
    assert status == 400
    assert body["details"] == [
        {
            "path": "widgets.1.box",
            "message": f"El elemento 2 (Texto) se sale de la pantalla (480 {chr(0xD7)} 320)",
        }
    ]
    status, body = client.json("POST", "/api/vista-previa", {"name": "mio", "theme": theme})
    assert status == 400 and body["details"][0]["path"] == "widgets.1.box"


def test_upload_name_in_query_is_rejected(env):
    _, client, user, *_ = env
    status, body = upload(client, "mio", None, png_bytes(), query="?nombre=a.png")
    assert status == 400 and "X-Luzeira-Filename" in body["error"]
    # Ni siquiera con la cabecera buena al lado
    status, _ = upload(client, "mio", "a.png", png_bytes(), query="?nombre=a.png")
    assert status == 400
    assert sorted(p.name for p in (user / "mio").iterdir()) == ["theme.json"]


@pytest.mark.parametrize(
    "encoded",
    [
        None,  # sin cabecera
        "",
        "a%2Fb.png",  # «/» escondida: se valida después de decodificar
        "a%5Cb.png",  # «\»
        "%2E%2E%2Fx.png",  # «../»
        "a%00.png",
        "%FF.png",  # no es UTF-8
        "c%3A.png",  # «:»
    ],
)
def test_upload_bad_filename_header(env, encoded):
    _, client, user, *_ = env
    status, body = upload(client, "mio", encoded, png_bytes())
    assert status == 400, body
    assert sorted(p.name for p in (user / "mio").iterdir()) == ["theme.json"]


def test_upload_filename_header_is_url_decoded(env):
    _, client, user, *_ = env
    status, body = upload(client, "mio", "Ca%C3%B1%C3%B3n%20grande.png", png_bytes())
    assert (status, body) == (200, {"path": "img/canon-grande.png"})
    assert (user / "mio" / "img" / "canon-grande.png").read_bytes() == png_bytes()
    # Sin codificar (bytes fuera de ASCII en la cabecera): no
    status, _, _ = client.request(
        "POST",
        "/api/temas/mio/recursos",
        png_bytes(),
        headers={"X-Luzeira-Filename": "cañón.png"},
    )
    assert status == 400


def test_upload_repeated_filename_header_is_400(env):
    _, client, user, *_ = env
    conn = http.client.HTTPConnection("127.0.0.1", client.server.port, timeout=10)
    data = png_bytes()
    conn.putrequest("POST", "/api/temas/mio/recursos", skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{client.server.port}")
    conn.putheader("X-Luzeira-Token", client.server.token)
    conn.putheader("X-Luzeira-Filename", "a.png")
    conn.putheader("X-Luzeira-Filename", "b.png")
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    assert conn.getresponse().status == 400
    conn.close()
    assert sorted(p.name for p in (user / "mio").iterdir()) == ["theme.json"]


def test_copy_with_edited_theme_saves_it_in_one_step(env):
    _, client, user, *_ = env
    edited = client.json("GET", "/api/temas/completo")[1]["theme"]
    edited["widgets"] = [
        {
            "type": "image",
            "file": "img/logo.jpg",
            "box": {"x": 0, "y": 0, "width": 40, "height": 40},
        }
    ]
    status, body = client.json(
        "POST", "/api/temas/completo/copiar", {"title": "Editada", "theme": edited}
    )
    assert (status, body) == (200, {"name": "editada"})
    got = client.json("GET", "/api/temas/editada")[1]
    assert got["theme"]["name"] == "Editada"  # manda «title»
    assert [w["type"] for w in got["theme"]["widgets"]] == ["image"]
    assert (user / "editada" / "img" / "logo.jpg").is_file()
    # El original no se ha tocado
    assert len(client.json("GET", "/api/temas/completo")[1]["theme"]["widgets"]) > 1


def test_copy_of_builtin_with_edited_theme(env):
    _, client, user, *_ = env
    edited = builtin_theme()
    edited["background"] = {"color": "#123456", "image": "fondo.png"}
    status, body = client.json(
        "POST", "/api/temas/esencial/copiar", {"title": "Mía", "theme": edited}
    )
    assert status == 200
    saved = json.loads((user / body["name"] / "theme.json").read_text("utf-8"))
    assert saved["background"]["color"] == "#123456"


@pytest.mark.parametrize(
    "edited,path",
    [
        # Fichero que no está ni en el original ni en la copia
        (theme_dict(background={"color": "#000000", "image": "no/esta.png"}), "background.image"),
        # Esquema roto
        (theme_dict(orientation="de-lado"), "orientation"),
    ],
)
def test_copy_with_bad_edited_theme_leaves_nothing(env, edited, path):
    _, client, user, *_ = env
    before = sorted(p.name for p in user.iterdir())
    status, body = client.json(
        "POST", "/api/temas/completo/copiar", {"title": "Rota", "theme": edited}
    )
    assert status == 400, body
    assert body["details"][0]["path"] == path
    assert sorted(p.name for p in user.iterdir()) == before  # ni copia ni temporal


def test_copy_with_theme_not_object_is_400(env):
    _, client, user, *_ = env
    before = sorted(p.name for p in user.iterdir())
    status, _ = client.json("POST", "/api/temas/mio/copiar", {"title": "X", "theme": [1]})
    assert status == 400
    assert client.json("POST", "/api/temas/mio/copiar", [1])[0] == 400
    assert sorted(p.name for p in user.iterdir()) == before


def test_copy_validates_files_in_the_new_folder_not_the_original(env, monkeypatch):
    """Si un recurso no llega a la copia, el tema editado que lo usa no vale aunque el
    original sí lo tenga."""
    _, client, user, *_ = env
    from luzeira.editor import almacen

    real_copy = almacen.ThemeStore._copy_files

    def copy_without_logo(src, dest):
        real_copy(src, dest)
        (dest / "img" / "logo.jpg").unlink()

    monkeypatch.setattr(almacen.ThemeStore, "_copy_files", staticmethod(copy_without_logo))
    edited = theme_dict(
        widgets=[
            {
                "type": "image",
                "file": "img/logo.jpg",
                "box": {"x": 0, "y": 0, "width": 40, "height": 40},
            }
        ]
    )
    before = sorted(p.name for p in user.iterdir())
    status, body = client.json(
        "POST", "/api/temas/completo/copiar", {"title": "Sin logo", "theme": edited}
    )
    assert status == 400 and body["details"][0]["path"] == "widgets.0.file"
    assert sorted(p.name for p in user.iterdir()) == before


# --- Remate 2 (revisor de la fase 5) ------------------------------------------------------


def _raw_get(server: EditorServer, headers: list[tuple[str, str]], path="/api/temas") -> int:
    """GET con las cabeceras **tal cual** (repetidas incluidas); devuelve el código."""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
    for key, value in headers:
        conn.putheader(key, value)
    conn.endheaders()
    status = conn.getresponse().status
    conn.close()
    return status


def test_repeated_host_header_is_403(env):
    server, *_ = env
    good = f"127.0.0.1:{server.port}"
    token = ("X-Luzeira-Token", server.token)
    assert _raw_get(server, [("Host", good), token]) == 200  # control: así sí
    for hosts in ([good, "evil.com"], ["evil.com", good], [good, good]):
        assert _raw_get(server, [*(("Host", h) for h in hosts), token]) == 403, hosts
        assert _raw_get(server, [("Host", h) for h in hosts], path="/") == 403, hosts


def test_repeated_token_header_is_403(env):
    server, *_ = env
    host = ("Host", f"127.0.0.1:{server.port}")
    good = server.token
    assert _raw_get(server, [host, ("X-Luzeira-Token", good)]) == 200  # control
    for tokens in ([good, "malo"], ["malo", good], [good, good]):
        headers = [host, *(("X-Luzeira-Token", t) for t in tokens)]
        assert _raw_get(server, headers) == 403, tokens


def test_static_through_junction_to_outside_is_404(env):
    """Una «junction» de Windows dentro de ``web/`` que apunta fuera: el fichero de
    dentro no es un enlace (``is_symlink`` da False), solo lo para ``is_relative_to``."""
    _winapi = pytest.importorskip("_winapi")
    if not hasattr(_winapi, "CreateJunction"):
        pytest.skip("sin junctions (no es Windows)")
    server, client, _, tmp_path, _ = env
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "robado.html").write_text("secreto", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(server.web_root / "enlace"))
    assert (server.web_root / "enlace" / "robado.html").is_file()  # el truco funciona
    status, _, data = client.request("GET", "/enlace/robado.html", token=None)
    assert status == 404 and b"secreto" not in data


def test_static_symlink_is_404(env):
    """Un enlace simbólico dentro de ``web/`` (aunque apunte dentro) no se sirve."""
    server, client, *_ = env
    link = server.web_root / "assets" / "enlace.js"
    try:
        link.symlink_to(server.web_root / "assets" / "app.js")
    except OSError:
        pytest.skip("este Windows no deja crear enlaces simbólicos sin permisos")
    assert client.request("GET", "/assets/enlace.js", token=None)[0] == 404


def _server_with(tmp_path: Path, read_sensor) -> EditorServer:
    user = tmp_path / "temas-concurrencia"
    (user / "mio").mkdir(parents=True)
    (user / "mio" / "theme.json").write_text(json.dumps(theme_dict()), encoding="utf-8")
    return EditorServer(
        sensors=lambda: SENSORS,
        read_sensor=read_sensor,
        apply_theme=lambda n: None,
        status=lambda: ("running", ""),
        user_themes=user,
        trash=tmp_path / "papelera-concurrencia",
    )


def test_preview_waiting_behind_a_newer_one_is_superseded(tmp_path):
    """A se está pintando (bloqueada en su sensor); entran B y luego C. B no se llega a
    pintar (409 «superseded»), C sí; nunca hay dos pintándose a la vez."""
    gate = threading.Event()
    entered = threading.Event()
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "renders": 0}

    def read_sensor(sensor_id):
        with lock:
            state["active"] += 1
            state["renders"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        entered.set()
        gate.wait(10)
        with lock:
            state["active"] -= 1
        return READINGS["cpu.usage"]

    server = _server_with(tmp_path, read_sensor)
    server.start()
    try:
        client = Client(server)
        sensor = {
            "type": "sensor",
            "sensor": "cpu.usage",
            "box": {"x": 0, "y": 0, "width": 60, "height": 20},
        }
        theme = theme_dict(widgets=[sensor])
        results: dict[str, tuple[int, bytes]] = {}

        def send(label):
            status, _, data = _preview(client, "mio", theme)
            results[label] = (status, data)

        def wait_generation(n):
            deadline = time.monotonic() + 5
            while server._generation < n:
                assert time.monotonic() < deadline, "la petición no llegó"
                time.sleep(0.01)

        threads = {k: threading.Thread(target=send, args=(k,)) for k in "ABC"}
        threads["A"].start()
        assert entered.wait(5)
        threads["B"].start()
        wait_generation(2)
        threads["C"].start()
        wait_generation(3)
        time.sleep(0.1)  # B y C ya esperan el cerrojo
        gate.set()
        for t in threads.values():
            t.join(10)
        assert results["A"][0] == 200
        assert results["B"][0] == 409
        assert json.loads(results["B"][1])["superseded"] is True
        assert results["C"][0] == 200
        assert Image.open(io.BytesIO(results["C"][1])).size == (480, 320)
        assert state["renders"] == 2  # A y C; B no se pintó
        assert state["max_active"] == 1
    finally:
        gate.set()
        server.stop()


def test_preview_stops_mid_render_when_a_newer_one_arrives(env, monkeypatch):
    """Si entra otra vista previa a mitad de pintar, se deja en la siguiente imagen."""
    from luzeira.editor import servidor

    server, _, user, *_ = env
    (user / "mio" / "a.png").write_bytes(png_bytes((64, 64)))
    widgets = [
        {"type": "image", "file": "a.png", "box": {"x": 0, "y": 0, "width": w, "height": w}}
        for w in (10, 20, 30)
    ]
    body = {"name": "mio", "theme": theme_dict(widgets=widgets)}
    opened: list[str] = []
    real_open = servidor._PreviewRenderer._open

    def open_and_bump(self, rel):
        opened.append(rel)
        server._generation += 1  # «entra» otra petición
        return real_open(self, rel)

    monkeypatch.setattr(servidor._PreviewRenderer, "_open", open_and_bump)
    with pytest.raises(servidor.PreviewSuperseded):
        server.preview(body)
    assert opened == ["a.png"]


def test_cached_preview_matches_plain_renderer(env):
    """La vista previa con caché es idéntica píxel a píxel a la del motor de la pantalla
    (fondo «cover», imágenes «contain» con otra proporción, texto encima)."""
    from luzeira.editor.almacen import parse_theme
    from luzeira.render.motor import Renderer
    from luzeira.temas import resolve_font

    server, client, user, *_ = env
    Image.radial_gradient("L").resize((300, 120)).convert("RGB").save(user / "mio" / "f.png")
    Image.new("RGBA", (50, 90), (10, 200, 30, 128)).save(user / "mio" / "w.png")

    def box(x, y, w, h):
        return {"x": x, "y": y, "width": w, "height": h}

    theme = theme_dict(
        background={"color": "#101010", "image": "f.png"},
        widgets=[
            {"type": "image", "file": "w.png", "box": box(5, 5, 120, 40)},
            {"type": "image", "file": "f.png", "box": box(200, 100, 77, 99)},
            {"type": "text", "text": "Hola", "box": box(0, 0, 200, 60)},
        ],
    )
    parsed = parse_theme(theme)
    files = server.store.check_files(parsed, user / "mio")
    expected = Renderer(
        parsed,
        font_for=lambda style: resolve_font(style, files),
        read_sensor=lambda i: Reading(None, ""),
        files=files,
    ).full_frame()
    for _ in range(2):  # en frío y desde la caché
        status, _, data = _preview(client, "mio", theme)
        assert status == 200
        got = Image.open(io.BytesIO(data)).convert("RGB")
        assert got.tobytes() == expected.convert("RGB").tobytes()


def test_scaled_cache_notices_changed_file(env):
    _, client, user, *_ = env
    path = user / "mio" / "a.png"
    path.write_bytes(png_bytes((8, 8), (255, 0, 0)))
    theme = theme_dict(background={"color": "#000000", "image": "a.png"})
    first = Image.open(io.BytesIO(_preview(client, "mio", theme)[2])).convert("RGB")
    assert first.getpixel((10, 10)) == (255, 0, 0)
    stat = path.stat()
    path.write_bytes(png_bytes((8, 8), (0, 0, 255)))  # mismo tamaño en bytes
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    second = Image.open(io.BytesIO(_preview(client, "mio", theme)[2])).convert("RGB")
    assert second.getpixel((10, 10)) == (0, 0, 255)


def test_scaled_cache_memory_limit_and_lru():
    from luzeira.editor.servidor import ScaledImageCache

    cache = ScaledImageCache(limit_bytes=3 * 100 * 100 * 4)  # caben 3 de 100x100 RGBA
    imgs = {k: Image.new("RGBA", (100, 100)) for k in "abcd"}
    for k in "abc":
        cache.put((k,), imgs[k])
    assert cache.get(("a",)) is imgs["a"]  # «a» pasa a ser la más reciente
    cache.put(("d",), imgs["d"])
    assert cache.get(("b",)) is None  # se expulsa la menos usada
    assert {k for k in "acd" if cache.get((k,)) is not None} == set("acd")
    assert cache.used <= cache.limit and len(cache) == 3
    cache.put(("enorme",), Image.new("RGBA", (1000, 1000)))  # más que el límite: no entra
    assert cache.get(("enorme",)) is None and len(cache) == 3


def test_server_socket_is_exclusive_on_windows(env):
    server, *_ = env
    if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        pytest.skip("SO_EXCLUSIVEADDRUSE solo existe en Windows")
    sock = server._httpd.socket
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
    thief = socket.socket()
    try:
        thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            thief.bind(("127.0.0.1", server.port))
    finally:
        thief.close()


# --- Carreras guardar ↔ borrar -------------------------------------------------------------


class _DeleteBeforeLock:
    """Envuelve el cerrojo del almacén: justo antes de cogerlo, «otro hilo» mueve el tema
    a la papelera (lo que pasaba entre la búsqueda del tema y la escritura)."""

    def __init__(self, store, folder: Path, trash: Path) -> None:
        self._real = store._lock
        self._folder = folder
        self._trash = trash
        self.fired = False

    def __enter__(self):
        if not self.fired and self._folder.exists():
            self.fired = True
            self._trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self._folder), str(self._trash / "movido"))
        return self._real.__enter__()

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)


@pytest.mark.parametrize("op", ["save", "copy", "upload", "delete"])
def test_write_after_concurrent_delete_is_404_not_500(env, op):
    from luzeira.editor.almacen import GONE

    server, client, user, tmp_path, _ = env
    folder = user / "mio"
    (folder / "a.png").write_bytes(png_bytes())
    wrapper = _DeleteBeforeLock(server.store, folder, tmp_path / "papelera-ajena")
    server.store._lock = wrapper
    if op == "save":
        status, body = client.json("PUT", "/api/temas/mio", theme_dict())
    elif op == "copy":
        status, body = client.json("POST", "/api/temas/mio/copiar", {"title": "Copia"})
    elif op == "upload":
        status, body = upload(client, "mio", "b.png", png_bytes())
    else:
        status, body = client.json("DELETE", "/api/temas/mio")
    assert wrapper.fired
    assert status == 404, body
    assert body["error"] in ("No existe ese tema.", GONE)
    assert not any(p.name.startswith(".") for p in user.iterdir())  # sin temporales


def test_folder_removed_mid_save_is_404_with_clear_message(env, monkeypatch):
    """La carpeta desaparece **después** de buscar el tema (borrada a mano): 404 claro."""
    from luzeira.editor.almacen import GONE

    server, client, user, *_ = env
    real = server.store.check_files

    def check_then_vanish(theme, directory):
        out = real(theme, directory)
        shutil.rmtree(user / "mio")
        return out

    monkeypatch.setattr(server.store, "check_files", check_then_vanish)
    status, body = client.json("PUT", "/api/temas/mio", theme_dict())
    assert status == 404 and body["error"] == GONE


# --- Textos para el usuario (RQ-12) ---------------------------------------------------------


def test_messages_have_no_jargon(env):
    _, client, user, *_ = env
    box = {"x": 0, "y": 0, "width": 9, "height": 9}
    bad_bool = theme_dict(
        widgets=[{"type": "text", "text": "a", "box": box, "style": {"bold": "quizá"}}]
    )
    status, body = client.json("PUT", "/api/temas/mio", bad_bool)
    assert status == 400
    assert body["details"] == [{"path": "widgets.0.style.bold", "message": "tiene que ser sí o no"}]
    # Imagen que falta: «elemento», no «widget»
    missing = theme_dict(widgets=[{"type": "image", "file": "no.png", "box": box}])
    status, body = client.json("PUT", "/api/temas/mio", missing)
    assert status == 400
    assert body["details"][0]["message"].startswith("La imagen del elemento 1 ")
    # Tema roto en disco
    damaged = "Este tema está dañado y no se puede abrir."
    (user / "mio" / "theme.json").write_text('{"format": "luzeira-theme/1", "name": 5}', "utf-8")
    status, body = client.json("GET", "/api/temas/mio")
    assert status == 400 and body["error"] == damaged
    (user / "mio" / "theme.json").write_text("{esto no es json", "utf-8")
    status, body = client.json("GET", "/api/temas/mio")
    assert status == 400 and body["error"] == damaged
    assert all("JSON" not in d["message"] for d in body.get("details", []))
    # Aplicar un tema roto: tampoco nombra theme.json
    status, body = client.json("POST", "/api/temas/mio/aplicar", b"")
    assert (status, body["error"]) == (400, damaged)
    # Datos del tema que no son un objeto
    status, body = client.json("POST", "/api/vista-previa", {"name": "mio", "theme": [1]})
    assert status == 400 and "{" not in body["error"]


def test_untranslated_pydantic_error_is_generic_spanish():
    from luzeira.editor.almacen import GENERIC_INVALID, _item_message

    item = {"type": "tipo_nuevo_de_pydantic", "msg": "Input should be blah", "loc": ("x",)}
    assert _item_message(item) == GENERIC_INVALID
    no_ctx = {"type": "value_error", "msg": "Value error, x", "loc": ()}
    assert _item_message(no_ctx) == GENERIC_INVALID
    bar = {"type": "value_error", "msg": "Value error, …", "loc": ()}
    bar["ctx"] = {"error": ValueError("max tiene que ser mayor que min")}
    assert _item_message(bar) == "el máximo tiene que ser mayor que el mínimo"


def test_editor_default_theme_matches_the_app():
    # Si se borra el tema en pantalla, el editor pone este: tiene que existir y ser el de
    # la app (editor/src/model/contract.ts).
    import re

    from luzeira.config import DEFAULT_THEME

    source = Path(__file__).resolve().parent.parent / "editor" / "src" / "model" / "contract.ts"
    match = re.search(r"export const DEFAULT_THEME = '([^']+)'", source.read_text(encoding="utf-8"))
    assert match and match.group(1) == DEFAULT_THEME


# --- Ajustes de la app (ventana «Ajustes») ------------------------------------------------


class FakeSettings:
    """Lo que haría la bandeja, apuntado. Importa de verdad con ``import_theme_zip``."""

    def __init__(self, user: Path) -> None:
        self.user = user
        self.calls: list[tuple] = []

    def snapshot(self) -> dict:
        return {"brightness": 50, "choices": []}

    def update(self, **kw) -> None:
        self.calls.append(("update", kw))

    def choose_display(self, choice_id, *, confirmed) -> None:
        self.calls.append(("display", choice_id, confirmed))

    def create_shortcut(self) -> str:
        self.calls.append(("shortcut",))
        return "Listo."

    def open_themes_folder(self) -> None:
        self.calls.append(("folder",))

    def import_theme(self, zip_path: Path) -> str:
        return cargar.import_theme_zip(zip_path, self.user).name

    def question(self) -> dict | None:
        return None

    def answer(self, looks_right: bool) -> str:
        raise AssertionError("sin pregunta pendiente")

    def updates_question(self) -> bool:
        return False

    def update_available(self) -> str | None:
        return None

    def check_updates(self) -> str:
        self.calls.append(("check",))
        return "Ya tienes la última versión."

    def install_update(self) -> str:
        self.calls.append(("install",))
        raise EditorError(409, "No hay ninguna versión nueva pendiente.")


@pytest.fixture
def with_settings(env):
    server, client, user, *_ = env
    fake = FakeSettings(user)
    server._settings = fake
    return client, fake, user


def zip_of(folder: Path) -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in folder.rglob("*"):
            if p.is_file():
                zf.write(p, f"{folder.name}/{p.relative_to(folder).as_posix()}")
    return buf.getvalue()


def test_settings_routes_are_404_without_a_tray(env):
    _, client, *_ = env
    for method, path in [("GET", "/api/ajustes"), ("POST", "/api/carpeta-temas")]:
        assert client.json(method, path)[0] == 404


def test_settings_routes_need_the_token(with_settings):
    client, fake, _ = with_settings
    for method, path, body in [
        ("GET", "/api/ajustes", None),
        ("PUT", "/api/ajustes", {"flip": True}),
        ("POST", "/api/pantalla", {"auto": True}),
        ("POST", "/api/acceso-escritorio", None),
        ("POST", "/api/carpeta-temas", None),
    ]:
        assert client.json(method, path, body, token=None)[0] == 403
        assert client.json(method, path, body, origin="http://evil.com")[0] == 403
    assert fake.calls == []


def test_settings_read_and_update(with_settings):
    client, fake, _ = with_settings
    assert client.json("GET", "/api/ajustes") == (200, {"brightness": 50, "choices": []})
    status, _ = client.json("PUT", "/api/ajustes", {"brightness": 75, "autostart": True})
    assert status == 200
    assert fake.calls == [
        ("update", {"brightness": 75, "flip": None, "autostart": True, "updates": None})
    ]
    assert client.json("PUT", "/api/ajustes", {"updates": False})[0] == 200
    assert fake.calls[-1] == (
        "update",
        {"brightness": None, "flip": None, "autostart": None, "updates": False},
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"brightness": True},
        {"brightness": "75"},
        {"flip": 1},
        {"updates": "sí"},
        {"updates": 1},
        {"otra": 1},
        [],
        b'"x"',
    ],
)
def test_settings_update_rejects_bad_values(with_settings, body):
    client, fake, _ = with_settings
    assert client.json("PUT", "/api/ajustes", body)[0] == 400
    assert fake.calls == []


def test_display_choice_routes(with_settings):
    client, fake, _ = with_settings
    assert client.json("POST", "/api/pantalla", {"auto": True})[0] == 200
    assert client.json("POST", "/api/pantalla", {"id": "lista:COM3:x", "confirmed": True})[0] == 200
    for bad in [{"auto": False}, {"id": 3}, {"id": ""}, {"id": "a", "confirmed": "sí"}]:
        assert client.json("POST", "/api/pantalla", bad)[0] == 400
    assert fake.calls == [("display", None, False), ("display", "lista:COM3:x", True)]


def test_shortcut_and_folder(with_settings):
    client, fake, _ = with_settings
    assert client.json("POST", "/api/acceso-escritorio") == (200, {"message": "Listo."})
    assert client.json("POST", "/api/carpeta-temas") == (200, {"ok": True})
    assert fake.calls == [("shortcut",), ("folder",)]


def test_import_theme_zip_from_the_editor(with_settings):
    client, _, user = with_settings
    data = zip_of(FIXTURES / "ejemplo-completo")
    status, body = client.json(
        "POST", "/api/importar-tema", data, headers={"X-Luzeira-Filename": "mi%20tema.zip"}
    )
    assert status == 200
    assert (user / body["name"] / "theme.json").is_file()


def test_import_rejects_bad_zip_and_wrong_name(with_settings):
    client, _, user = with_settings
    before = sorted(p.name for p in user.iterdir())
    headers = {"X-Luzeira-Filename": "tema.zip"}
    status, body = client.json("POST", "/api/importar-tema", b"no es un zip", headers=headers)
    assert status == 400 and body["error"]
    data = zip_of(FIXTURES / "ejemplo-completo")
    exe = {"X-Luzeira-Filename": "tema.exe"}
    assert client.json("POST", "/api/importar-tema", data, headers=exe)[0] == 400
    assert client.json("POST", "/api/importar-tema", data)[0] == 400  # sin nombre
    assert sorted(p.name for p in user.iterdir()) == before


def test_import_too_big_is_413(with_settings):
    client, *_ = with_settings
    big = b"x" * (cargar.MAX_ZIP_BYTES + 1)
    headers = {"X-Luzeira-Filename": "t.zip"}
    assert client.json("POST", "/api/importar-tema", big, headers=headers)[0] == 413


def test_question_is_in_info_and_is_answered(with_settings):
    client, fake, _ = with_settings
    fake.question = lambda: {"title": "¿Se ve bien?", "text": "…"}
    fake.answer = lambda looks_right: f"respuesta {looks_right}"
    status, info = client.json("GET", "/api/info")
    assert status == 200 and info["question"]["title"] == "¿Se ve bien?"
    body = {"looks_right": False}
    assert client.json("POST", "/api/pantalla/respuesta", body) == (
        200,
        {"message": "respuesta False"},
    )
    for bad in [{}, {"looks_right": 1}, {"looks_right": True, "x": 1}]:
        assert client.json("POST", "/api/pantalla/respuesta", bad)[0] == 400


def test_info_without_tray_has_no_question(env):
    _, client, *_ = env
    info = client.json("GET", "/api/info")[1]
    assert info["question"] is None and info["updates_question"] is False
    assert info["update_available"] is None


def test_update_routes(with_settings):
    client, fake, _ = with_settings
    fake.updates_question = lambda: True
    fake.update_available = lambda: "0.9.0"
    info = client.json("GET", "/api/info")[1]
    assert info["updates_question"] is True and info["update_available"] == "0.9.0"
    assert client.json("POST", "/api/actualizaciones/buscar") == (
        200,
        {"message": "Ya tienes la última versión."},
    )
    status, body = client.json("POST", "/api/actualizaciones/instalar")
    assert status == 409 and body["error"] == "No hay ninguna versión nueva pendiente."
    assert fake.calls == [("check",), ("install",)]
    for path in ("/api/actualizaciones/buscar", "/api/actualizaciones/instalar"):
        assert client.json("POST", path, token=None)[0] == 403
        assert client.json("POST", path, origin="http://evil.com")[0] == 403
        assert client.json("GET", path)[0] == 404
    assert fake.calls == [("check",), ("install",)]


# --- C-17: letras que faltan en la fuente ------------------------------------------------


def _box():
    return {"x": 0, "y": 0, "width": 200, "height": 40}


def test_preview_reports_letters_the_font_does_not_have(env):
    _, client, *_ = env
    widgets = [
        {"type": "text", "text": "Hola ñ €", "box": _box()},  # la fuente incluida las tiene
        {"type": "text", "text": "CPU \U0001f525 ☃", "box": _box()},  # emoji y muñeco
        {"type": "sensor", "sensor": "cpu.usage", "suffix": "☃", "box": _box()},
        {"type": "clock", "format": "date-long", "box": _box()},
    ]
    status, headers, _ = _preview(client, "mio", theme_dict(widgets=widgets))
    assert status == 200
    header = headers["x-luzeira-missing-glyphs"]
    assert header.isascii()
    assert json.loads(header) == [
        {"index": 1, "chars": "\U0001f525☃"},
        {"index": 2, "chars": "☃"},
    ]


def test_preview_without_missing_letters_sends_an_empty_list(env):
    _, client, *_ = env
    _, headers, _ = _preview(client, "mio", theme_dict())
    assert json.loads(headers["x-luzeira-missing-glyphs"]) == []
