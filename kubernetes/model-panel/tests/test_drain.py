"""RED (part of 6.2): drain via llama-router `/slots` (D3)."""
from __future__ import annotations

import pytest

from app.handoff.drain import DrainTimeout, slots_idle, wait_for_drain


def test_slots_idle_true_when_all_idle():
    assert slots_idle([{"id_task": -1, "state": 0}, {"id_task": -1, "state": "idle"}])


def test_slots_idle_false_when_any_busy():
    assert not slots_idle([{"id_task": -1, "state": 0}, {"id_task": 7, "state": 1}])


def test_wait_for_drain_returns_once_idle():
    responses = iter([
        [{"id_task": 3, "state": 1}],
        [{"id_task": 3, "state": 1}],
        [{"id_task": -1, "state": 0}],
    ])
    slept = []

    wait_for_drain(
        fetch_slots=lambda: next(responses),
        timeout=120,
        interval=1,
        sleep=slept.append,
        clock=iter([0, 1, 2, 3]).__next__,
    )

    assert slept == [1, 1]


def test_wait_for_drain_timeout_aborts():
    """Busy `/slots` responses that never idle must raise DrainTimeout, not
    proceed silently."""
    clock_values = iter([0, 10, 50, 121, 121])

    with pytest.raises(DrainTimeout):
        wait_for_drain(
            fetch_slots=lambda: [{"id_task": 1, "state": 1}],
            timeout=120,
            interval=10,
            sleep=lambda s: None,
            clock=lambda: next(clock_values),
        )
