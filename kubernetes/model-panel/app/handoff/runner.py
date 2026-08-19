"""Guarded step sequence with an explicit LIFO undo stack.

Generalizes `switch-model.sh`'s guarded shape (snapshot -> stop consumers ->
mutate -> probe -> restore-on-failure) into an explicit primitive shared by
both switch-to-Cloud and switch-to-Local sequences (see design.md's
Interfaces / Contracts section).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Protocol

logger = logging.getLogger(__name__)


class HandoffError(Exception):
    """Raised when a step fails. `phase` names the step that failed;
    `recoverable` signals whether a retry/repair is meaningful."""

    def __init__(self, phase: str, message: str, recoverable: bool = True):
        super().__init__(message)
        self.phase = phase
        self.message = message
        self.recoverable = recoverable

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.phase}] {self.message}"


class Step(Protocol):
    name: str

    def apply(self, ctx: Any) -> None: ...

    def undo(self, ctx: Any) -> None: ...


@dataclass
class FunctionStep:
    """A `Step` built from two plain callables — used both by real handoff
    steps (`steps.py`) and by fakes in tests."""

    name: str
    apply_fn: Callable[[Any], None]
    undo_fn: Callable[[Any], None] = lambda ctx: None

    def apply(self, ctx: Any) -> None:
        self.apply_fn(ctx)

    def undo(self, ctx: Any) -> None:
        self.undo_fn(ctx)


class StepRunner:
    """Runs an ordered list of `Step`s. On failure, unwinds every
    already-applied step in reverse (LIFO) order via its `undo()`, then
    raises. A step that never applied is never undone, and no step after
    the failing one ever runs (no forced scale)."""

    def __init__(self, steps: List[Step]):
        self._steps = steps
        self._completed: List[Step] = []

    def run(self, ctx: Any) -> None:
        current: Step | None = None
        try:
            for step in self._steps:
                current = step
                step.apply(ctx)
                self._completed.append(step)
        except HandoffError:
            self._unwind(ctx)
            raise
        except Exception as exc:
            self._unwind(ctx)
            phase = current.name if current is not None else "unknown"
            raise HandoffError(phase=phase, message=str(exc), recoverable=True) from exc

    def _unwind(self, ctx: Any) -> None:
        while self._completed:
            step = self._completed.pop()
            try:
                step.undo(ctx)
            except Exception:
                logger.exception("undo failed for step %s", getattr(step, "name", "?"))
