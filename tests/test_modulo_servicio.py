"""Test cruzado: el servicio en C# escribe y Luzeira lee (ADR-007, ADR-008).

``modulo-sensores/tools/escribir-muestra`` escribe un ``sensores.bin`` con el escritor
real del servicio (``LuzeiraSensores.Core``) y aquí se lee con
``luzeira.sensores.modulo.parse_section``. Si cambia un offset o un tamaño en cualquiera
de los dos lados, esto cae: el formato lo comprueban los dos programas, no dos copias de
la misma tabla.

Necesita el SDK de .NET que fija ``modulo-sensores/global.json``. Se busca en la
instalación por usuario (``%LOCALAPPDATA%\\Microsoft\\dotnet``) y en el PATH; si no está,
el test se salta.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from luzeira.sensores import modulo

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "modulo-sensores"
TOOL = MODULE / "tools" / "escribir-muestra"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="el módulo es solo de Windows")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_NOLOGO="1",
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
    )
    return env


def _dotnet() -> Path:
    """El ``dotnet.exe`` que tenga el SDK de global.json, o se salta el test."""
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Microsoft" / "dotnet" / "dotnet.exe")
    on_path = shutil.which("dotnet")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        # Desde la carpeta del módulo, «dotnet --version» respeta global.json: si falla,
        # ese dotnet no tiene el SDK fijado.
        probe = subprocess.run(
            [str(candidate), "--version"],
            cwd=MODULE,
            capture_output=True,
            text=True,
            env=_env(),
            timeout=60,
        )
        if probe.returncode == 0:
            return candidate
    pytest.skip("no hay un SDK de .NET con la versión de modulo-sensores/global.json")


@pytest.fixture(scope="module")
def write_sample(tmp_path_factory):
    dotnet = _dotnet()
    artifacts = tmp_path_factory.mktemp("dotnet")

    def write(path: Path, status: int = 0) -> bytes:
        result = subprocess.run(
            [
                str(dotnet),
                "run",
                "--project",
                str(TOOL),
                "-c",
                "Release",
                "--artifacts-path",
                str(artifacts),
                "--",
                str(path),
                str(status),
            ],
            cwd=MODULE,
            capture_output=True,
            text=True,
            env=_env(),
            timeout=600,
        )
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        return path.read_bytes()

    return write


def _module_version() -> str:
    props = (MODULE / "Directory.Build.props").read_text(encoding="utf-8")
    match = re.search(r"<Version>([^<]+)</Version>", props)
    assert match, "Directory.Build.props sin <Version>"
    return match.group(1)


def test_luzeira_reads_what_the_service_writes(write_sample, tmp_path):
    data = write_sample(tmp_path / "sensores.bin")

    assert len(data) == modulo.SECTION_SIZE == 3136
    assert modulo.layout_version(data) == modulo.LAYOUT_VERSION
    section = modulo.parse_section(data)
    assert section.sequence == 42
    assert section.status == modulo.STATUS_OK
    assert section.module_version == _module_version()
    # Cada valor, exacto (son representables en binario), y la ccd2 sin dato.
    assert section.values == {
        "cpu.temperature": 47.25,
        "cpu.temperature.ccd1": 45.5,
        "cpu.power": 61.875,
        "cpu.voltage": 1.1875,
    }
    # Y por campos, para que un fallo diga dónde está.
    fields = modulo.HEADER.unpack_from(data, 0)
    assert fields[0] == modulo.MAGIC
    assert fields[1:5] == (1, modulo.HEADER.size, modulo.ENTRY.size, 5)
    assert fields[6] == 1_764_000_000_000
    assert fields[8:] == (0, 0)


def test_ids_written_are_the_closed_list_in_order(write_sample, tmp_path):
    data = write_sample(tmp_path / "sensores.bin")
    ids = []
    for i in range(5):
        raw_id, _value, _flags, reserved = modulo.ENTRY.unpack_from(
            data, modulo.HEADER.size + i * modulo.ENTRY.size
        )
        assert reserved == 0
        ids.append(raw_id.rstrip(b"\0").decode("ascii"))
    assert ids == [s.id for s in modulo.SENSORS]
    # Nada escrito después de la última entrada.
    assert data[modulo.HEADER.size + 5 * modulo.ENTRY.size :] == bytes(
        modulo.SECTION_SIZE - modulo.HEADER.size - 5 * modulo.ENTRY.size
    )


@pytest.mark.parametrize(
    "status",
    [modulo.STATUS_NO_PAWNIO, modulo.STATUS_UNSUPPORTED_CPU, modulo.STATUS_STARTING],
)
def test_statuses_reach_luzeira(write_sample, tmp_path, status):
    section = modulo.parse_section(write_sample(tmp_path / f"estado{status}.bin", status))
    assert section.status == status
    assert section.values == {}


def test_uninstaller_never_deletes_recursively():
    # Revisión de seguridad, MEDIA 2: como administrador, un borrado recursivo de una
    # carpeta que pudo crear otro podría seguir un enlace y borrar lo que no es nuestro.
    # El desinstalador borra sensores.bin y la carpeta vacía, y solo si es la suya.
    iss = (ROOT / "instalador" / "luzeira-sensores.iss").read_text(encoding="utf-8")
    assert "DelTree(" not in iss
    delete = iss.split("procedure DeleteDataFolder;", 1)[1].split("\nend;", 1)[0]
    assert "IsExpectedSddl(FolderSddl(Path), '{#DataFolderSddl}')" in delete
    assert delete.index("IsExpectedSddl") < delete.index("DeleteFile(")


def test_every_license_named_in_the_notices_ships_with_the_module():
    # C-72: MPL-2.0, LGPL-2.1, Apache-2.0 y MIT obligan a llevar su texto con el binario.
    notices = (MODULE / "TERCEROS.txt").read_text(encoding="utf-8")
    named = set(re.findall(r"licencias\\([\w.-]+\.txt)", notices))
    shipped = {p.name for p in (MODULE / "licencias").glob("*.txt")}
    assert named == shipped
    assert len(shipped) >= 6
    iss = (ROOT / "instalador" / "luzeira-sensores.iss").read_text(encoding="utf-8")
    assert r'Source: "..\modulo-sensores\licencias\*.txt"; DestDir: "{app}\licencias"' in iss


def test_uninstaller_waits_longer_than_luzeira_takes_to_let_go_of_the_file():
    # C-77.6: Luzeira tiene sensores.bin abierto sin FILE_SHARE_DELETE (ADR-008) y lo
    # suelta cuando lleva STALE_SECONDS sin cambiar, más un ciclo de lectura. El
    # desinstalador para el servicio y reintenta el borrado: tiene que esperar más que eso.
    iss = (ROOT / "instalador" / "luzeira-sensores.iss").read_text(encoding="utf-8")
    delete = iss.split("procedure DeleteDataFolder;", 1)[1].split("\nend;", 1)[0]
    tries = int(re.search(r"for I := 1 to (\d+) do", delete).group(1))
    pause = int(re.search(r"Sleep\((\d+)\)", delete).group(1)) / 1000
    read_cycle = 1.5  # el refresco más lento de los sensores
    assert tries * pause > 2 * (modulo.STALE_SECONDS + read_cycle)
    uninstall = iss.split("procedure CurUninstallStepChanged", 1)[1]
    assert uninstall.index("StopService;") < uninstall.index("DeleteDataFolder;")
