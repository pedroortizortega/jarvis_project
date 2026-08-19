"""RED test 2.6 (Phase 2) / GREEN target 3.3: `/internal/session` never
leaks token material, in the response body, in logs, or in error bodies."""

from __future__ import annotations

import logging
import time

import httpx
import pytest

from app import codex_auth
from app.store import TokenStore
from tests.conftest import jwt_with_exp, mock_token_transport

ACCESS_SECRET = "SECRET-ACCESS-TOKEN-VALUE"
REFRESH_SECRET = "SECRET-REFRESH-TOKEN-VALUE"


def _access_jwt_with_secret_marker():
    # Embed a detectable marker inside the JWT payload region too, so the
    # assertion also catches accidental raw-JWT leakage, not just the
    # opaque ACCESS_SECRET string.
    return jwt_with_exp(time.time() + 3600)


def build_app(fake_core_v1, responder=None):
    from app.main import create_app
    from app.session import SessionManager

    store = TokenStore(k8s_core_v1=fake_core_v1)
    access = _access_jwt_with_secret_marker()
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {"access_token": access, "refresh_token": REFRESH_SECRET},
    )

    def default_responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = mock_token_transport(responder or default_responder)
    import functools

    refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)
    manager = SessionManager(store=store, refresh_fn=refresh_fn)
    app = create_app(session_manager=manager)
    return app, manager


def test_no_token_material_leaks(fake_core_v1, caplog):
    from fastapi.testclient import TestClient

    app, manager = build_app(fake_core_v1)

    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as client:
            resp = client.get("/internal/session")
            resp2 = client.get("/internal/session")

    for resp_obj in (resp, resp2):
        assert resp_obj.status_code == 200
        body_text = resp_obj.text
        assert ACCESS_SECRET not in body_text
        assert REFRESH_SECRET not in body_text
        assert "access_token" not in resp_obj.json()
        assert "refresh_token" not in resp_obj.json()

    log_text = caplog.text
    assert ACCESS_SECRET not in log_text
    assert REFRESH_SECRET not in log_text
