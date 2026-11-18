"""Avisar de versiones nuevas e instalarlas cuando el usuario lo pide (ADR-006).

Cómo se sabe que una versión es de verdad de Luzeira:

1. Cada release publica ``manifest.json`` (versión, nombre del instalador, tamaño y
   SHA-256) y ``manifest.json.sig``: una firma **Ed25519** de los bytes exactos del
   manifiesto, hecha a mano por quien mantiene el proyecto con una clave privada que
   **nunca** está en GitHub (``scripts/firmar.py``).
2. La app lleva dentro solo las claves **públicas** (:data:`PUBLIC_KEYS`). Comprueba la
   firma **antes** de leer el manifiesto: si no casa con ninguna clave, se descarta.
3. Con el manifiesto ya fiable, descarga el instalador de esa misma release, mira que
   tenga el tamaño y el SHA-256 que dice y solo entonces lo puede lanzar.

Así, aunque alguien se hiciera con la cuenta de GitHub, no podría colar un instalador:
le faltaría la clave privada. La attestation de GitHub (``gh attestation verify``) queda
como segunda comprobación para personas, no dentro de la app.

Red: solo se conecta si el usuario lo ha activado (``AppConfig.updates``) o si pulsa
«Buscar actualizaciones». Solo HTTPS, solo a GitHub (lista cerrada de hosts, también en
las redirecciones), y no envía nada: una petición normal sin cookies ni identificadores.

Nada de aquí se configura desde fuera: las URL son constantes. Los tests cambian la red
por un objeto falso que se pasa como parámetro (``http=``).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from luzeira import __version__

log = logging.getLogger(__name__)

REPO = "eriik-rz/luzeira"
RELEASES = f"https://github.com/{REPO}/releases"
MANIFEST_URL = f"{RELEASES}/latest/download/manifest.json"
SIGNATURE_URL = f"{RELEASES}/latest/download/manifest.json.sig"
#: GitHub sirve los ficheros de una release redirigiendo a su almacén. Ningún otro host
#: vale, ni siquiera en una redirección.
ALLOWED_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)

#: Claves públicas Ed25519 (32 bytes en base64) de quien firma las versiones: la que se
#: usa y una de reserva, por si hubiera que cambiar de clave sin dejar a nadie sin
#: actualizaciones. Vacío = no hay con qué comprobar: no se busca nada.
PUBLIC_KEYS: tuple[str, ...] = (
    "9sCofpm/Fpa1cLFv8cPDv5xPAOvRjguMchK/fvBPkKQ=",  # principal (2026-11-18)
    "J+ArjKOLJIEbsR45x/RjHNTk9HKSANuzcSb3UCAduh0=",  # reserva, guardada aparte
)

MAX_MANIFEST_BYTES = 4096
MAX_SIGNATURE_BYTES = 512
MAX_INSTALLER_BYTES = 200 * 1024 * 1024
TIMEOUT = 20  # s por petición
_CHUNK = 64 * 1024

# [0-9] y no \d: \d admite cifras de otros alfabetos (árabes, devanagari…). config.py usa la misma.
_VERSION = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")

# Parámetro propio del instalador: espera a que esta Luzeira se cierre, conserva el
# «Iniciar con Windows» tal como estaba y vuelve a abrir la app al terminar (luzeira.iss).
INSTALLER_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ACTUALIZAR")


class UpdateError(Exception):
    """Algo no ha salido bien. El mensaje es para el usuario, en español."""


Version = tuple[int, int, int]


def parse_version(text: str) -> Version:
    """``"1.2.3"`` → ``(1, 2, 3)``. Solo ese formato: nada de sufijos ni ceros delante."""
    match = _VERSION.match(text)
    if match is None:
        raise ValueError(f"versión no válida: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


class Manifest(BaseModel):
    """Lo que firma quien publica: qué versión es y cómo es su instalador."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    app: str = Field(pattern=r"^luzeira$")
    version: str = Field(pattern=_VERSION.pattern)
    file: str = Field(pattern=r"^Luzeira-\d{1,4}\.\d{1,4}\.\d{1,4}-instalador\.exe$")
    size: int = Field(gt=0, le=MAX_INSTALLER_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def parsed_version(self) -> Version:
        return parse_version(self.version)

    @property
    def installer_url(self) -> str:
        return f"{RELEASES}/download/v{self.version}/{self.file}"


def manifest_bytes(manifest: Manifest) -> bytes:
    """Los bytes que se firman (``scripts/firmar.py``): JSON compacto con claves en orden."""
    return manifest.model_dump_json().encode("ascii")


def _load_keys(keys: Iterable[str]) -> list[Ed25519PublicKey]:
    loaded = []
    for key in keys:
        raw = base64.b64decode(key, validate=True)
        loaded.append(Ed25519PublicKey.from_public_bytes(raw))
    return loaded


def verify_manifest(data: bytes, signature: bytes, keys: Iterable[str]) -> Manifest:
    """Comprueba la firma y, solo si vale, lee el manifiesto. Lanza :class:`UpdateError`."""
    try:
        sig = base64.b64decode(signature.strip(), validate=True)
    except ValueError:
        raise UpdateError("La firma de la versión nueva no es válida.") from None
    for key in _load_keys(keys):
        try:
            key.verify(sig, data)
        except InvalidSignature:
            continue
        break
    else:
        raise UpdateError("La firma de la versión nueva no es válida.")
    try:
        manifest = Manifest.model_validate_json(data)
    except ValidationError:
        raise UpdateError("La información de la versión nueva no es válida.") from None
    if not manifest.file.startswith(f"Luzeira-{manifest.version}-"):
        raise UpdateError("La información de la versión nueva no es válida.")
    return manifest


def is_newer(manifest: Manifest, installed: str, max_seen: str | None) -> bool:
    """¿Merece la pena ofrecerla? Tiene que ser más nueva que la instalada y no más
    vieja que la mayor que ya se haya visto: así no se puede ofrecer una versión
    antigua aunque esté bien firmada.

    Límite conocido (ADR-006): quien controle GitHub sí puede seguir sirviendo el
    manifiesto de la versión instalada (o un error) y la app diría «ya tienes la
    última». El manifiesto no caduca; TUF lo resolvería (C-55)."""
    version = manifest.parsed_version
    if version <= parse_version(installed):
        return False
    return max_seen is None or version >= parse_version(max_seen)


# --- red -----------------------------------------------------------------------------


class Http(Protocol):
    def get(self, url: str, limit: int) -> bytes: ...

    def save(self, url: str, dest: Path, limit: int) -> None: ...


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port:
        raise UpdateError("La descarga intentó ir a un sitio que no es GitHub.")


class _OnlyGitHub(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubHttp:
    """HTTPS con los certificados de Windows, sin cookies, y solo a
    :data:`ALLOWED_HOSTS`. Usa el proxy del sistema si lo hay (como el navegador): el
    TLS se sigue validando contra GitHub."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_OnlyGitHub())
        self._opener.addheaders = [("User-Agent", f"Luzeira/{__version__}")]

    def _open(self, url: str):
        _check_url(url)
        try:
            return self._opener.open(url, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError("Todavía no hay versiones con aviso de actualización.") from None
            raise UpdateError(f"GitHub ha contestado con un error ({exc.code}).") from None
        except (urllib.error.URLError, OSError):
            raise UpdateError("No he podido conectar con GitHub. ¿Hay internet?") from None

    def get(self, url: str, limit: int) -> bytes:
        with self._open(url) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError("La respuesta de GitHub es más grande de lo esperado.")
        return data

    def save(self, url: str, dest: Path, limit: int) -> None:
        total = 0
        with self._open(url) as response, dest.open("wb") as out:
            while chunk := response.read(_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise UpdateError("La descarga es más grande de lo esperado.")
                out.write(chunk)


# --- comprobar, descargar, instalar ------------------------------------------------------


def download_dir() -> Path:
    """``%LOCALAPPDATA%\\Luzeira\\actualizacion``: fuera de la carpeta del programa, que
    el instalador borra y vuelve a crear."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "Luzeira" / "actualizacion"


def check(
    *,
    installed: str = __version__,
    max_seen: str | None = None,
    http: Http | None = None,
    keys: Iterable[str] | None = None,
) -> Manifest | None:
    """La versión nueva firmada, o ``None`` si ya se tiene la última. ``keys``: las de
    :data:`PUBLIC_KEYS` si no se dan otras (solo los tests dan otras)."""
    keys = tuple(PUBLIC_KEYS if keys is None else keys)
    if not keys:
        raise UpdateError("Esta copia de Luzeira no puede comprobar actualizaciones.")
    http = http or GitHubHttp()
    data = http.get(MANIFEST_URL, MAX_MANIFEST_BYTES)
    signature = http.get(SIGNATURE_URL, MAX_SIGNATURE_BYTES)
    manifest = verify_manifest(data, signature, keys)
    return manifest if is_newer(manifest, installed, max_seen) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_installer(path: Path, manifest: Manifest) -> None:
    if path.stat().st_size != manifest.size or _sha256(path) != manifest.sha256:
        raise UpdateError("El instalador descargado no coincide con el firmado. No lo abro.")


def download(manifest: Manifest, *, http: Http | None = None, folder: Path | None = None) -> Path:
    """Descarga el instalador y lo comprueba. Se escribe con otro nombre y solo se
    renombra si casa con el manifiesto: nunca queda a la vista un ``.exe`` a medias."""
    http = http or GitHubHttp()
    folder = folder or download_dir()
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / manifest.file
    partial = folder / (manifest.file + ".descargando")
    try:
        http.save(manifest.installer_url, partial, manifest.size)
        verify_installer(partial, manifest)
        os.replace(partial, final)
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()
    return final


@dataclass(frozen=True)
class Launch:
    """Lo que hace falta para lanzar el instalador (se separa para poder probarlo)."""

    args: list[str]
    creationflags: int


def launch_command(installer: Path, log_file: Path) -> Launch:
    flags = 0
    if os.name == "nt":
        # Que el instalador no muera con Luzeira (sale justo después) ni comparta consola.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return Launch([str(installer), *INSTALLER_ARGS, f"/LOG={log_file}"], flags)


def launch(installer: Path, manifest: Manifest, log_file: Path) -> None:
    """Vuelve a comprobar el instalador justo antes y lo lanza. Quien llama cierra
    Luzeira a continuación: el instalador espera a que suelte su mutex."""
    verify_installer(installer, manifest)
    cmd = launch_command(installer, log_file)
    subprocess.Popen(cmd.args, creationflags=cmd.creationflags, close_fds=True)  # noqa: S603


def clean_downloads(folder: Path | None = None) -> None:
    """Al arrancar: borra lo que quedó de una actualización anterior (ya instalada o
    a medias). Lo que esté en uso se queda para la próxima vez."""
    folder = folder or download_dir()
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        with contextlib.suppress(OSError):
            path.unlink()
