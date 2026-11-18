"""Tests del log y su filtro de privacidad, con usuario y equipo inyectados."""

from __future__ import annotations

import logging
import os
import sys
import threading

import pytest

from visoria.sistema import registro
from visoria.sistema.registro import PrivacyFilter, setup_logging

USER = "Juanito"
HOST = "PC-SALON"
PROFILE = r"C:\Users\Juanito"


@pytest.fixture
def pf():
    return PrivacyFilter(usernames=[USER], computernames=[HOST], profile_dirs=[PROFILE])


@pytest.fixture
def clean_root():
    """Deja el logger raíz y los ganchos de excepciones como estaban."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    hooks = sys.excepthook, threading.excepthook
    yield root
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)
    sys.excepthook, threading.excepthook = hooks


def _record(msg: str, *args: object, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("visoria.test", logging.INFO, __file__, 1, msg, args, exc_info)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r"Leyendo C:\Users\Juanito\AppData\x.json", r"Leyendo <usuario>\AppData\x.json"),
        ("Leyendo c:/users/JUANITO/AppData/x.json", "Leyendo <usuario>/AppData/x.json"),
        (r"repr 'C:\\Users\\Juanito\\a'", r"repr '<usuario>\\a'"),
        (r"otro perfil D:\Users\Pepa\b", r"otro perfil <usuario>\b"),
        ("hola juanito, equipo pc-salon", "hola <usuario>, equipo <equipo>"),
        ("Juanitos y PC-SALONES no son datos", "Juanitos y PC-SALONES no son datos"),
        (r"C:\Users\Juanitos\x", r"<usuario>\x"),  # otro perfil: también se tapa
        ("nada que ocultar", "nada que ocultar"),
    ],
)
def test_scrub(pf, text, expected):
    assert pf.scrub(text) == expected


def test_filter_scrubs_args(pf):
    record = _record("usuario %s en %s", USER, HOST)
    assert pf.filter(record) is True
    assert record.getMessage() == "usuario <usuario> en <equipo>"


def test_filter_scrubs_exception_text(pf):
    try:
        raise FileNotFoundError(rf"{PROFILE}\falta.json en {HOST}")
    except FileNotFoundError:
        record = _record("fallo", exc_info=sys.exc_info())
    pf.filter(record)
    formatted = logging.Formatter("%(message)s").format(record)
    assert "Juanito" not in formatted
    assert "PC-SALON" not in formatted
    assert r"<usuario>\falta.json en <equipo>" in formatted


def test_default_filter_hides_real_username():
    name = os.environ.get("USERNAME")
    if not name:
        pytest.skip("sin USERNAME")
    assert name.casefold() not in PrivacyFilter().scrub(f"hola {name}").casefold()


def test_setup_logging_writes_scrubbed_lines(tmp_path, pf, clean_root):
    handler = setup_logging(tmp_path / "logs", privacy_filter=pf, capture_uncaught=False)
    logging.getLogger("visoria.algo").info("perfil %s", PROFILE + r"\x")
    handler.flush()
    text = (tmp_path / "logs" / "visoria.log").read_text(encoding="utf-8")
    assert "INFO visoria.algo: perfil <usuario>\\x" in text
    assert "Juanito" not in text
    assert text[:4].isdigit()  # empieza por la fecha


def test_setup_logging_twice_does_not_duplicate(tmp_path, pf, clean_root):
    setup_logging(tmp_path, privacy_filter=pf, capture_uncaught=False)
    handler = setup_logging(tmp_path, privacy_filter=pf, capture_uncaught=False)
    logging.getLogger("visoria.x").warning("una vez")
    handler.flush()
    assert (tmp_path / "visoria.log").read_text(encoding="utf-8").count("una vez") == 1


def test_rotation(tmp_path, pf, clean_root):
    handler = setup_logging(tmp_path, privacy_filter=pf, capture_uncaught=False)
    assert handler.maxBytes == 1024 * 1024 and handler.backupCount == 3
    line = "x" * 1000
    for _ in range(5 * 1100):
        logging.getLogger("visoria.x").info(line)
    handler.flush()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["visoria.log", "visoria.log.1", "visoria.log.2", "visoria.log.3"]
    assert all(p.stat().st_size <= 1024 * 1024 for p in tmp_path.iterdir())


def test_uncaught_exceptions_reach_the_log(tmp_path, pf, clean_root):
    handler = setup_logging(tmp_path, privacy_filter=pf)
    try:
        raise ValueError(f"roto en {HOST}")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    def worker() -> None:
        raise RuntimeError("roto en hilo")

    t = threading.Thread(target=worker, name="trabajador")
    t.start()
    t.join()
    handler.flush()
    text = (tmp_path / "visoria.log").read_text(encoding="utf-8")
    assert "CRITICAL" in text and "ValueError: roto en <equipo>" in text
    assert "trabajador" in text and "RuntimeError: roto en hilo" in text


def test_default_log_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert registro.default_log_dir() == tmp_path / "Visoria" / "logs"
