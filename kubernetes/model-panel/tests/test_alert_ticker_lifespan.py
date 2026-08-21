"""Phase 12: FastAPI `lifespan=` wiring for the session-alert ticker (D-09).

`TestClient(app)` without a `with` block never runs lifespan — the existing
20+ test modules that call routes without entering the context manager stay
completely unaffected by this change. `with TestClient(app)` runs lifespan
startup/shutdown; the ticker itself still fail-closes (D-19) unless both
`HERMES_WEBHOOK_URL` and `MODEL_PANEL_WEBHOOK_SECRET` are set, so even the
*existing* ~90 tests that already use `with TestClient(app)` spawn no
ticker thread because those env vars are never set in their fixtures.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import create_app


def _build_app_with_stub_clients(**overrides):
    from unittest.mock import MagicMock

    core_v1 = MagicMock()
    # `api_status` reads two different ConfigMaps through this same mocked
    # method: the handoff state ConfigMap (`StateStore.read()`, which
    # special-cases a 404 into a default `HandoffState()`) and the litellm
    # alias ConfigMap (`classify_qwen3_alias_target`, which feeds the body
    # through `yaml.safe_load` — a bare MagicMock isn't a str/bytes/stream
    # and PyYAML's reader loops forever pulling "more" from it instead of
    # raising). A 404-shaped exception satisfies both call sites' existing
    # except-clauses without hanging or needing per-name dispatch.
    class _NotFound(Exception):
        status = 404

    core_v1.read_namespaced_config_map.side_effect = _NotFound("not stubbed")
    apps_v1 = MagicMock()
    custom_objects_api = MagicMock()
    codex_shim_client = MagicMock()
    codex_shim_client.get_session_status.return_value = {"state": "valid", "reason": None, "expires_at": None}
    return create_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        custom_objects_api=custom_objects_api,
        codex_shim_client=codex_shim_client,
        **overrides,
    )


def test_testclient_without_with_block_spawns_no_ticker_thread():
    app = _build_app_with_stub_clients()
    client = TestClient(app)  # no `with` — lifespan never runs
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert getattr(app.state, "session_alerter", None) is None


def test_with_testclient_lifespan_start_spawns_ticker_and_shutdown_joins():
    from app.alerts.ticker import SessionAlertTicker

    started = {"flag": False}
    stopped = {"flag": False}

    class ObservingTicker(SessionAlertTicker):
        def start(self):
            started["flag"] = True
            super().start()

        def stop(self, timeout=None):
            stopped["flag"] = True
            super().stop(timeout=timeout)

    ticker = ObservingTicker(
        get_session_status=lambda: {"state": "valid", "reason": None, "expires_at": None},
        webhook_url="http://hermes.example/webhooks/route",
        webhook_secret="shh",
        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no delivery expected")),
        interval_seconds=60.0,  # long enough that no real tick fires during the test
    )

    app = _build_app_with_stub_clients(session_alert_ticker=ticker)

    with TestClient(app) as client:
        assert started["flag"] is True
        assert app.state.session_alerter is ticker.alerter
        # The thread should actually be running (fail-open case: secret+url set).
        assert ticker._thread is not None and ticker._thread.is_alive()
        resp = client.get("/healthz")
        assert resp.status_code == 200

    assert stopped["flag"] is True
    # join() inside stop() should have returned promptly (bounded timeout).
    assert ticker._thread is None or not ticker._thread.is_alive()


def test_existing_routes_unaffected_by_lifespan_zero_diff_on_api_status():
    """D-08 structural guarantee: /api/status is untouched by this feature —
    exercising it inside a `with TestClient` block (ticker fail-closed, no
    env vars set) must behave identically to before."""
    app = _build_app_with_stub_clients()
    import os

    os.environ["MODEL_PANEL_AUTH_TOKEN"] = "test-token"
    with TestClient(app) as client:
        resp = client.get("/api/status", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        body = resp.json()
        assert "session" in body
        assert body["session"]["state"] == "valid"
