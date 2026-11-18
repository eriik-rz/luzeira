"""scripts/firmar.py: lo que firma es lo que la app acepta, y la clave no cae en el repo."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from luzeira import actualizar

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def firmar():
    spec = importlib.util.spec_from_file_location("firmar", ROOT / "scripts" / "firmar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer(tmp_path):
    path = tmp_path / "Luzeira-0.2.0-instalador.exe"
    path.write_bytes(b"MZ instalador de prueba")
    return path


def sums_for(path: Path) -> str:
    return f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\nabc  otro.zip\n"


def test_key_roundtrip_and_signature_the_app_accepts(firmar, tmp_path, installer, monkeypatch):
    public = firmar.write_key(tmp_path / "clave.pem", b"una contrasena larga")
    assert b"ENCRYPTED" in (tmp_path / "clave.pem").read_bytes()  # cifrada, nunca en claro
    private = firmar.read_key(tmp_path / "clave.pem", b"una contrasena larga")
    monkeypatch.setattr(actualizar, "PUBLIC_KEYS", (public,))
    manifest = firmar.make_manifest("0.2.0", installer, sums_for(installer))
    data, sig = firmar.sign(manifest, private)
    assert actualizar.verify_manifest(data, sig, [public]) == manifest
    assert manifest.size == installer.stat().st_size


def test_wrong_password_is_refused(firmar, tmp_path):
    firmar.write_key(tmp_path / "clave.pem", b"una contrasena larga")
    with pytest.raises(SystemExit, match="contraseña"):
        firmar.read_key(tmp_path / "clave.pem", b"otra")


def test_a_key_the_app_does_not_know_does_not_sign(firmar, tmp_path, installer, monkeypatch):
    firmar.write_key(tmp_path / "clave.pem", b"una contrasena larga")
    private = firmar.read_key(tmp_path / "clave.pem", b"una contrasena larga")
    monkeypatch.setattr(actualizar, "PUBLIC_KEYS", ())
    manifest = firmar.make_manifest("0.2.0", installer, sums_for(installer))
    with pytest.raises(SystemExit, match="PUBLIC_KEYS"):
        firmar.sign(manifest, private)


def test_installer_must_match_sha256sums(firmar, installer):
    with pytest.raises(SystemExit, match="SHA256SUMS"):
        firmar.make_manifest("0.2.0", installer, "0" * 64 + "  " + installer.name)


@pytest.mark.parametrize("where", ["clave.pem", "docs/clave.pem", "src/luzeira/clave.pem"])
def test_the_key_never_goes_inside_the_repo(firmar, where):
    with pytest.raises(SystemExit, match="repositorio"):
        firmar.write_key(ROOT / where, b"una contrasena larga")
    assert not (ROOT / where).exists()


def test_an_existing_key_is_not_overwritten(firmar, tmp_path):
    firmar.write_key(tmp_path / "clave.pem", b"una contrasena larga")
    before = (tmp_path / "clave.pem").read_bytes()
    with pytest.raises(SystemExit, match="sobrescribo"):
        firmar.write_key(tmp_path / "clave.pem", b"una contrasena larga")
    assert (tmp_path / "clave.pem").read_bytes() == before
