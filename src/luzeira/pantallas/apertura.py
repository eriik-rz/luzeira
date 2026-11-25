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
    ModelToChoose,
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
    REV_C_SIZES,
    VID_PID_CH552,
    WEACT,
    WEACT_MODELS,
    DisplayChoice,
    PortInfo,
    identify,
    is_rev_c_awake,
    is_rev_c_bridge,
    list_ports,
    match_choice,
    model_by_key,
    rev_c_size,
)
from luzeira.pantallas.turing_rev_a import Command, TuringRevA
from luzeira.pantallas.turing_rev_c import SIZES as REV_C_DRIVER_SIZES
from luzeira.pantallas.turing_rev_c import TuringRevC, wake
from luzeira.pantallas.weact import PANELS as WEACT_PANELS
from luzeira.pantallas.weact import WeAct
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
    5. Una rev C → :class:`ModelToConfirm` con su tamaño si el serie de su puente lo dice
       (como la Kipye: la confirma el usuario); si no, :class:`ModelToChoose` (que la
       elija con su tamaño en el menú). Una que solo lo parece por su serie, con otro USB
       → :class:`UnsupportedModel`.
    6. Una WeAct → :class:`ModelToConfirm` si el prefijo del serie dice el tamaño (beta,
       con USB y serie sacados solo de código ajeno: la confirma el usuario); si no,
       :class:`ModelToChoose`.
    7. Nada → el mismo error de siempre.

    En 3 a 6 no se abre ningún puerto (ni siquiera para despertar la rev C).
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
    rev_c = [p for p, i in found if i.model is REV_C]
    # El puente primero: es el que se ve siempre, duerma o no, y su serie puede decir el
    # tamaño. Despierta, Windows ve además el otro aparato (los dos a la vez, #1030).
    port = next((p for p in rev_c if is_rev_c_bridge(p)), None)
    port = port or next((p for p in rev_c if is_rev_c_awake(p)), None)
    if port is not None:
        size = rev_c_size(port)
        if size is not None:
            raise ModelToConfirm(DisplayChoice.for_port(size, port), size.label_text)
        raise ModelToChoose(port.device, REV_C.label_text)
    if rev_c:
        raise UnsupportedModel(rev_c[0].device, REV_C.label_text)
    port = next((p for p, i in found if i.model in WEACT_MODELS), None)
    if port is not None:
        model = identify(port).model
        raise ModelToConfirm(DisplayChoice.for_port(model, port), model.label_text)
    port = next((p for p, i in found if i.model is WEACT), None)
    if port is not None:
        raise ModelToChoose(port.device, WEACT.label_text)
    raise DisplayError(Text(MSG_NOT_FOUND))


def open_chosen(
    choice: DisplayChoice,
    ports: Iterable | None = None,
    *,
    serial_factory: SerialFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
    relist: Callable[[], Iterable] | None = None,
):
    """Abre la pantalla que eligió el usuario, con el driver de ese modelo y nada más.

    Antes se revalida contra lo que Windows dice ahora (:func:`match_choice`); si ya no
    coincide, :class:`ChosenDisplayMissing` sin abrir nada. Si la pantalla contesta otra
    cosa que ese modelo, :class:`WrongModel` con el puerto ya cerrado.

    La pantalla devuelta lleva ``verified``: False si su primera prueba no ha podido
    demostrar que es ese modelo (la Kipye, que no tiene saludo, o una rev A que no
    contesta en un puerto que no es ``1a86:5722``). Esa elección no se recuerda sin que
    el usuario confirme que se ve bien. La rev C siempre: su saludo no dice el tamaño.

    ``relist`` vuelve a listar los puertos (por defecto, los de Windows o ``ports`` si se
    dan): la rev C cambia de puerto al despertar.
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
    if model in WEACT_MODELS:
        return WeAct.open(
            port.device, WEACT_PANELS[model.key], serial_factory=serial_factory, sleep=sleep
        )
    if model in REV_C_SIZES:
        if relist is None:
            relist = (lambda: ports) if ports is not None else (lambda: None)
        return open_rev_c(
            choice,
            port,
            lambda: list_ports(relist()),
            serial_factory=serial_factory,
            sleep=sleep,
        )
    raise ChosenDisplayMissing()  # modelo sin driver: no se llega (match_choice lo filtra)


def open_rev_c(
    choice: DisplayChoice,
    port: PortInfo,
    list_now: Callable[[], list[PortInfo]],
    *,
    serial_factory: SerialFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TuringRevC:
    """Abre la rev C elegida (``port`` es el que ha dado :func:`match_choice`).

    🔴 Al aparato despierto (ids genéricos de gadget de Linux) solo se le manda algo si es
    seguro que es el de esta pantalla (R5, punto 6):

    - Se eligió el propio aparato despierto: ``port`` ya lo es (mismo COM y mismos ids).
    - Se eligió el puente y el COM guardado en la elección es un aparato despierto que
      sigue ahí: es el que Luzeira despertó y comprobó la otra vez (al recordar la elección
      se guarda el COM con el que se abrió). Despierta, la pantalla deja ver los dos.
    - Si no, se despierta (:func:`~luzeira.pantallas.turing_rev_c.wake`) y vale solo el
      aparato que aparece a raíz de eso. Uno que ya estaba no cuenta.

    - Se eligió el puente y en **esta sesión** Luzeira ya despertó y comprobó (con su
      ``chs_``) un aparato despierto suyo que sigue ahí con el mismo COM, USB y serie
      (:data:`AWAKE_THIS_SESSION`). Sin esto, volver a elegirla por su puente (otro
      tamaño tras «No se ve bien», una conexión perdida, la vuelta del reposo) la
      despertaba otra vez, no aparecía nada nuevo y se pedía desenchufarla (revisor de la
      fase 5).

    Y luego el saludo tiene que contestar ``chs_…``: si no, :class:`WrongModel` (y se
    olvida ese despierto).
    """
    size = REV_C_DRIVER_SIZES[choice.model]
    bridge = _identity(port)
    device = port.device
    if is_rev_c_bridge(port):
        awake = [p for p in list_now() if is_rev_c_awake(p)]
        known = AWAKE_THIS_SESSION.get(bridge)
        found = next((p for p in awake if p.device == choice.device), None)
        found = found or next((p for p in awake if _identity(p) == known), None)
        if found is not None:
            device = found.device
        else:
            device = wake(port.device, list_now, serial_factory=serial_factory, sleep=sleep)
    try:
        display = TuringRevC.open(device, size, serial_factory=serial_factory, sleep=sleep)
    except WrongModel:
        AWAKE_THIS_SESSION.pop(bridge, None)
        raise
    if is_rev_c_bridge(port):
        awake_now = next((p for p in list_now() if p.device == device), None)
        if awake_now is not None and is_rev_c_awake(awake_now):
            AWAKE_THIS_SESSION[bridge] = _identity(awake_now)
    return display


# Puente de rev C → el aparato despierto que Luzeira despertó (o reconoció) y que contestó
# ``chs_`` en esta sesión; cada uno por su COM, USB y serie. Solo en memoria: al cerrar
# Luzeira se pierde (lo que dura entre arranques es el COM guardado en la elección).
AWAKE_THIS_SESSION: dict[tuple, tuple] = {}


def _identity(port: PortInfo) -> tuple:
    return (port.device, port.usb_id, port.serial)
