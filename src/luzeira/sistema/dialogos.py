"""Diálogos nativos de Windows: elegir un fichero (``GetOpenFileNameW`` de comdlg32) y
confirmar Sí/No (``MessageBoxW`` de user32).

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


# --- confirmar (MessageBoxW) -------------------------------------------------------------
# Referencia: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-messageboxw

MB_YESNO = 0x00000004
MB_ICONWARNING = 0x00000030
MB_ICONQUESTION = 0x00000020
MB_DEFBUTTON2 = 0x00000100  # el botón por defecto (Intro) es «No»
MB_SETFOREGROUND = 0x00010000  # que no se quede detrás de otras ventanas
MB_TOPMOST = 0x00040000
IDYES = 6


def _message_box():
    """``MessageBoxW`` de user32, o None fuera de Windows. Aparte para poder sustituirlo
    en los tests (que nunca abren un diálogo de verdad)."""
    try:
        return ctypes.windll.user32.MessageBoxW
    except (AttributeError, OSError):
        return None


def confirm_flags(*, warning: bool, default_no: bool = False) -> int:
    """Botones Sí/No. Con ``warning``: icono de advertencia y «No» por defecto, para que
    un Intro despistado no acepte algo arriesgado. Con ``default_no``: «No» por defecto
    pero con el icono de pregunta (p. ej. «¿se ve bien?»: un Intro no debe valer de «sí»)."""
    flags = MB_YESNO | MB_SETFOREGROUND | MB_TOPMOST
    if warning:
        return flags | MB_ICONWARNING | MB_DEFBUTTON2
    if default_no:
        return flags | MB_ICONQUESTION | MB_DEFBUTTON2
    return flags | MB_ICONQUESTION


def confirm(title: str, text: str, *, warning: bool = False, default_no: bool = False) -> bool:
    """Pregunta Sí/No con el diálogo de Windows. True solo si el usuario pulsa «Sí»
    (cerrar el diálogo o no estar en Windows cuenta como «No»)."""
    box = _message_box()
    if box is None:
        return False
    flags = confirm_flags(warning=warning, default_no=default_no)
    return box(None, text, title, flags) == IDYES
