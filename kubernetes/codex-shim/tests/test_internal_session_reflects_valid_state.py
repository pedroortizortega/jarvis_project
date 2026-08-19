"""Regression test for a bug found during live cluster verification (Amendment 4):
`GET /internal/session` reported `state: "not_configured"` for a freshly
provisioned, unexpired Secret, because the endpoint only populated the cache
via `manager._load_cached()` without ever calling `ensure_fresh()` — the only
path that transitions `_state` to "valid". A perfectly healthy token pair was
therefore reported unhealthy forever, until an unrelated proxy request
happened to run `ensure_fresh()`/`refresh()` first. This blocks D17's
fail-closed precondition from ever passing via a passive status poll alone.
"""

from __future__ import annotations

import time

from app.store import TokenStore
from tests.conftest import jwt_with_exp


def build_app(fake_core_v1):
    from app.main import create_app
    from app.session import SessionManager

    store = TokenStore(k8s_core_v1=fake_core_v1)
    # A token that is comfortably unexpired (1 hour out) — no refresh needed.
    access = jwt_with_exp(time.time() + 3600)
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {"access_token": access, "refresh_token": "REFRESH-VALUE"},
    )
    manager = SessionManager(store=store)
    app = create_app(session_manager=manager)
    return app


def test_valid_unexpired_secret_reports_state_valid(fake_core_v1):
    from fastapi.testclient import TestClient

    app = build_app(fake_core_v1)

    with TestClient(app) as client:
        resp = client.get("/internal/session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "valid", (
        "a freshly provisioned, unexpired Secret must report 'valid' on a "
        f"passive status poll alone (no proxy request first); got {body!r}"
    )
    assert body["expires_at"] is not None
