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


# --- Aviso «abre el editor» entre copias ------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="eventos con nombre de Windows")
def test_another_copy_can_ask_to_open_the_editor():
    import threading

    from luzeira.sistema.instancia import EditorRequests

    name = f"LuzeiraPrueba-{uuid.uuid4().hex}"  # nunca el de la app de verdad
    asked = threading.Event()
    listener = EditorRequests(name)
    assert EditorRequests(name).request() is False  # nadie escucha todavía
    assert listener.listen(asked.set, poll_ms=50)
    try:
        assert EditorRequests(name).request() is True
        assert asked.wait(2)
    finally:
        listener.stop()
    assert EditorRequests(name).request() is False  # tras parar, ya no hay nadie


def test_editor_requests_without_windows_do_nothing():
    from luzeira.sistema.instancia import EditorRequests

    requests = EditorRequests("x", kernel32=None)
    requests._kernel32 = None
    assert requests.request() is False
    assert requests.listen(lambda: None) is False
    requests.stop()


# --- Objetos de otros programas (R4-08) -------------------------------------------------
# Con un kernel32 falso: nunca se crean los nombres reales ``Local\Luzeira`` ni
# ``Local\Luzeira-AbrirEditor`` (los usa la Luzeira instalada).


class FakeKernel32:
    """Imita CreateMutexW/CreateEventW/OpenEventW: devuelve ``handle`` y deja ``error``
    como último error, como haría Windows."""

    def __init__(self, error=0, handle=77):
        self.error = error
        self.handle = handle
        self.closed = []
        self.signalled = []
        self.attributes = []

    def _create(self, attributes):
        import ctypes

        self.attributes.append(attributes)
        ctypes.set_last_error(self.error)
        return self.handle

    def CreateMutexW(self, attributes, _owner, _name):
        return self._create(attributes)

    def CreateEventW(self, attributes, _manual, _initial, _name):
        return self._create(attributes)

    def OpenEventW(self, _access, _inherit, _name):
        return self.handle

    def SetEvent(self, handle):
        self.signalled.append(handle)
        return 1

    def WaitForSingleObject(self, _handle, ms):
        import time

        time.sleep(ms / 1000)
        return 258  # WAIT_TIMEOUT

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


class FakeLabel:
    def __init__(self):
        import ctypes

        from luzeira.sistema.permisos import SECURITY_ATTRIBUTES

        self.attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, False)
        self.closed = False

    def close(self):
        self.closed = True


def _fake_instance(k32, ours):
    def check(_handle):
        if isinstance(ours, Exception):
            raise ours
        return ours

    return SingleInstance("x", kernel32=k32, owner_check=check, label=lambda: None)


def test_a_mutex_squatted_by_another_program_does_not_stop_luzeira():
    k32 = FakeKernel32(error=183)
    lock = _fake_instance(k32, ours=False)
    assert lock.acquire() is True
    assert lock.acquired and lock.foreign
    assert k32.closed == [77]  # no se queda con el handle del objeto ajeno
    lock.release()
    assert not lock.acquired


def test_a_mutex_from_another_luzeira_still_refuses():
    lock = _fake_instance(FakeKernel32(error=183), ours=True)
    assert lock.acquire() is False
    assert not lock.foreign


def test_a_mutex_we_cannot_open_is_someone_elses():
    lock = _fake_instance(FakeKernel32(error=5, handle=0), ours=True)
    assert lock.acquire() is True
    assert lock.foreign


def test_if_the_owner_cannot_be_read_two_copies_are_still_avoided():
    lock = _fake_instance(FakeKernel32(error=183), ours=OSError("GetSecurityInfo"))
    assert lock.acquire() is False


def test_mutex_and_event_are_created_with_the_integrity_label():
    k32 = FakeKernel32()
    labels = []

    def label():
        labels.append(FakeLabel())
        return labels[-1]

    lock = SingleInstance("x", kernel32=k32, owner_check=lambda _h: True, label=label)
    assert lock.acquire() and not lock.foreign
    from luzeira.sistema.instancia import EditorRequests

    requests = EditorRequests("x", k32, owner_check=lambda _h: True, label=label)
    assert requests.listen(lambda: None, poll_ms=10)
    requests.stop()
    assert len(labels) == 2 and all(lb.closed for lb in labels)
    assert all(a is not None for a in k32.attributes)


def test_an_event_created_by_another_program_is_not_listened_to():
    from luzeira.sistema.instancia import EditorRequests

    k32 = FakeKernel32(error=183)
    requests = EditorRequests("x", k32, owner_check=lambda _h: False, label=lambda: None)
    assert requests.listen(lambda: None) is False
    assert k32.closed == [77]


def test_an_event_from_our_own_user_is_listened_to():
    from luzeira.sistema.instancia import EditorRequests

    k32 = FakeKernel32(error=183)
    requests = EditorRequests("x", k32, owner_check=lambda _h: True, label=lambda: None)
    assert requests.listen(lambda: None, poll_ms=10) is True
    requests.stop()


def test_an_event_created_by_another_program_is_never_signalled():
    from luzeira.sistema.instancia import EditorRequests

    k32 = FakeKernel32()
    assert EditorRequests("x", k32, owner_check=lambda _h: False).request() is False
    assert k32.signalled == []
    assert EditorRequests("x", k32, owner_check=lambda _h: True).request() is True
    assert k32.signalled == [77]


def test_real_objects_get_the_medium_label_and_count_as_ours(name):
    import ctypes

    from luzeira.sistema import instancia, permisos

    lock = SingleInstance(name)
    try:
        assert lock.acquire() and not lock.foreign
        security = permisos.handle_security(lock._handle, kernel_object=True)
        assert security.integrity == permisos.INTEGRITY_MEDIUM
        assert security.owner == permisos.current_user_sid()
        assert instancia.object_is_ours(lock._handle)
        # Un objeto sin etiqueta (Windows lo trata como medio) también es nuestro.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        plain = k32.CreateMutexW(None, False, f"Local\\{name}-sin-etiqueta")
        try:
            assert instancia.object_is_ours(plain)
        finally:
            k32.CloseHandle(plain)
    finally:
        lock.release()


def test_object_is_ours_rejects_low_integrity_and_strangers(monkeypatch):
    from luzeira.sistema import instancia, permisos

    me = "S-1-5-21-1-2-3-1001"
    monkeypatch.setattr(permisos, "current_user_sid", lambda: me)
    cases = {
        (me, None): True,
        (me, permisos.INTEGRITY_MEDIUM): True,
        (permisos.SID_ADMINISTRATORS, 0x3000): True,
        (permisos.SID_SYSTEM, 0x4000): True,
        (me, permisos.INTEGRITY_LOW): False,  # lo creó un proceso de integridad baja
        (me, 0): False,  # «no fiable»
        ("S-1-5-21-1-2-3-1002", None): False,  # otro usuario
    }
    for (owner, integrity), expected in cases.items():
        security = permisos.Security(owner, (), integrity)
        monkeypatch.setattr(permisos, "handle_security", lambda *_a, s=security, **_k: s)
        assert instancia.object_is_ours(1) is expected, (owner, integrity)
