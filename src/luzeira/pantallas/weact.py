"""Driver **beta** de la WeAct Display FS V1: 3,5" (320x480) y 0,96" (80x160).

Escrito con palabras y estructura propias desde los hechos del informe
``docs/investigacion/tanda-11/weact-y-otras/informe.md``, contrastados (solo lectura,
2026-11-25) con ``library/lcd/lcd_comm_weact_a.py`` (3,5") y ``lcd_comm_weact_b.py``
(0,96") de turing-smart-screen-python (GPL-3.0,
https://github.com/mathoudebine/turing-smart-screen-python, commit ``36ebc0a``), que
vienen del repo del fabricante (WeActStudio.SystemMonitor, GPL-3.0, ``61acffa``), y con
el documento de ingeniería inversa de ``slipalison/bezel`` (GPL-3.0, ``3f6144f``,
``docs/reverse-engineering/protocol-weact.md``). No se ha copiado su código.

⚠ Beta: nadie lo ha probado con una pantalla de verdad (el equipo de pruebas no tiene ninguna).

Cómo habla esta pantalla:

- Puerto serie USB (CDC), como las Turing: 115200 baudios nominales, RTS/CTS.
- Cada orden es su código, sus datos (números de 16 bits con el byte bajo primero) y un
  ``0x0A`` al final. Las de lectura llevan el bit alto puesto en el código.
- Imagen: la orden con el rectángulo (esquinas **inclusivas**) y detrás los píxeles, sin
  marca de fin ni confirmación: la pantalla cuenta cuántos le tocan por el rectángulo.
- Gira sola: la orientación es una orden y las coordenadas van ya en la orientación actual.

🔴 Lista cerrada: este módulo solo sabe cuatro órdenes (:class:`Command`): leer la versión
(es el saludo), orientación, brillo e imagen. Las demás que traen las referencias (rellenar
de color, activar los informes del sensor, «liberar» y la imagen comprimida) no existen
aquí ni como constante; de órdenes peligrosas (reinicio, firmware) no se conoce ninguna,
pero que el firmware no las tenga es [SIN VERIFICAR]. ``tests/test_pantallas_seguridad.py``
lo vigila.

🔴 Sin marco: si una escritura de píxeles se corta, la pantalla sigue esperando el resto y
tomará lo siguiente por píxeles (no hay forma documentada de resincronizarla). Por eso las
escrituras cortas son error (``write_all``), hay plazo por imagen y una imagen cortada es
:class:`ImageCut` (reenchufar), como en las otras betas.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from PIL import Image, ImageChops

from luzeira.idioma import Text
from luzeira.pantallas.comun import (
    IMAGE_TIMEOUT,
    DisplayError,
    ImageCut,
    NoAnswer,
    Orientation,
    SerialFactory,
    Transport,
    WrongModel,
    open_probed,
    write_all,
)
from luzeira.pantallas.modelos import model_by_key
from luzeira.pantallas.rgb565 import to_rgb565_le

END = 0x0A  # el último byte de toda orden
READ = 0x80  # el bit que convierte un código en «lectura»


class Command(IntEnum):
    """Los cuatro códigos que usa Luzeira. No hay más."""

    SET_ORIENTATION = 0x02  # + 1 byte: 0-3 (los valores de :class:`Orientation`)
    SET_BRIGHTNESS = 0x03  # + nivel 0-255 + tiempo de fundido en ms (2 bytes)
    DISPLAY_BITMAP = 0x05  # + x0, y0, x1, y1 (2 bytes cada uno) y luego los píxeles
    READ_VERSION = 0x42 | READ  # = 0xC2; contesta 19 bytes, versión en ASCII en 1..8


VERSION_ANSWER = 19
VERSION_TEXT = slice(1, 9)  # «V1.0.0.0» en el log de un dueño del 0,96" (TSP, issue #863)
HELLO_ATTEMPTS = 2  # solo si no llega nada, como la rev B
HELLO_PAUSE = 0.5
# Fundido del brillo: el que manda la referencia (1000 ms). Qué hace exactamente el campo
# no está documentado (bezel, «unknown») [SIN VERIFICAR].
FADE_MS = 1000


def _brg565_be(image: Image.Image) -> bytes:
    """El «BRG565» que describe CheshirCa/WeAct35 (MIT, ``994fc44``,
    ``Include/weact_display.h``) para el 0,96": 5 bits de azul, 5 de rojo y 6 de verde
    (en ese orden, de más a menos significativo), byte alto primero. No es el formato por
    defecto de ningún modelo: queda para cambiarlo si una carta de colores en un 0,96" de
    verdad lo pide (informe, «Conflicto de formato de píxel»). [SIN VERIFICAR]"""
    r, g, b = image.convert("RGB").split()
    high = ImageChops.add(b.point([(v >> 3) << 3 for v in range(256)]),
                          r.point([(v >> 3) >> 2 for v in range(256)]))  # fmt: skip
    low = ImageChops.add(r.point([((v >> 3) & 0x03) << 6 for v in range(256)]),
                         g.point([v >> 2 for v in range(256)]))  # fmt: skip
    return Image.merge("LA", (high, low)).tobytes()


PIXEL_FORMATS: dict[str, Callable[[Image.Image], bytes]] = {
    "rgb565-le": to_rgb565_le,  # RGB565, byte bajo primero (como la rev A)
    "brg565-be": _brg565_be,
}


@dataclass(frozen=True)
class Panel:
    """Un tamaño de WeAct. ``width`` x ``height`` en vertical (la orientación nativa)."""

    model_key: str
    width: int
    height: int
    # 3,5": RGB565 byte bajo primero en todas las fuentes. 0,96": lo mismo según la
    # referencia, el código del fabricante y Libre Panel (un dueño lo confirma en Windows),
    # pero WeAct35 dice «BRG565»: se deja como dato para poder cambiarlo. [SIN VERIFICAR]
    pixel_format: str = "rgb565-le"
    # False: la lectura de versión demuestra que es una WeAct, pero no que los colores
    # salgan bien: quien la elige confirma «¿se ve bien?» (C-78, el 0,96").
    colours_sure: bool = True


PANEL_35 = Panel("weact-35", 320, 480)
PANEL_096 = Panel("weact-096", 80, 160, colours_sure=False)
PANELS = {p.model_key: p for p in (PANEL_35, PANEL_096)}


def order(command: Command, *data: int) -> bytes:
    """Una orden: código, datos y ``0x0A``."""
    return bytes([command, *data, END])


def le16(value: int) -> tuple[int, int]:
    """Un número de 16 bits, byte bajo primero."""
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{value} no cabe en 16 bits")
    return value & 0xFF, value >> 8


def brightness_command(percent: int) -> bytes:
    """Brillo 0-100 % → orden. 0-255, mayor = más brillo, con el fundido de la referencia.
    (bezel: brillo 100 % = ``03 FF E8 03 0A``.)"""
    if not 0 <= percent <= 100:
        raise ValueError(f"brillo {percent} fuera de rango (0-100)")
    return order(Command.SET_BRIGHTNESS, int(percent / 100 * 255), *le16(FADE_MS))


def parse_version(answer: bytes) -> str | None:
    """La versión si ``answer`` es la respuesta de una WeAct; None si no lo es.

    Se exige lo único documentado: 19 bytes y texto ASCII imprimible en los bytes 1 a 8
    (sin contar espacios o ceros a los lados). El resto de la respuesta no se conoce y no
    se mira. El texto no se registra en ningún log."""
    if len(answer) != VERSION_ANSWER:
        return None
    text = answer[VERSION_TEXT].strip(b"\x00 ")
    if not text or any(not 0x21 <= byte <= 0x7E for byte in text):
        return None
    return text.decode("ascii")


class WeAct:
    """Pantalla WeAct Display FS (beta). Se crea con :meth:`open`."""

    @property
    def verified(self) -> bool:
        """Si abre, ha contestado a la lectura de versión como una WeAct; en el 0,96", con
        el formato de color en duda, además tiene que verse bien (bandeja)."""
        return self._panel.colours_sure

    def __init__(
        self,
        transport: Transport,
        panel: Panel,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._panel = panel
        self._pixels = PIXEL_FORMATS[panel.pixel_format]
        self._sleep = sleep
        self._monotonic = monotonic
        self.device: str | None = None
        self.orientation = Orientation.PORTRAIT
        self.version: str | None = None
        self._brightness = 50

    @property
    def model_key(self) -> str:
        return self._panel.model_key

    @classmethod
    def open(
        cls,
        device: str,
        panel: Panel,
        *,
        retries: int = 1,
        delay: float = 1.0,
        serial_factory: SerialFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> WeAct:
        """Abre ``device`` y lee la versión. Si no contesta como una WeAct, cierra el
        puerto y :class:`WrongModel` sin mandar nada más."""
        return open_probed(
            device,
            lambda transport: cls(transport, panel, sleep=sleep),
            retries=retries,
            delay=delay,
            serial_factory=serial_factory,
            sleep=sleep,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> WeAct:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- el puerto -------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        write_all(self._transport, data)

    def _read(self, size: int) -> bytes:
        """Como mucho ``size`` bytes (y como mucho el timeout del puerto, 1 s)."""
        try:
            return bytes(self._transport.read(size))[:size]
        except OSError as exc:
            raise DisplayError(Text("pantallas.conexion_perdida")) from exc

    def _flush(self) -> None:
        try:
            self._transport.reset_input_buffer()
        except OSError as exc:
            raise DisplayError(Text("pantallas.conexion_perdida")) from exc

    # --- órdenes ---------------------------------------------------------------

    def probe(self) -> None:
        """La primera prueba: leer la versión, antes que nada.

        - 19 bytes con su texto → se apunta la versión.
        - Otra cosa → :class:`WrongModel` (no se reintenta ni se manda nada más).
        - Nada tras ``HELLO_ATTEMPTS`` intentos → :class:`NoAnswer`.
        """
        for attempt in range(HELLO_ATTEMPTS):
            self._flush()
            self._write(order(Command.READ_VERSION))
            answer = self._read(VERSION_ANSWER)
            self._flush()
            if answer:
                version = parse_version(answer)
                if version is None:
                    model = model_by_key(self.model_key)
                    raise WrongModel(self.device or "?", model.label_text if model else "WeAct")
                self.version = version
                return
            if attempt < HELLO_ATTEMPTS - 1:
                self._sleep(HELLO_PAUSE)
        raise NoAnswer(self.device or "?")

    @property
    def width(self) -> int:
        return self._panel.height if self.orientation.is_landscape else self._panel.width

    @property
    def height(self) -> int:
        return self._panel.width if self.orientation.is_landscape else self._panel.height

    def set_brightness(self, percent: int) -> None:
        self._write(brightness_command(percent))
        self._brightness = percent

    def screen_off(self) -> None:
        """Brillo 0. La referencia además manda «liberar» (significado desconocido): aquí
        no. Que el brillo 0 apague del todo la luz: [SIN VERIFICAR]."""
        self._write(brightness_command(0))

    def screen_on(self) -> None:
        self._write(brightness_command(self._brightness))

    def set_orientation(self, orientation: Orientation) -> None:
        """La pantalla gira sola: 0 vertical, 1 vertical invertida, 2 horizontal, 3
        horizontal invertida (los mismos números que :class:`Orientation`)."""
        self.orientation = Orientation(orientation)
        self._write(order(Command.SET_ORIENTATION, int(self.orientation)))

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        """Pinta ``image`` con su esquina superior izquierda en (x, y); lo que se salga
        se recorta.

        Si los píxeles se cortan a medias (o no salen en ``IMAGE_TIMEOUT``), la pantalla
        se queda esperando el resto: :class:`ImageCut`, no un error cualquiera."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(
                f"({x}, {y}) está fuera de la pantalla {self.width}x{self.height}"  # i18n: no
            )
        w = min(image.width, self.width - x)
        h = min(image.height, self.height - y)
        if (w, h) != image.size:
            image = image.crop((0, 0, w, h))
        box = (*le16(x), *le16(y), *le16(x + w - 1), *le16(y + h - 1))
        data = self._pixels(image)
        chunk = self.width * 4  # dos filas de pantalla, como la referencia
        deadline = self._monotonic() + IMAGE_TIMEOUT
        try:
            self._write(order(Command.DISPLAY_BITMAP, *box))
            for start in range(0, len(data), chunk):
                if self._monotonic() > deadline:
                    raise DisplayError(Text("pantallas.conexion_perdida"))
                self._write(data[start : start + chunk])
        except DisplayError as exc:
            raise ImageCut() from exc
