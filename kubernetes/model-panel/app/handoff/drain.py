"""Drain via `llama-router` `/slots` (D3).

The panel is not on the data path, so it cannot count in-flight requests
itself. `/slots` is the only first-party in-flight signal the router
exposes (enabled via the `--slots` arg — see `deployment-router.yaml`).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List

DEFAULT_DRAIN_TIMEOUT_SECONDS = 120
DEFAULT_POLL_INTERVAL_SECONDS = 2

_IDLE_STATE_TOKENS = {"idle", "slot_state_idle", "0"}


class DrainTimeout(Exception):
    """Raised when the router is still busy at the drain budget. The caller
    MUST NOT proceed to scale down — busy at timeout means abort."""


def _slot_busy(slot: Dict[str, Any]) -> bool:
    state = slot.get("state")
    if isinstance(state, str):
        if state.strip().lower() not in _IDLE_STATE_TOKENS:
            return True
    elif isinstance(state, int):
        if state != 0:
            return True
    id_task = slot.get("id_task")
    if id_task not in (None, -1):
        return True
    if slot.get("is_processing"):
        return True
    return False


def slots_idle(slots: List[Dict[str, Any]]) -> bool:
    return not any(_slot_busy(slot) for slot in slots)


def wait_for_drain(
    fetch_slots: Callable[[], List[Dict[str, Any]]],
    timeout: int = DEFAULT_DRAIN_TIMEOUT_SECONDS,
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Poll `fetch_slots()` (llama-router `/slots` JSON, already parsed) until
    every slot is idle, or raise `DrainTimeout` at the budget without ever
    signalling the caller to proceed."""
    deadline = clock() + timeout
    while True:
        slots = fetch_slots()
        if slots_idle(slots):
            return
        if clock() >= deadline:
            raise DrainTimeout(f"router still busy after {timeout}s drain budget")
        sleep(interval)
