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

Si Luzeira corre **como administrador**, no descarga ni lanza nada (:func:`is_elevated`):
la carpeta de descargas es del usuario, y un programa sin permisos podría cambiar el
instalador justo antes de que una app elevada lo abriera (y heredaría los permisos).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from luzeira import __version__, idioma
from luzeira.idioma import t

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
TIMEOUT = 20  # s de espera por cada lectura (no es el total: eso son los plazos de abajo)
#: Plazo total para bajar el manifiesto o su firma (pesan menos de 4 KB).
SMALL_DEADLINE = 60
#: Velocidad mínima media del instalador: por debajo, se corta. Con 32 KB/s, uno de 20 MB
#: tiene más de 10 minutos; una conexión que gotea un byte cada 19 s, no horas.
MIN_SPEED = 32 * 1024  # bytes/s
_CHUNK = 64 * 1024

#: Cuánto puede subir de golpe el primer número de la versión (el «major») respecto a la
#: instalada. Con una clave robada se podría firmar la 9999.0.0: sin este tope se
#: guardaría como «la más alta vista» y, cambiada la clave, ninguna versión buena
#: volvería a ofrecerse nunca. Con 1: de la 0.1.7 se puede pasar a cualquier 1.x (la 1.0
#: tiene que poder llegar), pero no a la 2.0. Quien se salte una versión entera la
#: instala a mano, como antes.
MAX_MAJOR_JUMP = 1

# [0-9] y no \d: \d admite cifras de otros alfabetos (árabes, devanagari…). config.py usa la misma.
# Se compara con fullmatch: en Python, «$» también casa antes de un «\n» final (R3-14).
# No se cambia a «\Z»: el mismo patrón lo usa pydantic (Manifest.version) con su motor
# de Rust, que no conoce «\Z» y donde «$» ya es el final del texto.
_VERSION = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")

# Parámetro propio del instalador: espera a que esta Luzeira se cierre, conserva el
# «Iniciar con Windows» tal como estaba y vuelve a abrir la app al terminar (luzeira.iss).
INSTALLER_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ACTUALIZAR")


class UpdateError(Exception):
    """Algo no ha salido bien. El mensaje es para el usuario, en su idioma."""


Version = tuple[int, int, int]


def parse_version(text: str) -> Version:
    """``"1.2.3"`` → ``(1, 2, 3)``. Solo ese formato: nada de sufijos ni ceros delante."""
    match = _VERSION.fullmatch(text)
    if match is None:
        raise ValueError(f"versión no válida: {text!r}")  # i18n: no
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
        raise UpdateError(t("sistema.actualizar_firma_no_valida")) from None
    for key in _load_keys(keys):
        try:
            key.verify(sig, data)
        except InvalidSignature:
            continue
        break
    else:
        raise UpdateError(t("sistema.actualizar_firma_no_valida"))
    try:
        manifest = Manifest.model_validate_json(data)
    except ValidationError:
        raise UpdateError(t("sistema.actualizar_info_no_valida")) from None
    if not manifest.file.startswith(f"Luzeira-{manifest.version}-"):
        raise UpdateError(t("sistema.actualizar_info_no_valida"))
    return manifest


def is_newer(manifest: Manifest, installed: str, max_seen: str | None) -> bool:
    """¿Merece la pena ofrecerla? Tiene que ser más nueva que la instalada y no más
    vieja que la mayor que ya se haya visto: así no se puede ofrecer una versión
    antigua aunque esté bien firmada.

    Límite conocido (ADR-006): quien controle GitHub sí puede seguir sirviendo el
    manifiesto de la versión instalada (o un error) y la app diría «ya tienes la
    última». El manifiesto no caduca; TUF lo resolvería (C-55).

    Una versión que salta demasiado (:data:`MAX_MAJOR_JUMP`) no se ofrece: así tampoco
    se guarda como «la más alta vista» (quien llama solo guarda lo que se ofrece)."""
    version = manifest.parsed_version
    current = parse_version(installed)
    if version <= current:
        return False
    if version[0] > current[0] + MAX_MAJOR_JUMP:
        log.warning(
            "Manifiesto firmado con un salto de versión imposible (%s → %s): se ignora.",
            installed,
            manifest.version,
        )
        return False
    return max_seen is None or version >= parse_version(max_seen)


# --- red -----------------------------------------------------------------------------


class Http(Protocol):
    def get(self, url: str, limit: int) -> bytes: ...

    def save(self, url: str, dest: Path, limit: int) -> None: ...


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port:
        raise UpdateError(t("sistema.actualizar_fuera_de_github"))


class _OnlyGitHub(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubHttp:
    """HTTPS con los certificados de Windows, sin cookies, y solo a
    :data:`ALLOWED_HOSTS`. Usa el proxy del sistema si lo hay (como el navegador): el
    TLS se sigue validando contra GitHub.

    Plazos: :data:`TIMEOUT` es lo que se espera a cada lectura; además hay un plazo
    **total** por descarga, para que un servidor que manda un byte de vez en cuando no
    la tenga abierta horas. ``clock`` es el reloj (los tests pasan uno falso)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._opener = urllib.request.build_opener(_OnlyGitHub())
        self._opener.addheaders = [("User-Agent", f"Luzeira/{__version__}")]

    def _open(self, url: str):
        _check_url(url)
        try:
            return self._opener.open(url, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError(t("sistema.actualizar_sin_versiones")) from None
            raise UpdateError(t("sistema.actualizar_error_github", code=exc.code)) from None
        except (urllib.error.URLError, OSError):
            raise UpdateError(t("sistema.actualizar_sin_conexion")) from None

    def _chunks(self, url: str, limit: int, deadline: float, too_big: str):
        """Los trozos de la respuesta, cortando si pasa de ``limit`` bytes o del plazo.

        ``read1`` y no ``read``: ``read(n)`` se queda esperando hasta juntar ``n`` bytes
        (cada espera con su propio TIMEOUT), así que el plazo no se miraría nunca."""
        end = self._clock() + deadline
        total = 0
        with self._open(url) as response:
            while chunk := response.read1(_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise UpdateError(t(too_big))
                if self._clock() > end:
                    raise UpdateError(t("sistema.actualizar_lenta"))
                yield chunk

    def get(self, url: str, limit: int) -> bytes:
        return b"".join(
            self._chunks(url, limit, SMALL_DEADLINE, "sistema.actualizar_respuesta_grande")
        )

    def save(self, url: str, dest: Path, limit: int) -> None:
        deadline = SMALL_DEADLINE + limit / MIN_SPEED  # ``limit`` es el tamaño firmado
        with dest.open("wb") as out:
            for chunk in self._chunks(url, limit, deadline, "sistema.actualizar_descarga_grande"):
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
        raise UpdateError(t("sistema.actualizar_no_puede"))
    http = http or GitHubHttp()
    data = http.get(MANIFEST_URL, MAX_MANIFEST_BYTES)
    signature = http.get(SIGNATURE_URL, MAX_SIGNATURE_BYTES)
    manifest = verify_manifest(data, signature, keys)
    return manifest if is_newer(manifest, installed, max_seen) else None


def is_elevated() -> bool:
    """¿Corre este proceso como administrador (token elevado de Windows)?

    Concepto: con el UAC, una cuenta de administrador arranca los programas con un
    «token» limitado; solo «Ejecutar como administrador» les da el elevado. Se pregunta a
    Windows con ``GetTokenInformation(TokenElevation)``. Si no se puede saber, se
    contesta «sí»: es lo prudente (lo peor que pasa es que no se actualice sola)."""
    if os.name != "nt":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    import ctypes
    from ctypes import wintypes

    # WinDLL propios: cambiar argtypes en ctypes.windll afectaría a otros módulos.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]  # fmt: skip
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]  # fmt: skip
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    token_query, token_elevation = 0x0008, 20  # TOKEN_QUERY, TokenElevation
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        return True
    try:
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(
            token, token_elevation, ctypes.byref(elevation), ctypes.sizeof(elevation),
            ctypes.byref(size),
        )  # fmt: skip
        return True if not ok else bool(elevation.value)
    finally:
        kernel32.CloseHandle(token)


def _refuse_if_elevated(elevated: Callable[[], bool] | None) -> None:
    if (elevated or is_elevated)():
        log.warning("Luzeira corre como administrador: no se descarga ni se abre el instalador.")
        raise UpdateError(t("sistema.actualizar_elevada"))


def _open_locked(path: Path) -> BinaryIO:
    """Abre ``path`` para leer **sin dejar que nadie lo cambie, lo borre ni lo renombre**
    mientras esté abierto. En Windows: ``CreateFileW`` con ``FILE_SHARE_READ`` (y sin
    ``FILE_SHARE_WRITE`` ni ``FILE_SHARE_DELETE``). Windows sí deja ejecutarlo: lanzar
    un programa solo pide leerlo. Fuera de Windows (solo tests), un ``open`` normal."""
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]  # fmt: skip
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    generic_read, file_share_read, open_existing, normal = 0x80000000, 0x1, 3, 0x80
    handle = kernel32.CreateFileW(
        str(path), generic_read, file_share_read, None, open_existing, normal, None
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")  # cerrarlo cierra también el handle


def _check_open(f: BinaryIO, manifest: Manifest) -> None:
    """Tamaño y SHA-256 de lo que hay detrás de ESTE fichero abierto (no de su ruta)."""
    if os.fstat(f.fileno()).st_size != manifest.size:
        raise UpdateError(t("sistema.actualizar_instalador_distinto"))
    digest = hashlib.sha256()
    while chunk := f.read(_CHUNK):
        digest.update(chunk)
    if digest.hexdigest() != manifest.sha256:
        raise UpdateError(t("sistema.actualizar_instalador_distinto"))


def verify_installer(path: Path, manifest: Manifest) -> None:
    with _open_locked(path) as f:
        _check_open(f, manifest)


def download(
    manifest: Manifest,
    *,
    http: Http | None = None,
    folder: Path | None = None,
    elevated: Callable[[], bool] | None = None,
) -> Path:
    """Descarga el instalador y lo comprueba. Se escribe con otro nombre y solo se
    renombra si casa con el manifiesto: nunca queda a la vista un ``.exe`` a medias.

    Va a una subcarpeta **nueva** con nombre aleatorio dentro de ``folder``: así nadie
    puede haber dejado preparado antes un fichero o un enlace con ese nombre."""
    _refuse_if_elevated(elevated)
    http = http or GitHubHttp()
    base = folder or download_dir()
    base.mkdir(parents=True, exist_ok=True)
    folder = base / secrets.token_hex(8)
    folder.mkdir()  # sin exist_ok: si ya existiera, error
    final = folder / manifest.file
    partial = folder / (manifest.file + ".descargando")
    ok = False
    try:
        http.save(manifest.installer_url, partial, manifest.size)
        verify_installer(partial, manifest)
        os.replace(partial, final)
        ok = True
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()
        if not ok:
            with contextlib.suppress(OSError):
                folder.rmdir()
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
    # /LANG: el instalador habla el idioma elegido en Luzeira, no el de Windows. Sus
    # [Languages] se llaman con los códigos de la app (test_empaquetado lo vigila).
    lang = f"/LANG={idioma.current()}"
    return Launch([str(installer), *INSTALLER_ARGS, lang, f"/LOG={log_file}"], flags)


def launch(
    installer: Path,
    manifest: Manifest,
    log_file: Path,
    *,
    elevated: Callable[[], bool] | None = None,
) -> None:
    """Vuelve a comprobar el instalador justo antes y lo lanza. Quien llama cierra
    Luzeira a continuación: el instalador espera a que suelte su mutex.

    El fichero se abre bloqueado (:func:`_open_locked`), el SHA-256 se calcula sobre ese
    mismo fichero abierto y no se suelta hasta después de lanzarlo: entre comprobar y
    ejecutar nadie puede cambiarlo (sin esto habría un hueco, «TOCTOU»)."""
    _refuse_if_elevated(elevated)
    with _open_locked(installer) as locked:
        _check_open(locked, manifest)
        cmd = launch_command(installer, log_file)
        subprocess.Popen(cmd.args, creationflags=cmd.creationflags, close_fds=True)  # noqa: S603


def _is_plain_dir(path: Path) -> bool:
    """Carpeta de verdad: ni enlace simbólico ni unión (junction) a otro sitio."""
    return path.is_dir() and not path.is_symlink() and not path.is_junction()


def clean_downloads(folder: Path | None = None) -> None:
    """Al arrancar: borra lo que quedó de una actualización anterior (ya instalada o
    a medias), también sus subcarpetas aleatorias. Lo que esté en uso se queda para la
    próxima vez. No sigue enlaces: solo borra lo que hay dentro de verdad."""
    folder = folder or download_dir()
    if not _is_plain_dir(folder):
        return
    for path in folder.iterdir():
        with contextlib.suppress(OSError):
            if _is_plain_dir(path):
                for inner in path.iterdir():
                    with contextlib.suppress(OSError):
                        inner.unlink()
                path.rmdir()
            else:
                path.unlink()
