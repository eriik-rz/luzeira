"""Llamadas reales a Windows para ``windows_gpu`` (PDH, DXGI y D3DKMT) con ``ctypes``.

Cómo funciona ``ctypes``, en corto: ``ctypes.WinDLL("pdh")`` carga ``pdh.dll`` y deja
llamar a sus funciones como si fueran de Python. Como C no sabe nada de objetos de
Python, las estructuras de C se describen con clases ``ctypes.Structure`` (campo a campo,
en el mismo orden y tipo que en la documentación de Microsoft) y los datos de salida se
pasan «por referencia» (``ctypes.byref``): la función escribe en esa memoria.

Las estructuras salen de Microsoft Learn (enlace en cada una). Nada se copia de otros
proyectos. Este módulo solo se prueba en Windows de verdad; los tests unitarios usan una
API falsa (ver ``WindowsGpuApi`` en ``windows_gpu``).
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

from luzeira.sensores.windows_gpu import Adapter, PdhSample, PerfCaps, PerfData

# --- PDH -------------------------------------------------------------------------------
# https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhopenqueryw
# https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw
# https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1

# Nombres en inglés: ``PdhAddEnglishCounterW`` los acepta en cualquier idioma de Windows
# (medido en un Windows en español; otros idiomas, sin probar).
ENGINE_PATH = r"\GPU Engine(*)\Utilization Percentage"
DEDICATED_PATH = r"\GPU Adapter Memory(*)\Dedicated Usage"


class _PdhValueUnion(ctypes.Union):
    _fields_ = [
        ("longValue", ctypes.c_long),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("AnsiStringValue", ctypes.c_char_p),
        ("WideStringValue", ctypes.c_wchar_p),
    ]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):  # los nombres, los de Microsoft
    # https://learn.microsoft.com/en-us/windows/win32/api/pdh/ns-pdh-pdh_fmt_countervalue
    # ctypes mete solo el relleno que pone C entre el DWORD y la unión (8 bytes de double).
    _fields_ = [("CStatus", wintypes.DWORD), ("u", _PdhValueUnion)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    # https://learn.microsoft.com/en-us/windows/win32/api/pdh/ns-pdh-pdh_fmt_countervalue_item_w
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", PDH_FMT_COUNTERVALUE)]


class RealPdhCounters:
    """Una consulta PDH con los dos contadores de GPU."""

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh")
        # PDH_STATUS es un LONG; como DWORD sin signo se compara bien con 0x800007D2.
        for fn in (
            "PdhOpenQueryW",
            "PdhAddEnglishCounterW",
            "PdhCollectQueryData",
            "PdhGetFormattedCounterArrayW",
            "PdhCloseQuery",
        ):
            getattr(self._pdh, fn).restype = wintypes.DWORD
        self._query = ctypes.c_void_p()
        _check(self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)), "PdhOpenQueryW")
        try:
            self._engine = self._add(ENGINE_PATH)
            self._dedicated = self._add(DEDICATED_PATH)
        except Exception:
            self.close()
            raise

    def _add(self, path: str) -> ctypes.c_void_p:
        handle = ctypes.c_void_p()
        status = self._pdh.PdhAddEnglishCounterW(self._query, path, None, ctypes.byref(handle))
        _check(status, "PdhAddEnglishCounterW")
        return handle

    def collect(self) -> PdhSample | None:
        _check(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")
        engines = self._array(self._engine)
        dedicated = self._array(self._dedicated)
        if engines is None or dedicated is None:
            return None
        return PdhSample(engines=engines, dedicated=dedicated)

    def _array(self, counter: ctypes.c_void_p) -> dict[str, float] | None:
        """Lee todas las instancias de un contador con comodín.

        Se llama dos veces: la primera, sin búfer, para saber cuántos bytes hacen falta
        (PDH responde ``PDH_MORE_DATA``); la segunda, con el búfer. Si entre medias
        aparece un proceso nuevo puede volver a faltar sitio: se reintenta.
        """
        for _ in range(3):
            size = wintypes.DWORD(0)
            count = wintypes.DWORD(0)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None
            )
            if status != PDH_MORE_DATA:
                return None  # sin dos muestras todavía, o sin instancias
            buf = (ctypes.c_byte * size.value)()
            status = self._pdh.PdhGetFormattedCounterArrayW(
                counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf
            )
            if status == PDH_MORE_DATA:
                continue
            if status != 0:
                return None
            # El búfer empieza con ``count`` elementos seguidos y detrás van los nombres
            # (los punteros ``szName`` apuntan dentro del mismo búfer).
            items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
            out: dict[str, float] = {}
            for i in range(count.value):
                item = items[i]
                if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                    out[item.szName] = item.FmtValue.u.doubleValue
            return out
        return None

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = ctypes.c_void_p()


# --- DXGI ------------------------------------------------------------------------------
# DXGI es COM: CreateDXGIFactory1 devuelve un puntero a un objeto cuya primera palabra
# apunta a su «vtable», una tabla de punteros a funciones en un orden fijo (el de la
# declaración de la interfaz, contando las heredadas). Para llamar a un método se coge el
# puntero de su posición y se llama pasando el propio objeto como primer argumento.
# https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-createdxgifactory1
# https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgifactory1-enumadapters1
# https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgiadapter1-getdesc1

IID_IDXGIFactory1 = "770aae78-f26f-4dba-a829-253c83d1b387"
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ADAPTER_FLAG_SOFTWARE = 2
# Posiciones en la vtable. IUnknown: 0 QueryInterface, 1 AddRef, 2 Release.
# IDXGIObject: 3-6. IDXGIFactory: 7-11. IDXGIFactory1: 12 EnumAdapters1, 13 IsCurrent.
# IDXGIAdapter: 7 EnumOutputs, 8 GetDesc, 9 CheckInterfaceSupport. IDXGIAdapter1: 10 GetDesc1.
_RELEASE = 2
_ENUM_ADAPTERS1 = 12
_GET_DESC1 = 10


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_text(cls, text: str) -> GUID:
        u = uuid.UUID(text)
        g = cls(u.time_low, u.time_mid, u.time_hi_version)
        g.Data4[:] = list(u.bytes[8:])
        return g


class LUID(ctypes.Structure):
    # https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-luid
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    @classmethod
    def from_int(cls, value: int) -> LUID:
        high = (value >> 32) & 0xFFFFFFFF
        # HighPart es un LONG con signo.
        return cls(value & 0xFFFFFFFF, high - (1 << 32) if high >= 1 << 31 else high)

    def to_int(self) -> int:
        return ((self.HighPart & 0xFFFFFFFF) << 32) | self.LowPart


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    # https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", wintypes.UINT),
        ("DeviceId", wintypes.UINT),
        ("SubSysId", wintypes.UINT),
        ("Revision", wintypes.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", LUID),
        ("Flags", wintypes.UINT),
    ]


def _method(obj: ctypes.c_void_p, index: int, restype, *argtypes):
    """Función de la posición ``index`` de la vtable del objeto COM ``obj``."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def list_adapters() -> list[Adapter]:
    dxgi = ctypes.WinDLL("dxgi")
    dxgi.CreateDXGIFactory1.restype = wintypes.DWORD
    factory = ctypes.c_void_p()
    iid = GUID.from_text(IID_IDXGIFactory1)
    _check(dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)), "CreateDXGIFactory1")
    out: list[Adapter] = []
    try:
        enum = _method(
            factory, _ENUM_ADAPTERS1, wintypes.DWORD, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
        )
        index = 0
        while index < 64:  # tope por si acaso; ningún PC tiene tantas
            adapter = ctypes.c_void_p()
            status = enum(factory, index, ctypes.byref(adapter))
            if status == DXGI_ERROR_NOT_FOUND:
                break
            _check(status, "EnumAdapters1")
            try:
                desc = DXGI_ADAPTER_DESC1()
                get_desc = _method(
                    adapter, _GET_DESC1, wintypes.DWORD, ctypes.POINTER(DXGI_ADAPTER_DESC1)
                )
                _check(get_desc(adapter, ctypes.byref(desc)), "GetDesc1")
                out.append(
                    Adapter(
                        luid=desc.AdapterLuid.to_int(),
                        name=desc.Description,
                        vendor_id=desc.VendorId,
                        dedicated_bytes=desc.DedicatedVideoMemory,
                        software=bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE),
                    )
                )
            finally:
                _method(adapter, _RELEASE, wintypes.ULONG)(adapter)
            index += 1
    finally:
        _method(factory, _RELEASE, wintypes.ULONG)(factory)
    return out


# --- D3DKMT ----------------------------------------------------------------------------
# https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/nf-d3dkmthk-d3dkmtopenadapterfromluid
# https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/nf-d3dkmthk-d3dkmtqueryadapterinfo
# https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/nf-d3dkmthk-d3dkmtcloseadapter
#
# ⚠ No documentado para programas normales: Learn lista KMTQAITYPE_NODEPERFDATA,
# _ADAPTERPERFDATA y _ADAPTERPERFDATA_CAPS pero sin su valor numérico, y contando su
# lista salen 60, 61 y 62. Medido en Windows 10 22H2 (19045): responden 61, 62 y 63 (la
# lista de Learn omite algún valor). Windows comprueba el tamaño exacto del búfer, así
# que un número equivocado casi siempre da «parámetro inválido» en vez de basura, y aun
# así cada valor pasa por límites de cordura en ``windows_gpu``.
# https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ne-d3dkmthk-_kmtqueryadapterinfotype
_KMT_NODEPERFDATA = 61
_KMT_ADAPTERPERFDATA = 62
_KMT_ADAPTERPERFDATA_CAPS = 63


class D3DKMT_OPENADAPTERFROMLUID(ctypes.Structure):
    _fields_ = [("AdapterLuid", LUID), ("hAdapter", wintypes.UINT)]


class D3DKMT_QUERYADAPTERINFO(ctypes.Structure):
    _fields_ = [
        ("hAdapter", wintypes.UINT),
        ("Type", ctypes.c_int),
        ("pPrivateDriverData", ctypes.c_void_p),
        ("PrivateDriverDataSize", wintypes.UINT),
    ]


class D3DKMT_CLOSEADAPTER(ctypes.Structure):
    _fields_ = [("hAdapter", wintypes.UINT)]


class D3DKMT_ADAPTER_PERFDATA(ctypes.Structure):
    # https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ns-d3dkmthk-_d3dkmt_adapter_perfdata
    # (los ULONGLONG van alineados a 8: ctypes lo hace solo). 64 bytes.
    _fields_ = [
        ("PhysicalAdapterIndex", ctypes.c_uint32),
        ("MemoryFrequency", ctypes.c_uint64),
        ("MaxMemoryFrequency", ctypes.c_uint64),
        ("MaxMemoryFrequencyOC", ctypes.c_uint64),
        ("MemoryBandwidth", ctypes.c_uint64),
        ("PCIEBandwidth", ctypes.c_uint64),
        ("FanRPM", wintypes.ULONG),
        ("Power", wintypes.ULONG),  # décimas de % (1 = 0,1 %)
        ("Temperature", wintypes.ULONG),  # décimas de °C (1 = 0,1 °C)
        ("PowerStateOverride", ctypes.c_ubyte),
    ]


class D3DKMT_ADAPTER_PERFDATACAPS(ctypes.Structure):
    # https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ns-d3dkmthk-_d3dkmt_adapter_perfdatacaps
    # Desde Windows 10 1803. 40 bytes.
    _fields_ = [
        ("PhysicalAdapterIndex", ctypes.c_uint32),
        ("MaxMemoryBandwidth", ctypes.c_uint64),
        ("MaxPCIEBandwidth", ctypes.c_uint64),
        ("MaxFanRPM", wintypes.ULONG),
        ("TemperatureMax", wintypes.ULONG),
        ("TemperatureWarning", wintypes.ULONG),
    ]


_NODE_FIELDS = [
    ("NodeOrdinal", ctypes.c_uint32),
    ("PhysicalAdapterIndex", ctypes.c_uint32),
    ("Frequency", ctypes.c_uint64),  # hercios
    ("MaxFrequency", ctypes.c_uint64),
    ("MaxFrequencyOC", ctypes.c_uint64),
    ("Voltage", wintypes.ULONG),
    ("VoltageMax", wintypes.ULONG),
    ("VoltageMaxOC", wintypes.ULONG),
    ("MaxTransitionLatency", ctypes.c_uint64),
]


class D3DKMT_NODE_PERFDATA(ctypes.Structure):
    # https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ns-d3dkmthk-_d3dkmt_node_perfdata
    # Learn la da con un ULONGLONG «Reserved» al final (64 bytes)...
    _fields_ = [*_NODE_FIELDS, ("Reserved", ctypes.c_uint64)]


class D3DKMT_NODE_PERFDATA_OLD(ctypes.Structure):
    # ...pero Windows 10 19045 solo acepta la versión sin él (56 bytes, medido). Se
    # prueban las dos y se recuerda la que funcione.
    _fields_ = _NODE_FIELDS


class RealKmtAdapter:
    def __init__(self, luid: int) -> None:
        self._gdi = ctypes.WinDLL("gdi32")
        for fn in ("D3DKMTOpenAdapterFromLuid", "D3DKMTQueryAdapterInfo", "D3DKMTCloseAdapter"):
            getattr(self._gdi, fn).restype = wintypes.LONG  # NTSTATUS
        request = D3DKMT_OPENADAPTERFROMLUID(LUID.from_int(luid), 0)
        _check(self._gdi.D3DKMTOpenAdapterFromLuid(ctypes.byref(request)), "OpenAdapterFromLuid")
        self._handle = request.hAdapter
        self._node_struct: type[ctypes.Structure] | None = None

    def _query(self, kind: int, data: ctypes.Structure) -> int:
        request = D3DKMT_QUERYADAPTERINFO(
            self._handle,
            kind,
            ctypes.cast(ctypes.pointer(data), ctypes.c_void_p),
            ctypes.sizeof(data),
        )
        return self._gdi.D3DKMTQueryAdapterInfo(ctypes.byref(request))

    def perf_data(self) -> PerfData:
        data = D3DKMT_ADAPTER_PERFDATA()
        _check(self._query(_KMT_ADAPTERPERFDATA, data), "ADAPTERPERFDATA")
        return PerfData(data.Temperature, data.FanRPM, data.Power)

    def perf_caps(self) -> PerfCaps:
        data = D3DKMT_ADAPTER_PERFDATACAPS()
        _check(self._query(_KMT_ADAPTERPERFDATA_CAPS, data), "ADAPTERPERFDATA_CAPS")
        return PerfCaps(data.MaxFanRPM, data.TemperatureMax)

    def clock_hz(self) -> int:
        """Frecuencia del motor 0 (en las tarjetas medidas, el 3D, el que se usa al jugar)."""
        kinds = (
            [self._node_struct]
            if self._node_struct
            else [D3DKMT_NODE_PERFDATA, D3DKMT_NODE_PERFDATA_OLD]
        )
        status = 0
        for kind in kinds:
            data = kind()  # NodeOrdinal = 0, PhysicalAdapterIndex = 0
            status = self._query(_KMT_NODEPERFDATA, data)
            if status == 0:
                self._node_struct = kind
                return int(data.Frequency)
        _check(status, "NODEPERFDATA")
        return 0

    def close(self) -> None:
        if self._handle:
            self._gdi.D3DKMTCloseAdapter(ctypes.byref(D3DKMT_CLOSEADAPTER(self._handle)))
            self._handle = 0


class RealWindowsGpuApi:
    def list_adapters(self) -> list[Adapter]:
        return list_adapters()

    def open_counters(self) -> RealPdhCounters:
        return RealPdhCounters()

    def open_adapter(self, luid: int) -> RealKmtAdapter:
        return RealKmtAdapter(luid)


def _check(status: int, what: str) -> None:
    """Convierte un código de error de Windows en ``OSError`` (con el código, sin rutas)."""
    if status != 0:
        raise OSError(f"{what} devolvió 0x{status & 0xFFFFFFFF:08X}")
