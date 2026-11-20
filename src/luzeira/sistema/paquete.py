"""¿Corre Luzeira como paquete MSIX (la versión de Microsoft Store)? (C-52)

Concepto: una app empaquetada tiene «identidad de paquete», y Windows la trata distinto:
lo que escribe en ``HKCU`` y las carpetas nuevas de ``AppData`` quedan aislados del
resto del sistema (medido en el prototipo, 2026-11-20). Por eso, dentro del paquete:

- «Iniciar con Windows» no puede usar la clave ``Run`` (Windows no la vería): se usa la
  tarea de inicio del manifiesto, que el usuario activa o desactiva en Configuración.
- El acceso del escritorio no sirve: apuntaría a ``WindowsApps``, y lanzar eso desde
  fuera del paquete da «Acceso denegado».
- El actualizador sobra: la Store actualiza sola.

Se pregunta con ``GetCurrentPackageFullName``: sin paquete devuelve
``APPMODEL_ERROR_NO_PACKAGE`` (15700); con paquete y búfer de tamaño 0,
``ERROR_INSUFFICIENT_BUFFER`` (122).

Fuente: «GetCurrentPackageFullName», Microsoft Learn (consultada 2026-11-20),
https://learn.microsoft.com/en-us/windows/win32/api/appmodel/nf-appmodel-getcurrentpackagefullname
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable

log = logging.getLogger(__name__)

APPMODEL_ERROR_NO_PACKAGE = 15700
ERROR_INSUFFICIENT_BUFFER = 122
#: Página de Configuración donde se activan las apps que arrancan con Windows.
STARTUP_SETTINGS_URI = "ms-settings:startupapps"


def _package_name_length() -> int:
    """Llama a GetCurrentPackageFullName con búfer vacío y devuelve su código."""
    length = ctypes.c_uint32(0)
    return ctypes.WinDLL("kernel32").GetCurrentPackageFullName(ctypes.byref(length), None)


def is_packaged(probe: Callable[[], int] | None = None) -> bool:
    """``True`` si este proceso tiene identidad de paquete MSIX.

    ``probe`` es para los tests (devuelve el código de Windows). Ante cualquier duda
    (otro sistema, función ausente, código raro) se responde ``False``: la versión normal.
    """
    if probe is None:
        if sys.platform != "win32":
            return False
        probe = _package_name_length
    try:
        code = probe()
    except (AttributeError, OSError):  # Windows sin la función: no hay paquetes
        return False
    if code == ERROR_INSUFFICIENT_BUFFER:
        return True
    if code != APPMODEL_ERROR_NO_PACKAGE:
        log.warning("GetCurrentPackageFullName devolvió %s: se sigue como versión normal", code)
    return False
