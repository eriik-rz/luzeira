"""Actualizaciones (ADR-006): la firma manda, nada de red sin pasar por la lista de GitHub,
y el instalador solo se abre si casa con lo firmado.

Las claves de prueba se generan aquí: la privada de verdad nunca está en el repo.
"""

from __future__ import annotations

import base64
import hashlib
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


def test_download_checks_size_and_hash(tmp_path, key):
    private, _ = key
    m = manifest()
    path = actualizar.download(m, http=github_with(private, m), folder=tmp_path)
    assert path == tmp_path / m.file and path.read_bytes() == INSTALLER


@pytest.mark.parametrize("served", [INSTALLER[:-1] + b"X", INSTALLER + b"!"])
def test_a_tampered_installer_never_stays(tmp_path, key, served):
    private, _ = key
    m = manifest()
    with pytest.raises(UpdateError):
        actualizar.download(m, http=github_with(private, m, served), folder=tmp_path)
    assert list(tmp_path.iterdir()) == []  # ni el bueno ni el «.descargando»


def test_launch_checks_again_right_before(tmp_path, monkeypatch):
    m = manifest()
    exe = tmp_path / m.file
    exe.write_bytes(INSTALLER)
    started = []
    monkeypatch.setattr(actualizar.subprocess, "Popen", lambda *a, **k: started.append(a))
    exe.write_bytes(INSTALLER[:-1] + b"X")  # cambiado tras descargarlo
    with pytest.raises(UpdateError):
        actualizar.launch(exe, m, tmp_path / "log.txt")
    assert started == []
    exe.write_bytes(INSTALLER)
    actualizar.launch(exe, m, tmp_path / "log.txt")
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
    actualizar.clean_downloads(tmp_path)
    assert list(tmp_path.iterdir()) == []
    actualizar.clean_downloads(tmp_path / "no-existe")  # sin carpeta, nada


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


@pytest.mark.parametrize("version", ["00.1.0", "\u0661.2.3", "1.2", "1.2.3.4", " 1.2.3"])
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
