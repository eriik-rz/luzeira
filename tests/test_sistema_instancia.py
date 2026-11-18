"""Tests de instancia única con nombres de mutex aleatorios (no chocan con la app real)."""

from __future__ import annotations

import subprocess
import sys
import uuid

import pytest

from luzeira.sistema.instancia import SingleInstance

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")


@pytest.fixture
def name():
    return f"LuzeiraTest-{uuid.uuid4().hex}"


def test_second_instance_is_refused(name):
    first, second = SingleInstance(name), SingleInstance(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        assert not second.acquired
    finally:
        first.release()
        second.release()


def test_name_is_free_after_release(name):
    first = SingleInstance(name)
    assert first.acquire()
    first.release()
    second = SingleInstance(name)
    try:
        assert second.acquire() is True
    finally:
        second.release()


def test_acquire_twice_and_release_twice(name):
    lock = SingleInstance(name)
    assert lock.acquire() and lock.acquire()
    lock.release()
    lock.release()
    assert not lock.acquired


def test_context_manager(name):
    with SingleInstance(name) as lock:
        assert lock.acquired
        assert SingleInstance(name).acquire() is False
    assert not lock.acquired


def test_other_process_is_refused(name):
    code = (
        "import sys\n"
        "from luzeira.sistema.instancia import SingleInstance\n"
        f"sys.exit(0 if SingleInstance({name!r}).acquire() else 3)\n"
    )
    with SingleInstance(name) as lock:
        assert lock.acquired
        busy = subprocess.run([sys.executable, "-c", code], check=False, timeout=60)
    free = subprocess.run([sys.executable, "-c", code], check=False, timeout=60)
    assert busy.returncode == 3
    assert free.returncode == 0


@pytest.mark.parametrize("bad", ["", "con\\barra", "x" * 300])
def test_invalid_names(bad):
    with pytest.raises(ValueError):
        SingleInstance(bad)
