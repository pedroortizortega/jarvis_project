"""GPU-free check (D5): zero non-terminal pods requesting `nvidia.com/gpu`
in the `llms` namespace. Host device inspection (`nvidia-smi`) would need
privileges the panel must not have; this is the API-observable equivalent.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, List

GPU_RESOURCE_KEY = "nvidia.com/gpu"
TERMINAL_PHASES = {"Succeeded", "Failed"}

DEFAULT_GPU_WAIT_TIMEOUT_SECONDS = 300
DEFAULT_GPU_POLL_INTERVAL_SECONDS = 5


class GpuNotFreeError(Exception):
    """Raised when the GPU is not confirmed free within the wait budget.
    Caller MUST NOT complete the switch and MUST leave the last
    known-consistent state (spec: GPU Confirmation Timeout Blocks Switch)."""


def _is_terminal(pod: Any) -> bool:
    status = getattr(pod, "status", None)
    phase = getattr(status, "phase", None)
    return phase in TERMINAL_PHASES


def _requests_gpu(pod: Any, gpu_resource: str) -> bool:
    spec = getattr(pod, "spec", None)
    containers = getattr(spec, "containers", None) or []
    for container in containers:
        resources = getattr(container, "resources", None)
        requests = getattr(resources, "requests", None) or {}
        limits = getattr(resources, "limits", None) or {}
        if gpu_resource in requests or gpu_resource in limits:
            return True
    return False


def gpu_free(pods: Iterable[Any], gpu_resource: str = GPU_RESOURCE_KEY) -> bool:
    """True iff no non-terminal pod in `pods` requests `gpu_resource`.

    A pod that is `Running` with a `deletionTimestamp` set (still
    terminating) counts as non-terminal — it still holds the GPU until it
    is actually gone.
    """
    for pod in pods:
        if _is_terminal(pod):
            continue
        if _requests_gpu(pod, gpu_resource):
            return False
    return True


def wait_gpu_free(
    list_pods: Callable[[], List[Any]],
    timeout: int = DEFAULT_GPU_WAIT_TIMEOUT_SECONDS,
    interval: int = DEFAULT_GPU_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    gpu_resource: str = GPU_RESOURCE_KEY,
) -> None:
    """Poll `list_pods()` until `gpu_free()`, or raise `GpuNotFreeError` at
    the budget. Never forces a switch through on timeout."""
    deadline = clock() + timeout
    while True:
        pods = list_pods()
        if gpu_free(pods, gpu_resource):
            return
        if clock() >= deadline:
            raise GpuNotFreeError(f"GPU not confirmed free within {timeout}s")
        sleep(interval)
