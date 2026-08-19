from __future__ import annotations

from pathlib import Path
from typing import Any

from .runtime import OrchestrationRuntime


_RUNTIME: OrchestrationRuntime | None = None


def register(ctx: Any) -> None:
    global _RUNTIME
    root = Path(__file__).resolve().parents[2]
    _RUNTIME = OrchestrationRuntime(ctx, root)
    ctx.register_hook("pre_llm_call", _RUNTIME.pre_llm_call)
    ctx.register_hook("post_llm_call", _RUNTIME.finish_turn)
    ctx.register_hook("on_session_finalize", _RUNTIME.finish_session)
    ctx.register_hook("on_session_reset", _RUNTIME.finish_session)
    ctx.register_middleware("llm_execution", _RUNTIME.llm_execution)


__all__ = ["register"]
