"""scripts/firmar.py: lo que firma es lo que la app acepta, la clave no cae en el repo y
no se firma nada que no venga atestiguado por nuestro workflow en el commit de la etiqueta."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from luzeira import actualizar

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "firmar.py"
COMMIT = "e7490bf6172886651b16c0ae17629eb35bd2e454"


@pytest.fixture(scope="module")
def firmar():
    spec = importlib.util.spec_from_file_location("firmar", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["firmar"] = module  # dataclass lo busca ahí
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("firmar", None)


@pytest.fixture
def installer(tmp_path):
    path = tmp_path / "Luzeira-0.2.0-instalador.exe"
    path.write_bytes(b"MZ instalador de prueba")
    return path


def sums_for(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{digest}  {path.name}\n{'a' * 64}  Luzeira-0.2.0-portable.zip\n"


@pytest.fixture
def key(firmar, tmp_path, monkeypatch):
    """Una clave de prueba que firmar.py da por buena (como si estuviera en PUBLIC_KEYS)."""
    public = firmar.write_key(tmp_path / "clave.pem", b"una contrasena larga")
    monkeypatch.setattr(firmar, "PUBLIC_KEYS", (public,))
    return tmp_path / "clave.pem", public


def test_key_roundtrip_and_signature_the_app_accepts(firmar, key, installer):
    pem, public = key
    assert b"ENCRYPTED" in pem.read_bytes()  # cifrada, nunca en claro
    private = firmar.read_key(pem, b"una contrasena larga")
    manifest = firmar.make_manifest("0.2.0", installer, sums_for(installer))
    data, sig = firmar.sign(manifest, private)
    accepted = actualizar.verify_manifest(data, sig, [public])
    assert accepted.model_dump() == vars(manifest)
    assert manifest.size == installer.stat().st_size


def test_wrong_password_is_refused(firmar, key):
    with pytest.raises(SystemExit, match="contraseña"):
        firmar.read_key(key[0], b"otra")


def test_a_key_the_app_does_not_know_does_not_sign(firmar, key, installer, monkeypatch):
    private = firmar.read_key(key[0], b"una contrasena larga")
    monkeypatch.setattr(firmar, "PUBLIC_KEYS", ())
    manifest = firmar.make_manifest("0.2.0", installer, sums_for(installer))
    with pytest.raises(SystemExit, match="PUBLIC_KEYS"):
        firmar.sign(manifest, private)
    with pytest.raises(SystemExit, match="PUBLIC_KEYS"):
        firmar.sign_bytes(b"SHA256SUMS", private)


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


def test_attestation_must_come_from_our_release_workflow_tag_and_commit(firmar, tmp_path):
    args = firmar.attestation_args(tmp_path / "x.exe", "v0.2.0", COMMIT)
    assert args[:3] == ["attestation", "verify", str(tmp_path / "x.exe")]
    pairs = dict(zip(args[3::2], args[4::2], strict=False))
    assert pairs["-R"] == "eriik-rz/luzeira"
    assert pairs["--signer-workflow"] == "eriik-rz/luzeira/.github/workflows/release.yml"
    assert pairs["--source-ref"] == "refs/tags/v0.2.0"
    assert pairs["--source-digest"] == COMMIT  # R3-01: el commit, no solo la etiqueta
    assert "--deny-self-hosted-runners" in args


# --- sin luzeira: lo mismo que la app, leído o duplicado ---------------------------------


def test_reads_the_same_constants_as_the_app(firmar):
    assert firmar.REPO == actualizar.REPO
    assert firmar.PUBLIC_KEYS == actualizar.PUBLIC_KEYS  # leídas del código, no copiadas
    assert firmar.MAX_INSTALLER_BYTES == actualizar.MAX_INSTALLER_BYTES
    assert "^" + firmar._VERSION.pattern + "$" == actualizar._VERSION.pattern


@pytest.mark.parametrize(
    "version, size, sha",
    [
        ("0.2.0", 1, "a" * 64),
        ("1.0.0", 20_000_000, "0123456789abcdef" * 4),
        ("9999.9999.9999", actualizar.MAX_INSTALLER_BYTES, "f" * 64),
        ("0.10.3", 12345, hashlib.sha256(b"x").hexdigest()),
    ],
)
def test_manifest_bytes_are_identical_to_the_app(firmar, version, size, sha):
    # R3-02: firmar.py ya no importa luzeira; si sus bytes cambiaran, la firma no valdría.
    file = f"Luzeira-{version}-instalador.exe"
    fields = {"app": "luzeira", "version": version, "file": file, "size": size, "sha256": sha}
    ours = firmar.manifest_bytes(firmar.Manifest(**fields))
    assert ours == actualizar.manifest_bytes(actualizar.Manifest(**fields))


def test_importing_firmar_does_not_import_luzeira_pydantic_or_webview():
    # R3-02: cargar firmar.py no importa luzeira, pydantic ni webview. Ojo: esto NO prueba
    # que el entorno esté aislado (corre con el python del venv, cuyos .pth se procesan
    # aunque se pase -I): eso lo da «uv run --script».
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('firmar', {str(SCRIPT)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['firmar'] = m\n"
        "spec.loader.exec_module(m)\n"
        "bad = sorted(n for n in sys.modules if n.split('.')[0] in "
        "{'luzeira', 'pydantic', 'pydantic_core', 'webview'})\n"
        "print(bad); assert not bad, bad\n"
        "assert m.PUBLIC_KEYS\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_script_declares_only_cryptography_pinned_like_the_lock():
    # Cabecera PEP 723: uv run --script monta un entorno con solo esto.
    text = SCRIPT.read_text(encoding="utf-8")
    header = text.split("# ///", 2)[1]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    locked = lock.split('name = "cryptography"\nversion = "', 1)[1].split('"', 1)[0]
    assert f'dependencies = ["cryptography=={locked}"]' in header


# --- SHA256SUMS ---------------------------------------------------------------------------


def test_sha256sums_is_signed_and_verifiable(firmar, key, installer):
    # R3-13: también se firma SHA256SUMS, con la misma clave (Ed25519, base64).
    pem, public = key
    private = firmar.read_key(pem, b"una contrasena larga")
    data = sums_for(installer).encode()
    sig = firmar.sign_bytes(data, private)
    raw = base64.b64decode(public)
    Ed25519PublicKey.from_public_bytes(raw).verify(base64.b64decode(sig), data)


def test_security_md_publishes_the_same_public_keys():
    # Si se cambia de clave, SECURITY.md tiene que enseñar las nuevas para comprobar
    # SHA256SUMS.sig a mano.
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for key in actualizar.PUBLIC_KEYS:
        pem = Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        )
        assert pem.decode("ascii").strip() in text
    assert "--signer-workflow eriik-rz/luzeira/.github/workflows/release.yml" in text


@pytest.mark.parametrize(
    "sums",
    [
        "",
        "a" * 64 + "  Luzeira-0.2.0-instalador.exe",  # sin salto de línea final
        '{"app":"luzeira"}\n',  # nunca algo que pueda pasar por un manifest.json
        "A" * 64 + "  Luzeira-0.2.0-instalador.exe\n",
        "a" * 64 + "  ../Luzeira-0.2.0-instalador.exe\n",
    ],
)
def test_a_strange_sha256sums_is_not_signed(firmar, sums):
    with pytest.raises(SystemExit, match="formato"):
        firmar.check_sums(sums)


# --- cmd_sign: el orden -------------------------------------------------------------------


class FakeGitHub:
    """gh de mentira: sirve el borrador, apunta todo y atestigua solo ``attested``."""

    def __init__(self, exe: bytes, attested: str = COMMIT, sums: bytes | None = None):
        self.exe = exe
        self.attested = attested
        name = "Luzeira-0.2.0-instalador.exe"
        self.sums = sums or f"{hashlib.sha256(exe).hexdigest()}  {name}\n".encode()
        self.events: list[str] = []
        self.uploaded: dict[str, bytes] = {}

    def __call__(self, args) -> None:
        args = list(args)
        if args[:2] == ["release", "download"]:
            folder = Path(args[args.index("-D") + 1])
            (folder / "Luzeira-0.2.0-instalador.exe").write_bytes(self.exe)
            (folder / "SHA256SUMS").write_bytes(self.sums)
            self.events.append("download")
        elif args[:2] == ["attestation", "verify"]:
            digest = args[args.index("--source-digest") + 1]
            self.events.append("attest " + Path(args[2]).name)
            if digest != self.attested:
                raise SystemExit("gh ha fallado: no sigo.")
        elif args[:2] == ["release", "upload"]:
            self.events.append("upload")
            for path in args[5:]:
                self.uploaded[Path(path).name] = Path(path).read_bytes()
        else:  # pragma: no cover
            raise AssertionError(args)


def run_sign(firmar, monkeypatch, pem, gh, commit=COMMIT):
    events = gh.events
    real_read_key = firmar.read_key
    monkeypatch.setattr(
        firmar, "read_key", lambda *a: events.append("decrypt") or real_read_key(*a)
    )
    args = argparse.Namespace(etiqueta="v0.2.0", clave=pem)
    firmar.cmd_sign(
        args,
        run=gh,
        commit_of=lambda tag: events.append("git " + tag) or commit,
        password=lambda prompt: events.append("password") or "una contrasena larga",
    )


def test_cmd_sign_verifies_everything_before_touching_the_key(firmar, key, monkeypatch):
    pem, public = key
    other = "0123456789abcdef0123456789abcdef01234567"  # el commit que diga git, sea cual sea
    gh = FakeGitHub(b"MZ el instalador bueno", attested=other)
    run_sign(firmar, monkeypatch, pem, gh, commit=other)
    assert gh.events == [
        "git v0.2.0",
        "download",
        "attest Luzeira-0.2.0-instalador.exe",
        "attest SHA256SUMS",
        "password",
        "decrypt",
        "upload",
    ]
    assert set(gh.uploaded) == {"manifest.json", "manifest.json.sig", "SHA256SUMS.sig"}
    m = actualizar.verify_manifest(
        gh.uploaded["manifest.json"], gh.uploaded["manifest.json.sig"], [public]
    )
    assert m.sha256 == hashlib.sha256(b"MZ el instalador bueno").hexdigest()
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
        base64.b64decode(gh.uploaded["SHA256SUMS.sig"]), gh.sums
    )


def test_cmd_sign_refuses_a_build_from_another_commit(firmar, key, monkeypatch):
    # R3-01: con la cuenta robada, mover la etiqueta a otro commit y aprobar el entorno.
    # La attestation de ese build lleva otro commit: no se pide contraseña ni se sube nada.
    gh = FakeGitHub(b"MZ el instalador malo", attested="b2d24b9" + "0" * 33)
    with pytest.raises(SystemExit, match="gh ha fallado"):
        run_sign(firmar, monkeypatch, key[0], gh)
    assert "password" not in gh.events and "decrypt" not in gh.events
    assert "upload" not in gh.events and gh.uploaded == {}


def test_cmd_sign_without_the_local_tag_does_nothing(firmar, key, monkeypatch):
    gh = FakeGitHub(b"MZ")
    monkeypatch.setattr(
        firmar.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=""),
    )
    with pytest.raises(SystemExit, match="etiqueta"):
        firmar.cmd_sign(
            argparse.Namespace(etiqueta="v0.2.0", clave=key[0]),
            run=gh,
            password=lambda prompt: pytest.fail("no debía pedir la contraseña"),
        )
    assert gh.events == []


def test_tag_commit_reads_the_local_tag(firmar):
    # La etiqueta v0.1.7 existe en el repo (anotada): se resuelve a su commit.
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet",
                       "refs/tags/v0.1.7"], capture_output=True).returncode != 0:  # fmt: skip
        pytest.skip("sin etiquetas en este clon")
    assert firmar.tag_commit("v0.1.7") == COMMIT
    with pytest.raises(SystemExit, match="etiqueta"):
        firmar.tag_commit("v9999.0.0")


def test_cmd_sign_refuses_an_installer_that_does_not_match_sums(firmar, key, monkeypatch):
    gh = FakeGitHub(b"MZ uno", sums=b"0" * 64 + b"  Luzeira-0.2.0-instalador.exe\n")
    with pytest.raises(SystemExit, match="SHA256SUMS"):
        run_sign(firmar, monkeypatch, key[0], gh)
    assert "decrypt" not in gh.events and gh.uploaded == {}


def test_cmd_sign_refuses_a_sha256sums_with_extra_content(firmar, key, monkeypatch):
    # La línea del instalador está bien, pero sobra algo: no se firma ese SHA256SUMS.
    exe = b"MZ dos"
    line = f"{hashlib.sha256(exe).hexdigest()}  Luzeira-0.2.0-instalador.exe\n"
    gh = FakeGitHub(exe, sums=(line + '{"app":"luzeira"}\n').encode())
    with pytest.raises(SystemExit, match="formato"):
        run_sign(firmar, monkeypatch, key[0], gh)
    assert "decrypt" not in gh.events and gh.uploaded == {}


def test_lock_notice_says_whether_the_environment_is_pinned(firmar, tmp_path):
    # Sin firmar.py.lock, cffi y pycparser van sin fijar (C-77): se avisa, sin bloquear.
    lock = tmp_path / "firmar.py.lock"
    assert "no hay firmar.py.lock" in firmar.lock_notice(lock)
    lock.write_text("version = 1\n", encoding="utf-8")
    assert "fijado por firmar.py.lock" in firmar.lock_notice(lock)
    assert SCRIPT.with_name("firmar.py.lock") == firmar.LOCK


def test_the_docstring_does_not_promise_a_lock_that_is_not_there():
    text = SCRIPT.read_text(encoding="utf-8")
    if not SCRIPT.with_name("firmar.py.lock").is_file():
        assert "--locked" not in text
        assert "uv lock --script scripts/firmar.py" in text


def test_help_can_be_printed_on_a_spanish_windows_console():
    # La consola de Windows en español es cp1252: «⚠» en el docstring hizo fallar --help.
    module_doc = SCRIPT.read_text(encoding="utf-8").split('"""')[1]
    module_doc.encode("cp1252")


def test_main_prints_the_lock_notice(firmar, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(sys, "argv", ["firmar.py", "firmar", "v0.2.0", "--clave", "x.pem"])
    monkeypatch.setattr(firmar, "cmd_sign", lambda args: called.append(args.etiqueta))
    firmar.main()  # cmd_sign falso: no firma nada, solo se mira lo que imprime antes
    assert called == ["v0.2.0"]
    assert "firmar.py.lock" in capsys.readouterr().out


# --- cambiar-contraseña (C-76) ----------------------------------------------------------

FRASE = "caballo grapa bateria correcta nube faro"


@pytest.mark.parametrize(
    "password",
    ["corta", "contrasenamuylarga pero solo cinco palabras", "a b c d e f"],
    ids=["corta", "cinco-palabras", "seis-pero-corta"],
)
def test_a_password_that_is_not_a_passphrase_is_refused(firmar, password):
    with pytest.raises(SystemExit):
        firmar.check_passphrase(password)


def test_a_six_word_passphrase_is_accepted(firmar):
    firmar.check_passphrase(FRASE)


def test_rekey_keeps_the_same_key_and_leaves_the_old_file_alone(firmar, key, tmp_path):
    path, public = key
    before = path.read_bytes()
    dest = tmp_path / "nueva.pem"
    assert firmar.rekey(path, b"una contrasena larga", dest, FRASE.encode()) == public
    assert path.read_bytes() == before
    assert firmar.public_key_of(firmar.read_key(dest, FRASE.encode())) == public
    with pytest.raises(SystemExit):
        firmar.read_key(dest, b"una contrasena larga")  # la vieja ya no abre la nueva


def test_rekey_never_overwrites_and_refuses_a_key_the_app_does_not_know(
    firmar, key, tmp_path, monkeypatch
):
    path, _ = key
    taken = tmp_path / "ya-existe.pem"
    taken.write_bytes(b"algo")
    with pytest.raises(SystemExit):
        firmar.rekey(path, b"una contrasena larga", taken, FRASE.encode())
    assert taken.read_bytes() == b"algo"
    monkeypatch.setattr(firmar, "PUBLIC_KEYS", ("otra",))
    dest = tmp_path / "nueva.pem"
    with pytest.raises(SystemExit):
        firmar.rekey(path, b"una contrasena larga", dest, FRASE.encode())
    assert not dest.exists()


def test_rekey_never_writes_inside_the_repo(firmar, key):
    path, _ = key
    with pytest.raises(SystemExit):
        firmar.rekey(path, b"una contrasena larga", firmar.ROOT / "clave.pem", FRASE.encode())
    assert not (firmar.ROOT / "clave.pem").exists()


def test_cmd_rekey_asks_twice_and_stops_if_they_differ(firmar, key, tmp_path):
    import argparse

    path, _ = key
    dest = tmp_path / "nueva.pem"
    answers = iter(["una contrasena larga", FRASE, FRASE + " distinta"])
    args = argparse.Namespace(clave=path, salida=dest)
    with pytest.raises(SystemExit):
        firmar.cmd_rekey(args, password=lambda prompt: next(answers))
    assert not dest.exists()
    answers = iter(["una contrasena larga", FRASE, FRASE])
    firmar.cmd_rekey(args, password=lambda prompt: next(answers))
    assert firmar.read_key(dest, FRASE.encode())


# --- módulo de sensores: modulo.json, solo si la release trae su instalador --------------

MODULE = "LuzeiraSensores-0.2.0-setup.exe"


class FakeGitHubWithModule(FakeGitHub):
    """El mismo borrador, con el instalador del módulo en SHA256SUMS. ``unattested``: los
    ficheros cuya attestation falla (aunque el commit sea el bueno)."""

    def __init__(self, exe: bytes, module: bytes, *, module_sums: bytes | None = None,
                 unattested: frozenset[str] = frozenset()):  # fmt: skip
        super().__init__(exe)
        self.module = module
        self.unattested = unattested
        line = module_sums or f"{hashlib.sha256(module).hexdigest()}  {MODULE}\n".encode()
        self.sums += line

    def __call__(self, args) -> None:
        args = list(args)
        patterns = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
        if args[:2] == ["release", "download"] and patterns == [MODULE]:
            folder = Path(args[args.index("-D") + 1])
            (folder / MODULE).write_bytes(self.module)
            self.events.append("download " + MODULE)
            return
        if args[:2] == ["attestation", "verify"] and Path(args[2]).name in self.unattested:
            self.events.append("attest " + Path(args[2]).name)
            raise SystemExit("gh ha fallado: no sigo.")
        super().__call__(args)


def test_cmd_sign_signs_modulo_json_when_the_release_has_the_module(firmar, key, monkeypatch):
    pem, public = key
    gh = FakeGitHubWithModule(b"MZ el instalador bueno", b"MZ el modulo bueno")
    run_sign(firmar, monkeypatch, pem, gh)
    assert gh.events == [
        "git v0.2.0",
        "download",
        "attest Luzeira-0.2.0-instalador.exe",
        "attest SHA256SUMS",
        "download " + MODULE,
        "attest " + MODULE,  # atestiguado igual que el de la app, antes de la contraseña
        "password",
        "decrypt",
        "upload",
    ]
    assert set(gh.uploaded) == {
        "manifest.json", "manifest.json.sig", "SHA256SUMS.sig", "modulo.json", "modulo.json.sig"
    }  # fmt: skip
    m = actualizar.verify_module_manifest(
        gh.uploaded["modulo.json"], gh.uploaded["modulo.json.sig"], [public]
    )
    assert m.version == "0.2.0" and m.file == MODULE
    assert m.sha256 == hashlib.sha256(b"MZ el modulo bueno").hexdigest()
    assert m.size == len(b"MZ el modulo bueno")
    # La de la app sigue siendo la de la app.
    assert (
        actualizar.verify_manifest(
            gh.uploaded["manifest.json"], gh.uploaded["manifest.json.sig"], [public]
        ).file
        == "Luzeira-0.2.0-instalador.exe"
    )


def test_cmd_sign_refuses_a_module_that_is_not_attested(firmar, key, monkeypatch):
    gh = FakeGitHubWithModule(b"MZ", b"MZ modulo", unattested=frozenset({MODULE}))
    with pytest.raises(SystemExit, match="gh ha fallado"):
        run_sign(firmar, monkeypatch, key[0], gh)
    assert "password" not in gh.events and "decrypt" not in gh.events
    assert gh.uploaded == {}


def test_cmd_sign_refuses_a_module_that_does_not_match_sums(firmar, key, monkeypatch):
    gh = FakeGitHubWithModule(
        b"MZ", b"MZ modulo cambiado", module_sums=f"{'0' * 64}  {MODULE}\n".encode()
    )
    with pytest.raises(SystemExit, match="SHA256SUMS"):
        run_sign(firmar, monkeypatch, key[0], gh)
    assert "password" not in gh.events and gh.uploaded == {}


def test_two_module_installers_in_sums_are_refused(firmar):
    sums = f"{'a' * 64}  {MODULE}\n{'b' * 64}  LuzeiraSensores-0.3.0-setup.exe\n"
    with pytest.raises(SystemExit, match="más de un"):
        firmar.module_in_sums(sums)
    assert firmar.module_in_sums(f"{'a' * 64}  Luzeira-0.2.0-instalador.exe\n") is None
    assert firmar.module_in_sums(f"{'a' * 64}  {MODULE}\n") == MODULE
    # Lo que no tiene la forma exacta no cuenta como instalador del módulo.
    for name in ("LuzeiraSensores-0.2.0-setup.exe.bat", "LuzeiraSensores-02.0.0-setup.exe"):
        assert firmar.module_in_sums(f"{'a' * 64}  {name}\n") is None


@pytest.mark.parametrize(
    "version, size, sha",
    [
        ("0.1.0", 1, "a" * 64),
        ("0.2.0", 28_139_780, "0123456789abcdef" * 4),
        ("9999.9999.9999", actualizar.MAX_MODULE_BYTES, "f" * 64),
    ],
)
def test_module_manifest_bytes_are_identical_to_the_app(firmar, version, size, sha):
    file = f"LuzeiraSensores-{version}-setup.exe"
    fields = {"app": "luzeira-sensores", "version": version, "file": file, "size": size,
              "sha256": sha}  # fmt: skip
    ours = firmar.manifest_bytes(firmar.Manifest(**fields))
    assert ours == actualizar.module_manifest_bytes(actualizar.ModuleManifest(**fields))


def test_module_limits_and_names_are_the_same_as_the_app(firmar, tmp_path):
    assert firmar.MAX_MODULE_BYTES == actualizar.MAX_MODULE_BYTES
    exe = tmp_path / MODULE
    exe.write_bytes(b"MZ modulo")
    sums = f"{hashlib.sha256(b'MZ modulo').hexdigest()}  {MODULE}\n"
    m = firmar.make_module_manifest(exe, sums)
    assert actualizar.ModuleManifest(**vars(m)).file == MODULE  # la app lo acepta
    with pytest.raises(SystemExit, match="SHA256SUMS"):
        firmar.make_module_manifest(exe, f"{'0' * 64}  {MODULE}\n")
