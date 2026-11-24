"""Llamadas reales a Windows para la CPU: contadores PDH de frecuencia y la sección de
memoria compartida del módulo opcional de sensores (ADR-007).

Como ``windows_api``, solo se prueba en Windows de verdad; los tests usan lectores falsos
(``FrequencyCounters`` en ``cpu_frecuencia`` y ``SectionReader`` en ``modulo``).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from luzeira.sensores.modulo import HEADER, SectionUntrusted
from luzeira.sensores.windows_api import PDH_FMT_COUNTERVALUE, PDH_FMT_DOUBLE, _check

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


# --- Sección del módulo ----------------------------------------------------------------
# https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-openfilemappingw
# https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo
# https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-iswellknownsid

FILE_MAP_READ = 0x0004
#: Solo deja leer los permisos del objeto (para saber quién lo creó), nada más.
READ_CONTROL = 0x00020000
ERROR_FILE_NOT_FOUND = 2
SE_KERNEL_OBJECT = 6
OWNER_SECURITY_INFORMATION = 0x1
WIN_LOCAL_SYSTEM_SID = 22
WIN_BUILTIN_ADMINISTRATORS_SID = 26
#: Offset de la secuencia en la cabecera (ver ``modulo``).
_SEQUENCE_OFFSET = 24


class MEMORY_BASIC_INFORMATION(ctypes.Structure):  # los nombres, los de Microsoft
    # https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-memory_basic_information
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class RealSectionReader:
    """Abre la sección del módulo (si existe), comprueba quién la creó y copia su
    contenido. Nunca escribe en ella: se abre solo con ``FILE_MAP_READ`` y
    ``READ_CONTROL``."""

    def __init__(self, name: str, size: int) -> None:
        self._name = name
        self._size = size
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._adv = ctypes.WinDLL("advapi32")
        self._k32.OpenFileMappingW.restype = wintypes.HANDLE
        self._k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self._k32.MapViewOfFile.restype = ctypes.c_void_p
        self._k32.MapViewOfFile.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_size_t,
        ]
        self._k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32.VirtualQuery.restype = ctypes.c_size_t
        self._k32.VirtualQuery.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(MEMORY_BASIC_INFORMATION),
            ctypes.c_size_t,
        ]
        self._k32.LocalFree.argtypes = [ctypes.c_void_p]
        self._adv.GetSecurityInfo.restype = wintypes.DWORD
        self._adv.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._adv.IsWellKnownSid.restype = wintypes.BOOL
        self._adv.IsWellKnownSid.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._handle: int | None = None
        self._view: int | None = None
        self._view_size = 0

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

    def _open(self) -> bool:
        handle = self._k32.OpenFileMappingW(FILE_MAP_READ | READ_CONTROL, False, self._name)
        if not handle:
            error = ctypes.get_last_error()
            if error == ERROR_FILE_NOT_FOUND:
                return False  # el módulo no está instalado o no está en marcha
            raise OSError(f"OpenFileMappingW devolvió {error}")  # i18n: no
        try:
            self._check_owner(handle)
            view = self._k32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
            if not view:
                raise OSError(f"MapViewOfFile devolvió {ctypes.get_last_error()}")  # i18n: no
            info = MEMORY_BASIC_INFORMATION()
            if not self._k32.VirtualQuery(view, ctypes.byref(info), ctypes.sizeof(info)):
                self._k32.UnmapViewOfFile(view)
                raise OSError("VirtualQuery falló")  # i18n: no
        except BaseException:
            self._k32.CloseHandle(handle)
            raise
        if info.RegionSize < HEADER.size:
            self._k32.UnmapViewOfFile(view)
            self._k32.CloseHandle(handle)
            raise OSError("sección demasiado pequeña")  # i18n: no
        self._handle, self._view = handle, view
        # Nunca se lee más allá de lo que de verdad está mapeado.
        self._view_size = min(self._size, info.RegionSize)
        return True

    def _check_owner(self, handle: int) -> None:
        """Solo vale una sección creada por SYSTEM (el servicio) o un administrador: un
        usuario normal no puede crear objetos en ``Global\\`` con ese propietario, así
        que nadie sin privilegios puede hacerse pasar por el módulo."""
        owner = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        status = self._adv.GetSecurityInfo(
            handle,
            SE_KERNEL_OBJECT,
            OWNER_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if status != 0:
            raise OSError(f"GetSecurityInfo devolvió {status}")  # i18n: no
        try:
            trusted = owner.value and (
                self._adv.IsWellKnownSid(owner, WIN_LOCAL_SYSTEM_SID)
                or self._adv.IsWellKnownSid(owner, WIN_BUILTIN_ADMINISTRATORS_SID)
            )
        finally:
            self._k32.LocalFree(descriptor)
        if not trusted:
            raise SectionUntrusted()

    def close(self) -> None:
        if self._view is not None:
            self._k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None
