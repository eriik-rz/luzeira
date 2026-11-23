"""Guardián de los workflows de GitHub Actions (skill seguridad-luzeira).

Se leen como texto: sin dependencias nuevas y fallando con un mensaje claro.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_pinned_by_full_sha(path):
    # Una etiqueta (@v4) se puede mover a otro código; un SHA de 40 caracteres, no.
    uses = re.findall(r"uses:\s*(\S+)", _text(path))
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_permissions_declared_and_no_dangerous_triggers(path):
    text = _text(path)
    assert re.search(r"^permissions:", text, re.MULTILINE), "faltan los permisos por defecto"
    assert "pull_request_target" not in text
    assert "write-all" not in text


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkout_does_not_keep_credentials(path):
    text = _text(path)
    checkouts = text.count("uses: actions/checkout@")
    assert text.count("persist-credentials: false") >= checkouts


def test_release_has_no_cache_and_needs_approval():
    text = _text(ROOT / ".github" / "workflows" / "release.yml")
    assert "enable-cache: false" in text
    assert "actions/cache" not in text
    assert "environment: release" in text
    assert re.search(r"^permissions: \{\}", text, re.MULTILINE)


def test_release_stays_a_draft_until_signed():
    # ADR-006: quien mantiene el proyecto firma manifest.json fuera de GitHub y publica
    # a mano. Si el workflow publicara solo, saldría una versión sin firma.
    text = _text(ROOT / ".github" / "workflows" / "release.yml")
    assert "--draft " in text
    assert "--draft=false" not in text
    assert "dist/SHA256SUMS" in text.split("actions/attest@", 1)[1].split("upload-artifact", 1)[0]


def test_release_uses_the_same_tool_versions_as_the_build_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("empaquetar", ROOT / "scripts" / "empaquetar.py")
    emp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emp)
    text = _text(ROOT / ".github" / "workflows" / "release.yml")
    # El zip que baja el workflow es el que empaquetar.py exige por hash (3.12.10).
    assert "python-3.12.10-embed-amd64.zip" in text
    assert emp.PYTHON_TAG == "312"
    # Inno Setup: la misma versión cuyos binarios fija INNO_SHA256.
    assert "innosetup-7.1.1-x64.exe" in text
    assert "Inno Setup 7.1.1" in Path(emp.__file__).read_text(encoding="utf-8")
