"""Driver **beta** de la Turing «revisión C»: 2,1" (y 2,8" redonda), 5" y 8,8".

Escrito con palabras y estructura propias desde la descripción del informe
``docs/investigacion/tanda-4/otras-pantallas/informe.md`` (sección «Protocolo rev C»).
Los datos que el informe no daba se contrastaron (solo lectura, 2026-11-25) con:

- ``library/lcd/lcd_comm_rev_c.py`` y ``library/lcd/serialize.py`` de
  turing-smart-screen-python (GPL-3.0, https://github.com/mathoudebine/turing-smart-screen-python,
  ``main`` = ``36ebc0a``; el ``lcd_comm_rev_c.py`` es idéntico al del commit ``866e5ba``
  que leyó el informe). No se ha copiado su código: solo hechos (valores de bytes,
  formatos, orden de los mensajes).
- Su issue #90 (lista de órdenes capturadas a la app del fabricante), #1030 y #1075
  (respuestas reales al saludo, el 5" en Windows y en Linux), #1022 (el 2,8" redondo).
- ``docs/protocol/serial.md`` y ``rust/devices/src/turing_rev_c.rs`` de Libre Panel
  (GPL-3.0, https://github.com/syntensa/libre-panel, ``main`` = ``d8f76a9``): la regla de
  los mensajes que la pantalla pierde y el fallo del 8,8" en vertical de la referencia.

⚠ Beta: nadie lo ha probado aún con una pantalla de verdad.

Cómo habla esta pantalla:

- Va por **bloques de 250 bytes**: cada mensaje se rellena con ceros hasta un múltiplo de
  250, y el último byte de cada bloque **no se lee** (#90): los datos largos se cortan en
  trozos de 249 con un ``00`` entre trozo y trozo.
- Las órdenes empiezan por su código y ``EF 69 00 00 00``.
- Duerme: con nadie usándola, Windows solo ve su «puente» USB (``1a86:ca21``/``ca50``).
  Abrir el puerto del puente la despierta y aparece **otro** aparato USB a la vez, el que
  de verdad dibuja (ids de «gadget» de Linux con el serie ``20080411``). Eso lo hace
  ``pantallas.apertura`` con :func:`wake`; este driver habla con el despierto.
- El saludo dice la versión de su firmware («ROM»), pero **no el tamaño** (una de 2,1"
  contesta «5inch»): el tamaño lo elige el usuario.

🔴 Lista cerrada: este módulo solo sabe las nueve órdenes de :class:`Command`. Las que
guardan ajustes en la pantalla, la reinician, la apagan o tocan sus ficheros **no existen
aquí** ni como constante (``tests/test_pantallas_seguridad.py`` lo vigila). El giro se hace
girando la imagen en el PC, nunca con la orden de ajustes (que además pisaría la
configuración que el dueño puso con la app del fabricante).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntEnum

from PIL import Image

from luzeira.idioma import Text
from luzeira.pantallas.comun import (
    DisplayBusy,
    DisplayError,
    ImageCut,
    NoAnswer,
    Orientation,
    SerialFactory,
    Transport,
    WrongModel,
    default_serial_factory,
    is_access_denied,
    open_probed,
    write_all,
)
from luzeira.pantallas.modelos import PortInfo, is_rev_c_awake, model_by_key

BLOCK = 250  # la pantalla lee de 250 en 250
PIECE = BLOCK - 1  # bytes útiles por bloque: el último de cada bloque no se lee (#90)
MARK = b"\xef\x69"  # la «firma» que llevan detrás todas las órdenes
TAIL = MARK + bytes(3)  # código + esto = el principio de una orden


class Command(IntEnum):
    """El primer byte de cada orden que usa Luzeira. No hay más."""

    HELLO = 0x01
    SET_BRIGHTNESS = 0x7B
    STOP_VIDEO = 0x79  # deja de reproducir un vídeo guardado (lo hacen la app y la referencia)
    STOP_MEDIA = 0x96  # ídem con lo demás guardado; contesta 1024 bytes
    PRE_UPDATE_BITMAP = 0x86
    START_DISPLAY_BITMAP = 0x2C  # va solo, rellenado con 0x2C en vez de ceros
    DISPLAY_BITMAP = 0xC8  # cabecera de una imagen completa
    UPDATE_BITMAP = 0xCC  # cabecera de una actualización parcial
    QUERY_STATUS = 0xCF  # «¿has perdido algo?»: contesta 1024 bytes


def order(command: Command, *data: int) -> bytes:
    """Una orden corta: código + ``EF 69 00 00 00`` + sus datos (sin rellenar)."""
    return bytes([command]) + TAIL + bytes(data)


HELLO = order(Command.HELLO, 1, 0, 0, 0, 0xC5, 0xD3)
STOP_VIDEO = order(Command.STOP_VIDEO, 1)
STOP_MEDIA = order(Command.STOP_MEDIA, 1)
PRE_UPDATE_BITMAP = order(Command.PRE_UPDATE_BITMAP, 1)
QUERY_STATUS = order(Command.QUERY_STATUS, 1)
BRIGHTNESS = order(Command.SET_BRIGHTNESS, 1, 0, 0, 0)  # + 1 byte: 0-255, mayor = más brillo
IMAGE_HEAD = bytes([Command.DISPLAY_BITMAP]) + MARK + b"\x00"  # + 2 B tamaño + 2 B (ver abajo)
UPDATE_HEAD = bytes([Command.UPDATE_BITMAP]) + MARK + b"\x00"  # + 3 B tamaño + 3 ceros + 4 B nº

HELLO_ANSWER = 23  # la referencia lee 23 bytes («chs_5inch.dev1_rom1.88» son 22)
STATUS_ANSWER = 1024  # casi todas las respuestas vienen rellenadas a 1024 (#90)
HELLO_ATTEMPTS = 4  # solo si no llega nada; la referencia reintenta sin fin (#1030)
HELLO_PAUSE = 1.0
# ROM: el tercer campo separado por puntos («…rom1.88» → 88). La referencia solo se fía de
# 80-100; fuera de eso, o si no se entiende, usa 87. Aquí igual: es el valor por defecto
# documentado (R5, punto 7) y con él las parciales van en BGR de 3 bytes, como en la
# referencia con una ROM desconocida.
ROM_RANGE = range(80, 101)
DEFAULT_ROM = 87
BGRA_FROM_ROM = 89  # con ROM > 88 (y si no es la de 2,1"), las parciales van en BGRA
# Lo que la referencia pone tras el tamaño de una imagen completa: 480·480/64 = 3600, el
# mismo en los tres tamaños. Qué significa no lo sabe nadie [SIN VERIFICAR]; un usuario
# del 5" confirma que con otro valor no se ve bien (comentario de Goliohs en #1030).
IMAGE_SECOND_FIELD = 480 * 480 // 64
# Escrituras de como mucho 64.000 bytes: múltiplo de 250 (bloques enteros) y de 512 y 64
# (los paquetes USB de alta y de plena velocidad), así que por el cable salen los mismos
# paquetes que con una sola escritura [ESTIMACIÓN: no medido]. Hace falta porque una
# imagen completa del 8,8" son ~3,7 MB y una sola escritura pasaría del ``WRITE_TIMEOUT``
# de 5 s a nada que el enlace vaya despacio; y así el plazo por imagen se mira entre trozos.
WRITE_PIECE = 64_000
# Plazo para una imagen entera (R5-04). Más largo que el común: el 8,8" manda ~3,7 MB por
# imagen completa y nadie ha medido la velocidad de este enlace [SIN MEDIR].
IMAGE_TIMEOUT = 60.0

# Despertar (lo hace ``apertura``): como la referencia, abrir el puente cada segundo hasta
# 15 veces, y esperar un segundo más cuando aparece el despierto (contesta un poco después).
# Tope de mensajes por recuadro al partirlo (:meth:`TuringRevC._plan`): si hacen falta
# más, va la imagen entera. Uno o dos es lo normal; el tope corta un caso raro sin fin.
MAX_BOXES = 8

WAKE_ATTEMPTS = 15
WAKE_PAUSE = 1.0
WAKE_SETTLE = 1.0


@dataclass(frozen=True)
class Size:
    """Un tamaño de la rev C.

    ``width`` x ``height`` es lo que ve el tema en vertical. La pantalla guarda la imagen en
    su propio orden (``rows`` filas de ``columns`` píxeles) y cada orientación es un giro
    de la imagen hasta ese orden (``turns``: grados en sentido antihorario, como
    ``Image.rotate``). Los giros salen de la referencia para la imagen completa; las
    parciales se colocan donde ese mismo giro pone sus píxeles (así lo hace Libre Panel:
    la referencia tiene mal la columna del 8,8" en vertical).
    """

    model_key: str
    width: int
    height: int
    rows: int
    columns: int
    turns: dict[Orientation, int]
    bgra_partials: bool  # False: las parciales siempre en BGR (la de 2,1"/2,8")

    @property
    def size_code(self) -> int:
        """El tamaño de la imagen completa en bloques de 256 bytes (BGRA = 4 B/píxel).
        Da los valores de la referencia: 3600, 6000 y 14400."""
        return self.width * self.height * 4 // 256


# Las de 2,1" y 5" guardan la imagen «tumbada» (480 filas); la de 8,8", de pie (1920).
_TURNS_LYING = {
    Orientation.PORTRAIT: 90,
    Orientation.REVERSE_PORTRAIT: 270,
    Orientation.LANDSCAPE: 0,
    Orientation.REVERSE_LANDSCAPE: 180,
}
_TURNS_STANDING = {
    Orientation.PORTRAIT: 180,
    Orientation.REVERSE_PORTRAIT: 0,
    Orientation.LANDSCAPE: 270,
    Orientation.REVERSE_LANDSCAPE: 90,
}
SIZE_2_1 = Size("turing-rev-c-21", 480, 480, 480, 480, _TURNS_LYING, bgra_partials=False)
SIZE_5 = Size("turing-rev-c-5", 480, 800, 480, 800, _TURNS_LYING, bgra_partials=True)
SIZE_8_8 = Size("turing-rev-c-88", 480, 1920, 1920, 480, _TURNS_STANDING, bgra_partials=True)
SIZES = {s.model_key: s for s in (SIZE_2_1, SIZE_5, SIZE_8_8)}

_TRANSPOSE = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


def padded(message: bytes, fill: int = 0) -> bytes:
    """``message`` rellenado con ``fill`` hasta un múltiplo de 250 bytes."""
    return message + bytes([fill]) * (-len(message) % BLOCK)


def cut(data: bytes) -> bytes:
    """``data`` en trozos de 249 bytes con un ``00`` entre cada dos (no tras el último):
    así ningún dato cae en el byte 250 de un bloque, que la pantalla no lee.

    La referencia solo corta las parciales de más de 250 bytes; aquí siempre (con 249 o
    menos no cambia nada). Con justo 250, la referencia deja el último en el byte que no
    se lee; aquí no hace falta: :func:`_loses_tail` ya parte esa actualización en dos."""
    return b"\x00".join(data[i : i + PIECE] for i in range(0, len(data), PIECE))


def parse_hello(answer: bytes) -> int | None:
    """La ROM si ``answer`` es la respuesta de una rev C; None si no lo es.

    Respuestas reales: ``chs_5inch.dev1_rom1.88`` y ``…rom1.89`` (#1030 en Windows, #1075,
    #1022, #870). Se exige: ASCII imprimible, que empiece por ``chs_`` y que quepa en los 23
    bytes que se leen (se toleran ceros y saltos de línea al final). Más estricto que la
    referencia, que tira lo que no es imprimible esté donde esté. La ROM fuera de 80-100 o
    sin entender no rechaza la pantalla (sería rechazar una de verdad con un firmware más
    nuevo): vale ``DEFAULT_ROM``, como en la referencia.
    """
    text = answer.rstrip(b"\x00\r\n ")
    if not text.startswith(b"chs_") or len(text) > HELLO_ANSWER:
        return None
    if any(not 0x20 <= byte <= 0x7E for byte in text):
        return None
    fields = text.split(b".")
    if len(fields) >= 3 and fields[2].isdigit() and len(fields[2]) <= 3:
        rom = int(fields[2])
        if rom in ROM_RANGE:
            return rom
    return DEFAULT_ROM


def looks_like_an_order(window: bytes) -> bool:
    """¿Estos bytes, al principio de un bloque, se leerían como una orden? Cualquier
    código seguido de ``EF 69 00 00 00`` (la forma de todas las órdenes cortas)."""
    return len(window) >= 1 + len(TAIL) and window[1 : 1 + len(TAIL)] == TAIL


def cut_length(raw_length: int) -> int:
    """Lo que ocupan ``raw_length`` bytes una vez cortados con :func:`cut`."""
    return raw_length + (raw_length - 1) // PIECE if raw_length else 0


def defuse(
    raw: bytearray, is_colour: Callable[[int], bool], *, trailer: bytes = b""
) -> bytes | None:
    """El mensaje de datos (``raw`` cortado, ``trailer`` detrás y rellenado) sin ningún
    bloque que empiece como una orden (R5-02, punto 5). None si no se puede.

    Un tema puede pintar píxeles cuyos bytes, justo al principio de un bloque de 250,
    digan «``[orden] EF 69 00 00 00``». La pantalla sabe por la cabecera cuántos datos
    vienen y no debería leerlos como orden; pero si perdiera la cuenta y buscara órdenes
    al principio de cada bloque, ejecutaría lo que el tema quisiera, también las que
    borran. Qué hace de verdad su firmware no se sabe: el peligro es una [ESTIMACIÓN]
    (modelo de amenazas, «Sin verificar»). Se mira con cualquier código, no solo los
    prohibidos: ningún dato tiene por qué parecer una orden.

    Arreglo: se cambia el bit bajo de un byte de **color** (``is_colour``, con índices de
    ``raw``) entre los cinco que siguen al primero, que en una orden son fijos: basta con
    uno. Es 1/255 en un canal de un píxel: no se ve. Si no hay ningún color ahí (solo pasa
    cuando la cola ``EF 69`` cae en esos cinco), None: quien llama parte el recuadro.
    """
    data_end = cut_length(len(raw))
    framed = bytearray(padded(cut(bytes(raw)) + trailer))
    for start in range(0, len(framed), BLOCK):
        if not looks_like_an_order(framed[start : start + 1 + len(TAIL)]):
            continue
        for f in range(start + 1, start + 1 + len(TAIL)):
            i = f - f // BLOCK  # el byte de ``raw`` que ``cut`` puso aquí
            if f < data_end and is_colour(i):
                raw[i] ^= 1
                framed[f] ^= 1
                break
        else:
            return None
    return bytes(framed)


class TuringRevC:
    """Pantalla Turing de revisión C (beta), ya despierta. Se crea con :meth:`open`."""

    # La prueba (el saludo) demuestra que es una rev C, pero no su tamaño ni que los
    # colores y giros salgan bien (nadie lo ha probado): quien la elige confirma que se
    # ve bien (bandeja, como la Kipye).
    verified = False

    def __init__(
        self,
        transport: Transport,
        size: Size,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._size = size
        self._sleep = sleep
        self._monotonic = monotonic
        self.device: str | None = None
        self.orientation = Orientation.PORTRAIT
        self.rom = DEFAULT_ROM
        self._brightness = 50  # el que se restaura al encender
        self._count = 0  # nº de actualización parcial (la pantalla lo devuelve en su estado)
        self._reset_shadow()

    @property
    def model_key(self) -> str:
        return self._size.model_key

    def _reset_shadow(self) -> None:
        # Copia de lo que hay en pantalla: para mandarla entera si la pantalla pierde algo.
        self._shadow = Image.new("RGB", (self.width, self.height), "black")
        self._needs_full = True  # hasta la primera imagen completa no se sabe qué hay

    @classmethod
    def open(
        cls,
        device: str,
        size: Size,
        *,
        retries: int = 1,
        delay: float = 1.0,
        serial_factory: SerialFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> TuringRevC:
        """Abre el puerto **despierto** ``device`` y saluda. Si no contesta como una rev C,
        cierra y :class:`WrongModel` sin mandar nada más."""
        return open_probed(
            device,
            lambda transport: cls(transport, size, sleep=sleep),
            retries=retries,
            delay=delay,
            serial_factory=serial_factory,
            sleep=sleep,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> TuringRevC:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- el puerto -------------------------------------------------------------

    def _send(self, message: bytes, *, fill: int = 0, deadline: float | None = None) -> None:
        """Un mensaje, rellenado a bloques enteros, en escrituras de ``WRITE_PIECE``."""
        data = padded(message, fill)
        for start in range(0, len(data), WRITE_PIECE):
            if deadline is not None and self._monotonic() > deadline:
                raise DisplayError(Text("pantallas.conexion_perdida"))
            write_all(self._transport, data[start : start + WRITE_PIECE])

    def _read(self, size: int) -> bytes:
        """Como mucho ``size`` bytes (y como mucho el timeout del puerto, 1 s). 🔴 Lo que
        contesta la pantalla no se registra nunca: puede traer cualquier cosa."""
        try:
            return bytes(self._transport.read(size))[:size]
        except OSError as exc:
            raise DisplayError(Text("pantallas.conexion_perdida")) from exc

    def _flush(self) -> None:
        try:
            self._transport.reset_input_buffer()
        except OSError as exc:
            raise DisplayError(Text("pantallas.conexion_perdida")) from exc

    def _ask_status(self) -> None:
        """«¿Has perdido algo?». Si contesta ``needReSend:1`` (#90: se perdió una parcial),
        la próxima imagen va entera: un reenvío, no un bucle (como Libre Panel)."""
        self._flush()
        self._send(QUERY_STATUS)
        if b"needReSend:1" in self._read(STATUS_ANSWER):
            self._needs_full = True

    # --- órdenes ---------------------------------------------------------------

    def probe(self) -> None:
        """La primera prueba: solo el saludo, antes que cualquier otra orden.

        - Contesta ``chs_…`` → se apunta la ROM.
        - Contesta otra cosa → :class:`WrongModel` (no se reintenta ni se manda nada más).
        - No contesta nada tras ``HELLO_ATTEMPTS`` saludos → :class:`NoAnswer`.
        """
        for attempt in range(HELLO_ATTEMPTS):
            self._flush()
            self._send(HELLO)
            answer = self._read(HELLO_ANSWER)
            self._flush()
            if answer.strip(b"\x00"):
                rom = parse_hello(answer)
                if rom is None:
                    raise WrongModel(self.device or "?", _label(self.model_key))
                self.rom = rom
                return
            if attempt < HELLO_ATTEMPTS - 1:
                self._sleep(HELLO_PAUSE)
        raise NoAnswer(self.device or "?")

    @property
    def width(self) -> int:
        return self._size.height if self.orientation.is_landscape else self._size.width

    @property
    def height(self) -> int:
        return self._size.width if self.orientation.is_landscape else self._size.height

    @property
    def bgra_partials(self) -> bool:
        return self._size.bgra_partials and self.rom >= BGRA_FROM_ROM

    def _send_brightness(self, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError(f"brillo {percent} fuera de rango (0-100)")
        self._send(BRIGHTNESS + bytes([int(percent / 100 * 255)]))

    def set_brightness(self, percent: int) -> None:
        self._send_brightness(percent)
        self._brightness = percent

    def screen_off(self) -> None:
        """Brillo 0. La orden de apagar de este protocolo no se usa: la pantalla cambia de
        identidad USB y hay que volver a despertarla (#90); y en un 2,8" ni apaga (#1022).
        Si el brillo 0 apaga del todo la luz: [SIN VERIFICAR]."""
        self._send_brightness(0)

    def screen_on(self) -> None:
        """Lo que hacen la app del fabricante y la referencia al empezar: parar el vídeo o
        lo que la pantalla reproduzca por su cuenta (si no, puede taparnos), y el brillo."""
        self._send(STOP_VIDEO)
        self._send(STOP_MEDIA)
        self._read(STATUS_ANSWER)
        self._send_brightness(self._brightness)

    def set_orientation(self, orientation: Orientation) -> None:
        """Solo en el PC: no se manda nada (la imagen se gira en :meth:`draw_image`)."""
        self.orientation = Orientation(orientation)
        self._reset_shadow()

    def draw_image(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        """Pinta ``image`` con su esquina superior izquierda en (x, y); lo que se salga
        se recorta.

        La primera imagen (y la siguiente a un giro o a que la pantalla diga que ha perdido
        algo) se manda entera: se pinta sobre la copia de lo que hay y va la copia.

        Si se corta a medias (o no sale en ``IMAGE_TIMEOUT``), la pantalla deja de contestar
        hasta que se reenchufa (#1030): :class:`ImageCut`, sin reintentar a ciegas.
        """
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(
                f"({x}, {y}) está fuera de la pantalla {self.width}x{self.height}"  # i18n: no
            )
        w = min(image.width, self.width - x)
        h = min(image.height, self.height - y)
        image = image.convert("RGB")
        if (w, h) != image.size:
            image = image.crop((0, 0, w, h))
        self._shadow.paste(image, (x, y))
        deadline = self._monotonic() + IMAGE_TIMEOUT
        try:
            if self._needs_full or (x, y, w, h) == (0, 0, self.width, self.height):
                self._send_full(self._shadow, deadline)
            else:
                self._send_partial(image, x, y, deadline)
        except DisplayError as exc:
            raise ImageCut() from exc

    # --- imágenes --------------------------------------------------------------

    def _turn(self, image: Image.Image) -> Image.Image:
        turns = self._size.turns[self.orientation]
        return image.transpose(_TRANSPOSE[turns]) if turns else image

    def _send_full(self, image: Image.Image, deadline: float) -> None:
        """Imagen completa: aviso, bloque de ``2C``, cabecera con el tamaño y los píxeles en
        BGRA (4 bytes, alfa siempre ``FF``) ya girados al orden de la pantalla."""
        native = self._turn(image).convert("RGBA")  # de RGB: alfa = FF en todos
        raw = bytearray(native.tobytes("raw", "BGRA"))
        # Con el alfa a FF cada 4 bytes, ningún bloque puede empezar como una orden (lleva
        # tres ceros seguidos y ningún FF): ``defuse`` no toca nada (lo comprueban los
        # tests), pero se pasa igual por si algún día cambia el formato.
        body = defuse(raw, lambda i: i % 4 != 3)
        if body is None:  # pragma: no cover - sin colas no puede pasar
            raise DisplayError(Text("pantallas.conexion_perdida"))
        head = IMAGE_HEAD + self._size.size_code.to_bytes(2, "big")
        head += IMAGE_SECOND_FIELD.to_bytes(2, "big")
        self._send(PRE_UPDATE_BITMAP, deadline=deadline)
        self._send(bytes([Command.START_DISPLAY_BITMAP]), fill=Command.START_DISPLAY_BITMAP)
        self._send(head, deadline=deadline)
        self._send(body, deadline=deadline)
        self._read(STATUS_ANSWER)  # «full_png_sucess», a veces 1-3 s después (#1030)
        self._needs_full = False
        self._ask_status()

    def _box(self, image: Image.Image, x: int, y: int) -> tuple[Image.Image, int, int]:
        """El recuadro (x, y, w, h) del tema, girado: su imagen y su primera fila y columna
        en el orden de la pantalla."""
        turns = self._size.turns[self.orientation]
        w, h, width, height = image.width, image.height, self.width, self.height
        row, column = {
            0: (y, x),
            90: (width - x - w, y),
            180: (height - y - h, width - x - w),
            270: (x, height - y - h),
        }[turns]
        return self._turn(image), row, column

    def _rows(self, image: Image.Image, x: int, y: int) -> tuple[bytearray, int, int]:
        """Los datos de una parcial: por cada fila, su posición en la memoria de la
        pantalla (3 bytes), su ancho (2) y sus píxeles. Devuelve también la longitud de
        cada fila y los bytes por píxel."""
        native, row, column = self._box(image, x, y)
        bpp = 4 if self.bgra_partials else 3
        pixels = (
            native.convert("RGBA").tobytes("raw", "BGRA")
            if bpp == 4
            else native.tobytes("raw", "BGR")
        )
        line = native.width * bpp
        raw = bytearray()
        for i in range(native.height):
            raw += ((row + i) * self._size.columns + column).to_bytes(3, "big")
            raw += native.width.to_bytes(2, "big")
            raw += pixels[i * line : (i + 1) * line]
        return raw, 5 + line, bpp

    def _send_partial(self, image: Image.Image, x: int, y: int, deadline: float) -> None:
        """Una parcial, en los recuadros que hagan falta para que la pantalla no pierda
        ninguna (:func:`_loses_tail`). Si salen más de ``MAX_BOXES``, la imagen entera."""
        boxes = self._plan(image, x, y)
        if boxes is None:
            self._send_full(self._shadow, deadline)
            return
        for box, bx, by in boxes:
            if not self._send_box(box, bx, by, deadline):
                self._send_full(self._shadow, deadline)  # no debería pasar: ver _plan
                return

    def _fits(self, w: int, h: int) -> bool:
        """¿Un recuadro de w x h (del tema) cabe en un solo mensaje sin perder la cola?
        Se calcula con lo que ocupa: las filas de la pantalla, no las del tema (con el
        giro, el ancho del tema puede ser el alto en la memoria)."""
        turns = self._size.turns[self.orientation]
        columns, rows = (w, h) if turns in (0, 180) else (h, w)
        bpp = 4 if self.bgra_partials else 3
        return not _loses_tail(cut_length(rows * (5 + columns * bpp)))

    def _plan(self, image: Image.Image, x: int, y: int) -> list[_Box] | None:
        """Los recuadros que se mandan, en orden, o None si pasan de ``MAX_BOXES``.

        Sin recursión: una lista de pendientes. Un recuadro que no cabe se parte en dos con
        :func:`_cuts`, eligiendo el corte cuyo trozo grande ya cabe. Hace falta probar los
        dos sentidos (revisor de la fase 5): si cada fila de la pantalla ocupa 249 bytes
        (BGRA, 61 píxeles de ancho), quitarle filas no cambia nada y hay que quitarle una
        columna."""
        ready: list[_Box] = []
        pending: list[_Box] = [(image, x, y)]
        while pending:
            if len(ready) + len(pending) > MAX_BOXES:
                return None
            box = pending.pop(0)
            if self._fits(*box[0].size):
                ready.append(box)
                continue
            ways = _cuts(*box)
            if not ways:
                return None  # 1 x 1 siempre cabe: no se llega
            chosen = next((o for o in ways if self._fits(*o[0][0].size)), ways[-1])
            pending[:0] = chosen
        return ready

    def _send_box(self, image: Image.Image, x: int, y: int, deadline: float) -> bool:
        raw, row_len, bpp = self._rows(image, x, y)

        def is_colour(i: int) -> bool:
            k = i % row_len - 5  # los 5 primeros de cada fila son posición y ancho
            return k >= 0 and (bpp == 3 or k % 4 != 3)

        body = defuse(raw, is_colour, trailer=MARK)
        if body is None:
            return False
        # El tamaño cuenta los datos sin los ``00`` de ``cut`` y con la cola ``EF 69``.
        head = UPDATE_HEAD + (len(raw) + len(MARK)).to_bytes(3, "big") + bytes(3)
        head += (self._count & 0xFFFFFFFF).to_bytes(4, "big")
        self._send(head, deadline=deadline)
        self._send(body, deadline=deadline)
        self._count += 1
        self._ask_status()
        return True


_Box = tuple[Image.Image, int, int]  # imagen y su esquina en el tema


def _label(model_key: str) -> Text | str:
    model = model_by_key(model_key)
    return model.label_text if model is not None else model_key


def _loses_tail(length: int) -> bool:
    """¿La pantalla perdería (o podría leer como orden) una parcial de ``length`` bytes ya
    cortados? Detrás van ``EF 69`` y el relleno.

    - Libre Panel (``turing_rev_c.rs``): si ``EF 69`` empieza un bloque, queda partido
      entre dos o lo acaba justo, la pantalla pierde la actualización y se queda parada
      con ``needReSend:1``. Es decir, ``length % 250`` = 0, 249 o 248. [DATO de Libre
      Panel; el mecanismo no lo explica]
    - Nuestra: con 1, el bloque empieza por el último byte de color y sigue
      ``EF 69 00 00 00`` (la cola y el relleno): la forma de una orden, sin ningún color
      que tocar para romperla (:func:`defuse`).
    """
    return length % BLOCK in (0, 1, BLOCK - 2, BLOCK - 1)


def _cuts(image: Image.Image, x: int, y: int) -> list[list[_Box]]:
    """Las formas de partir un recuadro en dos: quitándole la última fila y quitándole la
    última columna (cada una, si tiene más de una). Cada forma es [trozo grande, tira].

    Que hay longitudes que la pantalla pierde y que partir el recuadro lo arregla es de
    Libre Panel (``rust/devices/src/turing_rev_c.rs``, ``d8f76a9``); cómo y por dónde se
    parte aquí es nuestro (:meth:`TuringRevC._plan`)."""
    w, h = image.size
    ways = []
    if h > 1:
        ways.append(
            [(image.crop((0, 0, w, h - 1)), x, y), (image.crop((0, h - 1, w, h)), x, y + h - 1)]
        )
    if w > 1:
        ways.append(
            [(image.crop((0, 0, w - 1, h)), x, y), (image.crop((w - 1, 0, w, h)), x + w - 1, y)]
        )
    return ways


# --- despertar -------------------------------------------------------------------------


def wake(
    bridge_device: str,
    list_now: Callable[[], Iterable[PortInfo]],
    *,
    serial_factory: SerialFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Despierta la pantalla cuyo puente es ``bridge_device`` y devuelve el COM del aparato
    despierto que aparece **a raíz de eso**.

    ``list_now`` da los puertos que hay ahora (sin abrir ninguno). De ellos solo cuentan
    los que parecen una rev C despierta (ids de gadget **y** serie ``20080411``,
    :func:`~luzeira.pantallas.modelos.is_rev_c_awake`), y no los que ya estaban antes de
    empezar: no se sabe de quién son (una Raspberry Pi en modo gadget, otra pantalla).
    Solo vale si aparece **uno** nuevo; con dos, no se sabe cuál es y no se abre ninguno.

    Despertar = abrir y cerrar el puerto del puente (al abrir, Windows sube DTR: es lo que
    la despierta, #1030), hasta ``WAKE_ATTEMPTS`` veces con ``WAKE_PAUSE`` entre medias. Al
    puente no se le escribe nada. Si otro programa lo tiene abierto: :class:`DisplayBusy`.
    """
    factory = serial_factory or default_serial_factory

    def awake() -> dict[tuple, str]:
        # Por COM, USB y serie (la descripción que da Windows no cuenta).
        return {(p.device, p.usb_id, p.serial): p.device for p in list_now() if is_rev_c_awake(p)}

    before = awake().keys()
    for _attempt in range(WAKE_ATTEMPTS):
        try:
            factory(bridge_device).close()
        except OSError as exc:
            if is_access_denied(exc):
                raise DisplayBusy(bridge_device) from exc
            # Otro fallo al abrir: puede estar cambiando de identidad; se sigue mirando.
        now = awake()
        new = [device for key, device in now.items() if key not in before]
        if len(new) == 1:
            sleep(WAKE_SETTLE)  # contesta un momento después de aparecer
            return new[0]
        if len(new) > 1:
            break
        sleep(WAKE_PAUSE)
    error = DisplayError(Text("pantallas.no_despierta", device=bridge_device))
    error.says_retrying = True
    raise error
