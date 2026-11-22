"""Lo que comparten todos los drivers de pantalla: el puerto, los errores y la orientación.

Los cuatro protocolos de puerto serie de esta familia (rev A, B, C y D) abren el puerto
igual: 115200 baudios nominales (es USB CDC: no limitan la velocidad real), control de
flujo RTS/CTS y 1 s de espera de lectura. Fuente: ``library/lcd/lcd_comm.py`` de
turing-smart-screen-python (GPL-3.0), resumido en
``docs/investigacion/tanda-4/otras-pantallas/informe.md``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

from luzeira.idioma import Text

if TYPE_CHECKING:
    from luzeira.pantallas.modelos import DisplayChoice

BAUDRATE = 115200
# Sin límite, una escritura con CTS bajo (pantalla colgada) se bloquea para siempre y el
# puerto nunca se cierra (revisor de la fase 3). 5 s sobra: un trozo de 2560 bytes tarda
# ~11 ms a los ~230 KB/s medidos.
WRITE_TIMEOUT = 5.0


class Orientation(IntEnum):
    PORTRAIT = 0
    REVERSE_PORTRAIT = 1
    LANDSCAPE = 2
    REVERSE_LANDSCAPE = 3

    @property
    def is_landscape(self) -> bool:
        return self in (Orientation.LANDSCAPE, Orientation.REVERSE_LANDSCAPE)

    @property
    def is_reversed(self) -> bool:
        return self in (Orientation.REVERSE_PORTRAIT, Orientation.REVERSE_LANDSCAPE)


class DisplayError(Exception):
    """Error al hablar con la pantalla, con un mensaje que se puede enseñar al usuario.

    El mensaje se escribe en el idioma activo al crear el error. Se crea con un
    :class:`~luzeira.idioma.Text` (clave + variables) para que el servicio pueda reescribirlo
    si se cambia de idioma mientras se ve (C-62); un ``str`` suelto también vale."""

    #: True si el mensaje ya dice que se sigue intentando: el servicio no lo repite.
    #: (Antes se buscaba la palabra «intentando» en el texto, que en otro idioma no está.)
    says_retrying = False


class DisplayBusy(DisplayError):
    """El puerto existe pero no se puede abrir: casi siempre otro programa lo tiene abierto
    (UsbPCMonitor, Libre Panel, otra Luzeira). La causa original va en ``__cause__``."""

    def __init__(self, device: str) -> None:
        self.device = device
        super().__init__(Text("pantallas.en_uso", device=device))


class ConfirmationNeeded(DisplayError):
    """Hay una pantalla con el USB de la rev A pero sin su número de serie: puede ser
    otro modelo con otro protocolo. No se le envía nada hasta que el usuario confirme.

    ``choice`` es None: lo que se confirma es la revisión A (la subclase
    :class:`ModelToConfirm` lleva otro modelo)."""

    choice: DisplayChoice | None = None

    def __init__(self, device: str, message: str | None = None) -> None:
        self.device = device
        super().__init__(message or Text("pantallas.confirmar_rev_a", device=device))


class ModelToConfirm(ConfirmationNeeded):
    """Hay una pantalla que parece de un modelo en beta, pero solo por su USB: no se le
    manda nada hasta que el usuario diga que es la suya. Lleva la elección propuesta."""

    def __init__(self, choice: DisplayChoice, label: str) -> None:
        self.choice = choice
        super().__init__(
            choice.device, Text("pantallas.confirmar_modelo", device=choice.device, model=label)
        )


class WrongModel(DisplayError):
    """La pantalla no ha contestado al saludo como el modelo elegido. El puerto ya está
    cerrado y no se ha probado ningún otro modelo."""

    def __init__(self, device: str, label: str) -> None:
        self.device = device
        super().__init__(Text("pantallas.modelo_equivocado", device=device, model=label))


class NoAnswer(DisplayError):
    """La pantalla no ha contestado nada al saludo. No es «otro modelo» (eso es
    :class:`WrongModel`): puede estar aún arrancando, así que se reintenta como cualquier
    otro fallo al abrir. El puerto ya está cerrado."""

    says_retrying = True

    def __init__(self, device: str) -> None:
        self.device = device
        super().__init__(Text("pantallas.no_contesta", device=device))


class UnsupportedModel(DisplayError):
    """Una pantalla reconocida por su USB que Luzeira aún no sabe manejar. No se abre.

    Se sigue buscando (enumerar puertos no manda nada): si se conecta otra pantalla
    compatible, se usa sola. El mensaje ya lo dice; no lleva el «lo sigo intentando»."""

    def __init__(self, device: str, label: str) -> None:
        self.device = device
        super().__init__(Text("pantallas.no_compatible", device=device, model=label))


class ChosenDisplayMissing(DisplayError):
    """La pantalla elegida a mano no está conectada ahora (o su USB ya no coincide)."""

    def __init__(self) -> None:
        super().__init__(Text("pantallas.elegida_no_esta"))


class Transport(Protocol):
    """Lo mínimo que el driver necesita de un puerto serie (pyserial lo cumple)."""

    def write(self, data: bytes, /) -> int | None: ...
    def read(self, size: int = 1, /) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


SerialFactory = Callable[[str], Transport]


def default_serial_factory(device: str) -> Transport:
    """Abre un puerto serie de verdad con los ajustes comunes de la familia."""
    import serial

    return serial.Serial(device, BAUDRATE, timeout=1, write_timeout=WRITE_TIMEOUT, rtscts=True)


def is_access_denied(exc: BaseException) -> bool:
    """¿El puerto está en uso por otro programa?

    pyserial en Windows no lanza ``PermissionError``: lanza ``SerialException`` con el
    error de Windows solo en el texto y sin encadenar, p. ej.
    «could not open port 'COM3': PermissionError(13, 'Acceso denegado.', None, 5)»
    (medido 2026-11-17 con COM3 abierto por otro proceso).
    """
    return isinstance(exc, PermissionError) or "PermissionError(" in str(exc)


def open_error(device: str, exc: BaseException) -> DisplayError:
    """El error para el usuario cuando un puerto no se puede abrir."""
    if is_access_denied(exc):
        error: DisplayError = DisplayBusy(device)
    else:
        error = DisplayError(Text("pantallas.no_puedo_abrir_reintento", device=device))
        error.says_retrying = True
    error.__cause__ = exc  # para el log; el mensaje es para el usuario
    return error


def open_probed(
    device: str,
    make: Callable[[Transport], object],
    *,
    retries: int,
    delay: float,
    serial_factory: SerialFactory | None,
    sleep: Callable[[float], None],
):
    """Abre ``device``, crea el driver con ``make`` y le hace su primera prueba (``probe``).

    - Si el puerto no abre, se pierde a mitad o la pantalla no contesta nada
      (:class:`NoAnswer`), se cierra y se reintenta (``retries``).
    - Si la pantalla contesta pero no como el modelo (:class:`WrongModel`), se cierra el
      puerto y se para ahí: ni otro intento ni otro modelo.
    """
    factory = serial_factory or default_serial_factory
    last_error: DisplayError = DisplayError(Text("pantallas.no_puedo_abrir", device=device))
    for attempt in range(max(1, retries)):
        transport = None
        try:
            transport = factory(device)
            display = make(transport)
            display.device = device  # type: ignore[attr-defined]
            display.probe()  # type: ignore[attr-defined]
            return display
        except WrongModel:
            _close_quietly(transport)
            raise
        except (OSError, DisplayError) as exc:
            # 🔴 Cerrar YA lo que llegó a abrirse (mismo motivo que en la rev A: si se deja
            # al recolector, el siguiente intento choca con nuestro propio handle).
            _close_quietly(transport)
            # «No contesta» ya es un mensaje para el usuario; lo demás, el genérico.
            last_error = exc if isinstance(exc, NoAnswer) else open_error(device, exc)
        if attempt < retries - 1:
            sleep(delay)
    raise last_error


def _close_quietly(transport: Transport | None) -> None:
    """Cerrar es lo último que se hace: si falla, da igual (el error de verdad es otro)."""
    if transport is not None:
        with contextlib.suppress(Exception):
            transport.close()
