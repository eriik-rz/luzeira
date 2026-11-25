"""Llamadas reales a Windows para la CPU: contadores PDH de frecuencia y el fichero
mapeado del módulo opcional de sensores (ADR-007, ADR-008).

Como ``windows_api``, solo se prueba en Windows de verdad; los tests de la fuente usan
lectores falsos (``FrequencyCounters`` en ``cpu_frecuencia`` y ``SectionReader`` en
``modulo``), y los del lector, ficheros de verdad en una carpeta temporal.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from luzeira.sensores.modulo import HEADER, SectionUntrusted
from luzeira.sensores.windows_api import PDH_FMT_COUNTERVALUE, PDH_FMT_DOUBLE, _check
from luzeira.sistema import permisos

# --- Frecuencia (PDH) ------------------------------------------------------------------
# https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcountervalue

FREQUENCY_PATH = r"\Processor Information(_Total)\Processor Frequency"
PERFORMANCE_PATH = r"\Processor Information(_Total)\% Processor Performance"


class RealFrequencyCounters:
    """Una consulta PDH con la frecuencia nominal y el porcentaje de rendimiento."""

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh")
        for fn in (
            "PdhOpenQueryW",
            "PdhAddEnglishCounterW",
            "PdhCollectQueryData",
            "PdhGetFormattedCounterValue",
            "PdhCloseQuery",
        ):
            getattr(self._pdh, fn).restype = wintypes.DWORD
        self._query = ctypes.c_void_p()
        _check(self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)), "PdhOpenQueryW")
        try:
            # Nombres en inglés: los de Windows en español están traducidos (medido).
            self._frequency = self._add(FREQUENCY_PATH)
            self._performance = self._add(PERFORMANCE_PATH)
            # Primera muestra: el porcentaje se calcula entre dos, la siguiente ya vale.
            _check(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")
        except Exception:
            self.close()
            raise

    def _add(self, path: str) -> ctypes.c_void_p:
        handle = ctypes.c_void_p()
        status = self._pdh.PdhAddEnglishCounterW(self._query, path, None, ctypes.byref(handle))
        _check(status, "PdhAddEnglishCounterW")
        return handle

    def _value(self, counter: ctypes.c_void_p) -> float | None:
        value = PDH_FMT_COUNTERVALUE()
        status = self._pdh.PdhGetFormattedCounterValue(
            counter, PDH_FMT_DOUBLE, None, ctypes.byref(value)
        )
        if status != 0 or value.CStatus != 0:
            return None
        return value.u.doubleValue

    def sample(self) -> tuple[float, float] | None:
        _check(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")
        nominal = self._value(self._frequency)
        percent = self._value(self._performance)
        if nominal is None or percent is None:
            return None
        return nominal, percent

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = ctypes.c_void_p()


# --- Fichero del módulo (ADR-008) ------------------------------------------------------
# https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
# https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-createfilemappingw
# https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath
# https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle

GENERIC_READ = 0x80000000
READ_CONTROL = 0x00020000
FILE_READ_ATTRIBUTES = 0x0080
#: Con solo READ_CONTROL|FILE_READ_ATTRIBUTES Windows no aplica el modo de compartir al
#: handle de la carpeta, y se podría renombrar aunque no se comparta el borrado (medido
#: el 2026-11-25 en el servicio del módulo). Pedir también listar la carpeta lo impide.
FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # para poder abrir una carpeta
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000  # abrir el enlace, no lo que apunta
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
PAGE_READONLY = 0x02
FILE_MAP_READ = 0x0004
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value
#: Offset de la secuencia en la cabecera (ver ``modulo``).
_SEQUENCE_OFFSET = 24


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


#: FOLDERID_ProgramData, {62AB5D82-FDC1-4DC3-A9DD-070D1D495D97} (KnownFolders.h).
FOLDERID_PROGRAM_DATA = _GUID(
    0x62AB5D82, 0xFDC1, 0x4DC3, (ctypes.c_ubyte * 8)(0xA9, 0xDD, 0x07, 0x0D, 0x1D, 0x49, 0x5D, 0x97)
)


def programdata_dir() -> Path:
    """``C:\\ProgramData`` (o donde esté), preguntado a Windows y **no** a la variable de
    entorno ``%ProgramData%``: quien lance Luzeira puede cambiar la variable y llevar al
    lector a una carpeta suya."""
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    path = ctypes.c_wchar_p()
    status = shell32.SHGetKnownFolderPath(
        ctypes.byref(FOLDERID_PROGRAM_DATA), 0, None, ctypes.byref(path)
    )
    try:
        if status != 0 or not path.value:
            raise OSError(f"SHGetKnownFolderPath devolvió {status:#x}")  # i18n: no
        return Path(path.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


#: Los que pueden escribir en la carpeta y en el fichero (ver el docstring de ``modulo``).
TRUSTED_WRITERS = frozenset(
    {
        permisos.SID_SYSTEM,
        permisos.SID_ADMINISTRATORS,
        permisos.SID_TRUSTED_INSTALLER,
        permisos.SID_CREATOR_OWNER,
    }
)


def check_security(security: permisos.Security, *, folder: bool, owners: frozenset[str]) -> None:
    """Lanza ``SectionUntrusted`` si el propietario no está en ``owners`` o si alguien de
    fuera de ``TRUSTED_WRITERS`` puede escribir (la máscara depende de si es carpeta)."""
    if security.owner not in owners:
        raise SectionUntrusted("propietario")  # i18n: no
    rights = permisos.FOLDER_WRITE_RIGHTS if folder else permisos.FILE_WRITE_RIGHTS
    if permisos.foreign_writers(security, TRUSTED_WRITERS, rights):
        raise SectionUntrusted("permisos")  # i18n: no


class RealModuleFileReader:
    """Abre el fichero del módulo (si existe), comprueba carpeta y fichero y copia su
    contenido. Nunca escribe: se abre con ``GENERIC_READ`` y se mapea ``PAGE_READONLY``.

    Orden, para que nadie cambie nada entre la comprobación y el uso:

    1. Se abre **la carpeta** sin seguir enlaces y sin compartir el borrado (mientras la
       tenemos abierta, nadie la puede renombrar ni cambiar por otra), y se comprueban
       por su handle el tipo, el propietario y la DACL.
    2. Se abre **el fichero** igual, sin seguir enlaces, y se comprueba por su handle.
    3. Se mapea como mucho ``min(tamaño real, size)``: nunca se lee más allá. Un fichero
       mapeado no se puede acortar mientras lo está.
    4. Se suelta la carpeta; el fichero y su vista se quedan hasta ``close()``.

    ``owners`` (propietarios aceptados) y ``security`` (cómo se leen los permisos de un
    handle) se inyectan en los tests: un fichero de ``tmp_path`` es del usuario, no de
    SYSTEM.
    """

    def __init__(
        self,
        folder: Path,
        name: str,
        size: int,
        *,
        owners: frozenset[str] = permisos.ADMIN_OWNERS,
        security: Callable[[int], permisos.Security] | None = None,
    ) -> None:
        self._folder = folder
        self._path = folder / name
        self._size = size
        self._owners = owners
        self._security = security or permisos.handle_security
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k.GetFileInformationByHandle.restype = wintypes.BOOL
        k.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
        ]
        k.GetFileSizeEx.restype = wintypes.BOOL
        k.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
        k.CreateFileMappingW.restype = wintypes.HANDLE
        k.CreateFileMappingW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
        ]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_size_t,
        ]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k
        self._file: int | None = None
        self._mapping: int | None = None
        self._view: int | None = None
        self._view_size = 0

    @property
    def closed(self) -> bool:
        return self._view is None and self._file is None and self._mapping is None

    def read(self) -> bytes | None:
        if self._view is None and not self._open():
            return None
        view = self._view
        if view is None:
            return None
        # Seqlock: se copia y se mira que la secuencia no cambiara mientras tanto.
        for _ in range(3):
            before = ctypes.string_at(view + _SEQUENCE_OFFSET, 8)
            data = ctypes.string_at(view, self._view_size)
            after = ctypes.string_at(view + _SEQUENCE_OFFSET, 8)
            if before == after:
                return data
        return data  # sigue cambiando: ``parse_section`` lo rechazará si está a medias

    def _create(self, path: Path, access: int, flags: int) -> int | None:
        """Abre sin seguir enlaces; ``None`` si no existe."""
        handle = self._k32.CreateFileW(
            str(path),
            access,
            FILE_SHARE_READ | FILE_SHARE_WRITE,  # sin FILE_SHARE_DELETE, a propósito
            None,
            OPEN_EXISTING,
            flags | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle is None or handle == _INVALID_HANDLE:
            error = ctypes.get_last_error()
            if error in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
                return None  # el módulo no está instalado
            raise OSError(f"CreateFileW devolvió {error}")  # i18n: no
        return handle

    def _attributes(self, handle: int) -> int:
        info = BY_HANDLE_FILE_INFORMATION()
        if not self._k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError(
                f"GetFileInformationByHandle devolvió {ctypes.get_last_error()}"  # i18n: no
            )
        return info.dwFileAttributes

    def _check(self, handle: int, *, folder: bool) -> None:
        attributes = self._attributes(handle)
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise SectionUntrusted("enlace")  # i18n: no
        if bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != folder:
            raise SectionUntrusted("tipo")  # i18n: no
        check_security(self._security(handle), folder=folder, owners=self._owners)

    def _open(self) -> bool:
        k = self._k32
        folder = self._create(
            self._folder,
            READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY,
            FILE_FLAG_BACKUP_SEMANTICS,
        )
        if folder is None:
            return False
        try:
            self._check(folder, folder=True)
            file = self._create(self._path, GENERIC_READ, 0)
            if file is None:
                return False
            try:
                self._check(file, folder=False)
                size = ctypes.c_longlong()
                if not k.GetFileSizeEx(file, ctypes.byref(size)):
                    raise OSError(f"GetFileSizeEx devolvió {ctypes.get_last_error()}")  # i18n: no
                if size.value < HEADER.size:
                    raise OSError("fichero demasiado pequeño")  # i18n: no
                view_size = min(self._size, size.value)
                mapping = k.CreateFileMappingW(file, None, PAGE_READONLY, 0, 0, None)
                if not mapping:
                    raise OSError(
                        f"CreateFileMappingW devolvió {ctypes.get_last_error()}"  # i18n: no
                    )
                view = k.MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, view_size)
                if not view:
                    error = ctypes.get_last_error()
                    k.CloseHandle(mapping)
                    raise OSError(f"MapViewOfFile devolvió {error}")  # i18n: no
            except BaseException:
                k.CloseHandle(file)
                raise
        finally:
            k.CloseHandle(folder)
        self._file, self._mapping, self._view = file, mapping, view
        self._view_size = view_size
        return True

    def close(self) -> None:
        if self._view is not None:
            self._k32.UnmapViewOfFile(self._view)
            self._view = None
        for name in ("_mapping", "_file"):
            handle = getattr(self, name)
            if handle is not None:
                self._k32.CloseHandle(handle)
                setattr(self, name, None)
