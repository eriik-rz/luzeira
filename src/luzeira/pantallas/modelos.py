"""Catálogo cerrado de modelos y detección **sin enviar ni un byte**.

Todo lo de aquí trabaja con lo que Windows ya sabe de cada puerto COM (VID:PID del USB y
número de serie), leído con ``serial.tools.list_ports.comports()``, que **no abre** el
puerto. Qué número de serie corresponde a qué modelo sale del informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (hechos tomados del código
GPL-3.0 de turing-smart-screen-python y de los issues de sus usuarios).

Reglas (C-44):

- Rev A por su serie ``USB35INCHIPS…``: se conecta sola, como siempre.
- Rev B por su serie ``2017-2-25`` **en el campo serie** (es también el texto del
  *fabricante* de muchas rev A: ese campo no cuenta) y con el USB ``1a86:5722``.
- Kipye (``454d:4e41``): no tiene serie conocido, así que solo se propone; el usuario
  confirma.
- Rev C (``CT…INCH`` dormida, ``20080411`` despierta): se reconoce para decir que aún
  no es compatible; nunca se abre.
- Un ``1a86:5722`` sin serie conocido puede ser rev A o rev B: se pregunta.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from luzeira.idioma import Text, t

VID_PID_CH552 = (0x1A86, 0x5722)  # rev A, UsbPCMonitor y XuanFang rev B
VID_PID_KIPYE = (0x454D, 0x4E41)
REV_A_SERIAL_PREFIX = "USB35INCHIPS"
REV_B_SERIAL = "2017-2-25"
REV_C_SLEEPING_SERIALS = frozenset({"CT21INCH", "USB7INCH", "CT88INCH", "CT50INCH"})
REV_C_AWAKE_SERIAL = "20080411"
REV_C_SLEEPING_IDS = frozenset({(0x1A86, 0xCA21), (0x1A86, 0xCA50)})
# Ids genéricos de «gadget USB» de Linux: los usan también otros aparatos (una Raspberry
# Pi en modo gadget). Solo cuentan junto al serie ``20080411``.
REV_C_AWAKE_IDS = frozenset({(0x0525, 0xA4A7), (0x1D6B, 0x0121), (0x1D6B, 0x0106)})


@dataclass(frozen=True)
class DisplayModel:
    key: str  # lo que se guarda en la configuración
    # El nombre del modelo tal cual (los nombres no se traducen). Si lleva palabras
    # («también», «revisión»), va en ``label_key`` y el nombre de aquí es solo de reserva.
    name: str
    usb_ids: frozenset[tuple[int, int]]
    beta: bool  # sin probar todavía en una pantalla de verdad
    supported: bool = True  # False: se reconoce, pero no hay driver
    # True: su primera prueba es un saludo con respuesta, así que puede decir «ahí no hay
    # una de estas». False (Kipye): la prueba no puede fallar y no demuestra nada.
    has_hello: bool = True
    label_key: str | None = None  # clave del catálogo para el nombre, si lleva palabras

    @property
    def label(self) -> str:
        """Lo que ve el usuario, en el idioma activo (se traduce al pedirlo, no al
        importar: así cambia en cuanto se cambia de idioma)."""
        return t(self.label_key) if self.label_key else self.name

    @property
    def label_text(self) -> str:
        """Como ``label``, pero un :class:`~luzeira.idioma.Text` si tiene clave: para los
        mensajes de error, que se pueden reescribir si se cambia de idioma."""
        return Text(self.label_key) if self.label_key else self.name

    @property
    def menu_label(self) -> str:
        return t("pantallas.modelo_beta", model=self.label) if self.beta else self.label


# Los nombres llevan una medida con decimales: van al catálogo por el separador decimal
# («3,5"» en español, «3.5"» en inglés).
REV_A = DisplayModel(
    "turing-rev-a",
    'Turing / UsbPCMonitor 3,5"',
    frozenset({VID_PID_CH552}),
    beta=False,
    label_key="pantallas.modelo_rev_a",
)
REV_B = DisplayModel(
    "xuanfang-rev-b",
    'XuanFang 3,5"',
    frozenset({VID_PID_CH552}),
    beta=True,
    label_key="pantallas.modelo_xuanfang",
)
KIPYE = DisplayModel(
    "kipye-rev-d",
    'Kipye Qiye 3,5"',
    frozenset({VID_PID_KIPYE}),
    beta=True,
    has_hello=False,
    label_key="pantallas.modelo_kipye",
)
REV_C = DisplayModel(
    "turing-rev-c",
    'Turing 2,1"/5"/8,8"',
    REV_C_SLEEPING_IDS | REV_C_AWAKE_IDS,
    beta=True,
    supported=False,
    label_key="pantallas.modelo_rev_c",
)

MODELS: tuple[DisplayModel, ...] = (REV_A, REV_B, KIPYE, REV_C)
SUPPORTED: tuple[DisplayModel, ...] = tuple(m for m in MODELS if m.supported)


def model_by_key(key: str) -> DisplayModel | None:
    return next((m for m in MODELS if m.key == key), None)


@dataclass(frozen=True)
class PortInfo:
    """Lo que Windows dice de un puerto COM. Sin abrirlo."""

    device: str
    vid: int | None = None
    pid: int | None = None
    serial: str = ""
    description: str = ""

    @property
    def usb_id(self) -> tuple[int, int] | None:
        return (self.vid, self.pid) if self.vid is not None and self.pid is not None else None

    @property
    def label(self) -> str:
        """Nombre para el menú: la descripción de Windows, que ya trae «(COM3)»."""
        text = self.description.strip()
        if not text:
            return self.device
        return text if self.device in text else f"{text} ({self.device})"


@dataclass(frozen=True)
class DisplayChoice:
    """Modelo + puerto elegidos (o propuestos). Se recuerda con el USB del puerto para
    revalidarla en cada arranque: el número de COM puede cambiar, el USB no."""

    model: str  # clave de :data:`MODELS`
    device: str
    vid: int | None = None
    pid: int | None = None
    serial: str = ""
    advanced: bool = False  # elegida en «Modo avanzado» (puerto o modelo fuera de la lista)

    @classmethod
    def for_port(cls, model: DisplayModel, port: PortInfo, *, advanced: bool = False):
        return cls(model.key, port.device, port.vid, port.pid, port.serial, advanced)


def list_ports(ports: Iterable | None = None) -> list[PortInfo]:
    """Los puertos COM del equipo, sin abrir ninguno."""
    if ports is None:
        from serial.tools.list_ports import comports

        ports = comports()
    return [
        PortInfo(
            device=str(p.device),
            vid=getattr(p, "vid", None),
            pid=getattr(p, "pid", None),
            serial=getattr(p, "serial_number", None) or "",
            description=getattr(p, "description", None) or "",
        )
        for p in ports
    ]


@dataclass(frozen=True)
class Identification:
    model: DisplayModel | None  # None: un 1a86:5722 que puede ser rev A o rev B
    sure: bool  # True: el número de serie lo identifica sin duda


def identify(port: PortInfo) -> Identification | None:
    """¿Qué pantalla hay en ``port``? Solo con su USB y su serie. None: ninguna conocida.

    ⚠ El serie va en ``PortInfo.serial`` (``serial_number`` de pyserial, la última parte
    de ``USB\\VID_1A86&PID_5722\\<serie>``). El fabricante USB no se mira nunca: en muchas
    rev A es justo ``2017-2-25``, el serie de la rev B.
    """
    if port.serial.startswith(REV_A_SERIAL_PREFIX):
        return Identification(REV_A, sure=True)  # igual que ``find_port`` de la rev A
    usb_id = port.usb_id
    if usb_id == VID_PID_CH552:
        if port.serial == REV_B_SERIAL:
            return Identification(REV_B, sure=True)
        return Identification(None, sure=False)
    if usb_id == VID_PID_KIPYE:
        return Identification(KIPYE, sure=False)
    if port.serial in REV_C_SLEEPING_SERIALS or usb_id in REV_C_SLEEPING_IDS:
        return Identification(REV_C, sure=port.serial in REV_C_SLEEPING_SERIALS)
    if usb_id in REV_C_AWAKE_IDS and port.serial == REV_C_AWAKE_SERIAL:
        return Identification(REV_C, sure=True)
    return None


def compatible_models(port: PortInfo) -> list[DisplayModel]:
    """Lista cerrada para «Elegir modelo y puerto»: los modelos con driver cuyo USB
    coincide con el del puerto. Un puerto sin USB (Bluetooth, módem…) no tiene ninguno.

    Si el número de serie ya identifica el modelo sin duda (una rev A con
    ``USB35INCHIPS…``), solo ese: ofrecer otro sería invitar a mandarle un protocolo
    que no es el suyo. Los demás quedan para el «Modo avanzado».
    """
    usb_id = port.usb_id
    if usb_id is None:
        return []
    found = identify(port)
    if found is not None and found.sure and found.model is not None:
        return [found.model] if found.model.supported else []
    return [m for m in SUPPORTED if usb_id in m.usb_ids]


def match_choice(choice: DisplayChoice, ports: Iterable[PortInfo]) -> PortInfo | None:
    """El puerto donde sigue estando la pantalla elegida, o None si ya no coincide.

    Con USB: mismo VID:PID y mismo serie (el COM puede haber cambiado; si hay varios
    iguales, se prefiere el mismo COM). Sin USB (solo en modo avanzado): el mismo COM y
    que siga sin USB.
    """
    model = model_by_key(choice.model)
    if model is None or not model.supported:
        return None
    ports = list(ports)
    if choice.vid is None or choice.pid is None:
        return next((p for p in ports if p.device == choice.device and p.usb_id is None), None)
    usb_id = (choice.vid, choice.pid)
    if not choice.advanced and usb_id not in model.usb_ids:
        return None  # una elección normal tiene que seguir siendo de la lista cerrada
    same = [p for p in ports if p.usb_id == usb_id and p.serial == choice.serial]
    if not choice.advanced:  # …y del puerto concreto (p. ej. no una rev B sobre una rev A)
        same = [p for p in same if model in compatible_models(p)]
    return next((p for p in same if p.device == choice.device), same[0] if same else None)
