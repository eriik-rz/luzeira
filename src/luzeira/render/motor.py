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
import math
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
from luzeira.temas.fuentes import font_for_text
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

# R2-02: Pillow rasteriza el texto **entero** antes de recortarlo a la caja. Con una
# fuente de métricas infladas, unas letras piden cientos de megapíxeles (CPU, memoria o
# ``DecompressionBombError``). Antes de pintar con la fuente del tema se mide el texto
# (``getbbox`` no rasteriza): si ocupa más de 4 veces su caja y más de 2 Mpx, se usa la
# de Luzeira. Un texto normal cabe en su caja (lo exige test_temas_incluidos); uno largo
# que se sale un poco, también pasa. 4 x 2048x2048 (la caja más grande) = el límite de
# Pillow que fija el cargador (``Image.MAX_IMAGE_PIXELS``).
# R3: la de Luzeira también puede pasarse (200 «⬤» a tamaño 300 son 34 Mpx, y Pillow da
# error por encima de 33,5 Mpx): con ella, el texto que no cabe se recorta antes de
# rasterizarlo (``_fit_text``). Lo recortado quedaba fuera de la caja: no se ve.
TEXT_BOX_FACTOR = 4
MAX_TEXT_PIXELS = 2_000_000

Box4 = tuple[int, int, int, int]  # (izquierda, arriba, derecha, abajo), derecha/abajo excluidas


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


def _text_too_big(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str, anchor: str, box: tuple[int, int]
) -> bool:
    """¿Pintar ``text`` con ``font`` pediría un lienzo absurdo para una caja ``box``?"""
    left, top, right, bottom = font.getbbox(text, anchor=anchor)
    area = max(0, right - left) * max(0, bottom - top)
    return area > max(TEXT_BOX_FACTOR * box[0] * box[1], MAX_TEXT_PIXELS)


def _cut(text: str, n: int, anchor: str) -> str:
    """Los ``n`` caracteres de ``text`` que se ven según el anclaje: el principio si va a
    la izquierda, el final si va a la derecha y el centro si va centrado."""
    if anchor[0] == "l":
        return text[:n]
    if anchor[0] == "r":
        return text[len(text) - n :]
    start = (len(text) - n) // 2
    return text[start : start + n]


def _fit_text(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str, anchor: str, box: tuple[int, int]
) -> str:
    """``text`` recortado hasta que pintarlo no pida un lienzo absurdo para ``box``.

    Atajo: una fuente de confianza (la de Luzeira, Segoe, las CJK de Windows) dibuja cada
    letra en menos de 3x3 veces su tamaño; si ni así se pasaría, no se mide nada (medir
    cuesta ~0,3 ms y el reloj cambia de texto cada segundo). Si no, búsqueda binaria del
    trozo más largo que cabe.
    """
    limit = max(TEXT_BOX_FACTOR * box[0] * box[1], MAX_TEXT_PIXELS)
    size = getattr(font, "size", 0) or 0
    if size and len(text) * 3 * size * 3 * size <= limit:
        return text
    if not _text_too_big(font, text, anchor, box):
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_too_big(font, _cut(text, mid, anchor), anchor, box):
            hi = mid - 1
        else:
            lo = mid
    return _cut(text, lo, anchor)


@dataclass(frozen=True)
class Fit:
    """Cómo cabe el tema en la pantalla: factor de escala y esquina del tema escalado.

    El tema se escala **igual en los dos ejes** (sin deformarlo) hasta tocar los bordes
    del lado que antes llegue, y se centra; lo que sobra se rellena con el color de fondo
    del tema. Un tema del mismo tamaño que la pantalla: ``scale`` 1 y sin desplazar.
    """

    scale: float
    x: int  # desplazamiento del tema escalado dentro de la pantalla
    y: int
    width: int  # tamaño del tema ya escalado
    height: int

    @property
    def identity(self) -> bool:
        return self.scale == 1 and self.x == 0 and self.y == 0

    def box(self, x: int, y: int, width: int, height: int) -> Box4:
        """Una caja del tema en píxeles de la pantalla. Se escalan los **bordes** (no el
        ancho): dos cajas pegadas en el tema siguen pegadas, sin rendijas de redondeo."""
        if self.identity:
            return (x, y, x + width, y + height)
        s = self.scale
        # Al encoger (escala < 1), una caja de 1-2 px pegada al borde redondeaba fuera de
        # la pantalla (revisor de la fase 4): se acota a la zona del tema, sin dejar nunca
        # una caja vacía (como mínimo 1 px).
        right_edge, bottom_edge = self.x + self.width, self.y + self.height
        right = min(right_edge, max(self.x + round(x * s) + 1, self.x + round((x + width) * s)))
        bottom = min(bottom_edge, max(self.y + round(y * s) + 1, self.y + round((y + height) * s)))
        left = min(self.x + round(x * s), right - 1)
        top = min(self.y + round(y * s), bottom - 1)
        return (left, top, right, bottom)


def fit(theme_size: tuple[int, int], screen: tuple[int, int]) -> Fit:
    """El escalado uniforme y centrado de un tema de ``theme_size`` en ``screen``."""
    if theme_size == screen:
        return Fit(1, 0, 0, *screen)
    scale = min(screen[0] / theme_size[0], screen[1] / theme_size[1])
    width = min(screen[0], max(1, round(theme_size[0] * scale)))
    height = min(screen[1], max(1, round(theme_size[1] * scale)))
    return Fit(scale, (screen[0] - width) // 2, (screen[1] - height) // 2, width, height)


def _anchor(align: str, width: int) -> tuple[int, str]:
    """(x, anclaje) de un texto en una caja de ``width``, centrado en vertical."""
    return {"left": (0, "lm"), "center": (width // 2, "mm"), "right": (width, "rm")}[align]


def _interval(widget: ClockWidget | SensorWidget | BarWidget) -> float:
    """Cada cuánto se repinta. El reloj, en cada tick (solo se envía si cambia el texto)."""
    return getattr(widget, "interval", 0.0)


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
        output_size: tuple[int, int] | None = None,
    ) -> None:
        """``files``: rutas relativas del tema -> ficheros ya validados por el cargador.

        ``output_size``: tamaño de la pantalla (ancho, alto, con su orientación ya
        aplicada). Sin él, el del tema (la vista previa del editor). Si no coincide, el
        tema se pinta escalado y centrado (:func:`fit`), **componiendo a la resolución de
        la pantalla**: cajas, letras, grosores e imágenes se escalan antes de pintar, así
        el texto sale nítido (escalar la imagen ya hecha lo emborronaría)."""
        self.theme = theme
        self._font_for_uncached = font_for
        self._fonts: dict[Style, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        # (estilo, texto, caja, anclaje) → (fuente con que se pinta, texto que se pinta):
        # C-65, la de CJK si le faltan letras; R3, el texto recortado si no cabe.
        self._text_fonts: dict[tuple, tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]] = {}
        # Estilos cuya fuente del tema ya ha fallado o dibuja desproporcionado: se pintan
        # con la de Luzeira desde entonces (y el aviso sale una vez, no en cada tick).
        self._broken_fonts: set[Style] = set()
        self._read_sensor = read_sensor
        self._files = dict(files or {})
        self._now = now
        self._clock = clock
        self._scaled_styles: dict[Style, Style] = {}
        # (estilo del tema, texto, caja) → estilo escalado y, si hace falta, encogido.
        self._fitted_styles: dict[tuple, Style] = {}
        self._frame: Image.Image | None = None  # lo que hay ahora en la pantalla
        self._target = output_size or theme.size
        self._build(self._target)

    def _build(self, size: tuple[int, int]) -> None:
        """Prepara fondo y capas para una pantalla de ``size``. Todo o nada: si algo falla
        (una imagen que ha cambiado en disco), el motor se queda como estaba."""
        old = getattr(self, "_fit", None), dict(self._scaled_styles)
        self._fit = fit(self.theme.size, size)
        self._scaled_styles.clear()
        self._fitted_styles.clear()
        try:
            background = self._build_background(size)
            layers = [self._make_layer(w) for w in self.theme.widgets]
        except BaseException:
            if old[0] is not None:
                self._fit, self._scaled_styles = old
            raise
        self.size, self._background, self._layers = size, background, layers
        self._frame = None

    def set_output_size(self, size: tuple[int, int]) -> None:
        """La pantalla es de ``size`` (al conectar, o al girarla). No pinta nada aún: la
        próxima imagen completa ya sale a ese tamaño (si preparar el tema falla, el fallo
        sale ahí, donde el servicio ya lo gestiona y reintenta)."""
        if size != self._target:
            self._target = size
            self._frame = None  # ``changed`` pide entonces una imagen completa

    def _style(self, style: Style) -> Style:
        """El estilo con la letra al tamaño de la pantalla (el mismo objeto sin escalar,
        para que las cachés de fuentes no cambien en el caso de siempre)."""
        if self._fit.scale == 1:
            return style
        scaled = self._scaled_styles.get(style)
        if scaled is None:
            # model_copy no valida: un tamaño por debajo del mínimo del tema (6) es lo
            # que toca al encoger un tema grande en una pantalla pequeña.
            size = max(1, round(style.size * self._fit.scale))
            scaled = self._scaled_styles[style] = style.model_copy(update={"size": size})
        return scaled

    def _text_style(self, widget, text: str, size: tuple[int, int]) -> Style:
        """Estilo con que se pinta ``text`` en la caja ya escalada (``size``).

        Al escalar, el tamaño de letra se redondea (28,125 → 28) y el hinting de la
        fuente cambia el ancho: un texto que cabía justo en el tema podía salirse de su
        caja escalada y recortarse (la «a» de «activo» en Neón vertical a 7"; revisor de
        la fase 4). Si cabía en el tema y escalado no cabe, se encoge la letra lo justo
        (hasta un 20 % como mucho). Uno que ya se salía en el tema se deja como está."""
        style = self._style(widget.style)
        if self._fit.scale == 1:
            return style
        key = (widget.style, text, size, widget.box.width, widget.box.height)
        found = self._fitted_styles.get(key)
        if found is None:
            if len(self._fitted_styles) >= 256:
                self._fitted_styles.clear()
            found = self._fitted_styles[key] = self._shrink_to_fit(widget, text, style, size)
        return found

    def _shrink_to_fit(self, widget, text: str, style: Style, size: tuple[int, int]) -> Style:
        if not self._overflows(style, text, size):
            return style
        b = widget.box
        if self._overflows(widget.style, text, (b.width, b.height)):
            return style
        floor = max(1, math.floor(style.size * 0.8))
        trial = style
        for letter in range(style.size - 1, floor - 1, -1):
            trial = style.model_copy(update={"size": letter})
            if not self._overflows(trial, text, size):
                break
        return trial

    def _overflows(self, style: Style, text: str, size: tuple[int, int]) -> bool:
        """¿``text`` pintado con ``style`` se sale por los lados de una caja ``size``?
        (1 px de margen, como test_temas_incluidos: hay letras que asoman 1 px)."""
        x, anchor = _anchor(style.align, size[0])
        try:
            font, visible = self._font_for_text(style, text, size, anchor)
            left, _, right, _ = font.getbbox(visible, anchor=anchor)
        except (OSError, ValueError, Image.DecompressionBombError):
            return False  # una fuente que falla al medir: lo resuelve _draw_text
        return x + left < -1 or x + right > size[0] + 1

    def _box_of(self, widget) -> Box4:
        b = widget.box
        return self._fit.box(b.x, b.y, b.width, b.height)

    def _font_for(self, style: Style) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Fuente para un estilo, en caché: resolverla comprueba el fichero en disco y
        se pinta cada segundo."""
        font = self._fonts.get(style)
        if font is None:
            font = self._fonts[style] = self._font_for_uncached(style)
        return font

    def _font_for_text(
        self,
        style: Style,
        text: str,
        box: tuple[int, int] | None = None,
        anchor: str = "lm",
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
        """(fuente, texto a pintar). La fuente del estilo, o la de CJK de Windows si a esa
        le faltan letras del texto. En caché: mirar los glifos pinta cada letra, y el reloj
        cambia cada segundo (por eso se vacía al llegar a un tope).

        Con ``box`` (la caja del widget): si la fuente del tema dibujaría el texto
        desproporcionado o falla al medirlo, la de Luzeira (R2-02); y con la de Luzeira,
        el texto recortado a lo que no pide un lienzo absurdo (R3)."""
        key = (style, text, box, anchor)
        found = self._text_fonts.get(key)
        if found is None:
            if len(self._text_fonts) >= 256:
                self._text_fonts.clear()
            found = self._choose_font(style, text, box, anchor)
            self._text_fonts[key] = found
        return found

    def _choose_font(
        self, style: Style, text: str, box: tuple[int, int] | None, anchor: str
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
        lang = idioma.current()
        if style.font and style not in self._broken_fonts:
            try:
                font = font_for_text(self._font_for(style), style, text, lang)
                if box is None or not _text_too_big(font, text, anchor, box):
                    return font, text
                log.warning(
                    "La fuente del tema dibuja el texto desproporcionado; uso la de Luzeira."
                )
            except (OSError, ValueError, Image.DecompressionBombError):
                # Mirar los glifos (CJK) rasteriza letra a letra: también puede fallar.
                log.warning("La fuente del tema falló al medir el texto; uso la de Luzeira.")
            self._broken_fonts.add(style)
        if style.font:
            style = style.model_copy(update={"font": None})
        font = font_for_text(self._font_for(style), style, text, lang)
        return font, (text if box is None else _fit_text(font, text, anchor, box))

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

    def _build_background(self, size: tuple[int, int]) -> Image.Image:
        """Fondo de toda la pantalla: el color del tema (también en las franjas que sobran
        al centrar un tema de otro tamaño) y la imagen del tema, escalada a la zona del
        tema."""
        bg = self.theme.background
        base = Image.new("RGBA", size, bg.color)
        if bg.image:
            f = self._fit
            base.paste(self._scaled(bg.image, (f.width, f.height), "cover"), (f.x, f.y))
        return base

    def _make_layer(self, widget) -> _Layer:
        dynamic = isinstance(widget, ClockWidget | SensorWidget | BarWidget)
        box = self._box_of(widget)
        layer = _Layer(widget, box, self._blank(widget, box), dynamic)
        if isinstance(widget, TextWidget):
            text = localized(widget.text, widget.text_i18n, idioma.current())
            self._draw_text(layer.image, text, self._text_style(widget, text, layer.image.size))
        elif isinstance(widget, ImageWidget):
            # La imagen original, escalada una sola vez a la caja ya escalada (no la del
            # tema escalada otra vez: saldría más borrosa).
            width, height = box[2] - box[0], box[3] - box[1]
            img = self._scaled(widget.file, (width, height), "contain")
            layer.image.alpha_composite(img, ((width - img.width) // 2, (height - img.height) // 2))
        return layer

    # --- pintado de un widget ------------------------------------------------------

    @staticmethod
    def _blank(widget, box: Box4) -> Image.Image:
        """Lienzo del widget (``box``, en píxeles de la pantalla): transparente, o de su
        color de fondo propio si lo tiene."""
        color = widget.style.background
        size = (box[2] - box[0], box[3] - box[1])
        return Image.new("RGBA", size, color if color else (0, 0, 0, 0))

    def _draw_text(self, tile: Image.Image, text: str, style: Style) -> None:
        draw = ImageDraw.Draw(tile)
        y = tile.height // 2
        x, anchor = _anchor(style.align, tile.width)
        # Dos intentos como mucho: con la fuente del tema y, si falla, con la de Luzeira.
        for _ in range(2):
            font, visible = self._font_for_text(style, text, tile.size, anchor)
            try:
                draw.text((x, y), visible, font=font, fill=style.color, anchor=anchor)
                return
            except (OSError, ValueError, Image.DecompressionBombError):
                # Una fuente del tema puede pasar la validación y fallar con ciertos
                # glifos o tamaños (bytecode de hinting roto, «OSError»; o un glifo
                # inflado que pide un lienzo enorme, «DecompressionBombError», que no
                # hereda de OSError). Se cae a la fuente de Luzeira en vez de tumbar la
                # app; no se registra el mensaje (puede llevar rutas personales).
                if style.font and style not in self._broken_fonts:
                    log.warning("La fuente del tema falló al pintar; uso la de Luzeira.")
                    self._broken_fonts.add(style)
                    self._text_fonts.clear()  # lo cacheado con esa fuente ya no vale
                    continue
                # Ni con la de Luzeira: no debería pasar (el texto ya va recortado), pero
                # un texto en blanco es mejor que parar la pantalla entera.
                log.warning("No se pudo pintar un texto del tema; se deja en blanco.")
                return

    def _draw_bar(self, tile: Image.Image, widget: BarWidget, reading: Reading) -> None:
        draw = ImageDraw.Draw(tile)
        w, h = tile.size
        if reading.value is None:
            # RQ-05: sin dato no se pinta igual que 0 %: solo el contorno de la pista.
            # Contorno de 1 px del tema: en una pantalla mayor, más grueso en proporción.
            thickness = max(1, round(self._fit.scale))
            draw.rectangle((0, 0, w - 1, h - 1), outline=widget.track, width=thickness)
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

    def _render(self, layer: _Layer) -> Image.Image:
        widget = layer.widget
        tile = self._blank(widget, layer.box)
        if isinstance(widget, ClockWidget):
            text = format_clock(widget.format, self._now())
            self._draw_text(tile, text, self._text_style(widget, text, tile.size))
        elif isinstance(widget, SensorWidget):
            text = format_reading(widget, self._read_sensor(widget.sensor))
            self._draw_text(tile, text, self._text_style(widget, text, tile.size))
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
        """Imagen completa (fondo + todas las capas). Reinicia el estado de cambios.

        Si la pantalla ha cambiado de tamaño (``set_output_size``), antes se prepara el
        tema para el tamaño nuevo."""
        if self._target != self.size:
            self._build(self._target)
        now = self._clock()
        for layer in self._layers:
            if layer.dynamic:
                layer.image = self._render(layer)
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
            tile = self._render(layer)
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
