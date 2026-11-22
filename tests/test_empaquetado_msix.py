"""Paquete MSIX para Microsoft Store (C-52): lo que se puede comprobar sin makeappx.

El paquete de verdad se prueba montándolo (``scripts/empaquetar_msix.py``), firmándolo
con un certificado de prueba e instalándolo (skill ``empaquetar``).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
NS = {
    "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10",
    "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}
PARAMS = "{" + NS["uap10"] + "}Parameters"


def _load_script():
    path = ROOT / "scripts" / "empaquetar_msix.py"
    spec = importlib.util.spec_from_file_location("empaquetar_msix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


msix = _load_script()


def _manifest(**overrides) -> ET.Element:
    args = {
        "name": "Luzeira.Prueba",
        "publisher": "CN=12345678-ABCD-4EF0-9876-0123456789AB",
        "publisher_name": "Luzeira",
        "version": "0.1.3",
    }
    args.update(overrides)
    return ET.fromstring(msix.render_manifest(**args))  # noqa: S314 — plantilla del repo


def test_manifest_is_valid_xml_with_the_identity_and_store_version():
    root = _manifest()
    identity = root.find("m:Identity", NS)
    assert identity.get("Name") == "Luzeira.Prueba"
    assert identity.get("Publisher") == "CN=12345678-ABCD-4EF0-9876-0123456789AB"
    assert identity.get("Version") == "0.1.3.0"  # la Store exige el cuarto número a 0


def test_the_app_runs_full_trust_with_the_embedded_python():
    root = _manifest()
    app = root.find("m:Applications/m:Application", NS)
    assert app.get("Executable") == "python\\pythonw.exe"
    assert app.get("EntryPoint") == "Windows.FullTrustApplication"
    caps = [c.get("Name") for c in root.find("m:Capabilities", NS)]
    assert caps == ["runFullTrust"]


def test_the_package_declares_every_language_of_the_app():
    # C-62: la Store anuncia los idiomas del manifiesto. Uno por idioma de la app, con la
    # región que lista la Store (pt = portugués de Brasil), y el español primero (por defecto).
    from luzeira import idioma

    tags = [r.get("Language") for r in _manifest().find("m:Resources", NS)]
    assert tags[0] == "es-es"
    assert len(tags) == len(set(tags))
    assert {t.split("-")[0] for t in tags} == set(idioma.NATIVE_NAMES)
    assert "pt-br" in tags


def test_the_icon_opens_the_editor_like_the_installer_shortcuts():
    app = _manifest().find("m:Applications/m:Application", NS)
    iss = (ROOT / "instalador" / "luzeira.iss").read_text(encoding="utf-8")
    assert app.get(PARAMS) == "-m luzeira --abrir-editor"
    assert f'Parameters: "{app.get(PARAMS)}"' in iss  # los mismos que el menú Inicio


def test_the_startup_task_opens_only_the_tray_and_is_on_by_default():
    ext = _manifest().find(".//desktop:Extension[@Category='windows.startupTask']", NS)
    # Windows no pasa uap10:Parameters a la tarea de inicio: el lanzador arranca sin ellos
    # (y sin editor al encender el PC).
    assert ext.get("Executable") == "Luzeira.exe"
    assert ext.get(PARAMS) is None
    task = ext.find("desktop:StartupTask", NS)
    assert task.get("Enabled") == "true"  # como el instalador (autoarranque marcado)


def test_the_store_identity_fits_the_manifest():
    identity = _manifest(
        name=msix.STORE_NAME,
        publisher=msix.STORE_PUBLISHER,
        publisher_name=msix.STORE_PUBLISHER_NAME,
    ).find("m:Identity", NS)
    assert identity.get("Name") == "eriik-rz.Luzeira"
    assert identity.get("Publisher") == "CN=536FC4AD-21B9-4738-9794-F634BF14D2D1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Lu"),  # demasiado corto
        ("name", "Luzeira Prueba"),  # espacio
        ("name", 'Luz"eira'),
        ("publisher", "Luzeira"),  # sin CN=
        ("publisher", 'CN=a" Evil="1'),
        ("publisher_name", "<script>"),
        ("publisher_name", "A & B"),
    ],
)
def test_identity_values_cannot_break_the_xml(field, value):
    with pytest.raises(SystemExit):
        _manifest(**{field: value})


@pytest.mark.parametrize("version", ["0.1", "0.1.3.1", "0.2.0rc1", "1.65536.0"])
def test_only_plain_versions_fit_msix(version):
    with pytest.raises(SystemExit):
        msix.msix_version(version)


def test_the_template_has_only_the_known_placeholders():
    text = msix.TEMPLATE.read_text(encoding="utf-8")
    found = set(re.findall(r"\$\{?(\w+)", text))
    assert found == {"name", "publisher", "publisher_name", "version"}


def test_tools_with_another_hash_are_refused(tmp_path):
    fake = tmp_path / "buildtools.nupkg"
    fake.write_bytes(b"no es el paquete de Microsoft")
    with pytest.raises(SystemExit, match="SHA-512"):
        msix.check_tools(fake)


def test_tools_hash_is_checked_as_nuget_publishes_it(tmp_path, monkeypatch):
    data = b"paquete de prueba"
    nupkg = tmp_path / "ok.nupkg"
    nupkg.write_bytes(data)
    monkeypatch.setattr(
        msix, "TOOLS_SHA512", base64.b64encode(hashlib.sha512(data).digest()).decode()
    )
    msix.check_tools(nupkg)  # no sale


def test_only_the_x64_tools_folder_is_extracted(tmp_path):
    nupkg = tmp_path / "tools.nupkg"
    with zipfile.ZipFile(nupkg, "w") as z:
        z.writestr(msix.TOOLS_DIR + "makeappx.exe", b"MZ")
        z.writestr(msix.TOOLS_DIR + "appxsip.dll", b"MZ")
        z.writestr(msix.TOOLS_DIR + "sub/otra.dll", b"MZ")
        z.writestr("bin/10.0.28000.0/x86/makeappx.exe", b"MZ")
        z.writestr("../fuera.exe", b"MZ")
    dest = tmp_path / "herramientas"
    dest.mkdir()
    assert msix.extract_tools(nupkg, dest) == dest / "makeappx.exe"
    assert sorted(p.name for p in dest.iterdir()) == ["appxsip.dll", "makeappx.exe"]
    assert not (tmp_path / "fuera.exe").exists()


def test_layout_has_assets_and_manifest_but_not_the_portable_launcher(tmp_path):
    src = tmp_path / "Luzeira"
    (src / "python").mkdir(parents=True)
    (src / "python" / "pythonw.exe").write_bytes(b"MZ")
    (src / "Luzeira.cmd").write_text("@echo off", encoding="utf-8")
    (src / "LICENSE").write_text("GPL", encoding="utf-8")
    layout = tmp_path / "paquete"
    msix.build_layout(src, layout, "<Package />")
    assert not (layout / "Luzeira.cmd").exists()
    assert (layout / "Luzeira.exe").read_bytes().startswith(b"MZ")  # el de la tarea de inicio
    assert "distlib" in (layout / "TERCEROS.txt").read_text(encoding="utf-8")
    assert (layout / "LICENSE").is_file()
    assert (layout / "AppxManifest.xml").read_text(encoding="utf-8") == "<Package />"
    for name, size in msix.ASSETS.items():
        with Image.open(layout / "Assets" / name) as im:
            assert im.size == (size, size)


def test_layout_refuses_a_folder_that_is_not_the_assembled_one(tmp_path):
    with pytest.raises(SystemExit, match="pythonw"):
        msix.build_layout(tmp_path, tmp_path / "paquete", "<Package />")


def test_the_launcher_runs_luzeira_with_the_packaged_python():
    w64 = (msix.distlib_dir() / "w64.exe").read_bytes()
    exe = msix.build_launcher(w64)
    assert exe.startswith(w64)
    rest = exe[len(w64) :]
    # Relativo a la carpeta del .exe: el paquete se instala en WindowsApps\<nombre>.
    assert rest.startswith(b"#!<launcher_dir>\\python\\pythonw.exe\r\n")
    with zipfile.ZipFile(io.BytesIO(exe)) as z:
        assert z.namelist() == ["__main__.py"]
        assert b"run_module('luzeira'" in z.read("__main__.py")
    assert msix.build_launcher(w64) == exe  # reproducible


def test_a_launcher_with_another_hash_is_refused():
    with pytest.raises(SystemExit, match="SHA-256"):
        msix.build_launcher(b"MZ otro lanzador")


def test_the_launcher_license_goes_to_the_notices():
    assert "distlib (PSF-2.0)" in msix.launcher_notice()
