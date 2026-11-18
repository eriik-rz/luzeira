"""Firma una versión de Luzeira para que la app la acepte como actualización (ADR-006).

Lo usa SOLO quien mantiene el proyecto, en su PC. La clave privada nunca entra en el
repositorio ni en GitHub: vive en un fichero cifrado con contraseña, con una copia en el
gestor de contraseñas.

Una vez (y otra para la clave de reserva)::

    uv run python scripts/firmar.py generar-clave --salida C:/ruta/fuera/del/repo/luzeira-firma.pem

Imprime la clave pública: va en ``PUBLIC_KEYS`` de ``src/luzeira/actualizar.py``.

En cada versión, con la release ya en borrador (la deja así el workflow ``release``)::

    uv run python scripts/firmar.py firmar v0.2.0 --clave C:/ruta/luzeira-firma.pem

Baja el instalador del borrador, comprueba su attestation de GitHub y su línea de
``SHA256SUMS``, escribe ``manifest.json`` y ``manifest.json.sig`` y los sube al borrador.
Publicar lo haces tú después: ``gh release edit v0.2.0 --draft=false -R eriik-rz/luzeira``.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from luzeira import actualizar  # noqa: E402
from luzeira.actualizar import Manifest, parse_version  # noqa: E402

MIN_PASSWORD = 12


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


def read_key(path: Path, password: bytes) -> Ed25519PrivateKey:
    try:
        key = load_pem_private_key(path.read_bytes(), password=password)
    except (ValueError, TypeError):
        sys.exit("No puedo abrir la clave: ¿contraseña equivocada?")
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("Esa clave no es Ed25519.")
    return key


def make_manifest(version: str, installer: Path, sums: str) -> Manifest:
    """El manifiesto del instalador, comprobando que casa con ``SHA256SUMS``."""
    parse_version(version)
    data = installer.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = f"{digest}  {installer.name}"
    if expected not in (line.strip() for line in sums.splitlines()):
        sys.exit(f"{installer.name} no casa con SHA256SUMS de la release. No firmo.")
    return Manifest(
        app="luzeira", version=version, file=installer.name, size=len(data), sha256=digest
    )


def sign(manifest: Manifest, private: Ed25519PrivateKey) -> tuple[bytes, bytes]:
    """``(manifest.json, manifest.json.sig)``. Antes comprueba que la app lo aceptaría."""
    public = public_key_of(private)
    if public not in actualizar.PUBLIC_KEYS:
        sys.exit(
            "Esa clave no está en PUBLIC_KEYS de src/luzeira/actualizar.py: la app no "
            "aceptaría esta firma. ¿Es la clave buena?"
        )
    data = actualizar.manifest_bytes(manifest)
    sig = base64.b64encode(private.sign(data))
    actualizar.verify_manifest(data, sig, actualizar.PUBLIC_KEYS)  # la misma comprobación
    return data, sig


def gh(*args: str) -> None:
    print("[firmar] $ gh " + " ".join(args))
    if subprocess.run(["gh", *args]).returncode != 0:  # noqa: S603, S607
        sys.exit("gh ha fallado: no sigo.")


def cmd_generate(args: argparse.Namespace) -> None:
    password = getpass.getpass("Contraseña nueva para la clave (mínimo 12 caracteres): ")
    if len(password) < MIN_PASSWORD:
        sys.exit("Demasiado corta.")
    if getpass.getpass("Repítela: ") != password:
        sys.exit("No coinciden.")
    public = write_key(args.salida, password.encode("utf-8"))
    print(
        f"\nClave privada (cifrada) en: {args.salida}\n"
        f"\nClave PÚBLICA (para PUBLIC_KEYS de src/luzeira/actualizar.py):\n\n    {public}\n"
        "\nGuarda en tu gestor de contraseñas, en una entrada nueva:\n"
        "  - la contraseña que acabas de poner;\n"
        "  - el fichero .pem como adjunto (o su texto, que va cifrado).\n"
        "Sin las dos cosas no se pueden firmar versiones nuevas."
    )


def cmd_sign(args: argparse.Namespace) -> None:
    tag = args.etiqueta
    if not tag.startswith("v"):
        sys.exit("La etiqueta es vX.Y.Z.")
    version = tag[1:]
    parse_version(version)
    repo = actualizar.REPO
    private = read_key(args.clave, getpass.getpass("Contraseña de la clave: ").encode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="luzeira-firmar-") as tmp:
        folder = Path(tmp)
        name = f"Luzeira-{version}-instalador.exe"
        gh("release", "download", tag, "-R", repo, "-p", name, "-p", "SHA256SUMS", "-D", tmp)
        installer = folder / name
        gh("attestation", "verify", str(installer), "-R", repo)
        sums = (folder / "SHA256SUMS").read_text(encoding="utf-8")
        manifest = make_manifest(version, installer, sums)
        data, sig = sign(manifest, private)
        (folder / "manifest.json").write_bytes(data)
        (folder / "manifest.json.sig").write_bytes(sig)
        gh(
            "release", "upload", tag, "-R", repo,
            str(folder / "manifest.json"), str(folder / "manifest.json.sig"),
        )  # fmt: skip
    print(
        f"\nFirmada {tag}. Revisa el borrador en GitHub y, si todo está bien, publícala:\n"
        f"    gh release edit {tag} --draft=false -R {repo}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(required=True)
    g = sub.add_parser("generar-clave", help="crea una clave de firma nueva, cifrada")
    g.add_argument("--salida", type=Path, required=True, help="fichero .pem, fuera del repo")
    g.set_defaults(func=cmd_generate)
    s = sub.add_parser("firmar", help="firma el borrador de una release")
    s.add_argument("etiqueta", help="vX.Y.Z")
    s.add_argument("--clave", type=Path, required=True, help="el .pem de generar-clave")
    s.set_defaults(func=cmd_sign)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
