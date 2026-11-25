"""Sensores del módulo opcional «Luzeira Sensores» (ADR-007, ADR-008): temperatura de la CPU.

El módulo es un servicio de Windows aparte, instalado con permiso de administrador por
quien lo quiera. Publica sus lecturas en un **fichero mapeado en memoria**,
``%ProgramData%\\Luzeira Sensores\\sensores.bin`` (ADR-008; antes era una sección
``Global\\``): el servicio no lee nada de nadie, y Luzeira solo lee. Sin módulo, Luzeira
funciona igual que siempre y estos sensores salen «—» con un aviso.

Como ese fichero lo escribe otro programa, aquí se trata como **datos no fiables**:
tamaño acotado, comprobación de cada campo y de los rangos físicos, y lista cerrada de
identificadores. Nada de lo que traiga puede hacer otra cosa que dar un número.

Formato del fichero (versión 1, little-endian; el servicio en C# usa el mismo; lo
llamamos «sección» por costumbre):

Cabecera, 64 bytes:

=====  =====  =====================================================================
Offs.  Tipo   Campo
=====  =====  =====================================================================
0      8 B    magia ``LUZSENS\\0``
8      u32    versión del formato (1)
12     u32    tamaño de la cabecera (64)
16     u32    tamaño de cada entrada (48)
20     u32    número de entradas (como mucho ``MAX_ENTRIES``)
24     u64    secuencia: impar mientras el servicio escribe (seqlock)
32     i64    hora de la última escritura (ms desde 1970), solo informativa
40     16 B   versión del módulo, ASCII rellena con ceros (p. ej. ``0.2.0``)
56     u32    estado: 0 bien, 1 falta PawnIO, 2 procesador no compatible, 3 arrancando
60     u32    reservado (0)
=====  =====  =====================================================================

Cada entrada, 48 bytes: identificador ASCII de 32 bytes relleno con ceros, valor
``float64`` y ``u32`` de banderas (bit 0: el valor es válido), más 4 bytes reservados.

El fichero mide como mucho ``SECTION_SIZE`` (64 + 64 · 48 = 3136 bytes; el servicio lo
puede dejar en 4 KB). El lector **nunca lee más allá de su tamaño real** ni de
``SECTION_SIZE``.

Lo que tiene que poner **el instalador** (ADR-008), y lo que comprueba el lector
(``windows_cpu.RealModuleFileReader``) antes de leer nada:

- La carpeta ``%ProgramData%\\Luzeira Sensores`` la crea el instalador con admin, con
  propietario **Administradores** (o SYSTEM) y una DACL **explícita y protegida** (sin
  herencia de ``ProgramData``, que da a Usuarios permiso de crear ficheros):
  SYSTEM y Administradores, control total; Usuarios, solo leer (``D:P(A;OICI;FA;;;SY)
  (A;OICI;FA;;;BA)(A;OICI;FR;;;BU)``). El fichero, igual (lo hereda de la carpeta).
- El lector rechaza (``SectionUntrusted``) la carpeta o el fichero si:
  - su propietario no es SYSTEM ni Administradores;
  - alguna ACE de permitir da a otro SID (que no sea SYSTEM, Administradores,
    TrustedInstaller ni CREATOR OWNER) permiso de escribir: en el fichero,
    ``FILE_WRITE_DATA``, ``FILE_APPEND_DATA``, ``DELETE``, ``WRITE_DAC``,
    ``WRITE_OWNER``, ``GENERIC_WRITE`` o ``GENERIC_ALL``; en la carpeta, además,
    ``FILE_ADD_FILE``, ``FILE_ADD_SUBDIRECTORY`` y ``FILE_DELETE_CHILD``. Cuentan
    también las ACE que solo se heredan, y la falta de DACL;
  - es un enlace o *reparse point* (symlink, junction).
- CREATOR OWNER se acepta aunque con la DACL protegida no debería aparecer: es un
  comodín que nunca casa con nadie en el propio objeto y, al heredarse, pasa a ser quien
  crea el hijo; y crear dentro de la carpeta ya exige ``FILE_ADD_FILE``, que solo pueden
  tener SYSTEM y Administradores. Rechazarlo no protegería de nada y rompería una
  carpeta bien hecha a la que Windows se lo añadiera.
- El lector abre con ``GENERIC_READ`` y comparte lectura y escritura (el servicio lo
  tiene abierto para escribir); mapea con ``PAGE_READONLY``/``FILE_MAP_READ``.

Cuándo se considera «sin respuesta»: si la secuencia no cambia en ``STALE_SECONDS``
según el reloj de Luzeira (no la hora que escribe el módulo, que podría estar mal).
Entonces el lector **suelta el fichero** (ADR-008, R4-10: así el servicio puede volver a
crearlo al reiniciarse) y no lo vuelve a abrir hasta pasados ``REOPEN_SECONDS``.
"""

from __future__ import annotations

import logging
import math
import os
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from luzeira.idioma import t
from luzeira.sensores.base import Reading, SensorInfo

log = logging.getLogger(__name__)

#: Carpeta (dentro de ``%ProgramData%``, que se pregunta a Windows) y fichero del módulo.
FOLDER_NAME = "Luzeira Sensores"
FILE_NAME = "sensores.bin"

MAGIC = b"LUZSENS\0"
LAYOUT_VERSION = 1
HEADER = struct.Struct("<8sIIIIQq16sII")
ENTRY = struct.Struct("<32sdII")
MAX_ENTRIES = 64
#: Lo más que se copia del fichero: cabecera y el máximo de entradas.
SECTION_SIZE = HEADER.size + MAX_ENTRIES * ENTRY.size

STATUS_OK = 0
STATUS_NO_PAWNIO = 1
STATUS_UNSUPPORTED_CPU = 2
STATUS_STARTING = 3

FLAG_VALID = 1

STALE_SECONDS = 5.0
#: Si el fichero no existe, o se soltó porque el módulo no respondía, se vuelve a mirar
#: como mucho cada tanto (no en cada ciclo).
REOPEN_SECONDS = 10.0

# Claves del catálogo (zona ``sensores``).
NOT_INSTALLED = "sensores.modulo_no_instalado"
NOT_AVAILABLE = "sensores.modulo_no_disponible"  # versión de la Store: texto neutro
NOT_RESPONDING = "sensores.modulo_no_responde"
NO_PAWNIO = "sensores.modulo_falta_pawnio"
UNSUPPORTED_CPU = "sensores.modulo_cpu_no_compatible"
STARTING = "sensores.modulo_arrancando"
NEWER_MODULE = "sensores.modulo_mas_nuevo"
UNTRUSTED = "sensores.modulo_no_fiable"
NO_VALUE = "sensores.modulo_sin_dato"


@dataclass(frozen=True)
class _Sensor:
    id: str
    label_key: str
    unit: str
    low: float
    high: float
    label_vars: tuple[tuple[str, str], ...] = ()


#: Lista cerrada: lo que el módulo publique fuera de ella se ignora. Los rangos son
#: físicos (lo que ningún procesador de escritorio da de verdad se descarta).
SENSORS: tuple[_Sensor, ...] = (
    _Sensor("cpu.temperature", "sensores.cpu.temperature", "°C", -20.0, 150.0),
    _Sensor(
        "cpu.temperature.ccd1", "sensores.cpu.temperature_ccd", "°C", -20.0, 150.0, (("n", "1"),)
    ),
    _Sensor(
        "cpu.temperature.ccd2", "sensores.cpu.temperature_ccd", "°C", -20.0, 150.0, (("n", "2"),)
    ),
    _Sensor("cpu.power", "sensores.cpu.power", "W", 0.0, 1000.0),
    _Sensor("cpu.voltage", "sensores.cpu.voltage", "V", 0.0, 3.0),
)
_BY_ID = {s.id: s for s in SENSORS}


class BadSection(ValueError):
    """La sección no tiene el formato esperado."""


@dataclass(frozen=True)
class Section:
    sequence: int
    module_version: str
    status: int
    values: dict[str, float]


def parse_section(data: bytes) -> Section:
    """Interpreta una copia de la sección. Lanza ``BadSection`` si algo no cuadra.

    No confía en nada: comprueba tamaños antes de leer, que la secuencia sea par (no a
    medio escribir), que cada identificador esté en la lista cerrada, que el valor sea
    un número finito y que caiga en su rango físico.
    """
    if len(data) < HEADER.size:
        raise BadSection("corta")  # i18n: no
    (magic, version, header_size, entry_size, count, sequence, _when, raw_version, status, _r) = (
        HEADER.unpack_from(data, 0)
    )
    if magic != MAGIC:
        raise BadSection("magia")  # i18n: no
    if version != LAYOUT_VERSION:
        raise BadSection("versión")  # i18n: no
    if header_size != HEADER.size or entry_size != ENTRY.size:
        raise BadSection("tamaños")  # i18n: no
    if count > MAX_ENTRIES or len(data) < HEADER.size + count * ENTRY.size:
        raise BadSection("entradas")  # i18n: no
    if sequence % 2:
        raise BadSection("a medio escribir")  # i18n: no
    values: dict[str, float] = {}
    for i in range(count):
        raw_id, value, flags, _reserved = ENTRY.unpack_from(data, HEADER.size + i * ENTRY.size)
        sensor = _BY_ID.get(_ascii(raw_id))
        if sensor is None or not flags & FLAG_VALID:
            continue
        if math.isfinite(value) and sensor.low <= value <= sensor.high:
            values[sensor.id] = value
    return Section(sequence, _ascii(raw_version), status, values)


def layout_version(data: bytes) -> int | None:
    """La versión del formato, si la cabecera tiene nuestra magia (para avisar de que el
    módulo es más nuevo que Luzeira en vez de decir que no responde)."""
    if len(data) < 12 or data[:8] != MAGIC:
        return None
    return struct.unpack_from("<I", data, 8)[0]


def _ascii(raw: bytes) -> str:
    text = raw.split(b"\0", 1)[0]
    if not text.isascii():
        return ""
    decoded = text.decode("ascii")
    return decoded if decoded.isprintable() else ""


class SectionUntrusted(Exception):
    """El fichero existe pero no es de fiar (propietario, permisos o enlace): se ignora."""


class SectionReader(Protocol):
    def read(self) -> bytes | None:
        """Una copia del fichero, o ``None`` si no existe (el módulo no está).

        Lanza ``SectionUntrusted`` si no es de fiar."""
        ...

    def close(self) -> None: ...


def real_reader() -> SectionReader:
    from luzeira.sensores.windows_cpu import RealModuleFileReader, programdata_dir

    return RealModuleFileReader(programdata_dir() / FOLDER_NAME, FILE_NAME, SECTION_SIZE)


#: Forma de la versión que publica el módulo. La comprobación estricta (sin ceros
#: delante…) la hace ``actualizar.parse_version``; aquí solo se descarta lo que no lo es.
_MODULE_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
#: Lecturas si el servicio está justo escribiendo (secuencia impar) al copiar.
_VERSION_TRIES = 3


def installed_version(opener: Callable[[], SectionReader] | None = None) -> str | None:
    """La versión del módulo instalado, leída **una vez** de su fichero (la escribe el
    propio servicio en la cabecera), para avisar de versiones nuevas.

    ``None`` si no está instalado, no se puede leer, no es de fiar (mismas comprobaciones
    que :class:`ModuleSource`, con el mismo lector) o la versión no tiene forma de versión.
    Nunca lanza. ``opener``: el lector de verdad (:func:`real_reader`) si no se da otro
    (solo los tests dan otro); con él, fuera de Windows ni siquiera se abre nada.

    Límite: si el servicio no ha llegado a arrancar nunca, no hay fichero y no se puede
    saber la versión: entonces no se avisa (como si no estuviera)."""
    if opener is None:
        if os.name != "nt":
            return None
        opener = real_reader
    reader: SectionReader | None = None
    try:
        reader = opener()
        for _ in range(_VERSION_TRIES):
            data = reader.read()
            if data is None:
                return None
            try:
                version = parse_section(data).module_version
            except BadSection:
                continue  # a medio escribir, o de un formato que no conocemos
            return version if _MODULE_VERSION.fullmatch(version) else None
        return None
    except SectionUntrusted:
        log.warning("No se mira la versión del módulo: su fichero no es de fiar.")
        return None
    except Exception as exc:
        # Solo el tipo de error: el mensaje podría llevar datos del equipo.
        log.warning("No se pudo leer la versión del módulo (%s).", type(exc).__name__)
        return None
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception as exc:
                log.debug("Al soltar el fichero del módulo: %s.", type(exc).__name__)


class ModuleSource:
    """Fuente de sensores que lee el fichero del módulo opcional."""

    def __init__(
        self,
        opener: Callable[[], SectionReader] = real_reader,
        clock: Callable[[], float] = time.monotonic,
        packaged: bool | None = None,
    ) -> None:
        self._opener = opener
        self._clock = clock
        if packaged is None:
            from luzeira.sistema.paquete import is_packaged

            packaged = is_packaged()
        # En la Store no se menciona el módulo dentro de la app (norma 10.1.5, ADR-007).
        self._missing = NOT_AVAILABLE if packaged else NOT_INSTALLED
        self._reader: SectionReader | None = None
        self._last_open: float | None = None
        self._last_sequence: int | None = None
        self._sequence_since: float | None = None
        self._warned: set[str] = set()
        #: Lo que se dice mientras se espera para volver a abrir.
        self._idle_reason = self._missing

    def sensors(self) -> list[SensorInfo]:
        return [
            SensorInfo(s.id, s.label_key, s.unit, "cpu", label_vars=s.label_vars) for s in SENSORS
        ]

    def read_all(self) -> dict[str, Reading]:
        try:
            return self._read()
        except SectionUntrusted:
            if self._first_time("no_fiable"):
                log.warning("Se ignora un fichero de sensores que no es de fiar.")
            self._drop()
            self._idle_reason = UNTRUSTED
            return self._all_missing(UNTRUSTED)
        except Exception as exc:
            # Solo el tipo de error: el mensaje podría llevar datos del equipo.
            if self._first_time(type(exc).__name__):
                log.warning("No se pudo leer el módulo de sensores (%s).", type(exc).__name__)
            self._drop()
            self._idle_reason = NOT_RESPONDING
            return self._all_missing(NOT_RESPONDING)

    def _read(self) -> dict[str, Reading]:
        reader = self._open_if_due()
        if reader is None:
            return self._all_missing(self._idle_reason)
        data = reader.read()
        if data is None:  # el servicio se paró o se desinstaló
            self._drop()
            self._idle_reason = self._missing
            return self._all_missing(self._missing)
        try:
            section = parse_section(data)
        except BadSection:
            version = layout_version(data)
            if version is not None and version > LAYOUT_VERSION:
                return self._all_missing(NEWER_MODULE)
            return self._all_missing(NOT_RESPONDING)
        if self._is_stale(section.sequence):
            # R4-10 (ADR-008): se suelta el fichero para que el servicio pueda volver a
            # crearlo si se reinicia, y no se reabre hasta pasado REOPEN_SECONDS. La
            # secuencia vista se recuerda: si al reabrir sigue igual, sigue «sin respuesta»
            # en vez de parecer fresca otros STALE_SECONDS.
            self._drop(keep_sequence=True)
            self._last_open = self._clock()
            self._idle_reason = NOT_RESPONDING
            return self._all_missing(NOT_RESPONDING)
        status_reason = {
            STATUS_NO_PAWNIO: NO_PAWNIO,
            STATUS_UNSUPPORTED_CPU: UNSUPPORTED_CPU,
            STATUS_STARTING: STARTING,
        }.get(section.status)
        if status_reason is not None:
            return self._all_missing(status_reason)
        out: dict[str, Reading] = {}
        for s in SENSORS:
            value = section.values.get(s.id)
            out[s.id] = (
                Reading(round(value, 2), s.unit)
                if value is not None
                else Reading(None, s.unit, reason=t(NO_VALUE))
            )
        return out

    def _open_if_due(self) -> SectionReader | None:
        if self._reader is not None:
            return self._reader
        now = self._clock()
        if self._last_open is not None and now - self._last_open < REOPEN_SECONDS:
            return None
        self._last_open = now
        self._reader = self._opener()
        return self._reader

    def _is_stale(self, sequence: int) -> bool:
        now = self._clock()
        if sequence != self._last_sequence:
            self._last_sequence = sequence
            self._sequence_since = now
            return False
        return self._sequence_since is not None and now - self._sequence_since > STALE_SECONDS

    def _all_missing(self, key: str) -> dict[str, Reading]:
        reason = t(key)
        return {s.id: Reading(None, s.unit, reason=reason) for s in SENSORS}

    def _drop(self, *, keep_sequence: bool = False) -> None:
        reader, self._reader = self._reader, None
        if not keep_sequence:
            self._last_sequence = None
            self._sequence_since = None
        if reader is not None:
            try:
                reader.close()
            except Exception as exc:
                log.debug("Al soltar el fichero del módulo: %s.", type(exc).__name__)

    def _first_time(self, key: str) -> bool:
        """Cada aviso va una vez al registro, no en cada ciclo."""
        if key in self._warned:
            return False
        self._warned.add(key)
        return True

    def close(self) -> None:
        self._drop()
