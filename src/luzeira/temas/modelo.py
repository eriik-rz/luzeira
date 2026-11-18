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
        raise ValueError("la ruta usa un nombre reservado de Windows (CON, NUL, COM1…)")
    return path


def _with_extensions(*allowed: str):
    def check(path: str) -> str:
        if path.rsplit(".", 1)[-1].lower() not in allowed:
            raise ValueError(f"extensión no permitida (solo {', '.join(allowed)})")
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


class _Strict(BaseModel):
    # Campos desconocidos = error: un campo nuevo implica subir la versión del formato.
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
            raise ValueError("max tiene que ser mayor que min")
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


ORIENTATION_SIZES = {
    "portrait": (320, 480),
    "reverse-portrait": (320, 480),
    "landscape": (480, 320),
    "reverse-landscape": (480, 320),
}


class Theme(_Strict):
    format: Literal["luzeira-theme/1"]
    name: str = Field(min_length=1, max_length=60)
    author: str | None = Field(default=None, max_length=60)
    orientation: Literal["portrait", "reverse-portrait", "landscape", "reverse-landscape"]
    background: Background = Background()
    widgets: list[Widget] = Field(default_factory=list, max_length=MAX_WIDGETS)

    @property
    def size(self) -> tuple[int, int]:
        return ORIENTATION_SIZES[self.orientation]

    @model_validator(mode="after")
    def _widgets_inside_screen(self) -> Theme:
        width, height = self.size
        for i, widget in enumerate(self.widgets):
            b = widget.box
            if b.x + b.width > width or b.y + b.height > height:
                raise ValueError(
                    f"el widget {i + 1} ({widget.type}) se sale de la pantalla {width}x{height}"
                )
        return self
