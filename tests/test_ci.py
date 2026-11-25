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


def _jobs(path: Path) -> dict[str, str]:
    """Nombre → texto de cada job (lo que cuelga de ``jobs:`` con dos espacios)."""
    text = _text(path)
    assert re.search(r"^jobs:\n", text, re.MULTILINE), path.name
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):[ \t]*\n", body, flags=re.MULTILINE)
    assert parts[0].strip() == "", f"{path.name}: algo raro antes del primer job"
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "release.yml", "seguridad.yml", "codeql.yml"}


#: Lo que ejecuta código de terceros (dependencias, sus scripts de instalación, tests).
_RUNS_DEPENDENCIES = re.compile(
    r"\b(npm|npx|uv|uvx|pip|pytest|node)\b|setup-uv@|setup-node@|setup-python@"
)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_job_that_can_sign_runs_dependencies(path):
    # R3-05: con id-token: write, un paquete malicioso (npm, PyPI) o un test podrían pedir
    # un token de OIDC y firmar en nombre de nuestro workflow lo que quisieran.
    for name, job in _jobs(path).items():
        if re.search(r"^\s+id-token:\s*write", job, re.MULTILINE):
            code = "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))
            assert not _RUNS_DEPENDENCIES.search(code), f"{path.name}:{name}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_declares_its_own_permissions(path):
    # O el workflow no da nada por defecto y cada job pide lo suyo, o lo de por defecto es
    # solo leer (ci.yml).
    text = _text(path)
    if re.search(r"^permissions: \{\}", text, re.MULTILINE):
        for name, job in _jobs(path).items():
            assert re.search(r"^    permissions:", job, re.MULTILINE), f"{path.name}:{name}"
    else:
        default = text.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
        assert default.split() == ["contents:", "read"], path.name
        assert "permissions:" not in text.split("\njobs:\n", 1)[1], path.name


def test_release_builds_without_signing_and_attests_apart():
    jobs = _jobs(ROOT / ".github" / "workflows" / "release.yml")
    assert list(jobs) == ["build", "attest", "release"]
    assert "id-token" not in jobs["build"] and "attestations" not in jobs["build"]
    assert 'npm_config_ignore_scripts: "true"' in jobs["build"]
    assert "package-manager-cache: false" in jobs["build"]
    assert "pytest" in jobs["build"]  # ci.yml no corre en etiquetas: se quedan aquí
    attest = jobs["attest"]
    assert "needs: build" in attest and "id-token: write" in attest
    steps = re.findall(r"uses:\s*([\w./-]+)@", attest)
    assert steps == ["actions/download-artifact", "actions/attest"]
    assert "run:" not in attest
    assert "needs: attest" in jobs["release"] and "id-token" not in jobs["release"]


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
    assert "workflow_run" not in text  # corre con permisos del repo tras un PR ajeno
    assert "write-all" not in text
    assert "secrets." not in text  # ninguno usa secretos (solo github.token)


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
    assert "dist/SHA256SUMS" in text.split("actions/attest@", 1)[1].split("\n  release:", 1)[0]


def test_security_workflow_runs_each_tool_pinned():
    # R3-04: cada herramienta por versión exacta (y gitleaks, además, por SHA-256).
    text = _text(ROOT / ".github" / "workflows" / "seguridad.yml")
    jobs = _jobs(ROOT / ".github" / "workflows" / "seguridad.yml")
    assert set(jobs) == {"zizmor", "pip-audit", "npm-audit", "gitleaks"}
    assert re.search(r"uvx zizmor==\d+\.\d+\.\d+ \.github/workflows", jobs["zizmor"])
    assert re.search(r"uvx pip-audit==\d+\.\d+\.\d+ .*--require-hashes", jobs["pip-audit"])
    assert "uv export --frozen" in jobs["pip-audit"]
    assert "windows" in jobs["pip-audit"]  # los marcadores de Windows (pywin32…) cuentan
    assert "npm audit --audit-level=high" in jobs["npm-audit"]
    assert "npm ci" not in text and "npm install" not in text
    assert "fetch-depth: 0" in jobs["gitleaks"] and "sha256sum -c" in jobs["gitleaks"]
    # La misma versión de gitleaks que el hook de pre-commit.
    hooks = _text(ROOT / ".pre-commit-config.yaml")
    version = re.search(r"gitleaks_(\d+\.\d+\.\d+)_linux", jobs["gitleaks"])[1]
    assert f"# frozen: v{version}" in hooks
    for job in jobs.values():
        assert "write" not in job.split("steps:", 1)[0]  # solo lectura


def test_codeql_covers_python_javascript_and_actions():
    text = _text(ROOT / ".github" / "workflows" / "codeql.yml")
    assert "language: [python, javascript-typescript, actions]" in text
    assert "build-mode: none" in text
    assert "security-events: write" in text


def test_codeql_builds_the_sensors_module():
    # ADR-007, punto 8: CodeQL para C#, viendo compilar el módulo (build-mode: manual).
    job = _jobs(ROOT / ".github" / "workflows" / "codeql.yml")["analizar-csharp"]
    assert "languages: csharp" in job and "build-mode: manual" in job
    assert "global-json-file: modulo-sensores/global.json" in job
    assert "dotnet restore LuzeiraSensores.slnx --locked-mode" in job
    assert "dotnet build" in job
    assert (
        job.index("codeql-action/init@")
        < job.index("dotnet build")
        < job.index("codeql-action/analyze@")
    )


def test_ci_builds_and_tests_the_sensors_module():
    jobs = _jobs(ROOT / ".github" / "workflows" / "ci.yml")
    job = jobs["modulo-sensores"]
    assert "global-json-file: modulo-sensores/global.json" in job
    assert "dotnet restore LuzeiraSensores.slnx --locked-mode" in job
    assert "dotnet test LuzeiraSensores.slnx" in job
    # Publicación en limpio y comprobada (ADR-008, punto 2).
    assert job.index("Remove-Item -Recurse -Force publish") < job.index("dotnet publish")
    assert "System.StartupHookProvider.IsSupported" in job and "coreclr.dll" in job
    assert '"10.0.15"' in job
    csproj = _text(ROOT / "modulo-sensores" / "src" / "LuzeiraSensores" / "LuzeiraSensores.csproj")
    assert "<RuntimeFrameworkVersion>10.0.15</RuntimeFrameworkVersion>" in csproj
    # El job de Python tiene el SDK: si no, el test cruzado C# ↔ Python se saltaría.
    assert "global-json-file: modulo-sensores/global.json" in jobs["verdes"]


def test_sensors_module_restores_only_signed_packages_from_nuget_org():
    text = _text(ROOT / "modulo-sensores" / "nuget.config")
    assert "<clear />" in text and text.count('<add key="nuget.org"') == 1
    assert '<add key="signatureValidationMode" value="require" />' in text
    assert "<owners>" in text and "<packageSourceMapping>" in text
    props = _text(ROOT / "modulo-sensores" / "Directory.Build.props")
    assert "<RestoreLockedMode>true</RestoreLockedMode>" in props


def test_dependabot_watches_every_ecosystem_with_cooldown():
    text = _text(ROOT / ".github" / "dependabot.yml")
    blocks = text.split("- package-ecosystem: ")[1:]
    found = {b.split("\n", 1)[0]: b for b in blocks}
    assert set(found) == {"github-actions", "uv", "npm", "nuget", "dotnet-sdk"}
    assert "directory: /editor" in found["npm"]
    assert "directory: /modulo-sensores" in found["nuget"]
    assert "directory: /modulo-sensores" in found["dotnet-sdk"]
    for block in found.values():
        assert "interval: weekly" in block
        assert "default-days: 7" in block
        assert 'patterns: ["*"]' in block  # un solo PR por ecosistema


def test_pre_commit_hooks_are_pinned_by_sha():
    # R3-12: una etiqueta de git se puede mover; un SHA, no (lo de pre-commit --freeze).
    text = _text(ROOT / ".pre-commit-config.yaml")
    revs = re.findall(r"^\s+rev:\s*(.+)$", text, re.MULTILINE)
    assert revs
    for rev in revs:
        assert re.fullmatch(r"[0-9a-f]{40}  # frozen: v\d+\.\d+\.\d+", rev), rev


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


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_multiline_steps_on_windows_stop_at_the_first_failure(path):
    # En Windows, «run:» va por defecto con PowerShell, que no se para cuando falla un
    # programa: el paso solo daba el resultado del último comando. Un lint roto salió en
    # verde (PR #5 de Dependabot, 2026-11-25). Cada paso de varias líneas de un job de
    # Windows va con bash (-eo pipefail) o con un «shell: pwsh» explícito y sus «throw».
    for name, job in _jobs(path).items():
        if "runs-on: windows" not in job:
            continue
        job_bash = re.search(
            r"^    defaults:\n      run:\n(?:        .*\n)*?        shell: bash$", job, re.MULTILINE
        )
        for step in re.split(r"^      - ", job, flags=re.MULTILINE)[1:]:
            block = re.search(r"^        run: \|\n((?:          .*\n?|\s*\n)+)", step, re.MULTILINE)
            if not block:
                continue
            commands = [
                line
                for line in block.group(1).splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            if len(commands) < 2:
                continue
            shell = re.search(r"^        shell: (\w+)", step, re.MULTILINE)
            effective = shell.group(1) if shell else ("bash" if job_bash else "por defecto")
            assert effective in {"bash", "pwsh"}, f"{path.name}/{name}: {commands[0].strip()}"
