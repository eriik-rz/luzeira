"""Quién manda en un objeto de Windows y quién puede escribir en él (R4-06, R4-08, ADR-008).

Conceptos, para quien no los conozca:

- Todo fichero, carpeta u objeto del núcleo (mutex, evento…) tiene un **descriptor de
  seguridad** con un **propietario** (un SID: el identificador de un usuario o grupo) y
  una **DACL**: la lista de «entradas» (ACE) que dicen a quién se le permite o se le
  niega qué. Cada ACE lleva un SID y una **máscara** de bits con los permisos.
- Un **SID** se escribe como ``S-1-5-18`` (SYSTEM) o ``S-1-5-32-544`` (el grupo
  Administradores). Se comparan como texto.
- La **etiqueta de integridad** (en la SACL) dice cuánto se fía Windows de quien creó el
  objeto: baja (``S-1-16-4096``, p. ej. un navegador en modo protegido), media
  (``S-1-16-8192``, lo normal) o alta (administrador). Un proceso de integridad baja no
  puede escribir en objetos de integridad media, pero sí **crear** objetos con nombre en
  la sesión antes que nosotros.

Aquí solo se **lee** y se **decide**: ``foreign_writers`` es una función pura (sin
Windows) para poder probar la decisión con listas de ACE inventadas; las funciones
``*_security`` leen el descriptor de verdad con ctypes.

Fuentes (Microsoft Learn, consultadas 2026-11-24):
- «GetSecurityInfo» y «GetNamedSecurityInfoW»: https://learn.microsoft.com/en-us/windows/win32/api/aclapi/
- «ACE_HEADER» y «ACCESS_ALLOWED_ACE»: https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-ace_header
- «Well-known SIDs»: https://learn.microsoft.com/en-us/windows/win32/secauthz/well-known-sids
- «File Security and Access Rights»: https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights
- «Mandatory Integrity Control»: https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# --- SID conocidos -----------------------------------------------------------------------

SID_SYSTEM = "S-1-5-18"
SID_ADMINISTRATORS = "S-1-5-32-544"
#: El servicio TrustedInstaller (dueño de los ficheros de Windows).
SID_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
#: «CREATOR OWNER»: un comodín que, al heredar, se cambia por quien crea el hijo. En el
#: propio objeto no casa con nadie: ningún proceso lleva ese SID.
SID_CREATOR_OWNER = "S-1-3-0"
SID_EVERYONE = "S-1-1-0"

#: Propietarios de fiar para lo que instala un administrador (ADR-008).
ADMIN_OWNERS = frozenset({SID_SYSTEM, SID_ADMINISTRATORS})

# --- Permisos (máscaras) -----------------------------------------------------------------

DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
GENERIC_ALL = 0x10000000
GENERIC_WRITE = 0x40000000
FILE_WRITE_DATA = 0x0002  # en una carpeta, el mismo bit es FILE_ADD_FILE
FILE_APPEND_DATA = 0x0004  # en una carpeta, FILE_ADD_SUBDIRECTORY
FILE_ADD_FILE = FILE_WRITE_DATA
FILE_ADD_SUBDIRECTORY = FILE_APPEND_DATA
FILE_DELETE_CHILD = 0x0040

#: Lo que deja cambiar el contenido de un fichero o quitarlo de en medio.
FILE_WRITE_RIGHTS = (
    FILE_WRITE_DATA | FILE_APPEND_DATA | DELETE | WRITE_DAC | WRITE_OWNER | GENERIC_WRITE
) | GENERIC_ALL
#: Lo que deja meter, borrar o cambiar cosas en una carpeta. ``DELETE`` también: con él se
#: puede **renombrar** la carpeta y poner otra con el mismo nombre en su lugar.
FOLDER_WRITE_RIGHTS = FILE_WRITE_RIGHTS | FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_DELETE_CHILD

# Tipos de ACE (``AceType``).
ACCESS_ALLOWED_ACE_TYPE = 0x0
ACCESS_DENIED_ACE_TYPE = 0x1
ACCESS_DENIED_OBJECT_ACE_TYPE = 0x6
ACCESS_DENIED_CALLBACK_ACE_TYPE = 0xA
ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE = 0xC
SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11
_DENY_TYPES = frozenset(
    {
        ACCESS_DENIED_ACE_TYPE,
        ACCESS_DENIED_OBJECT_ACE_TYPE,
        ACCESS_DENIED_CALLBACK_ACE_TYPE,
        ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE,
    }
)

#: Niveles de integridad (el último número del SID de la etiqueta).
INTEGRITY_LOW = 0x1000
INTEGRITY_MEDIUM = 0x2000

#: Marca de «sin DACL»: Windows lo trata como «todo el mundo puede todo».
NO_DACL = "<sin DACL>"


@dataclass(frozen=True)
class Ace:
    """Una entrada de la DACL. ``sid`` es ``None`` si su tipo no se sabe leer."""

    kind: int
    flags: int
    mask: int
    sid: str | None


@dataclass(frozen=True)
class Security:
    """Lo que interesa de un descriptor de seguridad.

    ``dacl`` es ``None`` si el objeto **no tiene DACL** (todo el mundo puede todo).
    ``integrity`` es el nivel de la etiqueta, o ``None`` si no la tiene (Windows la trata
    entonces como media)."""

    owner: str | None
    dacl: tuple[Ace, ...] | None
    integrity: int | None = None


def foreign_writers(security: Security, allowed: Iterable[str], rights: int) -> list[str]:
    """SID que, sin estar en ``allowed``, reciben alguno de los permisos ``rights``.

    Reglas, todas del lado de desconfiar:

    - Sin DACL → ``[NO_DACL]``: cualquiera puede escribir.
    - Las ACE de **denegar** se ignoran: solo quitan permisos, y no contarlas solo puede
      hacer que se rechace algo que en realidad era seguro, nunca al revés.
    - Las ACE de permitir que solo se heredan (``INHERIT_ONLY``) **sí cuentan**: dicen qué
      permisos tendrán los ficheros que se creen dentro.
    - Un tipo de ACE que no sea el normal de permitir (objeto, *callback*…) se trata como
      ajeno: en una carpeta de ficheros no debería aparecer.
    """
    if security.dacl is None:
        return [NO_DACL]
    allowed_set = frozenset(allowed)
    found: list[str] = []
    for ace in security.dacl:
        if ace.kind in _DENY_TYPES:
            continue
        if ace.kind != ACCESS_ALLOWED_ACE_TYPE:
            found.append(ace.sid or f"<ACE tipo {ace.kind}>")
            continue
        if ace.mask & rights and ace.sid not in allowed_set:
            found.append(ace.sid or "?")
    return found


# --- Lectura real (solo Windows) ---------------------------------------------------------

SE_FILE_OBJECT = 1
SE_KERNEL_OBJECT = 6
OWNER_SECURITY_INFORMATION = 0x1
DACL_SECURITY_INFORMATION = 0x4
LABEL_SECURITY_INFORMATION = 0x10
SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


class _AceHeader(ctypes.Structure):  # los nombres, los de Microsoft
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", ctypes.c_ushort),
    ]


class _Acl(ctypes.Structure):
    _fields_ = [
        ("AclRevision", ctypes.c_ubyte),
        ("Sbz1", ctypes.c_ubyte),
        ("AclSize", ctypes.c_ushort),
        ("AceCount", ctypes.c_ushort),
        ("Sbz2", ctypes.c_ushort),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


def _advapi() -> ctypes.WinDLL:
    """advapi32 propio (no ``ctypes.windll`` compartido) con los tipos de aquí."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.POINTER(ctypes.c_void_p)
    adv.GetSecurityInfo.restype = ctypes.c_uint32
    adv.GetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32] + [ptr] * 5
    adv.GetNamedSecurityInfoW.restype = ctypes.c_uint32
    adv.GetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32] + [
        ptr
    ] * 5
    adv.GetAce.restype = ctypes.c_int
    adv.GetAce.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ptr]
    adv.ConvertSidToStringSidW.restype = ctypes.c_int
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ptr,
        ctypes.c_void_p,
    ]
    adv.OpenProcessToken.restype = ctypes.c_int
    adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ptr]
    adv.GetTokenInformation.restype = ctypes.c_int
    adv.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    return adv


def _kernel32() -> ctypes.WinDLL:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.LocalFree.restype = ctypes.c_void_p
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetCurrentProcess.argtypes = []
    k.CloseHandle.restype = ctypes.c_int
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    return k


def _sid_text(adv: ctypes.WinDLL, k: ctypes.WinDLL, sid: int | None) -> str | None:
    if not sid:
        return None
    text = ctypes.c_wchar_p()
    if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW")  # i18n: no
    try:
        return text.value
    finally:
        k.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _aces(adv: ctypes.WinDLL, k: ctypes.WinDLL, acl: int) -> tuple[Ace, ...]:
    count = ctypes.cast(acl, ctypes.POINTER(_Acl)).contents.AceCount
    out: list[Ace] = []
    for i in range(count):
        ace = ctypes.c_void_p()
        if not adv.GetAce(acl, i, ctypes.byref(ace)) or not ace.value:
            raise OSError(ctypes.get_last_error(), "GetAce")  # i18n: no
        header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
        sid = None
        mask = 0
        # ACCESS_ALLOWED_ACE, ACCESS_DENIED_ACE y SYSTEM_MANDATORY_LABEL_ACE comparten
        # forma: cabecera (4 bytes), máscara (4) y el SID detrás. Los demás tipos llevan
        # otros campos en medio: no se leen (y ``foreign_writers`` desconfía de ellos).
        if header.AceType in (
            ACCESS_ALLOWED_ACE_TYPE,
            ACCESS_DENIED_ACE_TYPE,
            SYSTEM_MANDATORY_LABEL_ACE_TYPE,
        ):
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            sid = _sid_text(adv, k, ace.value + 8)
        out.append(Ace(header.AceType, header.AceFlags, mask, sid))
    return tuple(out)


def _integrity(label_aces: tuple[Ace, ...]) -> int | None:
    for ace in label_aces:
        if (
            ace.kind == SYSTEM_MANDATORY_LABEL_ACE_TYPE
            and ace.sid
            and ace.sid.startswith("S-1-16-")
        ):
            return int(ace.sid.rsplit("-", 1)[1])
    return None


def _security(call, label: bool) -> Security:  # type: ignore[no-untyped-def]
    adv, k = _advapi(), _kernel32()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    what = OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    if label:
        what |= LABEL_SECURITY_INFORMATION
    status = call(adv, what, owner, dacl, sacl, descriptor)
    if status != 0:
        raise OSError(status, "GetSecurityInfo")  # i18n: no
    try:
        aces = _aces(adv, k, dacl.value) if dacl.value else None
        integrity = _integrity(_aces(adv, k, sacl.value)) if label and sacl.value else None
        return Security(_sid_text(adv, k, owner.value), aces, integrity)
    finally:
        k.LocalFree(descriptor)


def handle_security(handle: int, *, kernel_object: bool = False) -> Security:
    """Propietario y DACL (y, en objetos del núcleo, la etiqueta) de un handle abierto.

    El handle necesita ``READ_CONTROL``. Leer por handle y no por ruta evita que alguien
    cambie el objeto entre la comprobación y el uso."""
    kind = SE_KERNEL_OBJECT if kernel_object else SE_FILE_OBJECT

    def call(adv, what, owner, dacl, sacl, descriptor):  # type: ignore[no-untyped-def]
        return adv.GetSecurityInfo(
            handle,
            kind,
            what,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            ctypes.byref(sacl),
            ctypes.byref(descriptor),
        )

    return _security(call, label=kernel_object)


def path_security(path: Path) -> Security:
    """Propietario y DACL de un fichero o carpeta por su ruta."""

    def call(adv, what, owner, dacl, sacl, descriptor):  # type: ignore[no-untyped-def]
        return adv.GetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            what,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            ctypes.byref(sacl),
            ctypes.byref(descriptor),
        )

    return _security(call, label=False)


def current_user_sid() -> str:
    """El SID del usuario de este proceso (p. ej. ``S-1-5-21-…-1001``)."""
    if sys.platform != "win32":  # pragma: no cover - solo fuera de Windows
        raise OSError("solo Windows")  # i18n: no
    adv, k = _advapi(), _kernel32()
    token = ctypes.c_void_p()
    if not adv.OpenProcessToken(k.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken")  # i18n: no
    try:
        size = ctypes.c_uint32(0)
        adv.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value or 256)
        if not adv.GetTokenInformation(token, _TOKEN_USER, buffer, len(buffer), ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation")  # i18n: no
        # TOKEN_USER empieza por SID_AND_ATTRIBUTES: el primer campo es el puntero al SID.
        sid = ctypes.c_void_p.from_buffer(buffer).value
        text = _sid_text(adv, k, sid)
        if text is None:
            raise OSError("token sin SID")  # i18n: no
        return text
    finally:
        k.CloseHandle(token)


class SecurityDescriptor:
    """Un descriptor de seguridad creado desde texto SDDL, listo para pasarlo como
    ``SECURITY_ATTRIBUTES`` al crear un objeto. Se libera con ``close()``."""

    def __init__(self, sddl: str) -> None:
        self._adv, self._k = _advapi(), _kernel32()
        self._sd = ctypes.c_void_p()
        if not self._adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(self._sd), None
        ):
            raise OSError(ctypes.get_last_error(), "ConvertStringSecurityDescriptor")  # i18n: no
        self.attributes = SECURITY_ATTRIBUTES(
            ctypes.sizeof(SECURITY_ATTRIBUTES), self._sd.value, False
        )

    def close(self) -> None:
        if self._sd.value:
            self._k.LocalFree(self._sd)
            self._sd = ctypes.c_void_p()
