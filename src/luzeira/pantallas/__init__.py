"""Drivers de pantallas soportadas.

- ``turing_rev_a``: Turing / UsbPCMonitor 3,5" (revisión A). Probada.
- ``xuanfang_rev_b``: XuanFang 3,5" y «flagship» (revisión B). **Beta.**
- ``kipye_rev_d``: Kipye Qiye 3,5". **Beta.**
- ``turing_rev_c``: Turing 2,1"/2,8", 5" y 8,8" (revisión C). **Beta.** Duerme y cambia de
  USB al despertar: la despierta ``apertura``.
- ``weact``: WeAct Display FS 3,5" y 0,96". **Beta.**

``modelos`` es el catálogo y la detección sin enviar bytes; ``apertura`` decide qué
driver abre qué puerto.
"""

from luzeira.pantallas.comun import (
    ChosenDisplayMissing,
    ConfirmationNeeded,
    DisplayBusy,
    DisplayError,
    ModelToConfirm,
    NoAnswer,
    Orientation,
    UnsupportedModel,
    WrongModel,
)
from luzeira.pantallas.modelos import DisplayChoice
from luzeira.pantallas.turing_rev_a import TuringRevA, find_port

__all__ = [
    "ChosenDisplayMissing",
    "ConfirmationNeeded",
    "DisplayBusy",
    "DisplayChoice",
    "DisplayError",
    "ModelToConfirm",
    "NoAnswer",
    "Orientation",
    "TuringRevA",
    "UnsupportedModel",
    "WrongModel",
    "find_port",
]
