"""Driver **beta** de la XuanFang 3,5" (revisión B) y su variante «flagship».

Escrito con palabras propias desde la especificación del informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (sección «Protocolo rev B»),
que resume ``library/lcd/lcd_comm_rev_b.py`` de turing-smart-screen-python (GPL-3.0,
https://github.com/mathoudebine/turing-smart-screen-python, commit ``866e5ba``) y el
issue #24 de ese proyecto. No se ha copiado su código.

⚠ Beta: nadie lo ha probado aún con una pantalla de verdad.

Protocolo:

- Paquetes de 10 bytes: ``[cmd] d0…d7 [cmd]``. El código de comando va al principio y al
  final; los 8 bytes de datos se rellenan con ceros.
- Solo cuatro comandos: saludo, orientación, imagen y brillo. Este protocolo no tiene
  comandos de reinicio ni de guardar nada en la pantalla, y el driver no lleva ninguno
  más (ni siquiera el de la luz trasera de la «flagship», que Luzeira no usa).
- Imágenes en RGB565 **big-endian** (al revés que la rev A).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import IntEnum

from PIL import Image

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
from luzeira.pantallas.rgb565 import to_rgb565_be
from luzeira.pantallas.turing_rev_a import aligned_rect

WIDTH, HEIGHT = 320, 480  # en vertical (orientación nativa)
# La referencia espera 50 ms tras cada imagen porque no lee respuestas: «reduce mucho
# las imágenes corruptas» (informe, rev B).
BITMAP_COOLDOWN = 0.05
# Tope de saludos por apertura: solo se repite si no llega nada (la pantalla puede tardar
# en estar lista). Si tras el último sigue sin llegar nada, es un fallo que se reintenta
# más tarde (:class:`NoAnswer`). Una respuesta que no encaja no se repite: se cierra y se
# avisa (:class:`WrongModel`).
HELLO_ATTEMPTS = 2


class Command(IntEnum):
    """Los cuatro comandos que usa Luzeira. No hay más."""

    HELLO = 0xCA
    SET_ORIENTATION = 0xCB
    DISPLAY_BITMAP = 0xCC
    SET_BRIGHTNESS = 0xCE


# Respuesta al saludo: ``CA 'H' 'E' 'L' 'L' 'O' 0A <sub> 00 CA``. <sub> dice la variante:
# (¿es flagship?, ¿brillo de 0 a 255?). Las A01/A02 solo saben «encendida» o «apagada».
SUB_REVISIONS = {
    0x01: (False, False),  # A01: rev B, brillo solo sí/no
    0x02: (True, False),  # A02: flagship, brillo solo sí/no
    0x11: (False, True),  # A11: rev B, brillo 0-255
    0x12: (True, True),  # A12: flagship, brillo 0-255
}
_HELLO_TEXT = b"HELLO"
_HELLO_MARK = 0x0A  # el byte que la referencia comprueba antes de la sub-revisión


def packet(command: int, data: bytes = b"") -> bytes:
    """Un paquete de 10 bytes: comando, 8 de datos (rellenos con ceros) y comando."""
    if len(data) > 8:
        raise ValueError(f"un paquete lleva como mucho 8 bytes de datos, no {len(data)}")
    return bytes([command]) + data.ljust(8, b"\0") + bytes([command])


def brightness_value(percent: int, *, full_range: bool) -> int:
    """Brillo 0-100 % → valor del protocolo.

    - Con brillo 0-255 (A11/A12): **mayor = más brillo** (al revés que la rev A).
    - Sin él (A01/A02): 0 = brillo máximo, 1 = apagada; solo 0 % apaga.
    """
    if not 0 <= percent <= 100:
        raise ValueError(f"brillo {percent} fuera de rango (0-100)")
    if full_range:
        return int(percent / 100 * 255)
    return 1 if percent == 0 else 0


class XuanFangRevB:
    """Pantalla XuanFang de revisión B (beta). Se crea con :meth:`open`."""

    model_key = "xuanfang-rev-b"
    verified = True  # si abre, es que ha contestado al saludo como una rev B

    def __init__(
        self,
        transport: Transport,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic  # reloj del plazo por imagen
        self.device: str | None = None
        self.orientation = Orientation.PORTRAIT
        self.flagship = False
        self.full_range_brightness = False  # hasta que el saludo diga lo contrario
        self._brightness = 50  # el que se restaura al encender
        self._reset_shadow()

    def _reset_shadow(self) -> None:
        # Copia de lo que hay en pantalla, para rellenar al ampliar un rectángulo.
        self._shadow = Image.new("RGB", (self.width, self.height), "black")

    @classmethod
    def open(
        cls,
        device: str,
        *,
        retries: int = 1,
        delay: float = 1.0,
        serial_factory: SerialFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> XuanFangRevB:
        """Abre ``device`` y saluda. Si la respuesta no es la de una rev B, cierra el
        puerto y lanza :class:`WrongModel` sin mandar nada más."""
        return open_probed(
            device,
            lambda transport: cls(transport, sleep=sleep),
            retries=retries,
            delay=delay,
            serial_factory=serial_factory,
            sleep=sleep,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> XuanFangRevB:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- comandos -------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        write_all(self._transport, data)

    def _send(self, command: Command, data: bytes = b"") -> None:
        self._write(packet(command, data))

    def probe(self) -> None:
        """La primera prueba: solo el saludo.

        - Contesta como una rev B → se apunta la variante.
        - Contesta otra cosa → :class:`WrongModel` (no es ese modelo; no se reintenta).
        - No contesta nada tras ``HELLO_ATTEMPTS`` saludos → :class:`NoAnswer` (puede
          estar arrancando: se reintenta más tarde, como con la rev A).
        """
        for _attempt in range(HELLO_ATTEMPTS):
            self._send(Command.HELLO, _HELLO_TEXT)
            answer = bytes(self._transport.read(10))
            self._transport.reset_input_buffer()
            if not answer:
                continue  # no ha llegado nada: otro saludo, hasta el tope
            sub = self.parse_hello(answer)
            if sub is None:
                raise WrongModel(self.device or "?", Text("pantallas.modelo_xuanfang"))
            self.flagship, self.full_range_brightness = SUB_REVISIONS[sub]
            return
        raise NoAnswer(self.device or "?")

    @staticmethod
    def parse_hello(answer: bytes) -> int | None:
        """La sub-revisión si ``answer`` es una respuesta de rev B válida; si no, None.

        Más estricto que la referencia (que solo avisa y sigue): aquí una respuesta mal
        formada significa «esa no es tu pantalla» y no se manda nada más.
        """
        if len(answer) != 10:
            return None
        if answer[0] != Command.HELLO or answer[9] != Command.HELLO:
            return None
        if answer[1:6] != _HELLO_TEXT or answer[6] != _HELLO_MARK:
            return None
        return answer[7] if answer[7] in SUB_REVISIONS else None

    @property
    def width(self) -> int:
        return HEIGHT if self.orientation.is_landscape else WIDTH

    @property
    def height(self) -> int:
        return WIDTH if self.orientation.is_landscape else HEIGHT

    def _send_brightness(self, percent: int) -> None:
        value = brightness_value(percent, full_range=self.full_range_brightness)
        self._send(Command.SET_BRIGHTNESS, bytes([value]))

    def set_brightness(self, percent: int) -> None:
        self._send_brightness(percent)
        self._brightness = percent

    def screen_off(self) -> None:
        """No hay comando de apagar: brillo 0 (si apaga del todo, sin verificar)."""
        self._send_brightness(0)

    def screen_on(self) -> None:
        self._send_brightness(self._brightness)

    def set_orientation(self, orientation: Orientation) -> None:
        """La pantalla solo sabe vertical (0) y horizontal (1). Las invertidas se hacen
        girando la imagen 180° en :meth:`draw_image`."""
        self.orientation = Orientation(orientation)
        self._reset_shadow()
        self._send(Command.SET_ORIENTATION, bytes([1 if self.orientation.is_landscape else 0]))

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        """Pinta ``image`` con su esquina superior izquierda en (x, y); lo que se salga
        se recorta.

        Si los píxeles se cortan a medias (o no salen en ``IMAGE_TIMEOUT``), la pantalla
        puede quedarse esperando el resto: :class:`ImageCut`, no un error cualquiera."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(
                f"({x}, {y}) está fuera de la pantalla {self.width}x{self.height}"  # i18n: no
            )
        w = min(image.width, self.width - x)
        h = min(image.height, self.height - y)
        if (w, h) != image.size:
            image = image.crop((0, 0, w, h))
        self._shadow.paste(image.convert("RGB"), (x, y))
        # Precaución [ESTIMACIÓN]: en la rev A la pantalla pierde el último bloque de 64
        # bytes si queda incompleto (medido). En la rev B nadie lo ha medido; ampliar el
        # rectángulo con lo que ya había en pantalla no estropea nada y lo evitaría.
        x, y, w, h = aligned_rect(x, y, w, h, self.width, self.height)
        region = self._shadow.crop((x, y, x + w, y + h))
        if self.orientation.is_reversed:
            # Invertidas por software: imagen girada 180° y coordenadas reflejadas.
            region = region.rotate(180)
            x, y = self.width - x - w, self.height - y - h
        # Coordenadas inclusivas, 16 bits big-endian cada una: x0, y0, x1, y1.
        box = b"".join(v.to_bytes(2, "big") for v in (x, y, x + w - 1, y + h - 1))
        data = to_rgb565_be(region)
        chunk = self.width * 8
        deadline = self._monotonic() + IMAGE_TIMEOUT
        # Desde la cabecera: si se corta ahí, tampoco se sabe cuántos bytes ha tomado.
        try:
            self._send(Command.DISPLAY_BITMAP, box)
            for start in range(0, len(data), chunk):
                if self._monotonic() > deadline:
                    raise DisplayError(Text("pantallas.conexion_perdida"))
                self._write(data[start : start + chunk])
        except DisplayError as exc:
            raise ImageCut() from exc
        self._sleep(BITMAP_COOLDOWN)
