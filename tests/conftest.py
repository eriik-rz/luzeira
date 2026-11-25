"""Lo común a todos los tests."""

from __future__ import annotations

import pytest
import serial
import serial.tools.list_ports

from luzeira import idioma


@pytest.fixture(autouse=True)
def spanish_by_default(monkeypatch):
    """Los tests comprueban los textos en español: el «idioma de Windows» se fija a ``es``
    (el runner de GitHub está en inglés) y cada test empieza en español."""
    monkeypatch.setattr(idioma, "windows_language", lambda: "es")
    idioma.set_language("es")
    yield
    idioma.set_language("es")


class RealSerialPortOpened(AssertionError):
    """Un test ha intentado abrir un puerto serie DE VERDAD.

    Hereda de ``AssertionError`` y no de ``OSError`` a propósito: los drivers convierten
    cualquier ``OSError`` en un «no puedo abrir la pantalla» y lo reintentan, así que un
    ``OSError`` se tragaría el intento sin que nadie se enterara."""


@pytest.fixture(autouse=True)
def no_real_serial_ports(monkeypatch):
    """🔴 Ningún test abre un puerto serie real ni lee los puertos del equipo.

    En el PC de quien pasa los tests puede haber una pantalla de verdad, en uso por su
    Luzeira instalada: un test que abriera su puerto la reiniciaría (se reinicia al abrir el
    puerto) y le mandaría bytes.

    - Se rompe el ``open`` de la clase de pyserial de la plataforma (``serial.Serial`` es
      ``serialwin32.Serial`` en Windows): así caen también quien la importó antes con
      ``from serial import Serial`` y ``serial_for_url`` con un COM. Quien quiera un puerto
      falso lo da con ``serial_factory`` o haciendo ``monkeypatch`` de ``serial.Serial``.
    - ``comports()`` no abre nada, pero devolvería los puertos reales de quien pasa los
      tests: siempre vacío (cada test pasa sus puertos falsos).

    Si algo lo intenta, falla en ese momento y, por si lo atrapó un ``except Exception``,
    también al terminar el test.
    """
    attempts: list[tuple] = []

    def refuse_open(self, *_args, **_kwargs):
        attempts.append((getattr(self, "port", None),))
        raise RealSerialPortOpened(f"un test ha intentado abrir el puerto real {self.port!r}")

    monkeypatch.setattr(serial.Serial, "open", refuse_open)
    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda *_a, **_k: [])
    # El de cada plataforma (``list_ports`` lo reexporta); fuera de Windows no existe.
    for name in ("list_ports_windows", "list_ports_posix", "list_ports_osx"):
        module = getattr(serial.tools, name, None)
        if module is not None:
            monkeypatch.setattr(module, "comports", lambda *_a, **_k: [])
    yield attempts
    assert not attempts, f"un test ha intentado abrir puertos serie reales: {attempts}"
