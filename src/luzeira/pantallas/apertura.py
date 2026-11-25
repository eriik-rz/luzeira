"""Qué driver abre qué pantalla: sola (detección por USB y serie) o la que elige el usuario.

Ninguna función de aquí manda bytes a un puerto que la detección no haya identificado o
que el usuario no haya elegido. Y nunca se encadena probando modelos: una elección = un
modelo = una prueba.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from luzeira.idioma import Text
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
from luzeira.pantallas.turing_rev_a import Command, TuringRevA
from luzeira.pantallas.xuanfang_rev_b import XuanFangRevB

MSG_NOT_FOUND = "pantallas.no_encuentro"  # clave del catálogo (el mensaje de la rev A)

# Lo que contesta al saludo una UsbPCMonitor de verdad: 6 bytes iguales a 0x01 (3,5"),
# 0x02 (5") o 0x03 (7"). El Turing oficial no contesta nada.
REV_A_HELLO_ANSWERS = frozenset({0x01, 0x02, 0x03})


def check_rev_a_answer(display: TuringRevA, usb_id: tuple[int, int] | None) -> bool:
    """¿Lo que ha contestado al saludo un puerto que el usuario dice que es una rev A lo
    demuestra? Devuelve ``verified``; si demuestra lo contrario, cierra y :class:`WrongModel`.

    - 6 bytes iguales a 0x01-0x03: una UsbPCMonitor. Verificada.
    - 🔴 Su propio saludo de vuelta (6 x 0x45): eso es un eco (un cable o un aparato que
      devuelve lo que recibe), no una pantalla (R5-06). Otra cosa que no tiene saludo.
    - Otros 6 bytes iguales: no es la respuesta de ningún modelo conocido, pero tampoco
      demuestra que no lo sea. Sin verificar: el usuario confirma que se ve bien.
    - Nada: el Turing oficial no contesta, así que solo cuenta con el USB de la rev A.
    - Cualquier otra cosa: ahí no hay una rev A.
    """
    answer = display.hello_answer
    device = display.device or "?"
    if not answer:
        return usb_id == VID_PID_CH552
    if len(answer) == 6 and len(set(answer)) == 1 and answer[0] != Command.HELLO:
        return answer[0] in REV_A_HELLO_ANSWERS
    display.close()
    raise WrongModel(device, REV_A.label_text)


def open_confirmed_rev_a(
    device: str,
    ports: Iterable | None = None,
    *,
    serial_factory: SerialFactory | None = None,
):
    """Abre como rev A el puerto que el usuario ha confirmado (un ``1a86:5722`` sin serie
    conocido) y comprueba su saludo como la elegida a mano (R5-06): un eco o una respuesta
    que no es de una rev A → :class:`WrongModel`, con el puerto ya cerrado."""
    port = next((p for p in list_ports(ports) if p.device == device), None)
    display = TuringRevA.open(device, retries=1, serial_factory=serial_factory)
    display.verified = check_rev_a_answer(display, port.usb_id if port else None)
    return display


def open_auto(
    ports: Iterable | None = None,
    *,
    serial_factory: SerialFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
):
    """Un intento de abrir la pantalla que haya, sin preguntar si no hace falta.

    Por orden:

    1. Una rev A reconocida por su serie (y su USB) → ``TuringRevA.open`` de ese puerto,
       con la misma apertura y los mismos bytes que antes de C-44. Antes se le pasaba
       ``None`` y ella volvía a buscar su puerto en los puertos reales del equipo, sin mirar
       ``ports`` ni ``serial_factory`` (R5-08): ahora abre el que se ha identificado aquí.
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

    port = first(REV_A, sure=True)
    if port is not None:
        return TuringRevA.open(port.device, retries=1, serial_factory=serial_factory)
    port = first(REV_B, sure=True)
    if port is not None:
        return XuanFangRevB.open(port.device, serial_factory=serial_factory, sleep=sleep)
    port = first(None, sure=False)
    if port is not None:
        raise ConfirmationNeeded(port.device)
    port = first(KIPYE, sure=False)
    if port is not None:
        raise ModelToConfirm(DisplayChoice.for_port(KIPYE, port), KIPYE.label_text)
    port = next((p for p, i in found if i.model is REV_C), None)
    if port is not None:
        raise UnsupportedModel(port.device, REV_C.label_text)
    raise DisplayError(Text(MSG_NOT_FOUND))


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
        display.verified = check_rev_a_answer(display, port.usb_id)
        return display
    if model is REV_B:
        return XuanFangRevB.open(port.device, serial_factory=serial_factory, sleep=sleep)
    if model is KIPYE:
        return KipyeRevD.open(port.device, serial_factory=serial_factory, sleep=sleep)
    raise ChosenDisplayMissing()  # modelo sin driver: no se llega (match_choice lo filtra)
