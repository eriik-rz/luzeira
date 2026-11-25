"""Empaqueta Luzeira para Windows: instalador (Inno Setup), zip portable y SHA256SUMS.

Decisión D-12 (fase 6): **Python 3.12 embebible oficial** (python.org, ejecutables firmados
por la PSF) + un ``site-packages`` propio + **Inno Setup por usuario** (sin admin).
La app arranca con ``python\\pythonw.exe -m luzeira``: no hay ``.exe`` propio que firmar.

Uso (desde la raíz del repo, con el Python del proyecto; solo usa la biblioteca estándar)::

    uv run python scripts/empaquetar.py --python-embebible RUTA\\python-3.12.10-embed-amd64.zip

Pasos (cada uno se explica en su función):

1. (opcional, ``--editor``) compila el editor web con ``npm ci`` + ``npm run build``.
2. Descomprime el embebible en ``build/Luzeira/python`` y ajusta ``python312._pth``.
3. Instala en ``python/Lib/site-packages`` las dependencias **de ejecución** fijadas en
   ``uv.lock`` (con sus hashes) y la wheel de Luzeira construida con ``uv build``.
4. Quita lo que no hace falta (y lo que llevaría rutas de esta máquina).
5. Precompila a ``.pyc`` reproducibles y fija las fechas de todos los ficheros.
6. Copia ``LICENSE``, ``README.md`` y genera ``TERCEROS.txt`` con las licencias.
7. Compila el instalador con ISCC (versión sacada de ``pyproject.toml``).
8. (opcional, ``--modulo``) publica el módulo de sensores (.NET) y compila su instalador,
   ``dist/LuzeiraSensores-<versión del módulo>-setup.exe`` (ADR-007).
9. Genera ``dist/Luzeira-<versión>-portable.zip`` y ``dist/SHA256SUMS``, y dice tamaños.

Red: solo la que use ``uv`` para bajar de PyPI las wheels ya fijadas en ``uv.lock``.
"""

from __future__ import annotations

import argparse
import contextlib
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "instalador" / "luzeira.iss"
ICON = ROOT / "src" / "luzeira" / "recursos" / "luzeira.ico"
EDITOR_INDEX = ROOT / "src" / "luzeira" / "editor" / "web" / "index.html"
MODULE_DIR = ROOT / "modulo-sensores"
MODULE_ISS = ROOT / "instalador" / "luzeira-sensores.iss"
#: El runtime de .NET que va dentro del módulo (``RuntimeFrameworkVersion``, ADR-008).
MODULE_RUNTIME = "10.0.15"
DEFAULT_ISCC = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 7" / "ISCC.exe"

#: Versión del Python embebible que se espera (la de ``.python-version`` y el desarrollo).
PYTHON_TAG = "312"
#: SHA-256 de ``python-3.12.10-embed-amd64.zip`` (11.133.606 B). python.org publica su MD5
#: (``fe8ef205f2e9c3ba44d0cf9954e1abd3``, página de la release, consultada 2026-11-18): el
#: fichero bajado por HTTPS coincide con él y de ese fichero sale este hash. El script
#: **ejecuta** el python.exe de ese zip (compileall): sin esto, un zip manipulado correría
#: código en la máquina de build y acabaría dentro del paquete.
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
#: SHA-256 de los binarios de Inno Setup 7.1.1 (edición x64, C-64) que compilan el
#: instalador o van dentro de él. El instalador de Inno se comprobó contra su ``.issig`` y
#: con ``gh release verify-asset``; ISCC.exe y las DLL llevan firma válida de Pyrsys B.V.
#: (2026-11-23). Los ``.e32`` no van firmados, por eso se fija el hash de todos. Otra
#: versión de Inno = otros hashes: se actualizan aquí a propósito, nunca en silencio.
INNO_SHA256 = {
    "ISCC.exe": "e0f9f1dc32032650c6629f6dee41eb6d20ba140e4e3c07c25d8a24f9ba781644",
    "ISCmplr.dll": "7bf5943088e20ab95863a640d99c0234cf98e57850af82cdb465589f994d87de",
    "ISPP.dll": "fef3c09323ffbcead73c306aa2626fc7dd1e316bf1a55d541d925286e5366015",
    # El ISCC de 64 bits carga esta, no islzma.dll (medido: sin ella no compila).
    "islzma-x64.dll": "40ff69ac37464035cec3338637f360f035f69beff8263c47f5913cbac8813158",
    "Setup.e32": "0bddcbb0d3a739a54a9e89341b4543b2df8c01ce3f93e76f6adf2388b1c59468",
    "SetupLdr.e32": "3c6c29ff87a2f3bcc0f9d36a3465f39de4cdc7fbb8086b7a053c5f6d84f709f8",
    # Traducciones del asistente (C-62): solo texto, pero acaban dentro del instalador y
    # cambian sus hashes, así que también se fijan.
    "Default.isl": "f2bbf8236200fd41919ac9cfde05841066ae4194deb36d06dc9445fdef99883b",
    "Languages/Spanish.isl": "cb071274c70d7de4b774b306c6750edef7e8b662f71bb551333a7ed84c5a2634",
    "Languages/German.isl": "36090e2ad4895d0f59c4716925a9739ecc9fb4fa2a7c0dc066ffcfb953d6ed59",
    "Languages/French.isl": "5dd8367b662b7f250bbfb6859e32a5ff98ac98a677bc1211f635d287e23cb801",
    "Languages/Italian.isl": "20636321f14d4c525cd2965242f765dcb772826a07ad9b224e6e6cacf232d8c8",
    "Languages/BrazilianPortuguese.isl": (
        "d0bddd148bf2e8ba999e0b40a6429d0711bc6476c2c5eac9c3da2b9ac88a4be4"
    ),
    "Languages/Polish.isl": "15f719b42c5fda5a4798b92ad38d090372e1d27c9658278148c7506a3d1f8191",
    "Languages/Turkish.isl": "5a27f42d9f9c2ced4b105e82e6bfd4a8e2dc08fdc12dc742fb7196be02c52a8f",
    "Languages/Russian.isl": "0def13d0a979bd3d062ef7ce960264df17f908fb62af46bb81970e2abee53919",
    "Languages/ChineseSimplified.isl": (
        "e0b0b350e2245f3c5e65586dfe43d574f6e7f06f2261149aba284954b3fc9a8d"
    ),
    "Languages/Japanese.isl": "4b5b3343eaf1a04c768979e92648e9080b2d0888f187c7a4300cc7ac89b2fc22",
}
#: Plataforma de las wheels: Windows de 64 bits, la única que soporta Luzeira v1.
PLATFORM = "x86_64-pc-windows-msvc"
#: Paquetes que PyPI solo publica como código fuente (sdist) y se dejan construir.
#: proxy-tools 0.1.0 (dependencia de pywebview, un solo .py) no tiene wheel. Su sdist va
#: verificado por el hash de uv.lock y lo que lo compila (setuptools), por BUILD_CONSTRAINTS.
SDIST_ALLOWED = {"proxy-tools"}
#: Versiones y hashes de las dependencias de compilación (setuptools…).
BUILD_CONSTRAINTS = ROOT / "scripts" / "restricciones-build.txt"
#: Fecha fija para ficheros y zip (2026-01-01 00:00 UTC) si no se pasa SOURCE_DATE_EPOCH.
DEFAULT_EPOCH = 1767225600

# Lo que se quita del paquete, con su porqué (medido en la build de la fase 6).
PRUNE_FILES = {
    # Pillow: AVIF (~7,5 MB) y el puente con Tk. Luzeira solo abre PNG/JPEG/GIF, y Pillow
    # importa cada plugin dentro de un try: sin estos .pyd sigue funcionando.
    "PIL/_avif.cp312-win_amd64.pyd",
    "PIL/_imagingtk.cp312-win_amd64.pyd",
    # nvidia-ml-py instala un ejemplo suelto como módulo de primer nivel «example».
    "example.py",
    # Candado que deja uv --target en la carpeta destino.
    ".lock",
}
#: Patrones (relativos a site-packages) que se borran: otras arquitecturas y Android.
PRUNE_GLOBS = (
    "webview/lib/*.jar",  # pywebview para Android
    # 🔴 webview/lib/runtimes/win-x86 y win-arm64 NO se quitan (~260 KB): pywebview 6.2.1
    # (platforms/edgechromium.py) recorre las tres y falla si falta alguna, y el editor no
    # abre. Pasó en la primera build; lo vigila smoke_test().
    "webview/lib/WebBrowserInterop.x86.dll",
    "clr_loader/ffi/dlls/x86",
    "clr_loader/ffi/dlls/*/*.pdb",  # símbolos de depuración
    "pythonnet/runtime/*.xml",  # documentación de la API .NET
    "*.dist-info/direct_url.json",  # lleva la ruta absoluta de la wheel local
    "*.dist-info/uv_cache.json",  # lleva la hora de la build: rompe la reproducibilidad
    "*.dist-info/REQUESTED",
)
#: Carpetas de tests de los paquetes de terceros (no se importan en ejecución).
PRUNE_DIR_NAMES = {"tests", "test", "__pycache__"}
#: Del propio embebible: SQLite no lo usa nadie (comprobado con grep en la build). ssl se
#: queda: lo necesitará el comprobador de actualizaciones por HTTPS (seguridad §5).
PRUNE_EMBED = ("sqlite3.dll", "_sqlite3.pyd")

PORTABLE_LAUNCHER = """@echo off
rem Luzeira portable: abre la app (icono junto al reloj) sin ventana de consola.
rem Los temas y la configuracion se guardan en %APPDATA%\\Luzeira, como en la instalada.
start "" "%~dp0python\\pythonw.exe" -m luzeira %*
"""


def log(msg: str) -> None:
    print(f"[empaquetar] {msg}", flush=True)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    log("$ " + subprocess.list2cmdline(cmd))
    return subprocess.run(cmd, check=True, **kwargs)  # noqa: S603 — órdenes fijas del script


#: Inno (VersionInfoVersion) solo admite números: ``X.Y.Z``. Una «0.2.0rc1» daría un error
#: poco claro de ISCC; mejor parar aquí.
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = data["project"]["version"]
    if not VERSION_RE.fullmatch(version):
        sys.exit(f"Versión «{version}» no válida para el instalador: tiene que ser X.Y.Z.")
    return version


def iss_defines() -> set[str]:
    """Los nombres que el ``.iss`` deja cambiar desde fuera (sus ``#ifndef``)."""
    text = ISS.read_text(encoding="utf-8")
    return set(re.findall(r"^#ifndef (\w+)", text, flags=re.MULTILINE))


def check_defines(defines: list[str]) -> None:
    """``--define`` solo con nombres que el ``.iss`` conoce y valores sin caracteres que
    cambien su sentido allí (comillas de Pascal, llaves de constantes de Inno)."""
    allowed = iss_defines() - {"AppVersion", "SourceDir", "OutputDir", "IconFile"}
    for item in defines:
        name, sep, value = item.partition("=")
        if not sep or name not in allowed:
            sys.exit(f"--define {item!r}: se admiten {', '.join(sorted(allowed))}.")
        if any(c in value for c in "'\"{}"):
            sys.exit(f"--define {item!r}: el valor no puede llevar comillas ni llaves.")


def editor_is_stale() -> bool:
    """¿Hay fuentes del editor más nuevas que el editor compilado?"""
    if not EDITOR_INDEX.is_file():
        return True
    sources = [
        *(ROOT / "editor" / "src").rglob("*"),
        ROOT / "editor" / "index.html",
        ROOT / "editor" / "package-lock.json",
    ]
    newest = max((p.stat().st_mtime for p in sources if p.is_file()), default=0)
    return newest > EDITOR_INDEX.stat().st_mtime


# --- 1. Editor ---------------------------------------------------------------------------


def build_editor() -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("No encuentro npm: hace falta Node.js para compilar el editor (--editor).")
    run([npm, "ci"], cwd=ROOT / "editor")
    run([npm, "run", "build"], cwd=ROOT / "editor")


# --- 2. Python embebible -----------------------------------------------------------------

#: Contenido de ``python312._pth``. Un ``._pth`` junto al ejecutable hace que Python
#: **solo** use estas rutas: ignora PYTHONPATH, el registro y la carpeta actual, así que la
#: app no puede cargar por accidente módulos de otro Python del equipo.
#: - ``python312.zip``: la biblioteca estándar precompilada que trae el embebible.
#: - ``.``: la carpeta de python.exe (los .pyd de la stdlib).
#: - ``Lib\\site-packages``: nuestras dependencias y el paquete luzeira.
#: - **Sin** ``import site`` (en el original viene comentado y así se queda). Activarlo
#:   solo serviría para procesar ficheros ``.pth`` y ninguna dependencia trae uno
#:   (``check_no_pth`` lo vigila). Y tiene un coste medido en la build: con ``import site``
#:   Python **añade el site-packages del usuario** (``%APPDATA%\\Python\\Python312\\
#:   site-packages``, ``site.ENABLE_USER_SITE`` = True), así que cualquier ``pip install
#:   --user`` o un ``.pth`` que deje otro programa ahí se ejecutaría al arrancar Luzeira.
PTH = "python312.zip\n.\nLib\\site-packages\n\n# Sin import site: ver scripts/empaquetar.py\n"


def check_embed_zip(embed_zip: Path) -> None:
    """Falla si el zip no es exactamente el Python embebible verificado (EMBED_SHA256)."""
    if not embed_zip.is_file():
        sys.exit(f"No encuentro {embed_zip}.")
    actual = sha256(embed_zip)
    if actual != EMBED_SHA256:
        sys.exit(
            f"{embed_zip} no es el Python embebible esperado: SHA-256 {actual}, "
            f"se esperaba {EMBED_SHA256}."
        )


def pth_imports_site(text: str) -> bool:
    """¿Activaría este ``._pth`` el módulo site? Igual que CPython (``getpath.py``): se
    quita lo que va tras ``#`` y se mira si la línea queda en ``import site``."""
    return any(line.partition("#")[0].strip() == "import site" for line in text.splitlines())


def unpack_python(embed_zip: Path, py_dir: Path) -> None:
    check_embed_zip(embed_zip)
    with zipfile.ZipFile(embed_zip) as zf:
        names = set(zf.namelist())
        expected = {f"python{PYTHON_TAG}.dll", "pythonw.exe", f"python{PYTHON_TAG}._pth"}
        if not expected <= names:
            sys.exit(f"{embed_zip} no parece el Python {PYTHON_TAG} embebible de 64 bits.")
        zf.extractall(py_dir)
    pth = py_dir / f"python{PYTHON_TAG}._pth"
    pth.write_text(PTH, encoding="ascii", newline="\r\n")
    if pth_imports_site(pth.read_text(encoding="ascii")):
        sys.exit("El ._pth activaría import site: ver el comentario de PTH.")
    for name in PRUNE_EMBED:
        (py_dir / name).unlink(missing_ok=True)


# --- 3. Dependencias ---------------------------------------------------------------------


def install_packages(site: Path, tmp: Path, *, run=run) -> None:
    """``run`` se inyecta en los tests para comprobar las órdenes sin instalar nada."""
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("No encuentro uv.")
    req = tmp / "requisitos.txt"
    # Solo dependencias de ejecución (sin el grupo dev), con hashes, tal como están en uv.lock.
    run(
        [
            uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-header",
            "--format",
            "requirements.txt",
            "-o",
            str(req),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,  # uv también lo escribe en pantalla
    )
    # Nada se compila en la máquina de build salvo lo de SDIST_ALLOWED (lista cerrada).
    names = [
        line.split("==")[0].strip()
        for line in req.read_text(encoding="utf-8").splitlines()
        if line and not line[0].isspace() and "==" in line
    ]
    no_build: list[str] = []
    for name in names:
        if name not in SDIST_ALLOWED:
            no_build += ["--only-binary", name]
    common = [
        "--target",
        str(site),
        "--python-platform",
        PLATFORM,
        "--python-version",
        "3.12",
        "--no-config",
        "--link-mode",
        "copy",
        "--python",
        sys.executable,
    ]
    run(
        [
            uv,
            "pip",
            "install",
            "--require-hashes",
            "--no-deps",
            "-r",
            str(req),
            "--build-constraints",
            str(BUILD_CONSTRAINTS),
            *no_build,
            *common,
        ],
        cwd=ROOT,
    )
    wheels = tmp / "wheel"
    run([uv, "build", "--wheel", "--no-create-gitignore", "-o", str(wheels)], cwd=ROOT)
    (wheel,) = wheels.glob("luzeira-*.whl")
    run([uv, "pip", "install", "--no-deps", str(wheel), *common], cwd=ROOT)


# --- 4. Poda -----------------------------------------------------------------------------


def prune(site: Path) -> list[str]:
    removed: list[str] = []

    def rm(path: Path) -> None:
        removed.append(path.relative_to(site).as_posix())
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    # uv --target deja lanzadores .exe de los scripts con la ruta del Python de la build.
    for name in ("bin", "Scripts"):
        if (site / name).exists():
            rm(site / name)
    for rel in sorted(PRUNE_FILES):
        if (site / rel).exists():
            rm(site / rel)
    for pattern in PRUNE_GLOBS:
        for path in sorted(site.glob(pattern)):
            rm(path)
    for path in sorted(site.rglob("*"), reverse=True):
        if path.is_dir() and path.name in PRUNE_DIR_NAMES and path.exists():
            # Solo dentro de paquetes de terceros; luzeira no trae tests en la wheel.
            rm(path)
        elif path.is_file() and path.suffix in {".h", ".c", ".pyx", ".pxd"}:
            rm(path)
    return removed


def clean_records(site: Path) -> None:
    """Quita de cada ``RECORD`` las líneas de ficheros ya podados (si no, el RECORD de
    luzeira cambiaría en cada build por el hash de ``uv_cache.json``)."""
    for record in site.glob("*.dist-info/RECORD"):
        lines = record.read_text(encoding="utf-8").splitlines()
        kept = [x for x in lines if x and (site / x.split(",")[0]).exists()]
        record.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")


def check_no_pth(site: Path) -> None:
    """El ``._pth`` va sin ``import site``: un ``.pth`` en site-packages se ignoraría."""
    found = sorted(p.name for p in site.glob("*.pth"))
    if found:
        sys.exit(f"Hay ficheros .pth ({found}): revisa PTH en scripts/empaquetar.py.")


# --- 5. Bytecode y fechas ----------------------------------------------------------------


def compile_bytecode(py_dir: Path) -> None:
    """``.pyc`` con *checked-hash* (PEP 552): no llevan la fecha del fuente, así que dos
    builds dan los mismos bytes, y Python los valida por hash. Así no tiene que escribir
    ``__pycache__`` en la carpeta del programa la primera vez (arranque más rápido y la
    desinstalación no se deja nada). ``-s`` quita la ruta de la build de ``co_filename``."""
    site = py_dir / "Lib" / "site-packages"
    run(
        [
            str(py_dir / "python.exe"),
            "-m",
            "compileall",
            "-q",
            "-j",
            "1",
            "--invalidation-mode",
            "checked-hash",
            "-s",
            str(py_dir),
            str(site),
        ],
        env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")},
    )


#: Lo que importa cada proceso de la app al arrancar: la bandeja, y la ventana del editor
#: con el motor de Edge (pythonnet + DLL de WebView2). Si la poda se lleva algo que hace
#: falta, se ve aquí y no en el equipo del usuario.
SMOKE_IMPORTS = (
    "import luzeira.bandeja, luzeira.editor.conexion, luzeira.editor.ventana; "
    "import webview.platforms.winforms, webview.platforms.edgechromium"
)


def smoke_test(py_dir: Path, *, run=run) -> None:
    """Arranca el Python ensamblado y carga los módulos de la app (``-B``: sin escribir
    ``.pyc``, la carpeta queda igual)."""
    run(
        [str(py_dir / "python.exe"), "-B", "-c", SMOKE_IMPORTS],
        env={
            **{
                name: os.environ[name]
                for name in ("SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)", "TEMP")
                if name in os.environ
            },
            # pywebview añade sus DLL a Path; solo System32, nada del PATH de la máquina.
            "PATH": str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"),
        },
    )


def fix_mtimes(base: Path, epoch: int) -> None:
    for path in base.rglob("*"):
        os.utime(path, (epoch, epoch))


# --- 6. Licencias ------------------------------------------------------------------------

LICENSE_PREFIXES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
#: Textos de licencia de los paquetes que no los traen (BSD/MIT obligan a incluirlos en lo
#: que se distribuye). Uno por paquete, con nombre normalizado: ``<nombre>.txt``.
LICENSES_DIR = ROOT / "instalador" / "licencias"


def normalize_name(name: str) -> str:
    """Nombre de paquete normalizado (PEP 503): ``proxy_tools`` → ``proxy-tools``."""
    return re.sub(r"[-_.]+", "-", name).lower()


def third_party_notices(site: Path) -> str:
    out = [
        "Luzeira — avisos de terceros",
        "=" * 30,
        "",
        "Luzeira es software libre (GPL-3.0-or-later, ver LICENSE.txt). Este paquete incluye",
        "software de terceros, cada uno con su licencia:",
        "",
        "* Python 3.12 (Python Software Foundation License y las de sus componentes:",
        "  OpenSSL, libffi, expat, zlib, bzip2, xz...): python\\LICENSE.txt",
        "* Fuente Inter (SIL Open Font License 1.1):",
        "  python\\Lib\\site-packages\\luzeira\\recursos\\fuentes\\OFL.txt",
        "* Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.*.dll, dentro de pywebview):",
        "  licencia de tipo BSD de Microsoft, texto más abajo.",
        "",
        "Paquetes de Python incluidos (de sus metadatos *.dist-info):",
        "",
    ]
    # El SDK de WebView2 no es un paquete de Python: su texto va fijo (BSD: obligatorio).
    sdk = (LICENSES_DIR / "webview2-sdk.txt").read_text(encoding="utf-8").strip()
    bodies: list[str] = ["", "-" * 78, "Microsoft Edge WebView2 SDK — License", "-" * 78, "", sdk]
    parser = email.parser.Parser()
    for info in sorted(site.glob("*.dist-info"), key=lambda p: p.name.lower()):
        meta = parser.parsestr((info / "METADATA").read_text(encoding="utf-8", errors="replace"))
        name, version = meta.get("Name", info.name), meta.get("Version", "?")
        if name.lower() == "luzeira":
            continue
        lic = meta.get("License-Expression") or ""
        if not lic:
            raw = (meta.get("License") or "").strip()
            lic = raw.splitlines()[0] if raw and len(raw) < 100 else ""
        if not lic:
            classifiers = [
                c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
            ]
            lic = "; ".join(c.split(" :: ")[-1] for c in classifiers) or "ver texto"
        out.append(f"- {name} {version}: {lic}")
        files = sorted(
            p
            for p in info.rglob("*")
            if p.is_file() and p.name.upper().startswith(LICENSE_PREFIXES)
        )
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
            bodies += ["", "-" * 78, f"{name} {version} — {f.name}", "-" * 78, "", text]
        if not files:
            raw = (meta.get("License") or "").strip()
            own_text = LICENSES_DIR / f"{normalize_name(name)}.txt"
            if own_text.is_file():  # el paquete no lo trae: va en el repo, con su fuente
                text = own_text.read_text(encoding="utf-8").strip()
                bodies += ["", "-" * 78, f"{name} {version} — License", "-" * 78, "", text]
            elif len(raw) >= 100:  # el texto completo viene en el campo License
                bodies += ["", "-" * 78, f"{name} {version} — License", "-" * 78, "", raw]
            else:
                # El paquete no trae el texto: queda dicho y se avisa (hay que añadirlo).
                urls = [meta.get("Home-page") or "", *(meta.get_all("Project-URL") or [])]
                where = next((u.split(", ")[-1] for u in urls if u), "su proyecto")
                missing = f"{name} {version}: el paquete no incluye el texto ({lic}); ver {where}"
                bodies += ["", "-" * 78, missing, "-" * 78]
                log(f"AVISO licencia sin texto: {missing}")
    return "\n".join([*out, "", "", "Textos de las licencias", "=" * 23, *bodies]) + "\n"


# --- 7 y 8. Instalador, portable y sumas -------------------------------------------------


def check_inno(iscc: Path) -> None:
    """Falla si algún binario de Inno no es el verificado (INNO_SHA256)."""
    for name, expected in INNO_SHA256.items():
        path = iscc.parent / name
        if not path.is_file():
            sys.exit(f"Falta {path} (Inno Setup 7.1.1). Usa --iscc.")
        if sha256(path) != expected:
            sys.exit(f"{path} no es el de Inno Setup 7.1.1 verificado (SHA-256 distinto).")


def build_installer(iscc: Path, src: Path, out: Path, version: str, defines: list[str]) -> Path:
    if not iscc.is_file():
        sys.exit(f"No encuentro ISCC.exe en {iscc} (Inno Setup 7). Usa --iscc.")
    check_inno(iscc)
    cmd = [
        str(iscc),
        "/Qp",
        f"/DAppVersion={version}",
        f"/DSourceDir={src}",
        f"/DOutputDir={out}",
        f"/DIconFile={ICON}",
    ]
    cmd += [f"/D{d}" for d in defines]
    run([*cmd, str(ISS)])
    return out / f"Luzeira-{version}-instalador.exe"


# --- 8. Módulo de sensores (opcional) ----------------------------------------------------


def module_version() -> str:
    """La versión del módulo (``<Version>`` de ``Directory.Build.props``): va aparte de la
    de Luzeira, porque se instala y se actualiza por separado."""
    props = (MODULE_DIR / "Directory.Build.props").read_text(encoding="utf-8")
    found = re.findall(r"<Version>([^<]*)</Version>", props)
    if len(found) != 1 or not VERSION_RE.fullmatch(found[0]):
        sys.exit(f"La versión del módulo en Directory.Build.props no es X.Y.Z: {found}")
    return found[0]


def find_dotnet() -> Path:
    """El ``dotnet`` del PATH (el de la CI) o el instalado por usuario en esta máquina, el
    primero que tenga el SDK de ``global.json``: el de ``Program Files`` suele ser solo el
    runtime, sin SDK (medido: «No .NET SDKs were found»)."""
    candidates = [shutil.which("dotnet")]
    candidates.append(
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "dotnet", "dotnet.exe")
    )
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        # Desde la carpeta del módulo, «dotnet --version» respeta global.json: falla si
        # ese SDK no está.
        probe = subprocess.run(  # noqa: S603 — ruta comprobada, argumentos fijos
            [candidate, "--version"], cwd=MODULE_DIR, capture_output=True, check=False
        )
        if probe.returncode == 0:
            return Path(candidate)
    sys.exit("No encuentro dotnet con el SDK que fija modulo-sensores/global.json.")


def check_module_publish(publish: Path) -> None:
    """Lo mismo que comprueba ci.yml (ADR-008, punto 2): sin startup hooks, con el runtime
    fijado y sin empaquetar en un solo fichero (que se descomprimiría en %TEMP%)."""
    config = json.loads((publish / "LuzeiraSensores.runtimeconfig.json").read_text("utf-8"))
    options = config["runtimeOptions"]
    if (
        options.get("configProperties", {}).get("System.StartupHookProvider.IsSupported")
        is not False
    ):
        sys.exit("El módulo publicado admite startup hooks. No se empaqueta.")
    if options["includedFrameworks"][0]["version"] != MODULE_RUNTIME:
        sys.exit(f"El módulo no lleva el runtime {MODULE_RUNTIME}. No se empaqueta.")
    for name in ("coreclr.dll", "LuzeiraSensores.dll", "LibreHardwareMonitorLib.dll"):
        if not (publish / name).is_file():
            sys.exit(f"Falta {name} en el módulo publicado (¿fichero único?).")


def build_module(dotnet: Path, iscc: Path, build: Path, out: Path) -> Path:
    """Publica el servicio en limpio (dependencias del lock, firmas exigidas por
    ``nuget.config``) y compila su instalador, que sí pide administrador."""
    version = module_version()
    publish = build / "modulo-sensores"
    if publish.exists():
        shutil.rmtree(publish)
    log(f"Módulo de sensores {version} → {publish}")
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    run(
        [str(dotnet), "restore", str(MODULE_DIR / "LuzeiraSensores.slnx"), "--locked-mode"], env=env
    )
    project = MODULE_DIR / "src" / "LuzeiraSensores"
    publish_cmd = [str(dotnet), "publish", str(project), "-c", "Release", "--no-restore"]
    run([*publish_cmd, "-o", str(publish)], env=env)
    check_module_publish(publish)
    check_inno(iscc)
    run(
        [
            str(iscc),
            "/Qp",
            f"/DAppVersion={version}",
            f"/DSourceDir={publish}",
            f"/DOutputDir={out}",
            f"/DIconFile={ICON}",
            str(MODULE_ISS),
        ]
    )
    return out / f"LuzeiraSensores-{version}-setup.exe"


def build_portable(src: Path, dest: Path, epoch: int) -> None:
    """Zip con entradas ordenadas, fecha fija y permisos fijos: misma build → mismos bytes."""
    stamp = time.gmtime(max(epoch, 315532800))[:6]  # el formato zip no admite antes de 1980
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(src.rglob("*"), key=lambda p: p.relative_to(src).as_posix()):
            if path.is_dir():
                continue
            info = zipfile.ZipInfo("Luzeira/" + path.relative_to(src).as_posix(), stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes(), compresslevel=9)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _git_emails() -> set[str]:
    """Emails de git de esta máquina (global y del repo), menos el ``noreply`` de GitHub,
    que ya es público a propósito."""
    git = shutil.which("git")
    if git is None:
        return set()
    try:
        out = subprocess.run(  # noqa: S603 — git de la máquina, órdenes fijas
            [git, "config", "--get-all", "user.email"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        out += subprocess.run(  # noqa: S603
            [git, "config", "--global", "--get-all", "user.email"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return set()
    return {
        line.strip().lower()
        for line in out.splitlines()
        if "@" in line and not line.strip().lower().endswith("@users.noreply.github.com")
    }


def on_github_runner() -> bool:
    """¿Se construye en una máquina de GitHub Actions?

    Allí el usuario (``runneradmin``), su carpeta y el nombre del equipo son los de una
    máquina desechable igual para todo el mundo, no de una persona. Y aparecen dentro de
    librerías ya compiladas en otras máquinas de GitHub: ``pydantic_core`` lleva rutas
    ``C:/Users/runneradmin/.cargo/…`` de donde lo compilaron (primera release,
    2026-11-18). Buscarlos ahí daría un falso positivo sin proteger a nadie."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def personal_needles() -> set[str]:
    """Lo que identifica a quien construye: no debe aparecer en nada que se publique.

    Se lee en el momento (nada de nombres escritos aquí) y se busca en minúsculas.
    Medido en la build de 2026-11-18: cero coincidencias en todos los ficheros, también
    en los binarios, así que se buscan todos sin lista de extensiones.

    En GitHub Actions solo el email de git (ver ``on_github_runner``)."""
    needles = set(_git_emails())
    if on_github_runner():
        return needles
    needles.add(str(Path.home()).lower())
    for var in ("USERNAME", "USER", "COMPUTERNAME"):
        value = os.environ.get(var, "")
        if len(value) >= 3:
            needles.add(value.lower())
    needles |= {n.replace("\\", "/") for n in needles}
    return needles


_WORD = rb"[a-z0-9\x80-\xff]"  # letra o cifra (bytes altos: letras no ASCII en UTF-8)


def contains_needle(data: bytes, needle: str) -> bool:
    """¿Aparece ``needle`` en ``data`` (ya en minúsculas), en UTF-8 o UTF-16?

    Un nombre suelto (usuario o equipo, solo letras y cifras) cuenta solo como palabra
    entera: si no, «erik» saltaría dentro de «içerik» (turco, C-62). Rutas y emails se
    buscan tal cual."""
    if not re.fullmatch(r"[a-z0-9]+", needle):
        return needle.encode() in data or needle.encode("utf-16-le") in data
    word = re.escape(needle.encode())
    if re.search(rb"(?<!" + _WORD + rb")" + word + rb"(?!" + _WORD + rb")", data):
        return True
    wide = needle.encode("utf-16-le")

    def letter(unit: bytes) -> bool:  # una unidad UTF-16 que es letra o cifra
        return len(unit) == 2 and (unit[1] != 0 or chr(unit[0]).isalnum())

    start = data.find(wide)
    while start != -1:
        end = start + len(wide)
        if not letter(data[max(0, start - 2) : start]) and not letter(data[end : end + 2]):
            return True
        start = data.find(wide, start + 1)
    return False


def check_no_personal_paths(base: Path) -> None:
    """Falla si algún fichero del paquete (de cualquier tipo) lleva rutas de esta máquina
    o datos de quien construye (usuario, equipo, email)."""
    # Rutas de la máquina de build (repo, Pythons, temporales, caché de uv).
    home = Path.home()
    places = [ROOT, base, Path(sys.base_prefix), Path(tempfile.gettempdir())]
    if not on_github_runner():  # en GitHub, la carpeta del usuario no es de nadie
        places += [home / "AppData", home / ".local", home / ".cache"]
    needles = {str(p).lower() for p in places} | personal_needles()
    needles |= {n.replace("\\", "/") for n in needles}
    for path in base.rglob("*"):
        if path.is_file():
            data = path.read_bytes().lower()
            for needle in needles:
                if contains_needle(data, needle):
                    # Sin decir cuál: el mensaje acabaría en un log público de CI.
                    sys.exit(f"Dato personal o ruta de la máquina dentro del paquete: {path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--python-embebible",
        type=Path,
        required=True,
        help="zip python-3.12.x-embed-amd64.zip de python.org (ya verificado)",
    )
    p.add_argument("--iscc", type=Path, default=DEFAULT_ISCC, help="ruta de ISCC.exe")
    p.add_argument("--build", type=Path, default=ROOT / "build", help="carpeta de ensamblado")
    p.add_argument("--salida", type=Path, default=ROOT / "dist", help="carpeta de resultados")
    p.add_argument("--temporal", type=Path, default=None, help="temporales (por defecto, %%TEMP%%)")
    p.add_argument(
        "--editor", action="store_true", help="compilar el editor antes (npm ci y build)"
    )
    p.add_argument("--sin-instalador", action="store_true", help="no llamar a ISCC")
    p.add_argument(
        "--modulo",
        action="store_true",
        help="también el instalador del módulo de sensores (necesita el SDK de .NET)",
    )
    p.add_argument(
        "--define",
        action="append",
        default=[],
        metavar="NOMBRE=VALOR",
        help="define extra para el .iss (solo pruebas: MutexName, RunKey...)",
    )
    args = p.parse_args(argv)
    # Con la salida redirigida a un fichero, Windows usa cp1252 y «→» rompería el build.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")
    check_defines(args.define)
    if args.modulo and args.sin_instalador:
        sys.exit("--modulo es un instalador: no va con --sin-instalador.")
    if args.define and args.salida.resolve() == (ROOT / "dist").resolve():
        # Un instalador con mutex o claves de prueba no puede acabar en dist/ con el
        # nombre del de verdad.
        sys.exit("Con --define, indica otra carpeta con --salida (no dist/).")

    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_EPOCH))
    version = project_version()
    t0 = time.perf_counter()

    if args.editor:
        build_editor()
    if editor_is_stale():
        # El editor compilado está en .gitignore: podría ser de otra versión.
        sys.exit(
            f"El editor compilado ({EDITOR_INDEX.relative_to(ROOT)}) falta o es más viejo "
            "que sus fuentes. Usa --editor (o «npm run build» en editor/)."
        )

    app_dir = args.build.resolve() / "Luzeira"
    py_dir = app_dir / "python"
    site = py_dir / "Lib" / "site-packages"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    site.mkdir(parents=True)

    log(f"Luzeira {version}: Python embebible → {py_dir}")
    unpack_python(args.python_embebible, py_dir)

    with tempfile.TemporaryDirectory(dir=args.temporal, prefix="empaquetar-") as tmp:
        install_packages(site, Path(tmp))
    for removed in prune(site):
        log(f"quitado: {removed}")
    clean_records(site)
    check_no_pth(site)
    compile_bytecode(py_dir)
    smoke_test(py_dir)

    shutil.copyfile(ROOT / "LICENSE", app_dir / "LICENSE.txt")
    shutil.copyfile(ROOT / "README.md", app_dir / "README.md")
    (app_dir / "TERCEROS.txt").write_text(third_party_notices(site), encoding="utf-8")
    (app_dir / "Luzeira.cmd").write_text(PORTABLE_LAUNCHER, encoding="ascii", newline="\r\n")
    check_no_personal_paths(app_dir)
    fix_mtimes(app_dir, epoch)

    out = args.salida.resolve()
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    if not args.sin_instalador:
        artifacts.append(build_installer(args.iscc, app_dir, out, version, args.define))
    portable = out / f"Luzeira-{version}-portable.zip"
    build_portable(app_dir, portable, epoch)
    artifacts.append(portable)
    if args.modulo:
        # En SHA256SUMS con lo demás: lo cubren la attestation y la firma de SHA256SUMS.
        artifacts.append(build_module(find_dotnet(), args.iscc, args.build.resolve(), out))
    # Formato de «sha256sum» (dos espacios, LF): se comprueba con «sha256sum -c» o a mano
    # con Get-FileHash en PowerShell.
    sums = "".join(f"{sha256(a)}  {a.name}\n" for a in artifacts)
    (out / "SHA256SUMS").write_text(sums, encoding="ascii", newline="\n")

    log(f"Hecho en {time.perf_counter() - t0:.0f} s.")
    log(f"Carpeta ensamblada: {mb(dir_size(app_dir))} ({app_dir})")
    for a in artifacts:
        log(f"{a.name}: {mb(a.stat().st_size)}")
    print(sums, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
