"""Diálogo nativo de Windows para elegir un fichero (``GetOpenFileNameW`` de comdlg32).

Sustituye a ``tkinter.filedialog`` (C-30): tkinter arrastra Tcl/Tk (~12 MB) y el Python
embebible con el que se distribuirá Luzeira no lo trae. Este es el diálogo de siempre de
Windows, en el idioma del sistema.

Referencia: https://learn.microsoft.com/en-us/windows/win32/api/commdlg/nf-commdlg-getopenfilenamew
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

OFN_FILEMUSTEXIST = 0x00001000
OFN_PATHMUSTEXIST = 0x00000800
OFN_NOCHANGEDIR = 0x00000008  # no cambiar el directorio de trabajo del proceso
OFN_EXPLORER = 0x00080000
_MAX_PATH = 32768


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def build_filter(description: str, patterns: str) -> str:
    """Filtro de comdlg32: pares «descripción\\0patrones\\0» terminados en otro \\0."""
    return f"{description}\0{patterns}\0\0"


def ask_open_file(title: str, description: str, patterns: str) -> str | None:
    """Pide un fichero. Devuelve su ruta, o None si el usuario cancela (o no hay Windows)."""
    try:
        comdlg32 = ctypes.windll.comdlg32
    except (AttributeError, OSError):
        return None
    buffer = ctypes.create_unicode_buffer(_MAX_PATH)
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.lpstrFilter = build_filter(description, patterns)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = _MAX_PATH
    ofn.lpstrTitle = title
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    if not comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return None  # cancelado (o error del diálogo: CommDlgExtendedError lo diría)
    return buffer.value or None
