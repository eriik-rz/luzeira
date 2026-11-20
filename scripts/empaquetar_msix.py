"""Monta el paquete MSIX de Luzeira para Microsoft Store (C-52).

Parte de la carpeta que ensambla ``scripts/empaquetar.py`` (``build/Luzeira``): el mismo
Python embebible y las mismas dependencias que el instalador. Le añade el manifiesto
(``instalador/AppxManifest.xml``) y los iconos, y lo empaqueta con ``makeappx``.

Uso (desde la raíz del repo)::

    uv run python scripts/empaquetar.py --python-embebible RUTA --editor --sin-instalador
    uv run python scripts/empaquetar_msix.py --herramientas RUTA_DEL_NUPKG

(``RUTA_DEL_NUPKG``: ``microsoft.windows.sdk.buildtools.<TOOLS_VERSION>.nupkg``.)

Herramientas: no hace falta instalar el SDK de Windows. ``makeappx`` viene en el paquete
NuGet ``Microsoft.Windows.SDK.BuildTools`` (un zip de 22 MB), que se comprueba por su
SHA-512 y se descomprime en una carpeta temporal.

El ``.msix`` sale **sin firmar**: la Store firma lo que se le sube. Para instalarlo en local
hay que firmarlo con un certificado de prueba (ver la skill ``empaquetar``).

Identidad: con ``--tienda``, la que dio Partner Center al reservar el nombre (para subir).
Sin él, una de prueba (para instalar en local), que la Store rechazaría.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "instalador" / "AppxManifest.xml"

#: SHA-512 (base64, como lo publica NuGet) de microsoft.windows.sdk.buildtools
#: 10.0.28000.2957 (22.369.897 B). Sale del catálogo de nuget.org (consultado 2026-11-20) y
#: coincide con el fichero bajado. makeappx.exe, signtool.exe y makepri.exe llevan firma
#: válida de Microsoft (comprobada con Get-AuthenticodeSignature). El script **ejecuta**
#: makeappx: otra versión = otro hash, que se cambia aquí a propósito.
TOOLS_VERSION = "10.0.28000.2957"
TOOLS_SHA512 = (
    "PhfL6f1d9c2CXCD2fyQkwBBFR6/csgVxWDWDj/9uIY3PU5aUR3vtoAJn7HP1d/cMrHGQhuSH1FXHvaZIjOIajw=="
)
#: Dentro del paquete NuGet, la carpeta de las herramientas de 64 bits.
TOOLS_DIR = "bin/10.0.28000.0/x64/"

#: Identidad de prueba (solo para instalar en local con un certificado propio).
TEST_NAME = "LuzeiraPrueba"
TEST_PUBLISHER = "CN=LuzeiraPrueba"
TEST_PUBLISHER_NAME = "Luzeira"
#: Identidad real, de Partner Center (Identidad del producto, 2026-11-20). No es secreta:
#: va dentro del paquete. Store ID 9MSNPHG5NZ40.
STORE_NAME = "eriik-rz.Luzeira"
STORE_PUBLISHER = "CN=536FC4AD-21B9-4738-9794-F634BF14D2D1"
STORE_PUBLISHER_NAME = "eriik-rz"

#: Reglas del manifiesto (Microsoft Learn, «Identity», consultada 2026-11-20): Name de 3 a
#: 50 caracteres alfanuméricos, punto o guion. El Publisher es un nombre X.500; aquí solo se
#: admite lo que da Partner Center (``CN=`` y un GUID o nombre simple): nada de comillas ni
#: ``<>&``, que romperían el XML.
NAME_RE = re.compile(r"[A-Za-z0-9.-]{3,50}")
PUBLISHER_RE = re.compile(r"CN=[A-Za-z0-9 .,=-]{1,200}")
PUBLISHER_NAME_RE = re.compile(r"[^<>&\"']{1,256}")
#: Versión de la app (``X.Y.Z``) → la de MSIX (``X.Y.Z.0``). La Store exige el último 0.
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

#: Iconos que pide el manifiesto: nombre → lado en píxeles.
ASSETS = {"StoreLogo.png": 50, "Square150.png": 150, "Square44.png": 44}
#: Lo de la carpeta ensamblada que no va al MSIX: el lanzador del zip portable.
EXCLUDE = {"Luzeira.cmd"}


def log(msg: str) -> None:
    print(f"[empaquetar-msix] {msg}", flush=True)


def check_tools(nupkg: Path) -> None:
    digest = base64.b64encode(hashlib.sha512(nupkg.read_bytes()).digest()).decode()
    if digest != TOOLS_SHA512:
        sys.exit(f"{nupkg.name}: SHA-512 distinto del esperado ({TOOLS_VERSION}). No se usa.")


def extract_tools(nupkg: Path, dest: Path) -> Path:
    """Saca la carpeta x64 de las herramientas y devuelve la ruta de ``makeappx.exe``."""
    with zipfile.ZipFile(nupkg) as z:
        for member in z.namelist():
            if not member.startswith(TOOLS_DIR) or member.endswith("/"):
                continue
            name = member[len(TOOLS_DIR) :]
            if "/" in name or "\\" in name or name in ("", ".", ".."):
                continue  # solo los ficheros de esa carpeta, sin subcarpetas ni rutas raras
            (dest / name).write_bytes(z.read(member))
    makeappx = dest / "makeappx.exe"
    if not makeappx.is_file():
        sys.exit(f"El paquete de herramientas no trae {TOOLS_DIR}makeappx.exe.")
    return makeappx


def msix_version(version: str) -> str:
    m = VERSION_RE.fullmatch(version)
    if not m or any(int(part) > 65535 for part in m.groups()):
        sys.exit(f"Versión «{version}» no válida para MSIX: tiene que ser X.Y.Z (máximo 65535).")
    return f"{version}.0"


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def render_manifest(*, name: str, publisher: str, publisher_name: str, version: str) -> str:
    for value, rule, what in (
        (name, NAME_RE, "--nombre"),
        (publisher, PUBLISHER_RE, "--publisher"),
        (publisher_name, PUBLISHER_NAME_RE, "--publisher-nombre"),
    ):
        if not rule.fullmatch(value):
            sys.exit(f"{what} «{value}» no es válido para el manifiesto.")
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        name=name,
        publisher=publisher,
        publisher_name=publisher_name,
        version=msix_version(version),
    )


def build_layout(src: Path, layout: Path, manifest: str) -> None:
    """Copia la carpeta ensamblada, añade los iconos y el manifiesto."""
    from luzeira.marca import draw_mark

    if not (src / "python" / "pythonw.exe").is_file():
        sys.exit(f"{src} no parece la carpeta de empaquetar.py (falta python\\pythonw.exe).")
    shutil.copytree(src, layout, ignore=lambda _d, names: [n for n in names if n in EXCLUDE])
    assets = layout / "Assets"
    assets.mkdir()
    for file_name, size in ASSETS.items():
        draw_mark(size).save(assets / file_name, optimize=True)
    (layout / "AppxManifest.xml").write_text(manifest, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--herramientas",
        type=Path,
        required=True,
        help=f"microsoft.windows.sdk.buildtools.{TOOLS_VERSION}.nupkg de nuget.org",
    )
    p.add_argument(
        "--carpeta", type=Path, default=ROOT / "build" / "Luzeira", help="carpeta ensamblada"
    )
    p.add_argument("--salida", type=Path, default=ROOT / "dist", help="carpeta de resultados")
    p.add_argument("--nombre", default=TEST_NAME, help="Identity Name (Partner Center)")
    p.add_argument(
        "--publisher", default=TEST_PUBLISHER, help="Identity Publisher (Partner Center)"
    )
    p.add_argument("--publisher-nombre", default=TEST_PUBLISHER_NAME, help="nombre del editor")
    p.add_argument(
        "--tienda", action="store_true", help="la identidad real de Partner Center (para subir)"
    )
    args = p.parse_args(argv)
    if args.tienda:
        args.nombre, args.publisher, args.publisher_nombre = (
            STORE_NAME,
            STORE_PUBLISHER,
            STORE_PUBLISHER_NAME,
        )

    check_tools(args.herramientas)
    version = project_version()
    manifest = render_manifest(
        name=args.nombre,
        publisher=args.publisher,
        publisher_name=args.publisher_nombre,
        version=version,
    )
    if args.nombre == TEST_NAME:
        log("Identidad de PRUEBA: sirve para instalar en local, no para subir a la Store.")
    args.salida.mkdir(parents=True, exist_ok=True)
    out = args.salida / f"Luzeira-{version}.msix"
    with tempfile.TemporaryDirectory(prefix="luzeira-msix-") as tmp:
        tools = Path(tmp) / "herramientas"
        tools.mkdir()
        makeappx = extract_tools(args.herramientas, tools)
        layout = Path(tmp) / "paquete"
        build_layout(args.carpeta, layout, manifest)
        out.unlink(missing_ok=True)
        cmd = [str(makeappx), "pack", "/d", str(layout), "/p", str(out), "/o"]
        log("$ " + subprocess.list2cmdline(cmd))
        # makeappx escribe una línea por fichero, y los errores también por stdout: se
        # enseña solo si falla.
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")  # noqa: S603
        if r.returncode != 0:
            errors = [x for x in r.stdout.splitlines() if "payload file" not in x]
            sys.exit("makeappx falló:\n" + "\n".join(errors[-20:]))
    log(f"{out.name}: {out.stat().st_size / 1e6:.1f} MB (sin firmar)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
