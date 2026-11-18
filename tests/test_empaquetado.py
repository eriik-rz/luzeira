"""Empaquetado (fase 6): lo que se puede comprobar sin construir nada.

El instalador real se prueba construyéndolo (``scripts/empaquetar.py``) e instalándolo en
una carpeta temporal; aquí se vigila que el ``.iss`` y el script no pierdan por el camino
las garantías que se decidieron (D-12): sin admin, mismo mutex y misma línea de ``Run``
que la app, limpieza al desinstalar y nada de ``import site``.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from packaging.markers import Marker

from luzeira.sistema import autoarranque, instancia

ROOT = Path(__file__).resolve().parent.parent
ISS_PATH = ROOT / "instalador" / "luzeira.iss"
ISS = ISS_PATH.read_text(encoding="utf-8")


def _load_script():
    spec = importlib.util.spec_from_file_location("empaquetar", ROOT / "scripts" / "empaquetar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


emp = _load_script()


def _setup(key: str) -> str:
    """Valor de una directiva de ``[Setup]``."""
    match = re.search(rf"^{key}=(.*)$", ISS, re.MULTILINE)
    assert match, f"falta {key} en [Setup]"
    return match.group(1).strip()


def _define(name: str) -> str:
    match = re.search(rf'^\s*#define {name} "(.*)"$', ISS, re.MULTILINE)
    assert match, f"falta el #define {name}"
    return match.group(1)


def _section(name: str) -> str:
    match = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", ISS, re.MULTILINE | re.DOTALL)
    assert match, f"falta la sección [{name}]"
    return match.group(1)


# --- .iss ---------------------------------------------------------------------------------


def test_installs_per_user_without_admin():
    assert _setup("PrivilegesRequired") == "lowest"
    # Nada de ofrecer «instalar para todos» (pediría UAC).
    assert "PrivilegesRequiredOverridesAllowed" not in ISS
    assert _setup("DefaultDirName") == r"{localappdata}\Programs\Luzeira"


def test_app_id_is_a_fixed_guid():
    assert _setup("AppId") == "{{{#AppGuid}}"
    # El de verdad, fijo para siempre: cambiarlo = Windows vería otra app al actualizar.
    assert _define("AppGuid") == "5E01DD71-2E73-46B3-9498-C53B2D1E1AB7"


def test_spanish_and_64_bit():
    assert 'MessagesFile: "compiler:Languages\\Spanish.isl"' in ISS
    assert _setup("ArchitecturesInstallIn64BitMode") == "x64compatible"


def test_app_mutex_matches_the_app():
    assert _setup("AppMutex") == "{#MutexName}"
    # Sin prefijo, Windows busca el mutex en el espacio de la sesión («Local\»): el mismo
    # que crea la app.
    assert "Local\\" + _define("MutexName") == instancia.SingleInstance().mutex_name


def test_run_value_matches_the_app(monkeypatch, tmp_path):
    assert _define("RunKey") == autoarranque.RUN_KEY_PATH
    assert _define("RunValue") == autoarranque.VALUE_NAME
    line = next(x for x in _section("Registry").splitlines() if x.startswith("Root: HKCU"))
    assert "Tasks: autoarranque" in line
    # Sin uninsdeletevalue: Inno lo borraría aunque apuntase a otra Luzeira (portable).
    assert "uninsdeletevalue" not in line
    data = re.search(r'ValueData: "((?:[^"]|"")*)"', line).group(1).replace('""', '"')
    # Lo que escribiría la app desde la carpeta instalada.
    app = tmp_path / "Luzeira"
    (app / "python").mkdir(parents=True)
    (app / "python" / "pythonw.exe").touch()
    monkeypatch.setattr(sys, "executable", str(app / "python" / "python.exe"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = autoarranque.default_command()
    installed = data.replace("{#PythonW}", _define("PythonW")).replace("{app}", str(app))
    assert installed == expected


def test_uninstall_removes_run_value_created_by_the_app():
    code = _section("Code")
    assert "RegDeleteValue(HKCU, '{#RunKey}', '{#RunValue}')" in code
    assert "PointsHere(Command)" in code  # solo si apunta a esta instalación


def test_uninstall_asks_before_deleting_user_data():
    code = _section("Code")
    # Valores exactos: un «Sí» borra estas carpetas enteras con DelTree.
    assert _define("UserDataDir") == r"{userappdata}\Luzeira"
    assert _define("UserLogsDir") == r"{localappdata}\Luzeira"
    assert _define("ProgramsDir") == "{userprograms}"
    # La pregunta tiene «No» por defecto y, en silencio, contesta «No».
    assert re.search(r"SuppressibleMsgBox\([^;]*MB_DEFBUTTON2, IDNO\) = IDYES", code)
    assert code.index("SuppressibleMsgBox") < code.index("DelTree(DataDir")


def test_tasks_desktop_unchecked_autostart_offered():
    tasks = _section("Tasks")
    desktop = next(x for x in tasks.splitlines() if x.startswith('Name: "escritorio"'))
    assert "Flags: unchecked" in desktop
    assert 'Name: "autoarranque"' in tasks


def test_launch_after_install_is_optional_and_not_in_silent_mode():
    run = _section("Run")
    assert "postinstall" in run
    assert "skipifsilent" in run


def test_shortcuts_launch_pythonw_with_the_package_icon():
    assert _define("PythonW") == r"{app}\python\pythonw.exe"
    icons = _section("Icons")
    assert icons.count('Parameters: "-m luzeira"') == 2
    icon = _define("AppIcon").replace("{app}\\python\\Lib\\site-packages\\", "")
    assert (ROOT / "src" / Path(icon)).is_file()


def test_shortcuts_carry_the_app_id_of_the_process():
    from luzeira.sistema.identidad import APP_ID

    # Mismo AUMID que fija la app: si no, al anclar la ventana saldría «pythonw».
    assert _section("Icons").count(f'AppUserModelID: "{APP_ID}"') == 2


def test_uninstall_leaves_no_python_folder():
    assert 'Type: filesandordirs; Name: "{app}\\python"' in _section("UninstallDelete")
    assert 'Type: filesandordirs; Name: "{app}\\python"' in _section("InstallDelete")


# --- scripts/empaquetar.py ----------------------------------------------------------------


def test_pth_comment_does_not_hide_import_site():
    # CPython quita lo que va tras «#»: «import site  # x» sí activaría site.
    assert emp.pth_imports_site("python312.zip\nimport site  # x\n")
    assert emp.pth_imports_site("  import site\r\n")
    assert not emp.pth_imports_site("#import site\n")
    assert not emp.pth_imports_site(emp.PTH)


def _fake_embed(tmp_path):
    embed = tmp_path / "embed.zip"
    with zipfile.ZipFile(embed, "w") as zf:
        for name in ("python312.dll", "pythonw.exe", "sqlite3.dll"):
            zf.writestr(name, b"x")
        zf.writestr("python312._pth", "python312.zip\n.\nimport site\n")
    return embed


def test_unpack_python_refuses_an_unverified_zip(tmp_path):
    with pytest.raises(SystemExit, match="SHA-256"):
        emp.unpack_python(_fake_embed(tmp_path), tmp_path / "py")


def test_unpack_python_writes_our_pth(tmp_path, monkeypatch):
    embed = _fake_embed(tmp_path)
    monkeypatch.setattr(emp, "EMBED_SHA256", emp.sha256(embed))
    py = tmp_path / "py"
    emp.unpack_python(embed, py)
    # El ._pth original (con import site) queda sustituido por el nuestro.
    assert (py / "python312._pth").read_text(encoding="ascii") == emp.PTH
    assert not (py / "sqlite3.dll").exists()


def test_inno_binaries_are_checked(tmp_path, monkeypatch):
    for name in emp.INNO_SHA256:
        (tmp_path / name).write_bytes(name.encode())
    with pytest.raises(SystemExit, match="verificado"):
        emp.check_inno(tmp_path / "ISCC.exe")
    monkeypatch.setattr(
        emp, "INNO_SHA256", {name: emp.sha256(tmp_path / name) for name in emp.INNO_SHA256}
    )
    emp.check_inno(tmp_path / "ISCC.exe")
    (tmp_path / "Setup.e32").unlink()
    with pytest.raises(SystemExit, match="Falta"):
        emp.check_inno(tmp_path / "ISCC.exe")


def test_installer_is_never_built_with_unchecked_inno(tmp_path, monkeypatch):
    iscc = tmp_path / "ISCC.exe"
    iscc.touch()
    order = []
    monkeypatch.setattr(emp, "check_inno", lambda path: order.append(("check", path)))
    monkeypatch.setattr(emp, "run", lambda cmd, **k: order.append(("run", cmd[0])))
    emp.build_installer(iscc, tmp_path, tmp_path, "0.1.0", [])
    assert order == [("check", iscc), ("run", str(iscc))]


def test_install_packages_requires_hashes_and_binaries(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "export" in cmd:
            # Lo que daría uv export: nombre==versión y sus hashes en líneas sangradas.
            req = Path(cmd[cmd.index("-o") + 1])
            req.write_text(
                "pillow==11.0.0 \\\n    --hash=sha256:aa\n"
                "proxy-tools==0.1.0 \\\n    --hash=sha256:bb\n",
                encoding="utf-8",
            )
        if "build" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True)
            (out / "luzeira-0.1.0-py3-none-any.whl").touch()

    emp.install_packages(tmp_path / "site", tmp_path, run=fake_run)
    export, deps, _build, own = calls
    assert "--frozen" in export and "--no-dev" in export
    assert "--require-hashes" in deps and "--no-deps" in deps
    # Lo que compila los sdist (setuptools) va fijado con hash.
    assert deps[deps.index("--build-constraints") + 1] == str(emp.BUILD_CONSTRAINTS)
    assert deps[deps.index("--only-binary") + 1] == "pillow"
    assert ["--only-binary", "proxy-tools"] not in [deps[i : i + 2] for i in range(len(deps))]
    assert "--no-deps" in own


def test_pth_isolated_without_import_site():
    lines = [x.strip() for x in emp.PTH.splitlines()]
    assert lines[:3] == ["python312.zip", ".", "Lib\\site-packages"]
    # Con «import site», Python añade el site-packages del usuario (%APPDATA%\Python).
    assert "import site" not in lines
    emp.PTH.encode("ascii")  # el ._pth se escribe en ASCII


def test_check_no_pth(tmp_path):
    emp.check_no_pth(tmp_path)
    (tmp_path / "algo.pth").write_text("import os\n")
    with pytest.raises(SystemExit):
        emp.check_no_pth(tmp_path)


def test_keeps_ssl_for_future_updates():
    pruned = " ".join([*emp.PRUNE_EMBED, *emp.PRUNE_FILES, *emp.PRUNE_GLOBS])
    assert "ssl" not in pruned
    assert "crypto" not in pruned


def test_only_listed_packages_may_build_from_source():
    assert sorted(emp.SDIST_ALLOWED) == ["proxy-tools"]


def test_version_comes_from_pyproject():
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert emp.project_version() == data["project"]["version"]


def test_portable_zip_is_reproducible(tmp_path):
    src = tmp_path / "src"
    (src / "python").mkdir(parents=True)
    (src / "python" / "b.txt").write_text("b")
    (src / "a.txt").write_text("a")
    first, second = tmp_path / "1.zip", tmp_path / "2.zip"
    emp.build_portable(src, first, emp.DEFAULT_EPOCH)
    (src / "a.txt").touch()  # otra fecha en disco: el zip no debe cambiar
    emp.build_portable(src, second, emp.DEFAULT_EPOCH)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as zf:
        assert zf.namelist() == ["Luzeira/a.txt", "Luzeira/python/b.txt"]


def test_third_party_notices_include_license_text(tmp_path):
    info = tmp_path / "demo-1.0.dist-info"
    (info / "licenses").mkdir(parents=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: demo\nVersion: 1.0\nLicense-Expression: MIT\n"
    )
    (info / "licenses" / "LICENSE").write_text("Texto MIT de prueba")
    own = tmp_path / "luzeira-0.1.0.dist-info"
    own.mkdir()
    (own / "METADATA").write_text("Metadata-Version: 2.4\nName: luzeira\nVersion: 0.1.0\n")
    text = emp.third_party_notices(tmp_path)
    assert "- demo 1.0: MIT" in text
    assert "Texto MIT de prueba" in text
    assert "- luzeira" not in text  # la propia licencia va aparte, en LICENSE.txt


def _runtime_packages() -> set[str]:
    """Los paquetes que van en el instalador: los de uv.lock sin el grupo dev."""
    out = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = set()
    for line in out.splitlines():
        if "==" not in line or line[0].isspace():
            continue
        requirement, _, marker = line.rstrip(" \\").partition(";")
        # Solo lo de Windows (este test corre en Windows, como la build): fuera pyobjc, Xlib…
        if not marker.strip() or Marker(marker.strip()).evaluate():
            names.add(emp.normalize_name(requirement.split("==")[0]))
    return names


def test_every_shipped_package_has_its_license_text(tmp_path):
    # Se montan los *.dist-info reales (del entorno de desarrollo) de lo que se distribuye.
    venv_site = Path(emp.__file__).resolve().parent.parent / ".venv" / "Lib" / "site-packages"
    if not venv_site.is_dir():
        pytest.skip("sin .venv")
    wanted = _runtime_packages()
    for info in venv_site.glob("*.dist-info"):
        if emp.normalize_name(info.name.removesuffix(".dist-info").rsplit("-", 1)[0]) in wanted:
            shutil.copytree(info, tmp_path / info.name)
    assert len(list(tmp_path.glob("*.dist-info"))) == len(wanted)
    text = emp.third_party_notices(tmp_path)
    assert "el paquete no incluye el texto" not in text
    for name in ("pyserial", "proxy-tools", "nvidia-ml-py"):
        assert (emp.LICENSES_DIR / f"{name}.txt").is_file()
    assert "NVIDIA Corporation" in text and "Chris Liechti" in text and "Tushman" in text
    assert "Copyright (C) Microsoft Corporation" in text  # SDK de WebView2 (BSD)


def test_personal_path_check_catches_build_paths(tmp_path):
    (tmp_path / "ok.txt").write_text("nada")
    emp.check_no_personal_paths(tmp_path)
    (tmp_path / "mal.json").write_text(str(emp.ROOT / "build"))
    with pytest.raises(SystemExit):
        emp.check_no_personal_paths(tmp_path)


def test_keeps_every_webview2_runtime():
    # pywebview 6.2.1 recorre win-arm64, win-x64 y win-x86 y falla si falta una: el editor
    # no abría en la primera build instalada (2026-11-18).
    assert not any("webview/lib/runtimes" in g for g in emp.PRUNE_GLOBS)


def test_build_runs_the_smoke_test_with_the_editor_engine():
    calls = []
    emp.smoke_test(Path("py"), run=lambda cmd, **k: calls.append((cmd, k)))
    ((cmd, kwargs),) = calls
    assert cmd[1:3] == ["-B", "-c"]  # -B: no escribe .pyc en la carpeta ensamblada
    assert "webview.platforms.edgechromium" in cmd[3] and "luzeira.editor.ventana" in cmd[3]
    assert "System32" in kwargs["env"]["PATH"]


def test_only_the_end_of_install_opens_the_editor():
    run_line = next(x for x in _section("Run").splitlines() if x.startswith("Filename"))
    assert 'Parameters: "-m luzeira --abrir-editor"' in run_line
    assert "postinstall" in run_line and "skipifsilent" in run_line
    # Los accesos directos y el autoarranque abren solo la bandeja.
    assert "--abrir-editor" not in _section("Icons") + _section("Registry")


def test_personal_data_check_looks_inside_binaries(tmp_path, monkeypatch):
    monkeypatch.setattr(emp, "_git_emails", lambda: {"alguien@ejemplo.org"})
    (tmp_path / "libreria.dll").write_bytes(b"\x00MZ" + "Alguien@Ejemplo.org".encode("utf-16-le"))
    with pytest.raises(SystemExit, match="Dato personal"):
        emp.check_no_personal_paths(tmp_path)


def test_defines_only_known_names_and_safe_values():
    emp.check_defines(["MutexName=LuzeiraPrueba", "RunValue=LuzeiraPrueba"])
    for bad in ("Nada=1", "MutexName", "AppVersion=9.9.9", "RunKey=a'b", "MutexName={app}"):
        with pytest.raises(SystemExit):
            emp.check_defines([bad])


def test_test_builds_never_land_in_dist():
    with pytest.raises(SystemExit, match="--salida"):
        emp.main(["--python-embebible", "x.zip", "--define", "MutexName=Prueba"])


@pytest.mark.parametrize("version", ["0.1.0", "1.10.200"])
def test_version_accepted(version):
    assert emp.VERSION_RE.fullmatch(version)


@pytest.mark.parametrize("version", ["0.2.0rc1", "1.0", "v1.0.0", "1.0.0-beta"])
def test_version_rejected(version):
    assert not emp.VERSION_RE.fullmatch(version)


def test_stale_editor_is_detected(tmp_path, monkeypatch):
    index = tmp_path / "web" / "index.html"
    index.parent.mkdir()
    index.touch()
    src = tmp_path / "editor" / "src"
    src.mkdir(parents=True)
    old = src / "viejo.ts"
    old.touch()
    os.utime(old, (1, 1))
    monkeypatch.setattr(emp, "ROOT", tmp_path)
    monkeypatch.setattr(emp, "EDITOR_INDEX", index)
    assert not emp.editor_is_stale()
    (src / "nuevo.tsx").touch()
    os.utime(index, (2, 2))
    assert emp.editor_is_stale()


def test_prune_removes_stray_top_level_files(tmp_path):
    for name in ("example.py", ".lock", "pynvml.py"):
        (tmp_path / name).write_text("x")
    removed = emp.prune(tmp_path)
    assert {"example.py", ".lock"} <= set(removed)
    assert (tmp_path / "pynvml.py").exists()


def test_requires_inno_6_3():
    assert "#if Ver < EncodeVer(6, 3, 0)" in ISS


def test_update_without_autostart_task_removes_only_our_value():
    code = _section("Code")
    step = code[code.index("procedure CurStepChanged") :]
    assert "not WizardIsTaskSelected('autoarranque') then\n    RemoveRunValueIfHere" in step
    remove = code[code.index("procedure RemoveRunValueIfHere") :]
    assert "PointsHere(Command) then" in remove.split("end;")[0]


def test_uninstall_removes_the_desktop_shortcut_made_by_the_app():
    code = _section("Code")
    assert _define("DesktopDir") == "{userdesktop}"
    assert r"{#DesktopDir}\Luzeira.lnk" in code and "ShortcutPointsHere(Desktop)" in code
    assert r'Name: "{#DesktopDir}\Luzeira"' in _section("Icons")


def test_portable_zip_order_does_not_depend_on_the_disk(tmp_path):
    # NTFS lista sin distinguir mayúsculas y rglob recorre carpeta a carpeta: el zip ordena
    # por la ruta completa, igual en cualquier sistema de ficheros.
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    for rel in ("a/z.txt", "a.txt", "a-b.txt", "B.txt"):
        (src / rel).write_text(rel)
    dest = tmp_path / "p.zip"
    emp.build_portable(src, dest, emp.DEFAULT_EPOCH)
    with zipfile.ZipFile(dest) as zf:
        names = [n.removeprefix("Luzeira/") for n in zf.namelist()]
    assert names == ["B.txt", "a-b.txt", "a.txt", "a/z.txt"]


def test_app_and_installer_share_the_mutex_name():
    assert _define("MutexName") == instancia.APP_MUTEX
    bandeja_src = (ROOT / "src" / "luzeira" / "bandeja.py").read_text(encoding="utf-8")
    assert "SingleInstance(APP_MUTEX)" in bandeja_src


def test_build_constraints_are_pinned_with_hashes():
    lines = [
        x
        for x in emp.BUILD_CONSTRAINTS.read_text(encoding="utf-8").splitlines()
        if x and not x.startswith("#")
    ]
    assert {x.split("==")[0] for x in lines} >= {"setuptools", "wheel"}
    for line in lines:
        assert re.fullmatch(r"[\w-]+==[\d.]+ --hash=sha256:[0-9a-f]{64}", line), line
