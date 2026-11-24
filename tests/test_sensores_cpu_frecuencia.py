"""Frecuencia real de la CPU por los contadores de Windows (``cpu_frecuencia``)."""

from __future__ import annotations

import sys

import pytest

from luzeira.sensores.base import Reading
from luzeira.sensores.cpu_frecuencia import MAX_ATTEMPTS, RETRY_SECONDS, CpuFrequency


class FakeCounters:
    def __init__(self, samples):
        self.samples = list(samples)
        self.closed = False

    def sample(self):
        return self.samples.pop(0) if self.samples else None

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(samples=(), fail=0, clock=None):
    """``fail``: cuántas veces falla abrir los contadores antes de abrirse."""
    state = {"fails": fail, "opened": 0}
    counters = FakeCounters(samples)

    def opener():
        state["opened"] += 1
        if state["fails"] > 0:
            state["fails"] -= 1
            raise OSError("PdhOpenQueryW devolvió 0xC0000BB8")
        return counters

    freq = CpuFrequency(opener=opener, clock=clock or Clock(), background=False)
    return freq, counters, state


def test_real_frequency_is_nominal_times_performance():
    freq, _, _ = make([(3001.0, 133.0)])
    assert freq.read() == Reading(3991, "MHz")


def test_first_sample_without_data_is_measuring():
    freq, _, _ = make([None, (3001.0, 100.0)])
    first = freq.read()
    assert first.value is None
    assert first.reason
    assert freq.read() == Reading(3001, "MHz")


@pytest.mark.parametrize(
    "sample",
    [(3001.0, 0.0), (3001.0, 9000.0), (float("nan"), 100.0), (3001.0, float("inf")), (-5.0, 100.0)],
)
def test_nonsense_counters_are_never_shown(sample):
    freq, _, _ = make([sample])
    assert freq.read().value is None


def test_failed_open_retries_a_few_times_then_gives_up():
    clock = Clock()
    freq, _, state = make([(3001.0, 100.0)] * 3, fail=MAX_ATTEMPTS + 1, clock=clock)
    assert freq.read().value is None
    freq.read()  # sin pasar el tiempo de espera no se reintenta
    assert state["opened"] == 1
    for _ in range(MAX_ATTEMPTS + 2):
        clock.now += RETRY_SECONDS
        assert freq.read().value is None
    assert state["opened"] == MAX_ATTEMPTS


def test_open_retry_succeeds_later():
    clock = Clock()
    freq, _, _ = make([(3001.0, 110.0)], fail=1, clock=clock)
    assert freq.read().value is None
    clock.now += RETRY_SECONDS
    assert freq.read() == Reading(3301, "MHz")


def test_close_releases_the_counters():
    freq, counters, _ = make([(3001.0, 100.0)])
    freq.read()
    freq.close()
    assert counters.closed


@pytest.mark.skipif(sys.platform != "win32", reason="contadores de Windows")
def test_real_counters_on_this_pc():
    import time

    from luzeira.sensores.windows_cpu import RealFrequencyCounters

    counters = RealFrequencyCounters()
    try:
        time.sleep(0.2)
        sample = counters.sample()
    finally:
        counters.close()
    assert sample is not None
    nominal, percent = sample
    assert 100 <= nominal <= 10_000
    assert 0 < percent < 1000
