"""Qué driver abre qué pantalla: sola (detección por USB y serie) o la que elige el usuario.

Ninguna función de aquí manda bytes a un puerto que la detección no haya identificado o
que el usuario no haya elegido. Y nunca se encadena probando modelos: una elección = un
modelo = una prueba.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from luzeira.idioma import t
from luzeira.pantallas.comun import (
    ChosenDisplayMissing,
    ConfirmationNeeded,
    DisplayError,
    ModelToConfirm,
    SerialFactory,
    UnsupportedModel,
    WrongModel,
)
from luzeira.pantallas.kipye_rev_d import KipyeRevD
from luzeira.pantallas.modelos import (
    KIPYE,
    REV_A,
    REV_B,
    REV_C,
    VID_PID_CH552,
    DisplayChoice,
    PortInfo,
    identify,
    list_ports,
    match_choice,
    model_by_key,
)
from luzeira.pantallas.turing_rev_a import TuringRevA
from luzeira.pantallas.xuanfang_rev_b import XuanFangRevB

MSG_NOT_FOUND = "pantallas.no_encuentro"  # clave del catálogo (el mensaje de la rev A)


def open_auto(
    ports: Iterable | None = None,
    *,
    serial_factory: SerialFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
):
    """Un intento de abrir la pantalla que haya, sin preguntar si no hace falta.

    Por orden:

    1. Una rev A reconocida por su serie → ``TuringRevA.open(None, retries=1)``, lo mismo
       que se hacía antes de C-44 (busca ella sola su puerto).
    2. Una rev B reconocida por su serie → su driver, que saluda y comprueba la respuesta.
    3. Un ``1a86:5722`` sin serie conocido → :class:`ConfirmationNeeded` (rev A o rev B:
       decide el usuario), como antes.
    4. Una Kipye → :class:`ModelToConfirm`: solo por su USB, la confirma el usuario
       (el servicio sigue buscando mientras: si aparece una rev A, se abre sola).
    5. Una rev C → :class:`UnsupportedModel`.
    6. Nada → el mismo error de siempre.

    En 3, 4 y 5 no se abre ningún puerto.
    """
    found = [(p, identify(p)) for p in list_ports(ports)]
    found = [(p, ident) for p, ident in found if ident is not None]

    def first(model, *, sure: bool) -> PortInfo | None:
        return next((p for p, i in found if i.model is model and i.sure is sure), None)

    if first(REV_A, sure=True) is not None:
        return TuringRevA.open(None, retries=1)
    port = first(REV_B, sure=True)
    if port is not None:
        return XuanFangRevB.open(port.device, serial_factory=serial_factory, sleep=sleep)
    port = first(None, sure=False)
    if port is not None:
        raise ConfirmationNeeded(port.device)
    port = first(KIPYE, sure=False)
    if port is not None:
        raise ModelToConfirm(DisplayChoice.for_port(KIPYE, port), KIPYE.label)
    port = next((p for p, i in found if i.model is REV_C), None)
    if port is not None:
        raise UnsupportedModel(port.device, REV_C.label)
    raise DisplayError(t(MSG_NOT_FOUND))


def open_chosen(
    choice: DisplayChoice,
    ports: Iterable | None = None,
    *,
    serial_factory: SerialFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
):
    """Abre la pantalla que eligió el usuario, con el driver de ese modelo y nada más.

    Antes se revalida contra lo que Windows dice ahora (:func:`match_choice`); si ya no
    coincide, :class:`ChosenDisplayMissing` sin abrir nada. Si la pantalla contesta otra
    cosa que ese modelo, :class:`WrongModel` con el puerto ya cerrado.

    La pantalla devuelta lleva ``verified``: False si su primera prueba no ha podido
    demostrar que es ese modelo (la Kipye, que no tiene saludo, o una rev A que no
    contesta en un puerto que no es ``1a86:5722``). Esa elección no se recuerda sin que
    el usuario confirme que se ve bien.
    """
    model = model_by_key(choice.model)
    port = match_choice(choice, list_ports(ports))
    if model is None or port is None:
        raise ChosenDisplayMissing()
    if model is REV_A:
        display = TuringRevA.open(port.device, retries=1, serial_factory=serial_factory)
        # El Turing oficial no contesta al saludo y las UsbPCMonitor contestan 6 bytes
        # iguales. Cualquier otra cosa es que ahí no hay una rev A.
        answer = display.hello_answer
        if answer and not (len(answer) == 6 and len(set(answer)) == 1):
            display.close()
            raise WrongModel(port.device, REV_A.label)
        # Sin respuesta solo cuenta como rev A con su USB (el Turing oficial no contesta):
        # en otro puerto, el silencio no demuestra nada.
        display.verified = bool(answer) or port.usb_id == VID_PID_CH552
        return display
    if model is REV_B:
        return XuanFangRevB.open(port.device, serial_factory=serial_factory, sleep=sleep)
    if model is KIPYE:
        return KipyeRevD.open(port.device, serial_factory=serial_factory, sleep=sleep)
    raise ChosenDisplayMissing()  # modelo sin driver: no se llega (match_choice lo filtra)
