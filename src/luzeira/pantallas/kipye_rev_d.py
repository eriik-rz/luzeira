"""Driver **beta** de la Kipye Qiye 3,5" (la «revisión D» de turing-smart-screen-python).

Escrito con palabras propias desde la especificación del informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (sección «Protocolo rev D»),
que resume ``library/lcd/lcd_comm_rev_d.py`` de turing-smart-screen-python (GPL-3.0,
https://github.com/mathoudebine/turing-smart-screen-python, commit ``866e5ba``). No se ha
copiado su código. Dos detalles que el informe no daba se contrastaron con ese fichero
(2026-11-18): las coordenadas de la ventana son **inclusivas** y el último paquete de
imagen va **sin rellenar** (más corto).

⚠ Beta: nadie lo ha probado aún con una pantalla de verdad, y la evidencia de este
protocolo es poca (un solo proyecto).

Protocolo:

- Órdenes cortas de bytes (casi todas de 4). No hay saludo: la primera prueba es un
  brillo, y lo único que se puede comprobar es que el puerto acepta lo que se le escribe.
- Imagen: definir ventana → entrar en modo imagen → paquetes de 64 bytes (``0x50`` + 63
  de píxeles RGB565 big-endian) → salir del modo imagen.
- La pantalla manda confirmaciones que no se usan: tras cada orden se vacía la entrada.
- Solo sabe vertical y vertical invertida; la horizontal se hace girando la imagen.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PIL import Image

from luzeira.idioma import Text
from luzeira.pantallas.comun import (
    DisplayError,
    ImageCut,
    Orientation,
    SerialFactory,
    Transport,
    open_probed,
    write_all,
)
from luzeira.pantallas.rgb565 import to_rgb565_be

WIDTH, HEIGHT = 320, 480  # en vertical (orientación nativa)

# Las órdenes que usa Luzeira. No hay más: ni rellenar de color, ni espejos, ni pedir
# información (la respuesta es desconocida).
ORIENTATION_NORMAL = b"\x43\x48\x00\x00"
ORIENTATION_UPSIDE_DOWN = b"\x43\x47\x00\x00"
BRIGHTNESS = b"\x43\x43"  # + nivel 0-500 en 2 bytes big-endian
WINDOW = b"\x43\x41"  # + x0, x1, y0, y1 (¡en ese orden!), 2 bytes big-endian cada uno
PICTURE_START = b"\x44\x00\x00\x00"
PICTURE_END = b"\x41\x00\x00\x00"
PICTURE_PACKET = 0x50  # primer byte de cada paquete de píxeles
PACKET_PIXEL_BYTES = 63
BRIGHTNESS_MAX = 500
PROBE_BRIGHTNESS = 50  # % del brillo de la primera prueba (el servicio pone luego el suyo)
# Plazo para una imagen entera (R5-04; ver ``comun.IMAGE_TIMEOUT``). Más largo que el de
# las otras: una imagen completa son ~4.900 escrituras de 64 bytes y nadie ha medido cuánto
# tarda en una Kipye de verdad [SIN MEDIR]. Sin plazo, el peor caso eran ~6,8 h.
# ⚠ No se juntan paquetes en escrituras más grandes para ir más deprisa: los bytes serían
# los mismos, pero no se sabe si la pantalla depende de recibir cada paquete en su propia
# transferencia USB (sin una para probarlo, no se toca).
IMAGE_TIMEOUT = 60.0


def brightness_command(percent: int) -> bytes:
    """Brillo 0-100 % → orden. 0-500, mayor = más brillo."""
    if not 0 <= percent <= 100:
        raise ValueError(f"brillo {percent} fuera de rango (0-100)")
    return BRIGHTNESS + (percent * BRIGHTNESS_MAX // 100).to_bytes(2, "big")


class KipyeRevD:
    """Pantalla Kipye Qiye 3,5" (beta). Se crea con :meth:`open`."""

    model_key = "kipye-rev-d"
    # Sin saludo, la primera prueba no puede decir si ahí hay una Kipye: abrir no lo
    # demuestra. Quien la elige tiene que confirmar que se ve bien (bandeja).
    verified = False

    def __init__(
        self, transport: Transport, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._transport = transport
        self._monotonic = monotonic  # reloj del plazo por imagen
        self.device: str | None = None
        self.orientation = Orientation.PORTRAIT
        self._brightness = PROBE_BRIGHTNESS

    @classmethod
    def open(
        cls,
        device: str,
        *,
        retries: int = 1,
        delay: float = 1.0,
        serial_factory: SerialFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> KipyeRevD:
        """Abre ``device`` y le manda un brillo como primera prueba."""
        return open_probed(
            device,
            cls,
            retries=retries,
            delay=delay,
            serial_factory=serial_factory,
            sleep=sleep,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> KipyeRevD:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- comandos -------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        write_all(self._transport, data)

    def _command(self, data: bytes) -> None:
        """Una orden y, después, tirar la confirmación que manda la pantalla.

        Vaciar la entrada también toca el puerto: con el USB desenchufado, pyserial lanza
        ``SerialException`` (un ``OSError``) y sin el ``try`` mataba el hilo del servicio
        en vez de pasar a reconectar (R5-05)."""
        self._write(data)
        try:
            self._transport.reset_input_buffer()
        except OSError as exc:
            raise DisplayError(Text("pantallas.conexion_perdida")) from exc

    def probe(self) -> None:
        """La primera prueba: un brillo. Sin saludo en este protocolo, solo demuestra que
        el puerto acepta escrituras (con RTS/CTS, una pantalla que no escucha las frena
        hasta el tiempo límite y sale un error)."""
        self.set_brightness(PROBE_BRIGHTNESS)

    @property
    def width(self) -> int:
        return HEIGHT if self.orientation.is_landscape else WIDTH

    @property
    def height(self) -> int:
        return WIDTH if self.orientation.is_landscape else HEIGHT

    def _send_brightness(self, percent: int) -> None:
        command = brightness_command(percent)
        # Dos veces: según la referencia, a veces la primera no se aplica.
        self._command(command)
        self._command(command)

    def set_brightness(self, percent: int) -> None:
        self._send_brightness(percent)
        self._brightness = percent

    def screen_off(self) -> None:
        """No hay orden de apagar: brillo 0 (si apaga del todo, sin verificar)."""
        self._send_brightness(0)

    def screen_on(self) -> None:
        self._send_brightness(self._brightness)

    def set_orientation(self, orientation: Orientation) -> None:
        """La pantalla gira 180° sola; la horizontal se hace en :meth:`draw_image`."""
        self.orientation = Orientation(orientation)
        upside_down = self.orientation.is_reversed
        self._command(ORIENTATION_UPSIDE_DOWN if upside_down else ORIENTATION_NORMAL)

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        """Pinta ``image`` con su esquina superior izquierda en (x, y); lo que se salga
        se recorta.

        Si se corta a medias (o no sale en ``IMAGE_TIMEOUT``), la pantalla puede quedarse
        en modo imagen esperando el resto: :class:`ImageCut`."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(
                f"({x}, {y}) está fuera de la pantalla {self.width}x{self.height}"  # i18n: no
            )
        w = min(image.width, self.width - x)
        h = min(image.height, self.height - y)
        image = image.convert("RGB")
        if (w, h) != image.size:
            image = image.crop((0, 0, w, h))
        if self.orientation.is_landscape:
            # La pantalla piensa en vertical: el punto (X, Y) de la imagen horizontal va a
            # la columna 319 - Y y la fila X. Se gira 90° a la derecha y se recoloca.
            image = image.rotate(270, expand=True)
            x0, x1 = WIDTH - y - h, WIDTH - y - 1
            y0, y1 = x, x + w - 1
        else:
            x0, x1 = x, x + w - 1
            y0, y1 = y, y + h - 1
        self._command(WINDOW + b"".join(v.to_bytes(2, "big") for v in (x0, x1, y0, y1)))
        data = to_rgb565_be(image)
        deadline = self._monotonic() + IMAGE_TIMEOUT
        try:
            self._command(PICTURE_START)
            for start in range(0, len(data), PACKET_PIXEL_BYTES):
                if self._monotonic() > deadline:
                    raise DisplayError(Text("pantallas.conexion_perdida"))
                self._write(bytes([PICTURE_PACKET]) + data[start : start + PACKET_PIXEL_BYTES])
            self._command(PICTURE_END)
        except DisplayError as exc:
            raise ImageCut() from exc
