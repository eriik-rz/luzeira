"""Driver de la Turing Smart Screen 3,5" revisión A (y UsbPCMonitor 3,5"/5"/7").

Escrito desde la descripción del protocolo (skill ``protocolo-turing-rev-a``), sin
copiar código de otros proyectos.

Resumen del protocolo:

- Puerto serie USB (CDC), 115200 baudios nominales, control de flujo RTS/CTS.
- Cada comando son 6 bytes: cuatro coordenadas de 10 bits empaquetadas + el código.
- Las imágenes se envían en RGB565 little-endian tras un comando DISPLAY_BITMAP.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntEnum

from PIL import Image

from luzeira.idioma import t
from luzeira.pantallas.comun import (
    BAUDRATE,
    WRITE_TIMEOUT,
    ConfirmationNeeded,
    DisplayBusy,
    DisplayError,
    Orientation,
    Transport,
    open_error,
)
from luzeira.pantallas.rgb565 import to_rgb565_le

__all__ = [  # lo que otros módulos importan de aquí (parte vive ya en ``comun``)
    "BAUDRATE",
    "WRITE_TIMEOUT",
    "ConfirmationNeeded",
    "DisplayBusy",
    "DisplayError",
    "Orientation",
    "PortMatch",
    "Transport",
    "TuringRevA",
    "find_port",
]

USB_VID = 0x1A86
USB_PID = 0x5722
USB_SERIAL = "USB35INCHIPSV2"
USB_SERIAL_PREFIX = "USB35INCHIPS"


class Command(IntEnum):
    """Códigos de comando de la revisión A."""

    RESET = 101
    CLEAR = 102
    TO_BLACK = 103
    SCREEN_OFF = 108
    SCREEN_ON = 109
    SET_BRIGHTNESS = 110
    SET_ORIENTATION = 121
    DISPLAY_BITMAP = 197
    HELLO = 69


@dataclass(frozen=True)
class Model:
    name: str
    width: int  # en vertical (orientación nativa)
    height: int


TURING_3_5 = Model('Turing Smart Screen 3,5"', 320, 480)
USBMONITOR_3_5 = Model('UsbPCMonitor 3,5"', 320, 480)
USBMONITOR_5 = Model('UsbPCMonitor 5"', 480, 800)
USBMONITOR_7 = Model('UsbPCMonitor 7"', 600, 1024)

# Respuesta a HELLO (6 bytes iguales) -> modelo. El Turing oficial no responde.
_HELLO_ANSWERS = {0x01: USBMONITOR_3_5, 0x02: USBMONITOR_5, 0x03: USBMONITOR_7}


def pack_command(command: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> bytes:
    """Empaqueta un comando en 6 bytes: x, y, ex, ey de 10 bits cada uno + el código."""
    for name, value in (("x", x), ("y", y), ("ex", ex), ("ey", ey)):
        if not 0 <= value <= 1023:
            raise ValueError(f"{name}={value} fuera de rango (0-1023)")
    return bytes(
        (
            x >> 2,
            ((x & 0b11) << 6) | (y >> 4),
            ((y & 0b1111) << 4) | (ex >> 6),
            ((ex & 0b111111) << 2) | (ey >> 8),
            ey & 0xFF,
            command,
        )
    )


def brightness_to_byte(percent: int) -> int:
    """Brillo 0-100 % -> valor del protocolo. Va invertido: 100 % es 0 (máximo)."""
    if not 0 <= percent <= 100:
        raise ValueError(f"brillo {percent} fuera de rango (0-100)")
    return int(255 - percent / 100 * 255)


@dataclass(frozen=True)
class PortMatch:
    """Puerto candidato y cómo se reconoció."""

    device: str
    by_serial: bool  # True: el número de serie identifica la rev A con seguridad


BLOCK_BYTES = 64  # la pantalla procesa los píxeles en bloques de este tamaño


def aligned_rect(
    x: int, y: int, w: int, h: int, screen_w: int, screen_h: int
) -> tuple[int, int, int, int]:
    """El rectángulo más pequeño que contiene (x, y, w, h), cabe en la pantalla y ocupa un
    número exacto de bloques de 64 bytes (2 bytes por píxel → ancho x alto múltiplo de 32).

    Siempre hay solución: el ancho y el alto de las pantallas son múltiplos de 32, así que
    en el peor caso basta con filas enteras.
    """
    pixels_per_block = BLOCK_BYTES // 2
    if (w * h) % pixels_per_block == 0:
        return x, y, w, h
    best: tuple[int, int, int, int] | None = None
    for new_h in range(h, screen_h + 1):
        for new_w in range(w, screen_w + 1):
            if (new_w * new_h) % pixels_per_block:
                continue
            if best is None or new_w * new_h < best[2] * best[3]:
                # Crece hacia la derecha/abajo; si choca con el borde, se desplaza.
                best = (min(x, screen_w - new_w), min(y, screen_h - new_h), new_w, new_h)
            break  # para este alto, el primer ancho válido es el menor
        if best is not None and new_h * w >= best[2] * best[3]:
            break  # más alto ya no puede dar un área menor
    if best is None:  # imposible con pantallas de lados múltiplos de 32
        raise ValueError(
            f"no hay rectángulo alineado para una pantalla {screen_w}x{screen_h}"  # i18n: no
        )
    return best


def find_port(ports: Iterable | None = None) -> PortMatch | None:
    """Busca la pantalla por número de serie o VID/PID. Nunca por número de COM.

    ⚠ El VID/PID 1A86:5722 lo comparten otros modelos, entre ellos la XuanFang 3,5",
    que habla otro protocolo (rev B). Por eso se prefiere el número de serie, y una
    coincidencia solo por VID/PID se devuelve marcada como no segura.
    """
    if ports is None:
        from serial.tools.list_ports import comports

        ports = comports()
    fallback: PortMatch | None = None
    for port in ports:
        serial_number = getattr(port, "serial_number", None) or ""
        if serial_number.startswith(USB_SERIAL_PREFIX):
            return PortMatch(port.device, by_serial=True)
        if fallback is None and (
            getattr(port, "vid", None) == USB_VID and getattr(port, "pid", None) == USB_PID
        ):
            fallback = PortMatch(port.device, by_serial=False)
    return fallback


class TuringRevA:
    """Pantalla de revisión A. Se crea con :meth:`open` o pasándole un transporte."""

    model_key = "turing-rev-a"  # clave del catálogo (``pantallas.modelos``)

    def __init__(self, transport: Transport, model: Model = TURING_3_5) -> None:
        self._transport = transport
        self.model = model
        self.device: str | None = None  # puerto, si se abrió con :meth:`open`
        self.hello_answer = b""  # respuesta cruda al último HELLO (para validar a mano)
        self.orientation = Orientation.PORTRAIT
        self._reset_shadow()

    def _reset_shadow(self) -> None:
        """Copia en memoria de lo que muestra la pantalla (ver ``draw_image``). Al abrir,
        al girar o al borrar no se sabe / es negro: se empieza en negro."""
        self._shadow = Image.new("RGB", (self.width, self.height), "black")

    # --- conexión -------------------------------------------------------------

    @classmethod
    def open(
        cls,
        port: str | None = None,
        *,
        retries: int = 10,
        delay: float = 1.0,
        serial_factory: Callable[..., Transport] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> TuringRevA:
        """Abre la pantalla, reintentando: al abrir el puerto la pantalla se reinicia
        y el COM puede tardar en reaparecer.

        ``port`` explícito = el usuario ya ha confirmado que es una rev A. Sin él, solo
        se abre sola una pantalla reconocida por su número de serie: si únicamente
        coincide el VID/PID no se envía ni un byte (podría ser una rev B) y se pide
        confirmación con :class:`ConfirmationNeeded`.
        """
        if serial_factory is None:
            import serial

            def serial_factory(device: str) -> Transport:
                return serial.Serial(
                    device, BAUDRATE, timeout=1, write_timeout=WRITE_TIMEOUT, rtscts=True
                )

        last_error = DisplayError(t("pantallas.no_encuentro"))
        for attempt in range(max(1, retries)):
            if port is not None:
                device, confirmed = port, True
            else:
                match = find_port()
                device = match.device if match else None
                confirmed = bool(match and match.by_serial)
            if device is None:
                last_error = DisplayError(t("pantallas.no_encuentro"))
            elif not confirmed:
                raise ConfirmationNeeded(device)
            else:
                transport = None
                try:
                    transport = serial_factory(device)
                    display = cls(transport)
                    display.device = device
                    display.model = display.hello()
                    display._reset_shadow()  # el tamaño depende del modelo
                    return display
                # serial.SerialException hereda de OSError; DisplayError sale de _write
                # si el puerto abre pero el saludo falla (la pantalla aún se reinicia).
                except (OSError, DisplayError) as exc:
                    # 🔴 Cerrar YA el puerto que sí llegó a abrirse. Si se deja al recolector,
                    # el siguiente intento choca con nuestro propio handle y sale un falso
                    # «en uso por otro programa» (revisor de la fase 4, medido).
                    if transport is not None:
                        with contextlib.suppress(Exception):
                            transport.close()
                    # Ocupada por otro programa o el genérico «no puedo abrirla» (con la
                    # causa en ``__cause__``, para el log): lo mismo que el resto de drivers.
                    last_error = open_error(device, exc)
            if attempt < retries - 1:
                sleep(delay)
        raise last_error

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> TuringRevA:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- comandos -------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        """Escribe en el puerto. Un USB desenchufado o colgado da un error para el usuario,
        no una traza de pyserial (``SerialException`` hereda de ``OSError``)."""
        try:
            self._transport.write(data)
        except OSError as exc:
            raise DisplayError(t("pantallas.conexion_perdida")) from exc

    def _send(self, command: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> None:
        self._write(pack_command(command, x, y, ex, ey))

    def hello(self) -> Model:
        """Pregunta el submodelo. El Turing oficial no responde: se asume 3,5"."""
        self._write(bytes([Command.HELLO] * 6))
        answer = self._transport.read(6)
        self._transport.reset_input_buffer()
        self.hello_answer = bytes(answer)
        if len(answer) == 6 and len(set(answer)) == 1:
            return _HELLO_ANSWERS.get(answer[0], TURING_3_5)
        return TURING_3_5

    @property
    def width(self) -> int:
        return self.model.height if self.orientation.is_landscape else self.model.width

    @property
    def height(self) -> int:
        return self.model.width if self.orientation.is_landscape else self.model.height

    def screen_on(self) -> None:
        self._send(Command.SCREEN_ON)

    def screen_off(self) -> None:
        self._send(Command.SCREEN_OFF)

    def set_brightness(self, percent: int) -> None:
        self._send(Command.SET_BRIGHTNESS, brightness_to_byte(percent))

    def set_orientation(self, orientation: Orientation) -> None:
        """Trama especial de 16 bytes: cabecera de 6 + orientación + ancho y alto."""
        self.orientation = Orientation(orientation)
        self._reset_shadow()
        frame = bytearray(16)
        frame[0:6] = pack_command(Command.SET_ORIENTATION)
        frame[6] = self.orientation + 100
        frame[7:9] = self.width.to_bytes(2, "big")
        frame[9:11] = self.height.to_bytes(2, "big")
        self._write(bytes(frame))

    def clear(self) -> None:
        """Borra la pantalla. El comando solo funciona en vertical, así que se pasa a
        vertical y luego se restaura la orientación que hubiera."""
        previous = self.orientation
        if previous != Orientation.PORTRAIT:
            self.set_orientation(Orientation.PORTRAIT)
        self._send(Command.CLEAR)
        self._reset_shadow()
        if previous != Orientation.PORTRAIT:
            self.set_orientation(previous)

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        """Pinta ``image`` con su esquina superior izquierda en (x, y).

        Lo que se salga de la pantalla se recorta. Se envía en trozos de
        ``ancho_pantalla * 8`` bytes, sin esperar respuesta entre trozos.
        """
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(
                f"({x}, {y}) está fuera de la pantalla {self.width}x{self.height}"  # i18n: no
            )
        w = min(image.width, self.width - x)
        h = min(image.height, self.height - y)
        if (w, h) != image.size:
            image = image.crop((0, 0, w, h))
        self._shadow.paste(image.convert("RGB"), (x, y))
        # 🔴 La pantalla procesa los píxeles en bloques de 64 bytes y PIERDE el último bloque
        # si está incompleto (medido 2026-11-17 en una pantalla real: una barra de 300x22
        # = 13.200 B dejaba sus últimos 8 píxeles sin pintar; 300x24 y 304x22, múltiplos
        # de 64, salían limpias). Se amplía el rectángulo hasta un múltiplo de 64 bytes y lo
        # que se añade se rellena con lo que ya había en pantalla (la copia ``_shadow``).
        x, y, w, h = aligned_rect(x, y, w, h, self.width, self.height)
        data = to_rgb565_le(self._shadow.crop((x, y, x + w, y + h)))
        # Coordenadas inclusivas: la última columna es x + w - 1.
        self._send(Command.DISPLAY_BITMAP, x, y, x + w - 1, y + h - 1)
        chunk = self.width * 8
        for start in range(0, len(data), chunk):
            self._write(data[start : start + chunk])
