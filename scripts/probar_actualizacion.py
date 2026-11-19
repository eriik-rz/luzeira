r"""Prueba de punta a punta del instalador en modo actualización (ADR-006, C-58).

Construye un instalador de prueba con defines aislados (otro AppGuid, otro mutex, otra
clave Run, carpetas propias en %TEMP%) y recorre lo que hace una actualización pedida
desde la app: esperar a que Luzeira suelte su mutex, conservar «Iniciar con Windows» como
estaba, reabrir la app, y si aborta, reabrir la que había (o no abrir otra si sigue
abierta). Al final desinstala y borra la clave de prueba.

Nunca toca la Luzeira instalada de verdad. «Reabrir» no arranca Luzeira (cogería la
pantalla): con el define ``ReopenParams`` el Python instalado copia un JSON, y la prueba
mira si apareció. Solo Windows; tarda unos 2 minutos.

    uv run python scripts/probar_actualizacion.py --python-embebible RUTA\python-embed.zip

Hace falta el editor compilado (``npm run build`` en ``editor/``) y Inno Setup, como
para ``scripts/empaquetar.py``.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from luzeira.actualizar import launch_command  # noqa: E402

GUID = "0E2E0E2E-1111-2222-3333-444455556666"
MUTEX = "LuzeiraPruebaAct"
RUN_KEY = r"Software\LuzeiraPruebaAct\Run"
RUN_VALUE = "LuzeiraPruebaAct"
UNINSTALL = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{GUID}}}_is1"
SETUP_MUTEX = "LuzeiraInstalador"  # SetupMutex del .iss (no se cambia en pruebas)
WAIT_STEPS = 6  # 3 s de espera al mutex en vez de 60

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = ctypes.c_void_p
k32.CloseHandle.argtypes = [ctypes.c_void_p]


class Mutex:
    """Un mutex con nombre, como el de la app, que se suelta una sola vez."""

    def __init__(self, name: str) -> None:
        self._handle = k32.CreateMutexW(None, False, name)
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._handle:
                k32.CloseHandle(self._handle)
                self._handle = None


class Run:
    def __init__(self, work: Path, setup: Path) -> None:
        self.work = work
        self.setup = setup
        self.app = work / "instalado"
        self.reopened = work / "reabierto.json"
        self.results: list[tuple[str, bool]] = []

    def check(self, name: str, ok: bool) -> None:
        self.results.append((name, ok))
        print(("OK    " if ok else "FALLA ") + name, flush=True)

    def wait_reopened(self, seconds: float = 30) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.reopened.exists():
                return True
            time.sleep(0.5)
        return False

    def as_the_app_would(self, log: str, hold: float = 3.0) -> tuple[int, float]:
        """Como la app: tiene su mutex, lanza el instalador desacoplado y lo suelta al
        salir (a los ``hold`` segundos, o al terminar el instalador si es antes)."""
        self.reopened.unlink(missing_ok=True)
        app = Mutex(MUTEX)
        cmd = launch_command(self.setup, self.work / log)
        t0 = time.monotonic()
        p = subprocess.Popen(cmd.args, creationflags=cmd.creationflags, close_fds=True)  # noqa: S603
        timer = threading.Timer(hold, app.release)
        timer.start()
        rc = p.wait(timeout=180)
        timer.cancel()
        app.release()
        return rc, time.monotonic() - t0


def run_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except FileNotFoundError:
        return None


def key_exists(path: str) -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, path).Close()
    except FileNotFoundError:
        return False
    return True


def build(work: Path, embed: Path) -> Path:
    defines = {
        "AppGuid": GUID,
        "MutexName": MUTEX,
        "RunKey": RUN_KEY,
        "RunValue": RUN_VALUE,
        "ProgramsDir": str(work / "menu"),
        "DesktopDir": str(work / "escritorio"),
        "UserDataDir": str(work / "datos"),
        "UserLogsDir": str(work / "logs"),
        "ReopenParams": f"-m json.tool {work / 'in.json'} {work / 'reabierto.json'}",
        "UpdateWaitSteps": str(WAIT_STEPS),
    }
    cmd = [
        sys.executable, str(ROOT / "scripts" / "empaquetar.py"),
        "--python-embebible", str(embed), "--salida", str(work / "dist"),
    ]  # fmt: skip
    for name, value in defines.items():
        cmd += ["--define", f"{name}={value}"]
    subprocess.run(cmd, check=True, cwd=ROOT)  # noqa: S603
    with (ROOT / "pyproject.toml").open("rb") as f:
        version = tomllib.load(f)["project"]["version"]
    return work / "dist" / f"Luzeira-{version}-instalador.exe"


def scenarios(r: Run) -> None:
    # 1. Instalación normal, silenciosa y con «Iniciar con Windows».
    p = subprocess.run(  # noqa: S603
        [str(r.setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={r.app}",
         "/TASKS=autoarranque", f"/LOG={r.work / 'log-instalar.txt'}"],
        timeout=300,
    )  # fmt: skip
    r.check("instala", p.returncode == 0 and (r.app / "python" / "pythonw.exe").exists())
    value = run_value()
    r.check("con autoarranque", value is not None and str(r.app).lower() in value.lower())
    r.check("una instalación normal no reabre la app", not r.reopened.exists())

    # 2. Actualización: espera al mutex, conserva el autoarranque y reabre.
    rc, dt = r.as_the_app_would("log-act-1.txt")
    r.check(f"actualiza esperando al mutex (rc={rc}, {dt:.1f} s)", rc == 0 and dt >= 3.0)
    r.check("reabre la app", r.wait_reopened())
    r.check("conserva el autoarranque", run_value() is not None)

    # 3. Si el usuario lo quitó desde la app, la actualización no lo vuelve a poner.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, RUN_VALUE)
    rc, _ = r.as_the_app_would("log-act-2.txt")
    r.check(f"actualiza otra vez (rc={rc})", rc == 0)
    r.check("reabre la app otra vez", r.wait_reopened())
    r.check("no vuelve a poner el autoarranque", run_value() is None)

    # 4. Sin /ACTUALIZAR y con la app abierta, se sigue negando como siempre.
    app = Mutex(MUTEX)
    p = subprocess.run(  # noqa: S603
        [str(r.setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/LOG={r.work / 'log-sin.txt'}"],
        timeout=120,
    )  # fmt: skip
    app.release()
    r.check(f"sin /ACTUALIZAR y con la app abierta, no instala (rc={p.returncode})", p.returncode)

    # 5. C-58: la app no suelta el mutex a tiempo → aborta y no abre una segunda.
    rc, _ = r.as_the_app_would("log-act-3.txt", hold=60.0)
    r.check(f"con la app sin cerrar, aborta (rc={rc})", rc != 0)
    r.check("y no abre una segunda Luzeira", not r.wait_reopened(5))

    # 6. C-58: otro instalador abierto → aborta y reabre la que había.
    other = Mutex(SETUP_MUTEX)
    rc, _ = r.as_the_app_would("log-act-4.txt", hold=0.5)
    other.release()
    r.check(f"con otro instalador abierto, aborta (rc={rc})", rc != 0)
    r.check("y vuelve a abrir la Luzeira que había", r.wait_reopened())


def clean_up(r: Run) -> None:
    uninstaller = r.app / "unins000.exe"
    if uninstaller.exists():
        p = subprocess.run(  # noqa: S603
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=180
        )
        time.sleep(3)  # el desinstalador se copia a %TEMP% y termina allí
        r.check("desinstala", p.returncode == 0 and not (r.app / "python").exists())
    r.check("sin entrada en Uninstall", not key_exists(UNINSTALL))
    r.check("sin valor en la Run de prueba", run_value() is None)
    for path in (RUN_KEY, r"Software\LuzeiraPruebaAct"):
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    r.check("clave de prueba borrada", not key_exists(r"Software\LuzeiraPruebaAct"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--python-embebible", type=Path, required=True)
    p.add_argument("--conservar", action="store_true", help="no borrar la carpeta de prueba")
    args = p.parse_args()
    work = Path(tempfile.mkdtemp(prefix="luzeira-act-"))
    if " " in str(work):
        sys.exit(f"La carpeta temporal tiene espacios ({work}): ReopenParams no los admite.")
    (work / "in.json").write_text('{"ok": 1}', encoding="utf-8")
    r = Run(work, build(work, args.python_embebible))
    try:
        scenarios(r)
    finally:
        clean_up(r)
        if not args.conservar:
            shutil.rmtree(work, ignore_errors=True)
    ok = all(ok for _, ok in r.results)
    print("TODO BIEN" if ok else f"HAY FALLOS (logs en {work})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
