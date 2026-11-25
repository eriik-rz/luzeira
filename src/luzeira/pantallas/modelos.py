"""Catálogo cerrado de modelos y detección **sin enviar ni un byte**.

Todo lo de aquí trabaja con lo que Windows ya sabe de cada puerto COM (VID:PID del USB y
número de serie), leído con ``serial.tools.list_ports.comports()``, que **no abre** el
puerto. Qué número de serie corresponde a qué modelo sale del informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (hechos tomados del código
GPL-3.0 de turing-smart-screen-python y de los issues de sus usuarios).

Reglas (C-44):

- Rev A por su serie ``USB35INCHIPS…`` **y** su USB ``1a86:5722``: se conecta sola, como
  siempre. El serie solo no basta: cualquier aparato puede anunciar ese texto (R5-07).
- Rev B por su serie ``2017-2-25`` **en el campo serie** (es también el texto del
  *fabricante* de muchas rev A: ese campo no cuenta) y con el USB ``1a86:5722``.
- Kipye (``454d:4e41``): no tiene serie conocido, así que solo se propone; el usuario
  confirma.
- Rev C (beta): se reconoce por su «puente» USB (``1a86:ca21``/``ca50``, el que se ve
  mientras duerme, serie ``CT…INCH``) o por el aparato despierto (ids de gadget de Linux
  **y** serie ``20080411``). Su saludo no dice el tamaño, así que hay un modelo por tamaño
  (2,1"/2,8", 5" y 8,8") y lo elige el usuario; ``REV_C`` es la familia, para reconocerla.
  Nunca se abre sola: como mucho se propone (si el serie del puente dice el tamaño).
- WeAct Display FS (beta): ``1a86:fe0c`` **y** el prefijo del serie (``AB`` 3,5",
  ``AD`` 0,96"). Se propone (el usuario confirma); con otro prefijo, que elija el tamaño.
- Un ``1a86:5722`` sin serie conocido puede ser rev A o rev B: se pregunta.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from luzeira.idioma import Text, t

VID_PID_CH552 = (0x1A86, 0x5722)  # rev A, UsbPCMonitor y XuanFang rev B
VID_PID_KIPYE = (0x454D, 0x4E41)
# WeAct Display FS: el mismo USB en los dos tamaños; el serie empieza por «AB» (3,5") o
# «AD» (0,96"). Fuente: ``lcd_comm_weact_a.py``/``_b.py`` de turing-smart-screen-python
# (commit 36ebc0a), resumido en ``docs/investigacion/tanda-11/weact-y-otras/informe.md``.
# Sale del código, no de un volcado USB real [SIN VERIFICAR].
VID_PID_WEACT = (0x1A86, 0xFE0C)
WEACT_SERIAL_PREFIXES = {"AB": "weact-35", "AD": "weact-096"}
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
# La familia rev C, para reconocerla (``identify``) y nombrarla. No se abre con este
# modelo (``supported=False``): sin tamaño no hay driver. Se abre con uno de los tres de
# abajo, que tienen el driver de ``turing_rev_c``.
REV_C = DisplayModel(
    "turing-rev-c",
    'Turing 2,1"/5"/8,8"',
    REV_C_SLEEPING_IDS | REV_C_AWAKE_IDS,
    beta=True,
    supported=False,
    label_key="pantallas.modelo_rev_c",
)
# Las claves no llevan puntos: el patrón de ``config.DisplaySetting.model`` no los admite.
REV_C_21 = DisplayModel(
    "turing-rev-c-21",
    'Turing 2,1"/2,8"',
    REV_C.usb_ids,
    beta=True,
    label_key="pantallas.modelo_rev_c_21",
)
REV_C_5 = DisplayModel(
    "turing-rev-c-5", 'Turing 5"', REV_C.usb_ids, beta=True, label_key="pantallas.modelo_rev_c_5"
)
REV_C_88 = DisplayModel(
    "turing-rev-c-88",
    'Turing 8,8"',
    REV_C.usb_ids,
    beta=True,
    label_key="pantallas.modelo_rev_c_88",
)
REV_C_SIZES: tuple[DisplayModel, ...] = (REV_C_21, REV_C_5, REV_C_88)
# Qué tamaño dice el serie del puente. Solo los que tienen una fuente:
# - ``CT50INCH``: un 5" de Lian Li que contesta «chs_5inch» (issue #1030 de TSP).
# - ``USB7INCH`` (5", a pesar del nombre) y ``CT88INCH`` (8,8"): Libre Panel
#   (``turing_rev_c.rs``); la referencia no asocia series a tamaños [SIN VERIFICAR].
# - ``CT21INCH`` no dice nada: lo llevan el 2,1", el 2,8" redondo de SAMA (#1022) y
#   también un 5" (#1075, #1022). Ahí elige el usuario.
REV_C_SIZE_BY_SERIAL = {"CT50INCH": REV_C_5, "USB7INCH": REV_C_5, "CT88INCH": REV_C_88}

# WeAct: la familia (para reconocer un ``fe0c`` con un serie desconocido) y los dos
# tamaños, que son los que tienen driver (``weact``).
WEACT = DisplayModel(
    "weact",
    "WeAct Display FS",
    frozenset({VID_PID_WEACT}),
    beta=True,
    supported=False,
    label_key="pantallas.modelo_weact",
)
WEACT_35 = DisplayModel(
    "weact-35",
    'WeAct Display FS 3,5"',
    frozenset({VID_PID_WEACT}),
    beta=True,
    label_key="pantallas.modelo_weact_35",
)
WEACT_096 = DisplayModel(
    "weact-096",
    'WeAct Display FS 0,96"',
    frozenset({VID_PID_WEACT}),
    beta=True,
    label_key="pantallas.modelo_weact_096",
)
WEACT_MODELS: tuple[DisplayModel, ...] = (WEACT_35, WEACT_096)

MODELS: tuple[DisplayModel, ...] = (
    REV_A,
    REV_B,
    KIPYE,
    *REV_C_SIZES,
    REV_C,
    *WEACT_MODELS,
    WEACT,
)
SUPPORTED: tuple[DisplayModel, ...] = tuple(m for m in MODELS if m.supported)
# Lo que el «Modo avanzado» puede mandar a cualquier puerto. Ni la rev C ni la WeAct: sus
# modelos solo casan con su propio USB (``match_choice``), así que ofrecerlas en otro
# puerto sería una opción muerta, y mandarlas a otro aparato, lo que R5-01/R5-02 prohíben.
ADVANCED: tuple[DisplayModel, ...] = tuple(
    m for m in SUPPORTED if m not in REV_C_SIZES and m not in WEACT_MODELS
)


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

    🔴 El serie de la rev A solo cuenta con su USB ``1a86:5722`` (R5-07): un aparato
    cualquiera (un Arduino) puede anunciar ``USB35INCHIPS…`` en su descriptor. Con otro USB
    no se reconoce como nada: ni se abre solo ni sale en la lista cerrada, solo en el modo
    avanzado (donde elegirlo es decisión explícita del usuario, con aviso).

    ⚠ El serie va en ``PortInfo.serial`` (``serial_number`` de pyserial, la última parte
    de ``USB\\VID_1A86&PID_5722\\<serie>``). El fabricante USB no se mira nunca: en muchas
    rev A es justo ``2017-2-25``, el serie de la rev B.
    """
    usb_id = port.usb_id
    if usb_id == VID_PID_CH552:
        if port.serial.startswith(REV_A_SERIAL_PREFIX):
            return Identification(REV_A, sure=True)  # igual que ``find_port`` de la rev A
        if port.serial == REV_B_SERIAL:
            return Identification(REV_B, sure=True)
        return Identification(None, sure=False)
    if usb_id == VID_PID_WEACT:
        model = model_by_key(WEACT_SERIAL_PREFIXES.get(port.serial[:2], ""))
        return Identification(model or WEACT, sure=model is not None)
    if usb_id == VID_PID_KIPYE:
        return Identification(KIPYE, sure=False)
    if port.serial in REV_C_SLEEPING_SERIALS or usb_id in REV_C_SLEEPING_IDS:
        return Identification(REV_C, sure=port.serial in REV_C_SLEEPING_SERIALS)
    if usb_id in REV_C_AWAKE_IDS and port.serial == REV_C_AWAKE_SERIAL:
        return Identification(REV_C, sure=True)
    return None


def is_rev_c(port: PortInfo) -> bool:
    """¿Es una rev C (o lo parece por su USB o su serie)? Es el único protocolo de la
    familia con órdenes que borran o reinician (R5-02): a un puerto así no se le manda el
    protocolo de ningún otro modelo, ni en el modo avanzado."""
    found = identify(port)
    return found is not None and found.model is REV_C


def is_rev_c_bridge(port: PortInfo) -> bool:
    """¿Es el puente USB de una rev C (lo que Windows ve mientras duerme)? Por su USB: el
    serie ``CT…INCH`` con otro VID:PID no basta (cualquiera puede anunciarlo, R5-07)."""
    return port.usb_id in REV_C_SLEEPING_IDS


def is_weact(port: PortInfo) -> bool:
    """¿Tiene el USB de una WeAct (``1a86:fe0c``), con el serie que sea?"""
    return port.usb_id == VID_PID_WEACT


def is_own_usb_only(port: PortInfo) -> bool:
    """¿Es un puerto al que solo se le manda el protocolo de su propio modelo, ni en el
    modo avanzado? La rev C (R5-02: tiene órdenes que borran) y la WeAct (sin marco: el
    protocolo de otro modelo se leería como píxeles u órdenes suyas; revisor de la fase 5)."""
    return is_rev_c(port) or is_weact(port)


def remembered_device(choice: DisplayChoice, opened: str | None) -> str:
    """El COM que se guarda al recordar ``choice`` tras abrirla en ``opened``.

    Normalmente el abierto (Windows puede haberla movido de COM). Con la rev C elegida por
    su puente, el abierto es el aparato despierto: se guarda ese, porque es como se
    reconoce el despierto que Luzeira ya comprobó (``apertura.open_rev_c``)… salvo si el
    puente no tiene serie: entonces ``match_choice`` exige el mismo COM del puente, y
    guardar el del despierto haría que no casara nunca más (revisor de la fase 5)."""
    bridge = PortInfo(choice.device, choice.vid, choice.pid, choice.serial)
    if is_rev_c_bridge(bridge) and not choice.serial:
        return choice.device
    return opened or choice.device


def is_rev_c_awake(port: PortInfo) -> bool:
    """¿Parece una rev C despierta? Ids de gadget **y** su serie: los ids solos los
    comparten otros aparatos (una Raspberry Pi en modo gadget)."""
    return port.usb_id in REV_C_AWAKE_IDS and port.serial == REV_C_AWAKE_SERIAL


def rev_c_size(port: PortInfo) -> DisplayModel | None:
    """El tamaño que dice el serie del puente, si lo dice (:data:`REV_C_SIZE_BY_SERIAL`)."""
    return REV_C_SIZE_BY_SERIAL.get(port.serial) if is_rev_c_bridge(port) else None


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
    if is_rev_c_bridge(port) or is_rev_c_awake(port):
        return list(REV_C_SIZES)  # el saludo no dice el tamaño: los tres
    found = identify(port)
    if found is not None and found.sure and found.model is not None:
        return [found.model] if found.model.supported else []
    # Los tamaños de la rev C solo en su puente o su despierto (arriba): un gadget con sus
    # ids y otro serie (una Raspberry Pi) no es una rev C.
    return [m for m in SUPPORTED if usb_id in m.usb_ids and m not in REV_C_SIZES]


def match_choice(choice: DisplayChoice, ports: Iterable[PortInfo]) -> PortInfo | None:
    """El puerto donde sigue estando la pantalla elegida, o None si ya no coincide (y
    entonces se vuelve a preguntar).

    - Con USB y número de serie, en la lista cerrada: mismo VID:PID y mismo serie. El COM
      puede haber cambiado (si hay varios iguales, se prefiere el mismo COM).
    - 🔴 En modo avanzado o sin número de serie (R5-01): mismo COM **y** mismo VID:PID. Sin
      serie, el VID:PID no distingue un aparato de otro igual: el chip ``1a86:7523`` de una
      pantalla es también el de muchas impresoras 3D y grabadoras láser. Seguirlo a otro
      COM sería mandarle píxeles a una máquina sin preguntar.
    - Sin USB (solo en modo avanzado): el mismo COM y que siga sin USB. No hay nada más con
      lo que reconocerlo.
    - 🔴 Nunca un puerto que se identifica como rev C (R5-02)…
    - …salvo para un modelo rev C, que solo vale en una rev C (:func:`_match_rev_c`).
    """
    model = model_by_key(choice.model)
    if model is None or not model.supported:
        return None
    if model in WEACT_MODELS:
        # 🔴 Una WeAct elegida solo casa con un USB de WeAct, también en modo avanzado.
        ports = [p for p in ports if p.usb_id == VID_PID_WEACT]
    if model in REV_C_SIZES:
        return _match_rev_c(choice, ports)
    if model not in WEACT_MODELS:
        ports = [p for p in ports if not is_own_usb_only(p)]
    else:
        ports = [p for p in ports if not is_rev_c(p)]
    if choice.vid is None or choice.pid is None:
        return next((p for p in ports if p.device == choice.device and p.usb_id is None), None)
    usb_id = (choice.vid, choice.pid)
    if not choice.advanced and usb_id not in model.usb_ids:
        return None  # una elección normal tiene que seguir siendo de la lista cerrada
    same = [p for p in ports if p.usb_id == usb_id and p.serial == choice.serial]
    if not choice.advanced:  # …y del puerto concreto (p. ej. no una rev B sobre una rev A)
        same = [p for p in same if model in compatible_models(p)]
    here = next((p for p in same if p.device == choice.device), None)
    if choice.advanced or not choice.serial:
        return here  # el mismo aparato solo se reconoce por su COM
    return here or (same[0] if same else None)


def _match_rev_c(choice: DisplayChoice, ports: Iterable[PortInfo]) -> PortInfo | None:
    """El puerto de una rev C elegida: su puente, o el aparato despierto si se eligió ese.

    - Elegida sobre el puente: mismo VID:PID y mismo serie (``CT…INCH`` dice el modelo, no
      la unidad, como el de la rev A), preferiblemente en el mismo COM; sin serie, el mismo
      COM (R5-01). ``apertura`` la despierta después.
    - Elegida sobre el aparato despierto: su serie ``20080411`` es el de cualquier gadget
      de ese tipo, así que vale solo el mismo COM con los mismos ids (como sin serie).
    - Nunca un puerto que no sea de rev C, aunque se eligiera en el modo avanzado.
    """
    if choice.vid is None or choice.pid is None:
        return None
    usb_id = (choice.vid, choice.pid)
    same = [
        p
        for p in ports
        if p.usb_id == usb_id
        and p.serial == choice.serial
        and (is_rev_c_bridge(p) or is_rev_c_awake(p))
    ]
    here = next((p for p in same if p.device == choice.device), None)
    if not same or not is_rev_c_bridge(same[0]) or not choice.serial:
        return here
    return here or same[0]
