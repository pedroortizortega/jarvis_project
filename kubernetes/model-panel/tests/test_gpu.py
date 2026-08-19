"""RED (part of 6.4): GPU-free check definition (D5)."""
from __future__ import annotations

import pytest

from app.handoff.gpu import GpuNotFreeError, gpu_free, wait_gpu_free
from tests.conftest import make_pod


def test_gpu_free_true_with_no_gpu_pods():
    pods = [make_pod("a", "llama-router", gpu=False)]
    assert gpu_free(pods)


def test_gpu_free_false_with_non_terminal_gpu_pod():
    pods = [make_pod("a", "llama-router", gpu=True, phase="Running")]
    assert not gpu_free(pods)


def test_gpu_free_true_when_gpu_pod_is_terminal():
    pods = [make_pod("a", "llama-router", gpu=True, phase="Succeeded")]
    assert gpu_free(pods)


def test_gpu_pod_still_terminating_counts_as_not_free():
    # Terminating pods are phase=Running with a deletionTimestamp set — they
    # still hold the GPU until they are actually gone.
    pods = [make_pod("a", "llama-router", gpu=True, phase="Running", deletion_timestamp="now")]
    assert not gpu_free(pods)


def test_wait_gpu_free_raises_on_timeout():
    clock_values = iter([0, 5, 31, 31])

    def list_pods():
        return [make_pod("a", "llama-router", gpu=True)]

    with pytest.raises(GpuNotFreeError):
        wait_gpu_free(
            list_pods=list_pods,
            timeout=30,
            interval=5,
            sleep=lambda s: None,
            clock=lambda: next(clock_values),
        )


def test_wait_gpu_free_returns_once_pod_gone():
    calls = iter([
        [make_pod("a", "llama-router", gpu=True)],
        [],
    ])
    wait_gpu_free(
        list_pods=lambda: next(calls),
        timeout=30,
        interval=1,
        sleep=lambda s: None,
        clock=iter([0, 1, 2]).__next__,
    )
