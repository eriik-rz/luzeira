# /// script
# requires-python = ">=3.12"
# dependencies = ["cryptography==50.0.2"]  # la misma que uv.lock
# ///
"""Firma una versión de Luzeira para que la app la acepte como actualización (ADR-006).

Lo usa SOLO quien mantiene el proyecto, en su PC. La clave privada nunca entra en el
repositorio ni en GitHub: vive en un fichero cifrado con contraseña, con una copia en el
gestor de contraseñas.

Se lanza **aislado** (``uv run --script``): con la cabecera de arriba (PEP 723), uv le
monta un entorno propio con solo ``cryptography`` (y lo que ella necesita). Así, la clave
se descifra sin cargar las demás dependencias de la app ni sus ficheros ``.pth``.

Todo el entorno va fijado, con hashes, en ``scripts/firmar.py.lock`` (creado con
``uv lock --script scripts/firmar.py``; C-77). uv lo usa solo; con ``--locked`` se niega
a seguir si la cabecera y el lock no cuadran. El script avisa al arrancar si falta.
Sin caracteres fuera de cp1252 en lo que se imprime: la consola de Windows en español no
los puede escribir y ``--help`` fallaría (medido el 2026-11-25 con un signo de aviso).

Por lo mismo (no cargar la app), este script **no importa** ``luzeira``: lee
``PUBLIC_KEYS`` y ``REPO`` del código de ``src/luzeira/actualizar.py`` como texto (sin
ejecutarlo) y duplica lo poco que necesita (``manifest_bytes``); los tests vigilan que dé
lo mismo que la app.

Una vez (y otra para la clave de reserva)::

    uv run --locked --script scripts/firmar.py generar-clave --salida C:/fuera/del/repo/firma.pem

Imprime la clave pública: va en ``PUBLIC_KEYS`` de ``src/luzeira/actualizar.py``.

Para cambiar la contraseña de una clave (la misma clave: las versiones instaladas siguen
aceptando sus firmas). Escribe una copia nueva y no toca la vieja::

    uv run --locked --script scripts/firmar.py cambiar-contraseña ^
        --clave C:/ruta/firma.pem --salida C:/ruta/firma-nueva.pem

La contraseña es una frase de 6 palabras o más (C-76): el PEM de ``cryptography`` deriva
la clave con PBKDF2 de solo 2048 vueltas y no deja subirlas, así que lo que la protege de
verdad es la longitud de la frase. Guárdala en una entrada del gestor distinta de la del
fichero: quien consiga una sola de las dos no puede firmar.

En cada versión, con la release ya en borrador (la deja así el workflow ``release``) y
la etiqueta ``vX.Y.Z`` en tu repo local (la que subiste)::

    uv run --locked --script scripts/firmar.py firmar v0.2.0 --clave C:/ruta/luzeira-firma.pem

Antes de pedir la contraseña: baja el instalador y ``SHA256SUMS`` del borrador y exige
que los dos vengan atestiguados por ``release.yml``, en esa etiqueta y en **el commit al
que apunta tu etiqueta local** (``--source-digest``): si alguien con la cuenta de GitHub
moviera la etiqueta a otro commit, no firmas su build. Después comprueba el instalador
contra ``SHA256SUMS``, pide la contraseña, firma ``manifest.json`` y ``SHA256SUMS`` y sube
``manifest.json``, ``manifest.json.sig`` y ``SHA256SUMS.sig`` al borrador.
Publicar lo haces tú después: ``gh release edit v0.2.0 --draft=false -R eriik-rz/luzeira``.

Las contraseñas se piden en una ventanita de Windows: así las escribes tú y no pasan por
ningún registro (ni por la conversación con Claude Code, si lo lanza él).
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

ROOT = Path(__file__).resolve().parent.parent
ACTUALIZAR = ROOT / "src" / "luzeira" / "actualizar.py"
LOCK = Path(__file__).resolve().with_name("firmar.py.lock")
#: Una frase de paso (C-76): con el KDF débil del PEM, la fuerza está en la longitud.
MIN_WORDS = 6
MIN_PASSWORD = 25

# Los mismos patrones que luzeira.actualizar (test_firmar compara los resultados).
_VERSION = re.compile(r"(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
_SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_INSTALLER_BYTES = 200 * 1024 * 1024
#: Una línea de SHA256SUMS: «<sha256>  <fichero>». Se exige el formato antes de firmarlo,
#: así la firma de SHA256SUMS nunca puede valer como la de un manifest.json (no es JSON).
_SUMS_LINE = re.compile(r"[0-9a-f]{64}  [A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def _constants(path: Path = ACTUALIZAR) -> dict[str, object]:
    """``REPO`` y ``PUBLIC_KEYS`` de actualizar.py, leídos como texto: ``ast`` analiza el
    código sin ejecutarlo, y ``literal_eval`` solo acepta valores literales."""
    found: dict[str, object] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id in {"REPO", "PUBLIC_KEYS"}:
            found[target.id] = ast.literal_eval(node.value)
    return found


_FOUND = _constants()
REPO: str = _FOUND["REPO"]  # type: ignore[assignment]
PUBLIC_KEYS: tuple[str, ...] = _FOUND["PUBLIC_KEYS"]  # type: ignore[assignment]


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(text)
    if match is None:
        sys.exit(f"Versión no válida: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


@dataclass(frozen=True)
class Manifest:
    """Lo mismo que ``luzeira.actualizar.Manifest``, en el mismo orden de campos."""

    app: str
    version: str
    file: str
    size: int
    sha256: str


def manifest_bytes(manifest: Manifest) -> bytes:
    """Los bytes que se firman: JSON compacto, campos en el orden de la clase. Tiene que
    dar exactamente lo mismo que ``luzeira.actualizar.manifest_bytes`` (test_firmar)."""
    fields = {
        "app": manifest.app,
        "version": manifest.version,
        "file": manifest.file,
        "size": manifest.size,
        "sha256": manifest.sha256,
    }
    return json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def ask_password(prompt: str) -> str:
    """Siempre en una ventanita: lo que se escribe no se ve ni sale por la salida del
    programa. No se usa ``getpass``: en Windows, ``isatty()`` es True también para NUL
    (``</dev/null``), y ``getpass`` se queda esperando una consola que no hay (pasó el
    2026-11-18)."""
    import tkinter

    # Ventana normal y siempre encima: un diálogo colgado de una ventana oculta
    # (simpledialog con root.withdraw()) puede quedarse invisible (pasó el 2026-11-18).
    root = tkinter.Tk()
    root.title("Luzeira: firma")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    result: dict[str, str] = {}
    tkinter.Label(root, text=prompt, padx=16, pady=10).pack()
    entry = tkinter.Entry(root, show="•", width=40)
    entry.pack(padx=16)

    def accept(_event=None) -> None:
        result["value"] = entry.get()
        root.destroy()

    tkinter.Button(root, text="Aceptar", command=accept, width=12).pack(pady=10)
    root.bind("<Return>", accept)
    root.after(100, lambda: (root.lift(), root.focus_force(), entry.focus_set()))
    root.mainloop()
    if "value" not in result:
        sys.exit("Cancelado.")
    return result["value"]


def public_key_of(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def check_outside_repo(path: Path) -> None:
    """La clave no puede acabar en el repo (ni en ``docs/``, que también es git)."""
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        sys.exit(f"La clave no puede ir dentro del repositorio ({ROOT}). Elige otra carpeta.")


def write_key(path: Path, password: bytes) -> str:
    check_outside_repo(path)
    if path.exists():
        sys.exit(f"Ya existe {path}: no lo sobrescribo.")
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(password)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pem)
    return public_key_of(private)


def check_passphrase(password: str) -> None:
    """Una frase de ``MIN_WORDS`` palabras o más y ``MIN_PASSWORD`` caracteres o más."""
    if len(re.findall(r"\w+", password)) < MIN_WORDS or len(password) < MIN_PASSWORD:
        sys.exit(
            f"Demasiado corta: usa una frase de {MIN_WORDS} palabras o más "
            f"(y {MIN_PASSWORD} caracteres o más), por ejemplo cinco o seis palabras al azar."
        )


def rekey(source: Path, old: bytes, dest: Path, new: bytes) -> str:
    """Escribe en ``dest`` la misma clave de ``source``, cifrada con ``new``, y comprueba
    que se lee y que es la misma. ``source`` no se toca. Devuelve la clave pública."""
    check_outside_repo(dest)
    if dest.exists():
        sys.exit(f"Ya existe {dest}: no lo sobrescribo.")
    private = read_key(source, old)
    _check_key(private)  # la que la app conoce: no recifrar una clave equivocada
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(new))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pem)
    if public_key_of(read_key(dest, new)) != public_key_of(private):  # pragma: no cover
        dest.unlink()
        sys.exit("La copia nueva no es la misma clave. La he borrado; la vieja sigue igual.")
    return public_key_of(private)


def read_key(path: Path, password: bytes) -> Ed25519PrivateKey:
    try:
        key = load_pem_private_key(path.read_bytes(), password=password)
    except (ValueError, TypeError):
        sys.exit("No puedo abrir la clave: ¿contraseña equivocada?")
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("Esa clave no es Ed25519.")
    return key


def check_sums(sums: str) -> None:
    """``SHA256SUMS`` tal como lo escribe empaquetar.py, y nada más."""
    lines = sums.splitlines()
    if not lines or not sums.endswith("\n") or not all(_SUMS_LINE.fullmatch(x) for x in lines):
        sys.exit("SHA256SUMS no tiene el formato esperado. No firmo.")


def make_manifest(version: str, installer: Path, sums: str) -> Manifest:
    """El manifiesto del instalador, comprobando que casa con ``SHA256SUMS``."""
    parse_version(version)
    data = installer.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = f"{digest}  {installer.name}"
    if expected not in (line.strip() for line in sums.splitlines()):
        sys.exit(f"{installer.name} no casa con SHA256SUMS de la release. No firmo.")
    manifest = Manifest(
        app="luzeira", version=version, file=installer.name, size=len(data), sha256=digest
    )
    # Lo que la app exige (luzeira.actualizar.Manifest y verify_manifest).
    if (
        manifest.file != f"Luzeira-{version}-instalador.exe"
        or not 0 < manifest.size <= MAX_INSTALLER_BYTES
        or not _SHA256.fullmatch(manifest.sha256)
    ):
        sys.exit("El manifiesto no lo aceptaría la app. No firmo.")
    return manifest


def _check_key(private: Ed25519PrivateKey) -> Ed25519PublicKey:
    """⚠ Compara con el ``PUBLIC_KEYS`` de este árbol, no con el de las versiones que tiene
    la gente instaladas. Si algún día se cambia de clave, la versión que la estrena se
    firma todavía con una que conozcan las anteriores (la de reserva está para eso)."""
    if public_key_of(private) not in PUBLIC_KEYS:
        sys.exit(
            "Esa clave no está en PUBLIC_KEYS de src/luzeira/actualizar.py: la app no "
            "aceptaría esta firma. ¿Es la clave buena?"
        )
    return private.public_key()


def sign_bytes(data: bytes, private: Ed25519PrivateKey) -> bytes:
    """Firma Ed25519 en base64, comprobada antes de devolverla."""
    public = _check_key(private)
    sig = base64.b64encode(private.sign(data))
    try:
        public.verify(base64.b64decode(sig), data)
    except InvalidSignature:  # pragma: no cover - no debería pasar nunca
        sys.exit("La firma recién hecha no se verifica. No subo nada.")
    return sig


def sign(manifest: Manifest, private: Ed25519PrivateKey) -> tuple[bytes, bytes]:
    """``(manifest.json, manifest.json.sig)``."""
    data = manifest_bytes(manifest)
    return data, sign_bytes(data, private)


def attestation_args(path: Path, tag: str, commit: str) -> list[str]:
    """``gh attestation verify`` estricto: solo vale lo que atestiguó NUESTRO workflow de
    release para ESA etiqueta y ESE commit. Sin el workflow, cualquier workflow del repo
    podría colar un exe atestiguado; sin el commit, quien controle el repo (la amenaza de
    ADR-006) movería la etiqueta a otro commit y lo atestiguaría ``release.yml``.

    ``--source-digest`` compara con el commit exacto que firmó GitHub en el certificado
    (``SourceRepositoryDigest``: 40 caracteres, sin prefijo; medido con la v0.1.7)."""
    return [
        "attestation", "verify", str(path), "-R", REPO,
        "--signer-workflow", f"{REPO}/.github/workflows/release.yml",
        "--source-ref", f"refs/tags/{tag}",
        "--source-digest", commit,
        "--deny-self-hosted-runners",
    ]  # fmt: skip


def gh(args: Sequence[str]) -> None:
    print("[firmar] $ gh " + " ".join(args))
    if subprocess.run(["gh", *args]).returncode != 0:  # noqa: S603, S607
        sys.exit("gh ha fallado: no sigo.")


def tag_commit(tag: str) -> str:
    """El commit al que apunta la etiqueta en TU repo local (no en GitHub)."""
    ref = f"refs/tags/{tag}^{{commit}}"  # refs/tags/: que no valga una rama llamada igual
    cmd = ["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", ref]
    done = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    commit = done.stdout.strip()
    if done.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        sys.exit(f"No tengo la etiqueta {tag} en el repo local. ¿La creaste aquí?")
    return commit


def cmd_generate(args: argparse.Namespace) -> None:
    password = ask_password(
        f"Frase de paso nueva para {args.salida.name} ({MIN_WORDS} palabras o más):"
    )
    check_passphrase(password)
    if ask_password("Repítela:") != password:
        sys.exit("No coinciden.")
    public = write_key(args.salida, password.encode("utf-8"))
    print(
        f"\nClave privada (cifrada) en: {args.salida}\n"
        f"\nClave PÚBLICA (para PUBLIC_KEYS de src/luzeira/actualizar.py):\n\n    {public}\n"
        "\nGuarda en tu gestor de contraseñas, en DOS entradas distintas:\n"
        "  - la frase de paso que acabas de poner;\n"
        "  - el fichero .pem como adjunto (o su texto, que va cifrado).\n"
        "Sin las dos cosas no se pueden firmar versiones nuevas."
    )


def cmd_rekey(args: argparse.Namespace, *, password: Callable[[str], str] = ask_password) -> None:
    """``password``: la ventanita (los tests la cambian)."""
    old = password(f"Contraseña ACTUAL de {args.clave.name}:")
    new = password(f"Frase de paso NUEVA ({MIN_WORDS} palabras o más):")
    check_passphrase(new)
    if password("Repite la frase nueva:") != new:
        sys.exit("No coinciden.")
    public = rekey(args.clave, old.encode("utf-8"), args.salida, new.encode("utf-8"))
    print(
        f"\nHecho: {args.salida} es la misma clave (pública {public}), con la frase nueva.\n"
        "Ahora:\n"
        "  1. Guarda la frase nueva en el gestor, en una entrada DISTINTA de la del .pem.\n"
        "  2. Cambia el adjunto .pem de su entrada por el nuevo.\n"
        f"  3. Cuando lo hayas comprobado, borra el viejo: {args.clave}"
    )


def cmd_sign(
    args: argparse.Namespace,
    *,
    run: Callable[[Sequence[str]], None] = gh,
    commit_of: Callable[[str], str] = tag_commit,
    password: Callable[[str], str] = ask_password,
) -> None:
    """``run``, ``commit_of`` y ``password``: gh, git y la ventanita (los tests los cambian)."""
    tag = args.etiqueta
    if not tag.startswith("v"):
        sys.exit("La etiqueta es vX.Y.Z.")
    version = tag[1:]
    parse_version(version)
    commit = commit_of(tag)
    with tempfile.TemporaryDirectory(prefix="luzeira-firmar-") as tmp:
        folder = Path(tmp)
        name = f"Luzeira-{version}-instalador.exe"
        installer, sums_path = folder / name, folder / "SHA256SUMS"
        run(["release", "download", tag, "-R", REPO, "-p", name, "-p", "SHA256SUMS", "-D", tmp])
        # Los dos atestiguados por el workflow de release de esta etiqueta y este commit:
        # si no, comprobar el exe contra SHA256SUMS del mismo borrador sería circular.
        run(attestation_args(installer, tag, commit))
        run(attestation_args(sums_path, tag, commit))
        sums_data = sums_path.read_bytes()
        sums = sums_data.decode("ascii", errors="replace")
        check_sums(sums)
        manifest = make_manifest(version, installer, sums)
        # La clave se descifra solo ahora, con todo comprobado.
        private = read_key(args.clave, password(f"Contraseña de {args.clave.name}:").encode())
        data, sig = sign(manifest, private)
        sums_sig = sign_bytes(sums_data, private)
        (folder / "manifest.json").write_bytes(data)
        (folder / "manifest.json.sig").write_bytes(sig)
        (folder / "SHA256SUMS.sig").write_bytes(sums_sig)
        run(
            [
                "release", "upload", tag, "-R", REPO,
                str(folder / "manifest.json"), str(folder / "manifest.json.sig"),
                str(folder / "SHA256SUMS.sig"),
            ]
        )  # fmt: skip
    print(
        f"\nFirmada {tag} (commit {commit}). Revisa el borrador en GitHub y, si todo está "
        f"bien, publícala:\n    gh release edit {tag} --draft=false -R {REPO}"
    )


def lock_notice(lock: Path = LOCK) -> str:
    """Qué tan fijado está el entorno en el que corre (se imprime al arrancar; no bloquea)."""
    if lock.is_file():
        return f"[firmar] Entorno fijado por {lock.name}."
    return (
        f"[firmar] AVISO: no hay {lock.name}: cffi y pycparser no están fijados (C-77). "
        "Para fijarlos: uv lock --script scripts/firmar.py"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(required=True)
    g = sub.add_parser("generar-clave", help="crea una clave de firma nueva, cifrada")
    g.add_argument("--salida", type=Path, required=True, help="fichero .pem, fuera del repo")
    g.set_defaults(func=cmd_generate)
    c = sub.add_parser("cambiar-contraseña", help="la misma clave, con otra contraseña")
    c.add_argument("--clave", type=Path, required=True, help="el .pem actual (no se toca)")
    c.add_argument("--salida", type=Path, required=True, help="el .pem nuevo, fuera del repo")
    c.set_defaults(func=cmd_rekey)
    s = sub.add_parser("firmar", help="firma el borrador de una release")
    s.add_argument("etiqueta", help="vX.Y.Z")
    s.add_argument("--clave", type=Path, required=True, help="el .pem de generar-clave")
    s.set_defaults(func=cmd_sign)
    args = p.parse_args()
    print(lock_notice())
    args.func(args)


if __name__ == "__main__":
    main()
