"""Qué interfaces de red son tarjetas de verdad (C-11), con ``GetIfTable2`` de Windows.

Problema: psutil da los bytes de **todas** las interfaces. Con una VPN (Tailscale, Radmin,
WireGuard…) el mismo tráfico pasa dos veces: cifrado por la tarjeta física y en claro
por el adaptador virtual de la VPN. Sumándolo todo, la red marca el doble.

Solución: contar solo las interfaces que Windows marca como hardware
(``HardwareInterface``), que no son filtros de otra (``FilterInterface``) ni túneles
(``TunnelType``). Es la misma información que usa ``Get-NetAdapter -Physical``.

Se lee sin admin y sin abrir nada. Los nombres (``Alias``) son los mismos que da psutil
en Windows (el «nombre descriptivo» del adaptador, p. ej. «Ethernet» o «Wi-Fi»).

Estructuras de Microsoft Learn:
- https://learn.microsoft.com/en-us/windows/win32/api/netioapi/nf-netioapi-getiftable2
- https://learn.microsoft.com/en-us/windows/win32/api/netioapi/ns-netioapi-mib_if_row2
- https://learn.microsoft.com/en-us/windows/win32/api/netioapi/ns-netioapi-mib_if_table2
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

IF_MAX_STRING_SIZE = 256
IF_MAX_PHYS_ADDRESS_LENGTH = 32
TUNNEL_TYPE_NONE = 0

# Bits de ``InterfaceAndOperStatusFlags`` (un BOOLEAN de bits en C, el primero en el bit 0).
_HARDWARE = 0x01
_FILTER = 0x02


@dataclass(frozen=True)
class Interface:
    name: str  # el «Alias» de Windows (lo que psutil usa como nombre)
    hardware: bool
    filter: bool
    tunnel_type: int

    @property
    def physical(self) -> bool:
        return self.hardware and not self.filter and self.tunnel_type == TUNNEL_TYPE_NONE


def physical_names(interfaces: list[Interface]) -> frozenset[str]:
    """Nombres de las interfaces que cuentan como tarjetas de verdad."""
    return frozenset(i.name for i in interfaces if i.physical)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class MIB_IF_ROW2(ctypes.Structure):  # los nombres, los de Microsoft
    _fields_ = [
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", wintypes.ULONG),
        ("InterfaceGuid", _GUID),
        ("Alias", ctypes.c_wchar * (IF_MAX_STRING_SIZE + 1)),
        ("Description", ctypes.c_wchar * (IF_MAX_STRING_SIZE + 1)),
        ("PhysicalAddressLength", wintypes.ULONG),
        ("PhysicalAddress", ctypes.c_ubyte * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("Mtu", wintypes.ULONG),
        ("Type", wintypes.ULONG),
        ("TunnelType", ctypes.c_int),
        ("MediaType", ctypes.c_int),
        ("PhysicalMediumType", ctypes.c_int),
        ("AccessType", ctypes.c_int),
        ("DirectionType", ctypes.c_int),
        ("InterfaceAndOperStatusFlags", ctypes.c_ubyte),
        ("OperStatus", ctypes.c_int),
        ("AdminStatus", ctypes.c_int),
        ("MediaConnectState", ctypes.c_int),
        ("NetworkGuid", _GUID),
        ("ConnectionType", ctypes.c_int),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("InOctets", ctypes.c_uint64),
        ("InUcastPkts", ctypes.c_uint64),
        ("InNUcastPkts", ctypes.c_uint64),
        ("InDiscards", ctypes.c_uint64),
        ("InErrors", ctypes.c_uint64),
        ("InUnknownProtos", ctypes.c_uint64),
        ("InUcastOctets", ctypes.c_uint64),
        ("InMulticastOctets", ctypes.c_uint64),
        ("InBroadcastOctets", ctypes.c_uint64),
        ("OutOctets", ctypes.c_uint64),
        ("OutUcastPkts", ctypes.c_uint64),
        ("OutNUcastPkts", ctypes.c_uint64),
        ("OutDiscards", ctypes.c_uint64),
        ("OutErrors", ctypes.c_uint64),
        ("OutUcastOctets", ctypes.c_uint64),
        ("OutMulticastOctets", ctypes.c_uint64),
        ("OutBroadcastOctets", ctypes.c_uint64),
        ("OutQLen", ctypes.c_uint64),
    ]


# Tamaño que da ``sizeof(MIB_IF_ROW2)`` en C para 64 bits. Si ctypes calculara otro, las
# filas se leerían desplazadas: mejor no filtrar nada que filtrar mal.
MIB_IF_ROW2_SIZE = 1352


def list_interfaces() -> list[Interface]:
    """Las interfaces que conoce Windows. Lanza ``OSError`` si no se pueden leer."""
    if ctypes.sizeof(MIB_IF_ROW2) != MIB_IF_ROW2_SIZE:
        raise OSError("MIB_IF_ROW2 no mide lo esperado")
    iphlpapi = ctypes.WinDLL("iphlpapi")
    table = ctypes.c_void_p()
    status = iphlpapi.GetIfTable2(ctypes.byref(table))
    if status != 0:
        raise OSError(f"GetIfTable2 devolvió {status}")
    try:
        count = ctypes.c_ulong.from_address(table.value).value
        # La tabla empieza tras NumEntries, alineada a 8 (la fila lleva enteros de 64 bits).
        rows = (MIB_IF_ROW2 * count).from_address(table.value + 8)
        return [
            Interface(
                name=row.Alias,
                hardware=bool(row.InterfaceAndOperStatusFlags & _HARDWARE),
                filter=bool(row.InterfaceAndOperStatusFlags & _FILTER),
                tunnel_type=row.TunnelType,
            )
            for row in rows
        ]
    finally:
        iphlpapi.FreeMibTable(table)
