"""Drivers de pantallas soportadas.

- ``turing_rev_a``: Turing / UsbPCMonitor 3,5" (revisión A). Probada.
- ``xuanfang_rev_b``: XuanFang 3,5" y «flagship» (revisión B). **Beta.**
- ``kipye_rev_d``: Kipye Qiye 3,5". **Beta.**
- La revisión C (Turing 2,1"/5"/8,8") se reconoce pero no tiene driver (C-44).

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
