"""Log rotado y sin datos personales (RQ-09, seguridad §7).

- Fichero ``luzeira.log`` que rota al llegar a 1 MiB y guarda 3 copias antiguas
  (``luzeira.log.1`` … ``.3``), en UTF-8.
- Un **filtro de privacidad** cambia, en cada mensaje y en el texto de las excepciones:
  el nombre de usuario de Windows → ``<usuario>``; la carpeta de perfil
  (``C:\\Users\\<nombre>``, con ``\\`` o ``/``, sin distinguir mayúsculas) → ``<usuario>``;
  el nombre del equipo → ``<equipo>``.
- Con ``pythonw`` no hay consola: una excepción no capturada desaparecería sin dejar
  rastro. ``setup_logging`` instala ganchos para que acaben en el log.

Fuentes: ``logging.handlers.RotatingFileHandler`` y ``logging.Filter`` (documentación
de Python 3.12, https://docs.python.org/3.12/library/logging.handlers.html y
https://docs.python.org/3.12/library/logging.html#filter-objects, consultadas 2026-11-17).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import socket
import sys
import threading
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "luzeira.log"
MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

USER_PLACEHOLDER = "<usuario>"
HOST_PLACEHOLDER = "<equipo>"

# Cualquier carpeta de perfil «X:\Users\algo» (también de otros usuarios o con nombre
# corto 8.3). El segmento del nombre llega hasta el siguiente separador, comilla, salto
# de línea o carácter que Windows no admite en un nombre. El espacio **no** lo corta (R4-12):
# «C:\Users\Otro Usuario\x» quedaba «<usuario> Usuario\x». Si la ruta acaba sin separador
# («… C:\Users\Otro Usuario y luego»), se tapa también lo que sigue: mejor de más que de menos.
_ANY_PROFILE = re.compile(r"""[A-Za-z]:[\\/]+Users[\\/]+[^\\/\r\n\t'"<>|?*:),;]+""", re.IGNORECASE)

_MARK = "_luzeira_handler"  # marca para reconocer nuestro handler al reconfigurar


def _current_usernames() -> list[str]:
    names = [os.environ.get("USERNAME", "")]
    with contextlib.suppress(OSError):
        names.append(os.getlogin())
    return [n for n in names if n]


def _current_computernames() -> list[str]:
    names = [os.environ.get("COMPUTERNAME", "")]
    with contextlib.suppress(OSError):
        names.append(socket.gethostname())
    return [n for n in names if n]


def _current_profiles() -> list[str]:
    return [p for p in (os.environ.get("USERPROFILE", ""), str(Path.home())) if p]


def _path_pattern(path: str) -> str:
    """Patrón de una ruta que admite ``\\`` o ``/`` (uno o varios) como separador."""
    parts = [re.escape(p) for p in re.split(r"[\\/]+", path.rstrip("\\/")) if p]
    # (?!\w): que «C:\Users\Ana» no case dentro de «C:\Users\Anabel».
    return r"[\\/]+".join(parts) + r"(?!\w)"


class PrivacyFilter(logging.Filter):
    """Filtro que borra usuario, carpeta de perfil y nombre de equipo de cada registro.

    Los valores se inyectan en los tests; por defecto se leen del sistema.
    """

    def __init__(
        self,
        usernames: Iterable[str] | None = None,
        computernames: Iterable[str] | None = None,
        profile_dirs: Iterable[str] | None = None,
    ) -> None:
        super().__init__()
        users = _unique(_current_usernames() if usernames is None else usernames)
        hosts = _unique(_current_computernames() if computernames is None else computernames)
        profiles = _unique(_current_profiles() if profile_dirs is None else profile_dirs)
        # Orden: primero las rutas completas (más largas), luego cualquier perfil, luego
        # los nombres sueltos. Así «C:\Users\Ana\x» queda «<usuario>\x» y no
        # «C:\Users\<usuario>\x» a medias.
        self._rules: list[tuple[re.Pattern[str], str]] = []
        for profile in sorted(profiles, key=len, reverse=True):
            self._rules.append(
                (re.compile(_path_pattern(profile), re.IGNORECASE), USER_PLACEHOLDER)
            )
        self._rules.append((_ANY_PROFILE, USER_PLACEHOLDER))
        # Nombres como palabra completa (que «Ana» no rompa «Anabel», pero sí cambie
        # «ana» o «ANA»). Los más largos primero.
        names = [(n, USER_PLACEHOLDER) for n in users] + [(n, HOST_PLACEHOLDER) for n in hosts]
        for name, placeholder in sorted(names, key=lambda item: len(item[0]), reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            self._rules.append((pattern, placeholder))

    def scrub(self, text: str) -> str:
        """Devuelve ``text`` sin los datos personales."""
        for pattern, placeholder in self._rules:
            text = pattern.sub(placeholder, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        # El mensaje se «congela» ya formateado para limpiar también los argumentos.
        try:
            message = record.getMessage()
        except Exception:
            # Argumentos que no casan con el mensaje (``log.warning("%s %s", x)``): sin el
            # try, el TypeError saltaba en quien escribe el log (R4-12), porque los filtros
            # van fuera de la protección de ``Handler.handleError``. Se guarda el texto sin
            # los argumentos (que podrían llevar datos) y se avisa en la propia línea.
            message = f"{record.msg} [argumentos del log que no casan]"
        record.msg = self.scrub(message)
        record.args = None
        if record.exc_info:
            # El Formatter usa exc_text si ya existe: se deja aquí la versión limpia.
            text = record.exc_text or logging.Formatter().formatException(record.exc_info)
            record.exc_text = self.scrub(text)
        elif record.exc_text:
            record.exc_text = self.scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self.scrub(record.stack_info)
        return True


def _unique(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        if value and value.casefold() not in {v.casefold() for v in seen}:
            seen[value] = None
    return list(seen)


def default_log_dir() -> Path:
    """``%LOCALAPPDATA%\\Luzeira\\logs`` (o ``~/AppData/Local/...`` si falta la variable)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "Luzeira" / "logs"


def setup_logging(
    log_dir: Path,
    level: int = logging.INFO,
    *,
    privacy_filter: PrivacyFilter | None = None,
    capture_uncaught: bool = True,
) -> logging.Handler:
    """Configura el log raíz para escribir en ``log_dir/luzeira.log``.

    Se puede llamar más de una vez: sustituye el handler anterior de Luzeira en vez de
    duplicar líneas. Devuelve el handler creado (los tests lo cierran con él).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    # El filtro va en el handler (no en el logger): así se aplica a los registros de
    # todos los módulos que lleguen a él, no solo a los del logger raíz.
    handler.addFilter(privacy_filter or PrivacyFilter())
    setattr(handler, _MARK, True)

    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(old)
        old.close()
    root.addHandler(handler)
    root.setLevel(level)

    if capture_uncaught:
        _install_excepthooks()
    return handler


def _install_excepthooks() -> None:
    """Manda al log las excepciones no capturadas del hilo principal y de los demás."""
    logger = logging.getLogger("luzeira")

    def excepthook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("Error no controlado", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        logger.critical(
            "Error no controlado en el hilo %s",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
