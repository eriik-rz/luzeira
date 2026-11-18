"""Que Windows reconozca a Luzeira como «Luzeira» y no como «pythonw».

Concepto: Windows agrupa las ventanas en la barra de tareas, y decide qué nombre e icono
usar al anclarlas, por el **AppUserModelID** (AUMID) del proceso. Si no lo fijamos, el
AUMID es el del ejecutable, ``pythonw.exe``, y al anclar sale «pythonw» con su icono.
Se arregla en tres sitios, todos con el mismo identificador:

1. El **proceso** (``set_process_app_id``): la bandeja y la ventana del editor.
2. Los **accesos directos** (``set_shortcut_app_id``): el del menú Inicio lo pone el
   instalador; el que crea la app (``accesos.py``) se marca aquí. Con eso, anclar la
   ventana usa el nombre y el icono del acceso directo.
3. La **ventana del editor** (``set_window_relaunch``): para la versión portable, que no
   tiene acceso en el menú Inicio, la propia ventana dice cómo se vuelve a abrir, con
   qué nombre y con qué icono.

Fuentes (Microsoft Learn, consultadas 2026-11-18):
- «Application User Model IDs (AppUserModelIDs)»,
  https://learn.microsoft.com/en-us/windows/win32/shell/appids
- «SetCurrentProcessExplicitAppUserModelID», «SHGetPropertyStoreForWindow»,
  «SHGetPropertyStoreFromParsingName» y «System.AppUserModel.RelaunchCommand».

Las propiedades se escriben con ``IPropertyStore`` (una interfaz COM). COM sin librerías
extra: un puntero a interfaz apunta a una tabla de funciones (vtable) y cada método es
una posición fija de esa tabla.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

# Formato recomendado «Empresa.Producto»; sin espacios, máx. 128 caracteres.
APP_ID = "Luzeira.Luzeira"
DISPLAY_NAME = "Luzeira"

_VT_LPWSTR = 31
_GPS_READWRITE = 2
# IPropertyStore: tras los 3 de IUnknown vienen GetCount, GetAt, GetValue, SetValue, Commit.
_RELEASE, _GET_VALUE, _SET_VALUE, _COMMIT = 2, 5, 6, 7
_IID_IPROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
# PKEY_AppUserModel_* (propkey.h): mismo GUID, distinto número de propiedad.
_FMTID_APPUSERMODEL = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_PID_RELAUNCH_COMMAND = 2
_PID_RELAUNCH_ICON = 3
_PID_RELAUNCH_NAME = 4
_PID_ID = 5


class _GUID(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_byte * 16)]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        guid = cls()
        ctypes.memmove(guid.bytes, uuid.UUID(text).bytes_le, 16)
        return guid


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    # vt + 3 reservados + unión de 16 bytes (24 en x64); solo se usa la cadena (VT_LPWSTR).
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort * 3),
        ("pwszVal", ctypes.c_void_p),
        ("padding", ctypes.c_void_p),
    ]


def _key(pid: int) -> _PROPERTYKEY:
    return _PROPERTYKEY(_GUID.parse(_FMTID_APPUSERMODEL), pid)


def set_process_app_id(shell32: object | None = None) -> bool:
    """Fija el AUMID del proceso. Hay que llamarlo antes de abrir ninguna ventana."""
    if shell32 is None:
        if sys.platform != "win32":
            return False
        shell32 = ctypes.WinDLL("shell32")
    try:
        result = shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID))
    except (OSError, AttributeError):
        log.warning("No se pudo fijar el identificador de la app en Windows")
        return False
    return result == 0  # S_OK


class _PropertyStore:
    """Envoltorio mínimo de un ``IPropertyStore`` (se libera con ``close``)."""

    def __init__(self, pointer: ctypes.c_void_p) -> None:
        self._ptr = pointer

    def _slot(self, index: int) -> int:
        vtable = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return vtable[index]

    def _method(self, index: int, *argtypes: object) -> object:
        # restype HRESULT: si el método falla, ctypes lanza OSError.
        prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        return prototype(self._slot(index))

    def set_text(self, pid: int, value: str) -> None:
        buffer = ctypes.create_unicode_buffer(value)
        variant = _PROPVARIANT(vt=_VT_LPWSTR, pwszVal=ctypes.cast(buffer, ctypes.c_void_p))
        key = _key(pid)
        # SetValue copia el valor: el buffer puede liberarse al salir.
        self._method(_SET_VALUE, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
            self._ptr, ctypes.byref(key), ctypes.byref(variant)
        )

    def get_text(self, pid: int) -> str | None:
        variant = _PROPVARIANT()
        key = _key(pid)
        self._method(_GET_VALUE, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
            self._ptr, ctypes.byref(key), ctypes.byref(variant)
        )
        if variant.vt != _VT_LPWSTR or not variant.pwszVal:
            return None
        text = ctypes.wstring_at(variant.pwszVal)
        ctypes.WinDLL("ole32").PropVariantClear(ctypes.byref(variant))
        return text

    def commit(self) -> None:
        self._method(_COMMIT)(self._ptr)

    def close(self) -> None:
        if self._ptr:
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(self._slot(_RELEASE))
            release(self._ptr)
            self._ptr = ctypes.c_void_p()


def _shell32() -> ctypes.WinDLL:
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetPropertyStoreFromParsingName.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetPropertyStoreFromParsingName.restype = ctypes.HRESULT
    shell32.SHGetPropertyStoreForWindow.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.HRESULT
    return shell32


def _open_file_store(path: Path) -> _PropertyStore:
    ctypes.WinDLL("ole32").CoInitialize(None)  # ya iniciado en este hilo = S_FALSE, sin daño
    pointer = ctypes.c_void_p()
    _shell32().SHGetPropertyStoreFromParsingName(
        str(path), None, _GPS_READWRITE, _GUID.parse(_IID_IPROPERTYSTORE), ctypes.byref(pointer)
    )  # restype HRESULT: si falla, ctypes lanza OSError
    return _PropertyStore(pointer)


def set_shortcut_app_id(path: Path) -> bool:
    """Marca un acceso directo (.lnk) con el AUMID de Luzeira."""
    if sys.platform != "win32":
        return False
    try:
        store = _open_file_store(path)
        try:
            store.set_text(_PID_ID, APP_ID)
            store.commit()
        finally:
            store.close()
    except OSError:
        log.warning("No se pudo marcar el acceso directo con el identificador de la app")
        return False
    return True


def read_shortcut_app_id(path: Path) -> str | None:
    """El AUMID que lleva un acceso directo (para las pruebas)."""
    store = _open_file_store(path)
    try:
        return store.get_text(_PID_ID)
    finally:
        store.close()


def set_window_relaunch(hwnd: int, command: str, icon: Path) -> bool:
    """Dice a Windows cómo volver a abrir Luzeira si se ancla esta ventana.

    Sin esto, la versión portable (sin acceso en el menú Inicio) se anclaría como
    «pythonw». ``command`` es la línea completa: ejecutable entre comillas + argumentos.
    """
    if sys.platform != "win32":
        return False
    try:
        pointer = ctypes.c_void_p()
        _shell32().SHGetPropertyStoreForWindow(
            hwnd, _GUID.parse(_IID_IPROPERTYSTORE), ctypes.byref(pointer)
        )
        store = _PropertyStore(pointer)
        try:
            # El ID primero; los de relanzar no valen sin él.
            store.set_text(_PID_ID, APP_ID)
            store.set_text(_PID_RELAUNCH_COMMAND, command)
            store.set_text(_PID_RELAUNCH_NAME, DISPLAY_NAME)
            store.set_text(_PID_RELAUNCH_ICON, f"{icon},0")
        finally:
            store.close()
    except OSError:
        log.warning("No se pudo decir a Windows cómo volver a abrir el editor")
        return False
    return True
