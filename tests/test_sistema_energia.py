"""Tests de los avisos de energía. No suspenden nada: la callback nativa se llama a mano
con los códigos de evento, y el registro real en Windows solo se da de alta y de baja."""

from __future__ import annotations

import gc
import logging
import sys

import pytest

from visoria.sistema import energia
from visoria.sistema.energia import (
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    PBT_APMSUSPEND,
    PowerListener,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def suspend(self) -> None:
        self.calls.append("suspend")

    def resume(self) -> None:
        self.calls.append("resume")


@pytest.fixture
def rec():
    return Recorder()


def _fire(listener: PowerListener, event: int) -> int:
    """Llama a la callback **nativa** (a través de ctypes), como haría Windows."""
    return listener._native(None, event, None)


@windows_only
def test_suspend_calls_on_suspend(rec):
    listener = PowerListener(rec.suspend, rec.resume)
    assert _fire(listener, PBT_APMSUSPEND) == 0
    assert rec.calls == ["suspend"]


@windows_only
def test_resume_is_not_duplicated(rec):
    listener = PowerListener(rec.suspend, rec.resume)
    for event in (PBT_APMSUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
        _fire(listener, event)
    assert rec.calls == ["suspend", "resume"]


@windows_only
def test_resume_without_user_still_resumes(rec):
    # Wake-on-LAN: solo llega PBT_APMRESUMEAUTOMATIC.
    listener = PowerListener(rec.suspend, rec.resume)
    for event in (PBT_APMSUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_APMSUSPEND, PBT_APMRESUMESUSPEND):
        _fire(listener, event)
    assert rec.calls == ["suspend", "resume", "suspend", "resume"]


@windows_only
def test_unknown_event_is_ignored(rec):
    listener = PowerListener(rec.suspend, rec.resume)
    assert _fire(listener, 0xA) == 0  # PBT_APMPOWERSTATUSCHANGE
    assert rec.calls == []


@windows_only
def test_user_callback_errors_do_not_escape(caplog):
    def boom() -> None:
        raise RuntimeError("fallo del usuario")

    listener = PowerListener(boom, boom)
    with caplog.at_level(logging.ERROR, logger="visoria.sistema.energia"):
        assert _fire(listener, PBT_APMSUSPEND) == 0
        assert _fire(listener, PBT_APMRESUMEAUTOMATIC) == 0
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        "Falló on_suspend al atender un aviso de energía",
        "Falló on_resume al atender un aviso de energía",
    ]
    assert all(r.exc_info for r in errors)


@windows_only
def test_native_callback_never_raises(monkeypatch, caplog, rec):
    # Segunda red: aunque falle el propio reparto de eventos, Windows recibe 0.
    listener = PowerListener(rec.suspend, rec.resume)

    def broken(event: int) -> None:
        raise SystemExit("ni esto")

    monkeypatch.setattr(listener, "_dispatch", broken)
    with caplog.at_level(logging.ERROR, logger="visoria.sistema.energia"):
        assert _fire(listener, PBT_APMSUSPEND) == 0
    assert any("aviso de energía 4" in r.getMessage() for r in caplog.records)


@windows_only
def test_start_and_stop_register_for_real(rec):
    listener = PowerListener(rec.suspend, rec.resume)
    assert listener.start() is True
    try:
        assert listener.active
        assert listener in energia._ACTIVE
        assert listener.start() is True  # idempotente
    finally:
        listener.stop()
    assert not listener.active
    assert listener not in energia._ACTIVE
    listener.stop()  # dos veces no falla
    assert rec.calls == []


@windows_only
def test_registered_listener_survives_gc(rec):
    listener = PowerListener(rec.suspend, rec.resume)
    listener.start()
    ident = id(listener)
    del listener
    gc.collect()
    alive = [item for item in energia._ACTIVE if id(item) == ident]
    assert alive, "el GC no debe liberar un escuchador registrado"
    alive[0].stop()


@windows_only
def test_context_manager(rec):
    with PowerListener(rec.suspend, rec.resume) as listener:
        assert listener.active
    assert not listener.active


def test_start_without_api_warns_and_returns_false(monkeypatch, caplog, rec):
    monkeypatch.setattr(energia, "_load_api", lambda: None)
    listener = PowerListener(rec.suspend, rec.resume)
    with caplog.at_level(logging.WARNING, logger="visoria.sistema.energia"):
        assert listener.start() is False
    assert not listener.active
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    listener.stop()


@windows_only
def test_start_logs_error_when_windows_refuses(monkeypatch, caplog, rec):
    def refuse(flags, params, handle):
        return 5  # ERROR_ACCESS_DENIED

    monkeypatch.setattr(energia, "_load_api", lambda: (refuse, lambda h: 0))
    listener = PowerListener(rec.suspend, rec.resume)
    with caplog.at_level(logging.ERROR, logger="visoria.sistema.energia"):
        assert listener.start() is False
    assert any("código 5" in r.getMessage() for r in caplog.records)
