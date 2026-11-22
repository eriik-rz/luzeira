"""Motor de render: convierte un tema + lecturas de sensores en imágenes para la pantalla.

La pantalla es lenta (~230 KB/s medidos: 1,3 s una imagen completa), así que el motor
separa dos capas (RQ-03):

- **Base**: fondo, textos fijos e imágenes. Se pinta una vez.
- **Dinámica**: relojes, sensores y barras. En cada ``tick`` se repinta cada widget en
  memoria y **solo se devuelve si sus píxeles han cambiado**. Un reloj HH:MM cambia una
  vez por minuto: 59 de cada 60 ticks no se envía nada.

El motor no sabe nada del puerto serie ni de dónde salen los datos: recibe funciones
(``font_for``, ``read_sensor``, ``now``) para poder probarlo sin hardware.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from luzeira import idioma
from luzeira.idioma import t
from luzeira.sensores.base import Reading
from luzeira.temas.cargar import open_validated_image
from luzeira.temas.modelo import (
    BarWidget,
    ClockWidget,
    ImageWidget,
    SensorWidget,
    Style,
    TextWidget,
    Theme,
    localized,
)

FontFor = Callable[[Style], ImageFont.FreeTypeFont | ImageFont.ImageFont]
ReadSensor = Callable[[str], Reading]

log = logging.getLogger(__name__)

NO_VALUE = "—"  # RQ-05: un sensor sin valor nunca se pinta como 0

# Margen al comparar con la hora de la próxima actualización. El reloj de Windows avanza
# a saltos de ~15,6 ms y ``poll()`` tarda ~15 ms: sin margen, un widget de 1 s se
# saltaba ~40 % de los ticks (medido por el revisor de la fase 3).
TICK_TOLERANCE = 0.25


@dataclass(frozen=True)
class Patch:
    """Trozo de pantalla a enviar: imagen y su esquina superior izquierda."""

    x: int
    y: int
    image: Image.Image


def weekday_names() -> list[str]:
    """Los días de la semana en el idioma activo, del lunes (0, como
    ``datetime.weekday()``) al domingo (6). Zona ``pantalla`` del catálogo."""
    return [t(f"pantalla.dia.{i}") for i in range(7)]


def month_names() -> list[str]:
    """Los meses en el idioma activo, de enero a diciembre."""
    return [t(f"pantalla.mes.{i}") for i in range(1, 13)]


def format_clock(fmt: str, moment: datetime) -> str:
    """Formatos cerrados del tema, en el idioma activo (catálogo ``pantalla``, el mismo que
    usa la vista previa del editor), sin depender del idioma del sistema."""
    if fmt == "HH:MM":
        return moment.strftime("%H:%M")
    if fmt == "HH:MM:SS":
        return moment.strftime("%H:%M:%S")
    if fmt == "date-short":
        return t(
            "pantalla.fecha_corta",
            dd=f"{moment.day:02d}",
            mm=f"{moment.month:02d}",
            aaaa=f"{moment.year:04d}",
        )
    weekday = t(f"pantalla.dia.{moment.weekday()}")
    if fmt == "weekday":
        return weekday
    if fmt == "date-long":
        month = t(f"pantalla.mes.{moment.month}")
        return t("pantalla.fecha_larga", dia=weekday, d=moment.day, mes=month)
    raise ValueError(f"formato de reloj desconocido: {fmt}")


def format_reading(widget: SensorWidget, reading: Reading, decimal_sep: str | None = None) -> str:
    """Compone el texto de un sensor con campos fijos (nunca str.format sobre texto del tema).

    ``decimal_sep``: por defecto, el del idioma activo (coma en español: 14,1 GB; punto
    en inglés: 14.1 GB).
    """
    if decimal_sep is None:
        decimal_sep = t("pantalla.decimal")
    lang = idioma.current()
    prefix = localized(widget.prefix, widget.prefix_i18n, lang)
    suffix = localized(widget.suffix, widget.suffix_i18n, lang)
    if reading.text is not None:  # C-12: sensor de texto
        text = reading.text
        if len(text) > widget.max_chars:
            text = text[: widget.max_chars - 1] + "…"
        return f"{prefix}{text}{suffix}"
    if reading.value is None:
        return f"{prefix}{NO_VALUE}"
    number = f"{reading.value:.{widget.decimals}f}".replace(".", decimal_sep)
    unit = f" {reading.unit}" if widget.show_unit and reading.unit else ""
    return f"{prefix}{number}{unit}{suffix}"


def _fit_contain(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Escala la imagen para que quepa entera en ``size`` sin deformarla."""
    img = image.copy()
    img.thumbnail(size, Image.Resampling.LANCZOS)
    return img


def _fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Escala y recorta para llenar ``size`` entero sin deformar (para el fondo).

    ``ImageOps.fit`` recorta primero en la imagen original y luego escala solo ese
    trozo. Escalar la imagen entera y recortar después reservaba gigas con un fondo
    muy alargado (4096x1 → 1.310.720x320 px; hallazgo del revisor de la fase 3).
    """
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS)


def _interval(widget: ClockWidget | SensorWidget | BarWidget) -> float:
    """Cada cuánto se repinta. El reloj, en cada tick (solo se envía si cambia el texto)."""
    return getattr(widget, "interval", 0.0)


Box4 = tuple[int, int, int, int]  # (izquierda, arriba, derecha, abajo), derecha/abajo excluidas


def _box_of(widget) -> Box4:
    b = widget.box
    return (b.x, b.y, b.x + b.width, b.y + b.height)


def _intersect(a: Box4, b: Box4) -> Box4 | None:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    return (left, top, right, bottom) if left < right and top < bottom else None


@dataclass
class _Layer:
    """Una capa por widget, en el orden del tema (los últimos quedan encima).

    ``image`` es RGBA del tamaño de la caja, transparente donde el widget no pinta: así
    un widget encima de otro deja ver el de abajo (D-11). Las capas fijas (texto, imagen)
    se pintan una vez; las dinámicas (reloj, sensor, barra) cuando les toca.
    """

    widget: object
    box: Box4
    image: Image.Image
    dynamic: bool
    next_update: float = 0.0
    last_pixels: bytes | None = field(default=None, repr=False)


class Renderer:
    def __init__(
        self,
        theme: Theme,
        *,
        font_for: FontFor,
        read_sensor: ReadSensor,
        files: Mapping[str, Path] | None = None,
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``files``: rutas relativas del tema -> ficheros ya validados por el cargador."""
        self.theme = theme
        self._font_for_uncached = font_for
        self._fonts: dict[Style, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._read_sensor = read_sensor
        self._files = dict(files or {})
        self._now = now
        self._clock = clock
        self.size = theme.size
        self._background = self._build_background()
        self._layers = [self._make_layer(w) for w in theme.widgets]
        self._frame: Image.Image | None = None  # lo que hay ahora en la pantalla

    def _font_for(self, style: Style) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Fuente para un estilo, en caché: resolverla comprueba el fichero en disco y
        se pinta cada segundo."""
        font = self._fonts.get(style)
        if font is None:
            font = self._fonts[style] = self._font_for_uncached(style)
        return font

    # --- capas fijas ---------------------------------------------------------------

    def _open(self, rel: str) -> Image.Image:
        path = self._files.get(rel)
        if path is None:
            # Fallo de programación (nunca llega al usuario): no se traduce.
            raise KeyError(f"el tema usa «{rel}» pero el cargador no lo validó")  # i18n: no
        # Se reabre con las mismas comprobaciones del cargador (formato fijo por
        # extensión, límite de lado, primer fotograma): el fichero podría haber cambiado
        # entre la carga y ahora (TOCTOU, revisor de la fase 3).
        return open_validated_image(path, path.suffix)

    def _scaled(self, rel: str, size: tuple[int, int], how: str) -> Image.Image:
        """Imagen del tema ya escalada: ``cover`` (fondo, recorta) o ``contain`` (cabe
        entera). La vista previa del editor la sobrescribe para guardarla en caché."""
        if how == "cover":
            return _fit_cover(self._open(rel).convert("RGB"), size)
        return _fit_contain(self._open(rel), size)

    def _build_background(self) -> Image.Image:
        bg = self.theme.background
        base = Image.new("RGBA", self.size, bg.color)
        if bg.image:
            base.paste(self._scaled(bg.image, self.size, "cover"))
        return base

    def _make_layer(self, widget) -> _Layer:
        dynamic = isinstance(widget, ClockWidget | SensorWidget | BarWidget)
        layer = _Layer(widget, _box_of(widget), self._blank(widget), dynamic)
        if isinstance(widget, TextWidget):
            text = localized(widget.text, widget.text_i18n, idioma.current())
            self._draw_text(layer.image, text, widget.style)
        elif isinstance(widget, ImageWidget):
            b = widget.box
            img = self._scaled(widget.file, (b.width, b.height), "contain")
            layer.image.alpha_composite(
                img, ((b.width - img.width) // 2, (b.height - img.height) // 2)
            )
        return layer

    # --- pintado de un widget ------------------------------------------------------

    @staticmethod
    def _blank(widget) -> Image.Image:
        """Lienzo del widget: transparente, o de su color de fondo propio si lo tiene."""
        b = widget.box
        color = widget.style.background
        return Image.new("RGBA", (b.width, b.height), color if color else (0, 0, 0, 0))

    def _draw_text(self, tile: Image.Image, text: str, style: Style) -> None:
        draw = ImageDraw.Draw(tile)
        font = self._font_for(style)
        y = tile.height // 2
        x, anchor = {
            "left": (0, "lm"),
            "center": (tile.width // 2, "mm"),
            "right": (tile.width, "rm"),
        }[style.align]
        try:
            draw.text((x, y), text, font=font, fill=style.color, anchor=anchor)
        except OSError:
            # Una fuente del tema puede pasar la validación y fallar con ciertos glifos o
            # tamaños (bytecode de hinting roto). Se cae a la fuente de Luzeira en vez de
            # tumbar la app; no se registra el mensaje (puede llevar rutas personales).
            log.warning("La fuente del tema falló al pintar; uso la de Luzeira.")
            fallback = self._font_for(style.model_copy(update={"font": None}))
            draw.text((x, y), text, font=fallback, fill=style.color, anchor=anchor)

    def _draw_bar(self, tile: Image.Image, widget: BarWidget, reading: Reading) -> None:
        draw = ImageDraw.Draw(tile)
        w, h = tile.size
        if reading.value is None:
            # RQ-05: sin dato no se pinta igual que 0 %: solo el contorno de la pista.
            draw.rectangle((0, 0, w - 1, h - 1), outline=widget.track)
            return
        draw.rectangle((0, 0, w - 1, h - 1), fill=widget.track)
        ratio = (reading.value - widget.min) / (widget.max - widget.min)
        ratio = min(1.0, max(0.0, ratio))
        if widget.direction == "horizontal":
            filled = round(w * ratio)
            if filled:
                draw.rectangle((0, 0, filled - 1, h - 1), fill=widget.fill)
        else:
            filled = round(h * ratio)
            if filled:
                draw.rectangle((0, h - filled, w - 1, h - 1), fill=widget.fill)

    def _render(self, widget: ClockWidget | SensorWidget | BarWidget) -> Image.Image:
        tile = self._blank(widget)
        if isinstance(widget, ClockWidget):
            self._draw_text(tile, format_clock(widget.format, self._now()), widget.style)
        elif isinstance(widget, SensorWidget):
            text = format_reading(widget, self._read_sensor(widget.sensor))
            self._draw_text(tile, text, widget.style)
        else:
            self._draw_bar(tile, widget, self._read_sensor(widget.sensor))
        return tile

    # --- composición ---------------------------------------------------------------

    def _compose(self, region: Box4) -> Image.Image:
        """La zona ``region`` tal como debe verse: fondo + todas las capas que la cruzan,
        en orden. Devuelve RGB del tamaño de la zona."""
        left, top = region[0], region[1]
        out = self._background.crop(region)
        for layer in self._layers:
            cut = _intersect(region, layer.box)
            if cut is None:
                continue
            lx, ly = layer.box[0], layer.box[1]
            out.alpha_composite(
                layer.image,
                dest=(cut[0] - left, cut[1] - top),
                source=(cut[0] - lx, cut[1] - ly, cut[2] - lx, cut[3] - ly),
            )
        return out.convert("RGB")

    # --- API ---------------------------------------------------------------------

    def full_frame(self) -> Image.Image:
        """Imagen completa (fondo + todas las capas). Reinicia el estado de cambios."""
        now = self._clock()
        for layer in self._layers:
            if layer.dynamic:
                layer.image = self._render(layer.widget)
                layer.last_pixels = layer.image.tobytes()
                layer.next_update = now + _interval(layer.widget)
        self._frame = self._compose((0, 0, *self.size))
        return self._frame.copy()

    def changed(self) -> list[Patch]:
        """Trozos de pantalla que han cambiado desde el último envío."""
        if self._frame is None:
            return [Patch(0, 0, self.full_frame())]
        now = self._clock()
        dirty: list[Box4] = []
        for layer in self._layers:
            if not layer.dynamic or now < layer.next_update - TICK_TOLERANCE:
                continue
            # Rejilla fija (siguiente += intervalo) para no acumular retraso; si nos hemos
            # quedado muy atrás (PC suspendido), se vuelve a enganchar desde ahora.
            layer.next_update += _interval(layer.widget)
            if layer.next_update < now:
                layer.next_update = now + _interval(layer.widget)
            tile = self._render(layer.widget)
            pixels = tile.tobytes()
            if pixels != layer.last_pixels:
                layer.image, layer.last_pixels = tile, pixels
                dirty.append(layer.box)
        patches = []
        for region in dirty:
            patch = self._changed_region(self._frame, region)
            if patch is not None:
                patches.append(patch)
        return patches

    def _changed_region(self, frame: Image.Image, region: Box4) -> Patch | None:
        """Recompone la zona y devuelve solo el rectángulo que cambia de verdad respecto a
        lo que hay en pantalla (``frame``, que se actualiza). Si «23 %» pasa a «24 %», unos
        pocos píxeles de un dígito: enviar la caja entera de 216x52 subía el tema Esencial
        a 65 KB/s (RQ-03)."""
        new = self._compose(region)
        bbox = ImageChops.difference(frame.crop(region), new).getbbox()
        if bbox is None:  # p. ej. el widget cambió pero otro opaco encima lo tapa
            return None
        frame.paste(new, region[:2])
        x, y = region[0] + bbox[0], region[1] + bbox[1]
        return Patch(x, y, new.crop(bbox))
