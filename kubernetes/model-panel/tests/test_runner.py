"""RED (6.1): StepRunner LIFO undo on partial failure — no forced scale."""
from __future__ import annotations

import pytest

from app.handoff.runner import FunctionStep, HandoffError, StepRunner


def test_undo_stack_unwinds_lifo_on_partial_failure():
    calls: list = []

    def make_step(name, fail=False):
        def apply_fn(ctx):
            if fail:
                raise RuntimeError(f"{name} failed")
            calls.append(f"apply:{name}")

        def undo_fn(ctx):
            calls.append(f"undo:{name}")

        return FunctionStep(name=name, apply_fn=apply_fn, undo_fn=undo_fn)

    steps = [
        make_step("scale_down"),
        make_step("pause_keda"),
        make_step("confirm_gpu_free", fail=True),
        make_step("patch_litellm"),  # never reached
    ]

    runner = StepRunner(steps)
    ctx = object()

    with pytest.raises(HandoffError) as excinfo:
        runner.run(ctx)

    assert excinfo.value.phase == "confirm_gpu_free"

    # Only the two successfully-applied steps were undone, in reverse (LIFO)
    # order. The failing step's own undo is never called (it never applied),
    # and the step after it never ran at all (no forced scale).
    assert calls == [
        "apply:scale_down",
        "apply:pause_keda",
        "undo:pause_keda",
        "undo:scale_down",
    ]


def test_successful_run_never_unwinds():
    calls: list = []

    def make_step(name):
        return FunctionStep(
            name=name,
            apply_fn=lambda ctx: calls.append(f"apply:{name}"),
            undo_fn=lambda ctx: calls.append(f"undo:{name}"),
        )

    steps = [make_step("a"), make_step("b")]
    runner = StepRunner(steps)
    runner.run(object())

    assert calls == ["apply:a", "apply:b"]


def test_undo_failure_does_not_mask_original_error():
    def apply_fail(ctx):
        raise RuntimeError("boom")

    def undo_fail(ctx):
        raise RuntimeError("undo also failed")

    def apply_ok(ctx):
        pass

    steps = [
        FunctionStep(name="first", apply_fn=apply_ok, undo_fn=undo_fail),
        FunctionStep(name="second", apply_fn=apply_fail, undo_fn=lambda ctx: None),
    ]
    runner = StepRunner(steps)

    with pytest.raises(HandoffError) as excinfo:
        runner.run(object())

    assert excinfo.value.phase == "second"
