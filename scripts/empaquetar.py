"""Empaqueta Visoria para Windows: instalador (Inno Setup), zip portable y SHA256SUMS.

Decisión D-12 (fase 6): **Python 3.12 embebible oficial** (python.org, ejecutables firmados
por la PSF) + un ``site-packages`` propio + **Inno Setup por usuario** (sin admin).
La app arranca con ``python\\pythonw.exe -m visoria``: no hay ``.exe`` propio que firmar.

Uso (desde la raíz del repo, con el Python del proyecto; solo usa la biblioteca estándar)::

    uv run python scripts/empaquetar.py --python-embebible RUTA\\python-3.12.10-embed-amd64.zip

Pasos (cada uno se explica en su función):

1. (opcional, ``--editor``) compila el editor web con ``npm ci`` + ``npm run build``.
2. Descomprime el embebible en ``build/Visoria/python`` y ajusta ``python312._pth``.
3. Instala en ``python/Lib/site-packages`` las dependencias **de ejecución** fijadas en
   ``uv.lock`` (con sus hashes) y la wheel de Visoria construida con ``uv build``.
4. Quita lo que no hace falta (y lo que llevaría rutas de esta máquina).
5. Precompila a ``.pyc`` reproducibles y fija las fechas de todos los ficheros.
6. Copia ``LICENSE``, ``README.md`` y genera ``TERCEROS.txt`` con las licencias.
7. Compila el instalador con ISCC (versión sacada de ``pyproject.toml``).
8. Genera ``dist/Visoria-<versión>-portable.zip`` y ``dist/SHA256SUMS``, y dice tamaños.

Red: solo la que use ``uv`` para bajar de PyPI las wheels ya fijadas en ``uv.lock``.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
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
ISS = ROOT / "instalador" / "visoria.iss"
ICON = ROOT / "src" / "visoria" / "recursos" / "visoria.ico"
EDITOR_INDEX = ROOT / "src" / "visoria" / "editor" / "web" / "index.html"
DEFAULT_ISCC = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"

#: Versión del Python embebible que se espera (la de ``.python-version`` y el desarrollo).
PYTHON_TAG = "312"
#: SHA-256 de ``python-3.12.10-embed-amd64.zip`` (11.133.606 B). python.org publica su MD5
#: (``fe8ef205f2e9c3ba44d0cf9954e1abd3``, página de la release, consultada 2026-11-18): el
#: fichero bajado por HTTPS coincide con él y de ese fichero sale este hash. El script
#: **ejecuta** el python.exe de ese zip (compileall): sin esto, un zip manipulado correría
#: código en la máquina de build y acabaría dentro del paquete.
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
#: SHA-256 de los binarios de Inno Setup 6.7.4 que compilan el instalador o van dentro de
#: él. ISCC.exe y las DLL llevan firma válida de Pyrsys B.V. (comprobada 2026-11-18); los
#: ``.e32`` no van firmados, por eso se fija el hash de todos. Otra versión de Inno = otros
#: hashes: se actualizan aquí a propósito, nunca en silencio.
INNO_SHA256 = {
    "ISCC.exe": "f02fa0e5f237b47d7ed1edae9b991866ec94fe02af61eb6a73e94f92fe8e0604",
    "ISCmplr.dll": "236555054ca65194c69544b4ef1d49ea345259745f5c4a2005aca7b5674e7cff",
    "ISPP.dll": "140b2d822bf30ed8aa3fed64764b2d2b92a80e0b9aafc1b8ff3e409706b9207e",
    "islzma.dll": "8af307ce3738ab1d37cc7e76b2f76615e2045597d4367c3e40d52b5f2fbd353d",
    "Setup.e32": "0c94d43e2c0f4d8371a2e1813b5446f3bc85ece3211dc4a6676159b534bbc483",
    "SetupLdr.e32": "998c58046b0808e75a030859962e1fe1ea600e8a5cf4646b09aadce77342c1a9",
}
#: Plataforma de las wheels: Windows de 64 bits, la única que soporta Visoria v1.
PLATFORM = "x86_64-pc-windows-msvc"
#: Paquetes que PyPI solo publica como código fuente (sdist) y se dejan construir.
#: proxy-tools 0.1.0 (dependencia de pywebview, MIT, un solo .py) no tiene wheel. Su sdist
#: va verificado por el hash de uv.lock; el backend de build (setuptools) no está fijado.
SDIST_ALLOWED = {"proxy-tools"}
#: Fecha fija para ficheros y zip (2026-01-01 00:00 UTC) si no se pasa SOURCE_DATE_EPOCH.
DEFAULT_EPOCH = 1767225600

# Lo que se quita del paquete, con su porqué (medido en la build de la fase 6).
PRUNE_FILES = {
    # Pillow: AVIF (~7,5 MB) y el puente con Tk. Visoria solo abre PNG/JPEG/GIF, y Pillow
    # importa cada plugin dentro de un try: sin estos .pyd sigue funcionando.
    "PIL/_avif.cp312-win_amd64.pyd",
    "PIL/_imagingtk.cp312-win_amd64.pyd",
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
rem Visoria portable: abre la app (icono junto al reloj) sin ventana de consola.
rem Los temas y la configuracion se guardan en %APPDATA%\\Visoria, como en la instalada.
start "" "%~dp0python\\pythonw.exe" -m visoria %*
"""


def log(msg: str) -> None:
    print(f"[empaquetar] {msg}", flush=True)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    log("$ " + subprocess.list2cmdline(cmd))
    return subprocess.run(cmd, check=True, **kwargs)  # noqa: S603 — órdenes fijas del script


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


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
#: - ``Lib\\site-packages``: nuestras dependencias y el paquete visoria.
#: - **Sin** ``import site`` (en el original viene comentado y así se queda). Activarlo
#:   solo serviría para procesar ficheros ``.pth`` y ninguna dependencia trae uno
#:   (``check_no_pth`` lo vigila). Y tiene un coste medido en la build: con ``import site``
#:   Python **añade el site-packages del usuario** (``%APPDATA%\\Python\\Python312\\
#:   site-packages``, ``site.ENABLE_USER_SITE`` = True), así que cualquier ``pip install
#:   --user`` o un ``.pth`` que deje otro programa ahí se ejecutaría al arrancar Visoria.
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
        [uv, "pip", "install", "--require-hashes", "--no-deps", "-r", str(req), *no_build, *common],
        cwd=ROOT,
    )
    wheels = tmp / "wheel"
    run([uv, "build", "--wheel", "--no-create-gitignore", "-o", str(wheels)], cwd=ROOT)
    (wheel,) = wheels.glob("visoria-*.whl")
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
            # Solo dentro de paquetes de terceros; visoria no trae tests en la wheel.
            rm(path)
        elif path.is_file() and path.suffix in {".h", ".c", ".pyx", ".pxd"}:
            rm(path)
    return removed


def clean_records(site: Path) -> None:
    """Quita de cada ``RECORD`` las líneas de ficheros ya podados (si no, el RECORD de
    visoria cambiaría en cada build por el hash de ``uv_cache.json``)."""
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
    "import visoria.bandeja, visoria.editor.conexion, visoria.editor.ventana; "
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
        "Visoria — avisos de terceros",
        "=" * 30,
        "",
        "Visoria es software libre (GPL-3.0-or-later, ver LICENSE.txt). Este paquete incluye",
        "software de terceros, cada uno con su licencia:",
        "",
        "* Python 3.12 (Python Software Foundation License y las de sus componentes:",
        "  OpenSSL, libffi, expat, zlib, bzip2, xz...): python\\LICENSE.txt",
        "* Fuente Inter (SIL Open Font License 1.1):",
        "  python\\Lib\\site-packages\\visoria\\recursos\\fuentes\\OFL.txt",
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
        if name.lower() == "visoria":
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
        path = iscc.with_name(name)
        if not path.is_file():
            sys.exit(f"Falta {path} (Inno Setup 6.7.4). Usa --iscc.")
        if sha256(path) != expected:
            sys.exit(f"{path} no es el de Inno Setup 6.7.4 verificado (SHA-256 distinto).")


def build_installer(iscc: Path, src: Path, out: Path, version: str, defines: list[str]) -> Path:
    if not iscc.is_file():
        sys.exit(f"No encuentro ISCC.exe en {iscc} (Inno Setup 6). Usa --iscc.")
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
    return out / f"Visoria-{version}-instalador.exe"


def build_portable(src: Path, dest: Path, epoch: int) -> None:
    """Zip con entradas ordenadas, fecha fija y permisos fijos: misma build → mismos bytes."""
    stamp = time.gmtime(max(epoch, 315532800))[:6]  # el formato zip no admite antes de 1980
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(src.rglob("*"), key=lambda p: p.relative_to(src).as_posix()):
            if path.is_dir():
                continue
            info = zipfile.ZipInfo("Visoria/" + path.relative_to(src).as_posix(), stamp)
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


def check_no_personal_paths(base: Path) -> None:
    """Falla si algún fichero de texto del paquete lleva la ruta de esta máquina."""
    # Rutas de la máquina de build (repo, Pythons, temporales, caché de uv). No se busca
    # la carpeta de usuario a secas: un comentario de ejemplo daría un falso positivo.
    home = Path.home()
    places = [
        ROOT,
        base,
        Path(sys.base_prefix),
        Path(tempfile.gettempdir()),
        home / "AppData",
        home / ".local",
        home / ".cache",
    ]
    needles = {str(p).lower() for p in places}
    needles |= {n.replace("\\", "/") for n in needles}
    for path in base.rglob("*"):
        if path.is_file() and path.suffix.lower() in {
            ".txt",
            ".json",
            ".py",
            ".pth",
            ".cfg",
            ".toml",
            ".cmd",
            ".pyc",
            "",
        }:
            data = path.read_bytes().lower()
            for n in needles:
                if n.encode() in data or n.encode("utf-16-le") in data:
                    sys.exit(f"Ruta personal dentro del paquete: {path}")


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
        "--define",
        action="append",
        default=[],
        metavar="NOMBRE=VALOR",
        help="define extra para el .iss (solo pruebas: MutexName, RunKey...)",
    )
    args = p.parse_args(argv)

    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", DEFAULT_EPOCH))
    version = project_version()
    t0 = time.perf_counter()

    if args.editor:
        build_editor()
    if not EDITOR_INDEX.is_file():
        sys.exit(
            f"Falta el editor compilado ({EDITOR_INDEX.relative_to(ROOT)}). "
            "Compílalo con «npm run build» en editor/ o usa --editor."
        )

    app_dir = args.build.resolve() / "Visoria"
    py_dir = app_dir / "python"
    site = py_dir / "Lib" / "site-packages"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    site.mkdir(parents=True)

    log(f"Visoria {version}: Python embebible → {py_dir}")
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
    (app_dir / "Visoria.cmd").write_text(PORTABLE_LAUNCHER, encoding="ascii", newline="\r\n")
    check_no_personal_paths(app_dir)
    fix_mtimes(app_dir, epoch)

    out = args.salida.resolve()
    out.mkdir(parents=True, exist_ok=True)
    artifacts: list[Path] = []
    if not args.sin_instalador:
        artifacts.append(build_installer(args.iscc, app_dir, out, version, args.define))
    portable = out / f"Visoria-{version}-portable.zip"
    build_portable(app_dir, portable, epoch)
    artifacts.append(portable)
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
