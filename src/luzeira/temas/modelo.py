"""Formato de los temas ``luzeira-theme/1`` (contrato fijado en la fase 3).

Un tema es **solo datos** (RQ-10): un ``theme.json`` validado con estos modelos, más
imágenes y fuentes en la misma carpeta. Nada de código, plantillas ni formatos de
texto libres: los textos de los sensores se componen con campos fijos
(``decimals``, ``prefix``, ``suffix``), nunca con ``str.format`` sobre texto del tema,
que permitiría leer atributos internos de Python.

Las claves van en inglés (como el código) para que el formato sirva a cualquiera; lo
que ve el usuario (``name``, ``text``) puede ir en cualquier idioma.

Las rutas a ficheros (``image``, ``file``, ``font``) aquí solo se validan por forma;
el cargador comprueba que existen dentro de la carpeta del tema y su tipo real.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from luzeira.idioma import Text
from luzeira.temas.rutas import has_reserved_component

FORMAT = "luzeira-theme/1"
MAX_WIDGETS = 64

Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


# Nombre de fichero relativo y sencillo: «carpeta/sub/nombre.ext». Las carpetas no
# llevan puntos y el nombre solo uno (antes de la extensión), así que «..», «\», «C:»
# y las rutas absolutas son imposibles por construcción. El cargador lo vuelve a
# comprobar contra el disco.
def _not_reserved(path: str) -> str:
    # 🆕 fase 3 (revisión del cargador): «CON.png», «COM3.gif», «aux/x.png» pasan el
    # patrón pero Windows los trata como dispositivos. Se rechazan ya en el esquema para
    # que también lo vea el editor.
    if has_reserved_component(path):
        raise ValueError(Text("temas.modelo.reservado"))
    return path


def _with_extensions(*allowed: str):
    def check(path: str) -> str:
        if path.rsplit(".", 1)[-1].lower() not in allowed:
            raise ValueError(Text("temas.modelo.extension", allowed=", ".join(allowed)))
        return path

    return AfterValidator(check)


RelPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        pattern=r"^([A-Za-z0-9_\-]+/)*[A-Za-z0-9_\-]+\.[A-Za-z0-9]{2,5}$",
    ),
    AfterValidator(_not_reserved),
]
ImagePath = Annotated[RelPath, _with_extensions("png", "jpg", "jpeg", "gif")]
FontPath = Annotated[RelPath, _with_extensions("ttf", "otf")]
SensorId = Annotated[str, Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$", max_length=60)]
ShortText = Annotated[str, Field(max_length=20)]
#: Traducciones de un texto del tema (C-62): ``{"en": "NET", "de": "NETZ"}``. Solo datos;
#: si falta el idioma activo se pinta el texto normal. Como mucho 20 idiomas.
LangCode = Annotated[str, Field(pattern=r"^[a-z]{2}$")]
TextTranslations = Annotated[
    dict[LangCode, Annotated[str, Field(max_length=200)]], Field(max_length=20)
]
ShortTranslations = Annotated[dict[LangCode, ShortText], Field(max_length=20)]


def localized(text: str, translations: dict[str, str] | None, language: str) -> str:
    """El texto en ``language`` si el tema lo trae traducido; si no, el de siempre."""
    return (translations or {}).get(language, text)


class RangeError(ValueError):
    """``max`` no es mayor que ``min`` en una barra. Con clase propia para que el editor
    lo reconozca sin comparar textos (que cambian con el idioma)."""


class WidgetOffScreen(ValueError):
    """Un widget se sale de la pantalla. Lleva los datos para que el editor lo cuente con
    sus palabras y lo cuelgue de la caja de ese elemento (``n`` cuenta desde 1)."""

    def __init__(self, n: int, kind: str, width: int, height: int) -> None:
        super().__init__(Text("temas.modelo.fuera", n=n, kind=kind, width=width, height=height))
        self.n, self.kind, self.width, self.height = n, kind, width, height


class _Strict(BaseModel):
    # Campos desconocidos = error. Así una versión vieja rechaza un tema con un campo que
    # no conoce en vez de pintarlo mal. Un campo nuevo **opcional** que no cambia los temas
    # que no lo usan no sube la versión del formato (``text_i18n``, ``canvas``); uno que
    # cambie el significado de lo que ya hay, sí.
    # allow_inf_nan=False: «NaN» o «Infinity» en min/max de una barra pasaban la validación
    # y pintaban mal (agente del servidor, fase 5).
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Box(_Strict):
    x: int = Field(ge=0, le=2048)
    y: int = Field(ge=0, le=2048)
    width: int = Field(ge=1, le=2048)
    height: int = Field(ge=1, le=2048)


class Style(_Strict):
    font: FontPath | None = None  # fichero .ttf/.otf del tema; None = fuente incluida en Luzeira
    size: int = Field(default=20, ge=6, le=300)
    bold: bool = False
    color: Color = "#ffffff"
    align: Literal["left", "center", "right"] = "center"
    background: Color | None = None  # None = se ve el fondo del tema


class _Widget(_Strict):
    box: Box
    style: Style = Style()


class TextWidget(_Widget):
    type: Literal["text"]
    text: str = Field(max_length=200)
    text_i18n: TextTranslations | None = None


class ClockWidget(_Widget):
    type: Literal["clock"]
    # Formatos cerrados (no strftime libre): salen en español sin depender del sistema.
    format: Literal["HH:MM", "HH:MM:SS", "date-short", "date-long", "weekday"] = "HH:MM"


class SensorWidget(_Widget):
    type: Literal["sensor"]
    sensor: SensorId
    decimals: int = Field(default=0, ge=0, le=3)
    prefix: ShortText = ""
    suffix: ShortText = ""  # se añade tras la unidad si show_unit
    prefix_i18n: ShortTranslations | None = None
    suffix_i18n: ShortTranslations | None = None
    show_unit: bool = True
    interval: float = Field(default=1.0, ge=0.5, le=3600)
    # C-12: los sensores de texto (nombre de la GPU…) se recortan a este largo con «…».
    max_chars: int = Field(default=40, ge=1, le=200)


class BarWidget(_Widget):
    type: Literal["bar"]
    sensor: SensorId
    min: float = 0
    max: float = 100
    direction: Literal["horizontal", "vertical"] = "horizontal"
    fill: Color = "#ff8c00"
    track: Color = "#2a2f3a"
    interval: float = Field(default=1.0, ge=0.5, le=3600)

    @model_validator(mode="after")
    def _range(self) -> BarWidget:
        if self.max <= self.min:
            raise RangeError(Text("temas.modelo.max_min"))
        return self


class ImageWidget(_Widget):
    type: Literal["image"]
    file: ImagePath


Widget = Annotated[
    TextWidget | ClockWidget | SensorWidget | BarWidget | ImageWidget,
    Field(discriminator="type"),
]


class Background(_Strict):
    color: Color = "#000000"
    image: ImagePath | None = None


#: Tamaño del tema sin ``canvas`` (el de siempre: la pantalla de 3,5"), por orientación.
ORIENTATION_SIZES = {
    "portrait": (320, 480),
    "reverse-portrait": (320, 480),
    "landscape": (480, 320),
    "reverse-landscape": (480, 320),
}

#: Lienzos que puede pedir un tema (``canvas``), en vertical (ancho <= alto). En
#: horizontal valen los mismos girados. Solo tamaños de pantallas que existen, para que
#: los límites de memoria del render se puedan calcular (lado máximo: ``MAX_CANVAS_SIDE``):
#:
#: - 320x480: Turing Smart Screen 3,5" y UsbPCMonitor 3,5" (rev A: «TURING_3_5» y
#:   «USBMONITOR_3_5» en ``pantallas/turing_rev_a.py``), XuanFang rev B y Kipye rev D
#:   (``WIDTH, HEIGHT`` de sus drivers). Es el tamaño sin ``canvas``.
#: - 480x800: UsbPCMonitor 5" (rev A, responde 0x02 al HELLO) y Turing 5" (rev C).
#: - 600x1024: UsbPCMonitor 7" (rev A, responde 0x03 al HELLO).
#: - 480x480: Turing 2,1" (rev C).
#: - 480x1920: Turing 8,8" (rev C).
#:
#: Los de la rev C los da el driver de la rev C (en preparación, 2026-11-25).
CANVAS_SIZES_PORTRAIT: tuple[tuple[int, int], ...] = (
    (320, 480),
    (480, 480),
    (480, 800),
    (600, 1024),
    (480, 1920),
)
#: Todos los lienzos permitidos (vertical y horizontal), sin repetir el cuadrado.
CANVAS_SIZES: tuple[tuple[int, int], ...] = tuple(
    dict.fromkeys([*CANVAS_SIZES_PORTRAIT, *((h, w) for w, h in CANVAS_SIZES_PORTRAIT)])
)
MAX_CANVAS_SIDE = 1920

#: Tope del área de los widgets, en «pantallas enteras» del tema (suma de ancho x alto de
#: sus cajas entre ancho x alto del lienzo). El motor guarda una imagen RGBA por widget
#: del tamaño de su caja **en la pantalla**, y repinta las dinámicas en cada tick: sin
#: tope, 64 sensores a pantalla completa en un lienzo de 480x1920 pedían +458 MB y 5,9 s
#: por tick (revisor de la fase 4), y 64 textos fijos, 234 MB. Medido en los 32 temas
#: incluidos: como mucho 0,64 pantallas de widgets dinámicos («reloj») y 1,66 de todos
#: («ambar», con paneles superpuestos). Los topes dejan unas 3 y 5 veces eso de margen
#: para temas de usuario con capas encima de otras. Como va en pantallas del tema, el
#: tope vale igual al escalar a otra pantalla (el tema escalado nunca es mayor que ella).
MAX_DYNAMIC_SCREENS = 2
MAX_WIDGET_SCREENS = 8


def is_landscape(orientation: str) -> bool:
    return orientation in ("landscape", "reverse-landscape")


def canvas_fits_orientation(size: tuple[int, int], orientation: str) -> bool:
    """¿Un lienzo de ``size`` va con esa orientación? Vertical: ancho <= alto;
    horizontal: ancho >= alto. El cuadrado vale para las dos."""
    width, height = size
    return width >= height if is_landscape(orientation) else width <= height


class CanvasNotAllowed(ValueError):
    """El tema pide un lienzo que no es de ninguna pantalla conocida (o que no va con su
    orientación). Con clase propia para que el editor lo cuelgue del campo ``canvas``."""

    def __init__(self, width: int, height: int, orientation: str) -> None:
        allowed = ", ".join(
            f"{w}x{h}" for w, h in CANVAS_SIZES if canvas_fits_orientation((w, h), orientation)
        )
        super().__init__(Text("temas.modelo.lienzo", width=width, height=height, allowed=allowed))
        self.width, self.height, self.orientation = width, height, orientation


class TooMuchArea(ValueError):
    """Los widgets del tema (o los dinámicos: reloj, sensor, barra) ocupan demasiado."""

    def __init__(self, key: str, screens: float, limit: int) -> None:
        super().__init__(Text(key, max=limit))
        self.screens, self.limit = screens, limit


class Canvas(_Strict):
    """Tamaño de la pantalla para la que está hecho el tema (opcional; desde 2026-11-25).

    Sin él, el de siempre según la orientación (``ORIENTATION_SIZES``). Si la pantalla
    real es de otro tamaño, el motor escala el tema entero sin deformarlo y lo centra.
    """

    width: int = Field(ge=1, le=MAX_CANVAS_SIDE)
    height: int = Field(ge=1, le=MAX_CANVAS_SIDE)


class Theme(_Strict):
    format: Literal["luzeira-theme/1"]
    name: str = Field(min_length=1, max_length=60)
    author: str | None = Field(default=None, max_length=60)
    orientation: Literal["portrait", "reverse-portrait", "landscape", "reverse-landscape"]
    # Opcional y sin subir la versión del formato: un tema sin él es idéntico al de antes
    # (y al guardarlo no se escribe «canvas»: ``exclude_if``), así que lo siguen leyendo
    # las versiones viejas. Uno con él lo rechazan (modelo estricto), y está bien: no
    # sabrían pintarlo en una pantalla de otro tamaño.
    canvas: Canvas | None = Field(default=None, exclude_if=lambda value: value is None)
    background: Background = Background()
    widgets: list[Widget] = Field(default_factory=list, max_length=MAX_WIDGETS)

    @property
    def size(self) -> tuple[int, int]:
        """Tamaño del tema (ancho, alto): el lienzo si lo trae; si no, el de siempre."""
        if self.canvas is not None:
            return (self.canvas.width, self.canvas.height)
        return ORIENTATION_SIZES[self.orientation]

    @model_validator(mode="before")
    @classmethod
    def _usual_canvas_is_no_canvas(cls, data: object) -> object:
        """Un ``canvas`` igual al tamaño de siempre de su orientación es como no ponerlo:
        así al guardar no se escribe y el tema lo siguen leyendo las versiones viejas."""
        if isinstance(data, dict):
            canvas, orientation = data.get("canvas"), data.get("orientation")
            if (
                isinstance(canvas, dict)
                and set(canvas) == {"width", "height"}
                and isinstance(orientation, str)
                and orientation in ORIENTATION_SIZES
                and (canvas["width"], canvas["height"]) == ORIENTATION_SIZES[orientation]
            ):
                return {**data, "canvas": None}
        return data

    @model_validator(mode="after")
    def _widgets_inside_screen(self) -> Theme:
        if self.canvas is not None:
            size = (self.canvas.width, self.canvas.height)
            if size not in CANVAS_SIZES or not canvas_fits_orientation(size, self.orientation):
                raise CanvasNotAllowed(*size, self.orientation)
        width, height = self.size
        for i, widget in enumerate(self.widgets):
            b = widget.box
            if b.x + b.width > width or b.y + b.height > height:
                raise WidgetOffScreen(i + 1, widget.type, width, height)
        screen = width * height
        areas = [(w.box.width * w.box.height, w.type) for w in self.widgets]
        dynamic = sum(a for a, kind in areas if kind in ("clock", "sensor", "bar")) / screen
        if dynamic > MAX_DYNAMIC_SCREENS:
            raise TooMuchArea("temas.modelo.dinamicos", dynamic, MAX_DYNAMIC_SCREENS)
        total = sum(a for a, _ in areas) / screen
        if total > MAX_WIDGET_SCREENS:
            raise TooMuchArea("temas.modelo.area", total, MAX_WIDGET_SCREENS)
        return self
