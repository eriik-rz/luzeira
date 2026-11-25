"""Actualizaciones (ADR-006): la firma manda, nada de red sin pasar por la lista de GitHub,
y el instalador solo se abre si casa con lo firmado.

Las claves de prueba se generan aquí: la privada de verdad nunca está en el repo.
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from luzeira import actualizar, idioma
from luzeira.actualizar import Manifest, UpdateError

INSTALLER = b"MZ esto haria de instalador " * 100


def new_key() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.b64encode(public).decode("ascii")


def manifest(version: str = "0.2.0", data: bytes = INSTALLER, **changes) -> Manifest:
    fields = {
        "app": "luzeira",
        "version": version,
        "file": f"Luzeira-{version}-instalador.exe",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    fields.update(changes)
    return Manifest(**fields)


def signed(private: Ed25519PrivateKey, m: Manifest) -> tuple[bytes, bytes]:
    data = actualizar.manifest_bytes(m)
    return data, base64.b64encode(private.sign(data))


class FakeHttp:
    """GitHub de mentira: sirve lo que se le dé por URL y apunta lo que se pide."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.asked: list[str] = []

    def get(self, url: str, limit: int) -> bytes:
        self.asked.append(url)
        data = self.files[url]
        if len(data) > limit:
            raise UpdateError("grande")
        return data

    def save(self, url: str, dest: Path, limit: int) -> None:
        dest.write_bytes(self.get(url, limit))


@pytest.fixture
def key():
    return new_key()


def github_with(private, m: Manifest, installer: bytes = INSTALLER) -> FakeHttp:
    data, sig = signed(private, m)
    return FakeHttp(
        {
            actualizar.MANIFEST_URL: data,
            actualizar.SIGNATURE_URL: sig,
            m.installer_url: installer,
        }
    )


# --- firma ------------------------------------------------------------------------------


def test_a_good_signature_gives_the_manifest(key):
    private, public = key
    m = manifest()
    data, sig = signed(private, m)
    assert actualizar.verify_manifest(data, sig, [public]) == m


def test_the_backup_key_also_works(key):
    private, public = key
    _, other = new_key()
    data, sig = signed(private, manifest())
    assert actualizar.verify_manifest(data, sig, [other, public]).version == "0.2.0"


def test_another_key_or_a_changed_byte_is_rejected(key):
    private, public = key
    data, sig = signed(private, manifest())
    stranger, _ = new_key()
    _, bad_sig = signed(stranger, manifest())
    with pytest.raises(UpdateError, match="firma"):
        actualizar.verify_manifest(data, bad_sig, [public])
    with pytest.raises(UpdateError, match="firma"):
        actualizar.verify_manifest(data.replace(b"0.2.0", b"0.2.1"), sig, [public])
    with pytest.raises(UpdateError, match="firma"):
        actualizar.verify_manifest(data, b"no es base64 !!", [public])


def test_a_signed_but_malformed_manifest_is_rejected(key):
    private, public = key
    for raw in [
        b"{}",
        b'{"app":"otra","version":"0.2.0","file":"Luzeira-0.2.0-instalador.exe",'
        b'"size":1,"sha256":"' + b"a" * 64 + b'"}',
        # el nombre no es el de esa versión
        b'{"app":"luzeira","version":"0.2.0","file":"Luzeira-0.3.0-instalador.exe",'
        b'"size":1,"sha256":"' + b"a" * 64 + b'"}',
        # campos de más
        b'{"app":"luzeira","version":"0.2.0","file":"Luzeira-0.2.0-instalador.exe",'
        b'"size":1,"sha256":"' + b"a" * 64 + b'","url":"https://evil.example"}',
        # tamaño como texto (estricto)
        b'{"app":"luzeira","version":"0.2.0","file":"Luzeira-0.2.0-instalador.exe",'
        b'"size":"1","sha256":"' + b"a" * 64 + b'"}',
    ]:
        sig = base64.b64encode(private.sign(raw))
        with pytest.raises(UpdateError, match="no es válida"):
            actualizar.verify_manifest(raw, sig, [public])


@pytest.mark.parametrize(
    "changes",
    [
        {"file": "..\\Luzeira-0.2.0-instalador.exe"},
        {"file": "Luzeira-0.2.0-instalador.exe.bat"},
        {"version": "0.2.0-rc1"},
        {"version": "01.2.0"},
        {"sha256": "A" * 64},
        {"size": 0},
        {"size": actualizar.MAX_INSTALLER_BYTES + 1},
    ],
)
def test_manifest_fields_are_strict(changes):
    with pytest.raises(ValueError):
        manifest(**changes)


# --- versiones ----------------------------------------------------------------------------


def test_only_newer_versions_are_offered():
    m = manifest("0.2.0")
    assert actualizar.is_newer(m, "0.1.0", None)
    assert actualizar.is_newer(m, "0.1.9", "0.2.0")  # la misma que ya se vio: vale
    assert not actualizar.is_newer(m, "0.2.0", None)  # la instalada
    assert not actualizar.is_newer(m, "0.3.0", None)  # más vieja que la instalada
    assert not actualizar.is_newer(m, "0.1.0", "0.2.1")  # alguien quiere «congelarla»
    assert actualizar.is_newer(manifest("0.10.0"), "0.9.0", None)  # números, no texto


def test_an_impossible_jump_is_neither_offered_nor_seen(key):
    # R3-07: con una clave robada se firma la 9999.0.0. Si se ofreciera, se guardaría como
    # «la más alta vista» y ninguna versión buena volvería a ofrecerse.
    assert not actualizar.is_newer(manifest("9999.0.0"), "0.1.7", None)
    assert not actualizar.is_newer(manifest("2.0.0"), "0.1.7", None)
    assert actualizar.is_newer(manifest("1.0.0"), "0.1.7", None)  # la 1.0 tiene que llegar
    assert actualizar.is_newer(manifest("1.9999.9999"), "0.1.7", None)
    assert actualizar.is_newer(manifest("0.9999.0"), "0.1.7", None)
    assert actualizar.is_newer(manifest("3.0.0"), "2.5.0", None)
    # check() devuelve None, y bandeja solo guarda max_seen de lo que se ofrece.
    private, public = key
    http = github_with(private, manifest("9999.0.0"))
    assert actualizar.check(installed="0.1.7", http=http, keys=[public]) is None


# --- comprobar ----------------------------------------------------------------------------


def test_check_finds_a_new_version(key):
    private, public = key
    m = manifest("0.2.0")
    http = github_with(private, m)
    assert actualizar.check(installed="0.1.0", http=http, keys=[public]) == m
    assert http.asked == [actualizar.MANIFEST_URL, actualizar.SIGNATURE_URL]


def test_check_says_none_when_up_to_date(key):
    private, public = key
    http = github_with(private, manifest("0.1.0"))
    assert actualizar.check(installed="0.1.0", http=http, keys=[public]) is None


def test_check_without_keys_never_connects():
    http = FakeHttp({})
    with pytest.raises(UpdateError):
        actualizar.check(installed="0.1.0", http=http, keys=[])
    assert http.asked == []


def test_urls_are_fixed_to_the_official_repo():
    assert actualizar.MANIFEST_URL.startswith("https://github.com/eriik-rz/luzeira/releases/")
    assert manifest().installer_url == (
        "https://github.com/eriik-rz/luzeira/releases/download/v0.2.0/Luzeira-0.2.0-instalador.exe"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/eriik-rz/luzeira/releases/latest",
        "https://github.com.evil.example/x",
        "https://evil.example/x",
        "https://github.com:8443/x",
        "file:///C:/Windows/notepad.exe",
    ],
)
def test_only_https_github_hosts_are_allowed(url):
    with pytest.raises(UpdateError, match="GitHub"):
        actualizar._check_url(url)


def test_redirects_outside_github_are_refused():
    handler = actualizar._OnlyGitHub()
    with pytest.raises(UpdateError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x.exe")


# --- descargar e instalar ------------------------------------------------------------------


def not_elevated() -> bool:
    return False


def test_download_checks_size_and_hash(tmp_path, key):
    private, _ = key
    m = manifest()
    path = actualizar.download(
        m, http=github_with(private, m), folder=tmp_path, elevated=not_elevated
    )
    assert path.name == m.file and path.read_bytes() == INSTALLER
    # En una subcarpeta nueva de nombre aleatorio, distinta en cada descarga (R3-08).
    assert path.parent.parent == tmp_path and len(path.parent.name) == 16
    again = actualizar.download(
        m, http=github_with(private, m), folder=tmp_path, elevated=not_elevated
    )
    assert again.parent != path.parent


@pytest.mark.parametrize("served", [INSTALLER[:-1] + b"X", INSTALLER + b"!"])
def test_a_tampered_installer_never_stays(tmp_path, key, served):
    private, _ = key
    m = manifest()
    with pytest.raises(UpdateError):
        actualizar.download(
            m, http=github_with(private, m, served), folder=tmp_path, elevated=not_elevated
        )
    assert list(tmp_path.iterdir()) == []  # ni el bueno, ni el «.descargando», ni la carpeta


def test_elevated_never_downloads_nor_launches(tmp_path, key, monkeypatch):
    # R3-08: como administrador, el hueco entre comprobar y abrir sería una elevación.
    private, _ = key
    m = manifest()
    http = github_with(private, m)
    with pytest.raises(UpdateError, match="administrador"):
        actualizar.download(m, http=http, folder=tmp_path, elevated=lambda: True)
    assert http.asked == [] and list(tmp_path.iterdir()) == []
    exe = tmp_path / m.file
    exe.write_bytes(INSTALLER)
    started = []
    monkeypatch.setattr(actualizar.subprocess, "Popen", lambda *a, **k: started.append(a))
    with pytest.raises(UpdateError, match="administrador"):
        actualizar.launch(exe, m, tmp_path / "log.txt", elevated=lambda: True)
    assert started == []
    # Sin decirle nada, pregunta a Windows de verdad (is_elevated), no a un valor fijo.
    monkeypatch.setattr(actualizar, "is_elevated", lambda: True)
    with pytest.raises(UpdateError, match="administrador"):
        actualizar.launch(exe, m, tmp_path / "log.txt")
    assert started == []


def test_is_elevated_asks_windows():
    # Los tests no corren como administrador (ni en local ni en GitHub Actions con el
    # usuario runneradmin, cuyo token sí está elevado: allí solo se mira que conteste).
    result = actualizar.is_elevated()
    assert isinstance(result, bool)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        assert result is False


@pytest.mark.skipif(os.name != "nt", reason="el bloqueo es de Windows")
def test_the_installer_is_locked_while_checked_and_launched(tmp_path, monkeypatch):
    # R3-08 (TOCTOU): el SHA-256 se calcula sobre el fichero abierto y bloqueado, y
    # nadie puede reescribirlo, borrarlo ni cambiarlo de nombre hasta después de Popen.
    m = manifest()
    exe = tmp_path / m.file
    exe.write_bytes(INSTALLER)
    attempts = []

    def fake_popen(args, **kwargs):
        for attempt in (
            lambda: exe.open("r+b"),
            lambda: exe.unlink(),
            lambda: os.replace(exe, tmp_path / "otro.exe"),
        ):
            try:
                opened = attempt()
                if opened is not None:
                    opened.close()
            except PermissionError:
                attempts.append("bloqueado")
            else:
                attempts.append("cambiado")
        # Leer (lo que hace Windows al ejecutarlo) sí se puede.
        assert exe.read_bytes() == INSTALLER

    monkeypatch.setattr(actualizar.subprocess, "Popen", fake_popen)
    actualizar.launch(exe, m, tmp_path / "log.txt", elevated=not_elevated)
    assert attempts == ["bloqueado"] * 3
    exe.write_bytes(INSTALLER)  # suelto después: ya se puede


@pytest.mark.skipif(os.name != "nt", reason="el bloqueo es de Windows")
def test_a_locked_installer_can_still_be_run(tmp_path):
    # Que el bloqueo no impida lo que tiene que pasar: Windows ejecuta un .exe abierto
    # así. Un programa del sistema copiado al tmp, que solo sale con un código.
    exe = tmp_path / "prueba.exe"
    exe.write_bytes((Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe").read_bytes())
    with actualizar._open_locked(exe) as locked:
        assert hashlib.sha256(locked.read()).digest() == hashlib.sha256(exe.read_bytes()).digest()
        done = subprocess.run([str(exe), "/d", "/c", "exit 7"], check=False)
    assert done.returncode == 7


def test_the_hash_is_taken_from_the_open_file_not_the_path(tmp_path, monkeypatch):
    # Si alguien cambiara el fichero en la ruta después de abrirlo, cuenta lo abierto.
    m = manifest()
    exe = tmp_path / m.file
    exe.write_bytes(INSTALLER)
    calls = []
    real = actualizar._check_open
    monkeypatch.setattr(actualizar, "_check_open", lambda f, mm: calls.append(f) or real(f, mm))
    monkeypatch.setattr(actualizar.subprocess, "Popen", lambda *a, **k: None)
    actualizar.launch(exe, m, tmp_path / "log.txt", elevated=not_elevated)
    assert len(calls) == 1 and calls[0].closed  # se usó el abierto, y ya está cerrado


def test_launch_checks_again_right_before(tmp_path, monkeypatch):
    m = manifest()
    exe = tmp_path / m.file
    exe.write_bytes(INSTALLER)
    started = []
    monkeypatch.setattr(actualizar.subprocess, "Popen", lambda *a, **k: started.append(a))
    exe.write_bytes(INSTALLER[:-1] + b"X")  # cambiado tras descargarlo
    with pytest.raises(UpdateError):
        actualizar.launch(exe, m, tmp_path / "log.txt", elevated=not_elevated)
    assert started == []
    exe.write_bytes(INSTALLER)
    actualizar.launch(exe, m, tmp_path / "log.txt", elevated=not_elevated)
    assert len(started) == 1


def test_installer_is_silent_and_marked_as_an_update(tmp_path):
    cmd = actualizar.launch_command(tmp_path / "x.exe", tmp_path / "log.txt")
    assert cmd.args[1:5] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ACTUALIZAR"]
    assert cmd.args[-1] == f"/LOG={tmp_path / 'log.txt'}"
    assert cmd.args[-2] == "/LANG=es"  # el idioma de Luzeira (conftest: español)
    idioma.set_language("de")
    cmd = actualizar.launch_command(tmp_path / "x.exe", tmp_path / "log.txt")
    assert "/LANG=de" in cmd.args
    assert "/CURRENTUSER" not in cmd.args  # no hace nada en nuestro instalador (C-53)


def test_clean_downloads(tmp_path):
    (tmp_path / "Luzeira-0.2.0-instalador.exe").write_bytes(b"x")
    (tmp_path / "0123456789abcdef").mkdir()  # la subcarpeta aleatoria de download()
    (tmp_path / "0123456789abcdef" / "Luzeira-0.2.0-instalador.exe").write_bytes(b"x")
    actualizar.clean_downloads(tmp_path)
    assert list(tmp_path.iterdir()) == []
    actualizar.clean_downloads(tmp_path / "no-existe")  # sin carpeta, nada


@pytest.mark.skipif(os.name != "nt", reason="uniones (junctions) de Windows")
def test_clean_downloads_does_not_follow_junctions(tmp_path):
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "importante.txt").write_bytes(b"no se borra")
    folder = tmp_path / "actualizacion"
    folder.mkdir()
    done = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(folder / "enlace"), str(outside)],
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done
    actualizar.clean_downloads(folder)
    assert (outside / "importante.txt").read_bytes() == b"no se borra"


def test_the_shipped_keys_are_two_distinct_ed25519_public_keys():
    keys = actualizar.PUBLIC_KEYS
    assert len(keys) == 2 and len(set(keys)) == 2  # la principal y la de reserva
    assert len(actualizar._load_keys(keys)) == 2


def test_the_signature_is_checked_before_reading_anything(key):
    _, public = key
    stranger, _ = new_key()
    garbage = b'{"esto": "no es un manifiesto"}'
    with pytest.raises(UpdateError, match="firma"):  # y no «no es válida»
        actualizar.verify_manifest(garbage, base64.b64encode(stranger.sign(garbage)), [public])


@pytest.mark.parametrize(
    "version", ["00.1.0", "\u0661.2.3", "1.2", "1.2.3.4", " 1.2.3", "1.2.3\n", "1.2.3 "]
)
def test_versions_are_only_plain_ascii_numbers(version):
    with pytest.raises(ValueError):
        actualizar.parse_version(version)


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read(self, n: int = -1) -> bytes:
        end = len(self.data) if n < 0 else self.pos + n
        chunk = self.data[self.pos : end]
        self.pos += len(chunk)
        return chunk

    read1 = read  # el cliente real lee con read1 (una sola espera por trozo)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_real_client_follows_only_github_redirects():
    http = actualizar.GitHubHttp()
    assert any(isinstance(h, actualizar._OnlyGitHub) for h in http._opener.handlers)


def test_the_real_client_never_reads_past_the_limit(tmp_path, monkeypatch):
    http = actualizar.GitHubHttp()
    monkeypatch.setattr(http._opener, "open", lambda url, timeout: FakeResponse(b"x" * 100))
    assert http.get(actualizar.MANIFEST_URL, 100) == b"x" * 100
    with pytest.raises(UpdateError):
        http.get(actualizar.MANIFEST_URL, 99)
    with pytest.raises(UpdateError):
        http.save(actualizar.MANIFEST_URL, tmp_path / "a", 99)
    http.save(actualizar.MANIFEST_URL, tmp_path / "b", 100)
    assert (tmp_path / "b").read_bytes() == b"x" * 100
    with pytest.raises(UpdateError, match="GitHub"):
        http.get("https://evil.example/manifest.json", 100)


class DrippingResponse(FakeResponse):
    """Un servidor que gotea: cada trozo llega, pero el reloj avanza ``step`` segundos."""

    def __init__(self, data: bytes, clock: list[float], step: float) -> None:
        super().__init__(data)
        self.clock = clock
        self.step = step

    def read1(self, n: int = -1) -> bytes:
        self.clock[0] += self.step
        return super().read(min(n, 10))


def test_the_real_client_has_a_total_deadline(tmp_path, monkeypatch):
    # R3-10: TIMEOUT es por lectura; sin plazo total, un byte cada 19 s duraría horas.
    now = [1000.0]
    http = actualizar.GitHubHttp(clock=lambda: now[0])
    data = b"x" * 100  # 10 trozos de 10 bytes (y una lectura vacía al final)

    def serve(step):
        monkeypatch.setattr(
            http._opener, "open", lambda url, timeout: DrippingResponse(data, now, step)
        )

    serve(5)  # 50 s: dentro de SMALL_DEADLINE (60 s)
    assert http.get(actualizar.MANIFEST_URL, 4096) == data
    serve(7)  # 70 s: se corta
    with pytest.raises(UpdateError, match="lenta"):
        http.get(actualizar.MANIFEST_URL, 4096)
    # Instalador: el plazo crece con el tamaño firmado (velocidad mínima).
    allowed = actualizar.SMALL_DEADLINE + 100 / actualizar.MIN_SPEED
    serve(allowed / 10 * 0.9)
    http.save(actualizar.MANIFEST_URL, tmp_path / "ok", 100)
    assert (tmp_path / "ok").read_bytes() == data
    serve(allowed / 10 * 1.2)
    with pytest.raises(UpdateError, match="lenta"):
        http.save(actualizar.MANIFEST_URL, tmp_path / "lento", 100)


def test_the_installer_deadline_is_generous_for_a_real_installer():
    # Un instalador de 20 MB (los de la 0.1.x pesan unos 20) tiene más de 10 minutos.
    assert actualizar.SMALL_DEADLINE + 20 * 1024 * 1024 / actualizar.MIN_SPEED > 600
