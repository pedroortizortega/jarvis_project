"""codex-shim FastAPI app: `/internal/session`, `/healthz`, proxy routes.

`/internal/session` is the D8' status source the panel polls. It — and every
log line this service emits — must never surface token material (2.6).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.codex_auth import AuthError
from app.proxy import build_router
from app.session import SessionManager
from app.store import SecretNotFound, TokenStore

logger = logging.getLogger("codex_shim")


def create_app(
    session_manager: SessionManager | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    app = FastAPI(title="codex-shim", version="0.1.0")

    manager = session_manager or SessionManager(store=TokenStore())
    app.state.session_manager = manager

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        # Liveness only — does NOT assert session validity (design.md
        # `codex-shim` interface table).
        return JSONResponse({"status": "ok"})

    @app.get("/internal/session")
    async def internal_session() -> JSONResponse:
        # `ensure_fresh()` — not a bare cache load — is what actually
        # transitions `_state` to "valid" (or triggers+classifies a refresh
        # when within the skew window). A passive status poll must reflect
        # the real session state on its own, without depending on some
        # unrelated proxy request having run ensure_fresh()/refresh() first.
        try:
            await manager.ensure_fresh()
        except SecretNotFound:
            pass
        except AuthError:
            pass  # state/reason already classified by the session manager
        status = manager.status()
        # Explicitly never include token material — status() already only
        # returns state/expires_at/last_refresh/last_error_code/reason, but
        # assert the shape defensively here so a future field addition to
        # SessionStatus cannot silently leak a token through this endpoint.
        safe_status = {
            "state": status["state"],
            "expires_at": status["expires_at"],
            "last_refresh": status["last_refresh"],
            "last_error_code": status["last_error_code"],
            "reason": status["reason"],
        }
        return JSONResponse(safe_status)

    app.include_router(build_router(manager, http_client=http_client))

    return app


app = create_app()
