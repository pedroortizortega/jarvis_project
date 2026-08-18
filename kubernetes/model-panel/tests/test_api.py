"""RED/GREEN tests for Phase 8: `app/main.py` HTTP wiring.

Covers bearer auth, `GET /api/status`, `POST /api/switch` (including the
"toggle disabled during in-progress switch" spec scenario and the D17
fail-closed precondition surfaced as 409), `POST /api/repair`, and
`GET /healthz`.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app.handoff.state import HandoffState, StateStore
from tests.conftest import FakeAppsV1Api, FakeCoreV1Api, FakeCustomObjectsApi, make_pod

TOKEN = "test-bearer-token"


class FakeShimClient:
    def __init__(self, status: Dict[str, Any] | None = None, error: Exception | None = None):
        self._status = status
        self._error = error
        self.calls = 0

    def get_session_status(self) -> Dict[str, Any]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._status


def build_app(
    *,
    core_v1: FakeCoreV1Api | None = None,
    apps_v1: FakeAppsV1Api | None = None,
    custom_objects_api: FakeCustomObjectsApi | None = None,
    codex_shim_client: Any = None,
    initial_state: HandoffState | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    token: str | None = TOKEN,
):
    from app.main import create_app

    core_v1 = core_v1 if core_v1 is not None else FakeCoreV1Api()
    apps_v1 = apps_v1 if apps_v1 is not None else FakeAppsV1Api(replicas={"llama-router": 1}, available={"llama-router": 1})
    custom_objects_api = custom_objects_api if custom_objects_api is not None else FakeCustomObjectsApi()

    state_store = StateStore(core_v1=core_v1)
    if initial_state is not None:
        state_store.write(initial_state)

    if monkeypatch is not None and token is not None:
        monkeypatch.setenv("MODEL_PANEL_AUTH_TOKEN", token)
    elif monkeypatch is not None:
        monkeypatch.delenv("MODEL_PANEL_AUTH_TOKEN", raising=False)

    app = create_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        custom_objects_api=custom_objects_api,
        state_store=state_store,
        codex_shim_client=codex_shim_client,
        fetch_router_slots=lambda: [],
        preload_probe=lambda alias: None,
        restart_litellm=lambda: None,
        sleep=lambda _s: None,
    )
    return app, core_v1, apps_v1, custom_objects_api, state_store


def auth_headers(token: str = TOKEN) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# /healthz — no auth
# ---------------------------------------------------------------------------


def test_healthz_no_auth_required(monkeypatch):
    app, *_ = build_app(monkeypatch=monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Bearer auth
# ---------------------------------------------------------------------------


def test_status_rejects_missing_bearer(monkeypatch):
    app, *_ = build_app(monkeypatch=monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/status")
    assert resp.status_code == 401


def test_status_rejects_wrong_bearer(monkeypatch):
    app, *_ = build_app(monkeypatch=monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/status", headers=auth_headers("wrong-token"))
    assert resp.status_code == 401


def test_status_fails_closed_when_token_unset(monkeypatch):
    app, *_ = build_app(monkeypatch=monkeypatch, token=None)
    with TestClient(app) as client:
        resp = client.get("/api/status", headers=auth_headers())
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------


def test_status_steady_state_local(monkeypatch):
    core_v1 = FakeCoreV1Api()
    apps_v1 = FakeAppsV1Api(replicas={"llama-router": 1}, available={"llama-router": 1})
    shim = FakeShimClient(status={"state": "valid", "expires_at": 123.0, "last_refresh": 1.0,
                                   "last_error_code": None, "reason": None})
    app, *_ = build_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        codex_shim_client=shim,
        initial_state=HandoffState(mode="local", profile="daily", phase="idle"),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.get("/api/status", headers=auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "local"
    assert body["profile"] == "daily"
    assert body["phase"] == "idle"
    assert body["transitioning"] is False
    assert body["session"]["state"] == "valid"
    assert shim.calls == 1


def test_status_surfaces_partial_degraded_state(monkeypatch):
    app, *_ = build_app(
        initial_state=HandoffState(
            mode="local", profile="daily", phase="degraded", target="cloud", error="drain timed out"
        ),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.get("/api/status", headers=auth_headers())
    body = resp.json()
    assert body["phase"] == "degraded"
    assert body["error"] == "drain timed out"


def test_status_session_unreachable_is_non_fatal(monkeypatch):
    shim = FakeShimClient(error=RuntimeError("connection refused"))
    app, *_ = build_app(codex_shim_client=shim, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/status", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json()["session"]["state"] == "unreachable"


# ---------------------------------------------------------------------------
# POST /api/switch
# ---------------------------------------------------------------------------


def test_switch_rejects_when_already_transitioning(monkeypatch):
    app, core_v1, apps_v1, *_ = build_app(
        initial_state=HandoffState(mode="local", profile="daily", phase="transitioning", target="cloud"),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/switch", json={"target": "cloud"}, headers=auth_headers())
    assert resp.status_code == 409
    # No new mutation attempted while a switch is already in progress.
    assert "patch_namespaced_deployment_scale" not in apps_v1.calls


def test_switch_to_cloud_fail_closed_on_invalid_session(monkeypatch):
    shim = FakeShimClient(status={"state": "expired_needs_relogin", "reason": "token expired",
                                   "expires_at": None, "last_refresh": None, "last_error_code": "401"})
    core_v1 = FakeCoreV1Api()
    apps_v1 = FakeAppsV1Api(replicas={"llama-router": 1, "vllm": 0, "vllm-big-model": 0,
                                       "vllm-small-model": 0, "llama-server": 0,
                                       "llama-server-q3": 0, "llama-server-q6": 0})
    app, *_ = build_app(core_v1=core_v1, apps_v1=apps_v1, codex_shim_client=shim, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/api/switch", json={"target": "cloud"}, headers=auth_headers())
    assert resp.status_code == 409
    body = resp.json()
    assert body["session_state"] == "expired_needs_relogin"
    # D17: zero K8s mutations before the precondition is satisfied.
    assert apps_v1.calls == []
    assert "patch_namespaced_config_map" not in core_v1.calls


def test_switch_to_cloud_accepted_returns_202(monkeypatch):
    shim = FakeShimClient(status={"state": "valid", "expires_at": None, "last_refresh": None,
                                   "last_error_code": None, "reason": None})
    core_v1 = FakeCoreV1Api()
    core_v1.seed_configmap(
        "litellm-config",
        "llms",
        {"config.yaml": "model_list:\n  - model_name: qwen3\n    litellm_params:\n      model: openai/x\n"},
    )
    apps_v1 = FakeAppsV1Api(
        replicas={
            "llama-router": 1, "vllm": 0, "vllm-big-model": 0, "vllm-small-model": 0,
            "llama-server": 0, "llama-server-q3": 0, "llama-server-q6": 0,
        }
    )
    app, *_ , state_store = build_app(
        core_v1=core_v1, apps_v1=apps_v1, codex_shim_client=shim, monkeypatch=monkeypatch
    )
    with TestClient(app) as client:
        resp = client.post("/api/switch", json={"target": "cloud"}, headers=auth_headers())
        app.state.executor.shutdown(wait=True)
    assert resp.status_code == 202
    assert "transition_id" in resp.json()


def test_switch_rejects_invalid_target(monkeypatch):
    app, *_ = build_app(monkeypatch=monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/api/switch", json={"target": "banana"}, headers=auth_headers())
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/repair
# ---------------------------------------------------------------------------


def test_repair_rejects_when_not_degraded(monkeypatch):
    app, *_ = build_app(
        initial_state=HandoffState(mode="local", profile="daily", phase="idle"), monkeypatch=monkeypatch
    )
    with TestClient(app) as client:
        resp = client.post("/api/repair", headers=auth_headers())
    assert resp.status_code == 400


def test_repair_retries_recorded_target_when_degraded(monkeypatch):
    shim = FakeShimClient(status={"state": "valid", "expires_at": None, "last_refresh": None,
                                   "last_error_code": None, "reason": None})
    core_v1 = FakeCoreV1Api()
    core_v1.seed_configmap(
        "litellm-config",
        "llms",
        {"config.yaml": "model_list:\n  - model_name: qwen3\n    litellm_params:\n      model: openai/x\n"},
    )
    apps_v1 = FakeAppsV1Api(
        replicas={
            "llama-router": 1, "vllm": 0, "vllm-big-model": 0, "vllm-small-model": 0,
            "llama-server": 0, "llama-server-q3": 0, "llama-server-q6": 0,
        }
    )
    app, *_ = build_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        codex_shim_client=shim,
        initial_state=HandoffState(mode="local", profile="daily", phase="degraded", target="cloud", error="boom"),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/repair", headers=auth_headers())
        app.state.executor.shutdown(wait=True)
    assert resp.status_code == 202


def test_repair_dispatches_a_degraded_profile_target_to_profile_switch(monkeypatch):
    """Regression test found by /code-review (Amendment 5): a degraded
    PROFILE switch (D18/D18a) writes state.target as "daily"/"large", not
    "cloud"/"local". Repair must route it through switch_profile(), not
    steps.switch_to() (which raises ValueError for an unrecognized target,
    silently swallowed by run_switch_in_background's bare except after the
    client already got a 202)."""
    import app.main as main_mod

    calls: list[tuple[str, Any]] = []

    def fake_switch_profile(profile: str, ctx: Any) -> None:
        calls.append(("switch_profile", profile))

    def fake_switch_to(target: str, ctx: Any) -> None:
        calls.append(("switch_to", target))
        raise AssertionError("repair must not route a profile target through switch_to")

    monkeypatch.setattr(main_mod.steps, "switch_profile", fake_switch_profile)
    monkeypatch.setattr(main_mod.steps, "switch_to", fake_switch_to)

    app, *_ = build_app(
        initial_state=HandoffState(
            mode="local", profile="daily", phase="degraded", target="large", error="boom"
        ),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/repair", headers=auth_headers())
        app.state.executor.shutdown(wait=True)

    assert resp.status_code == 202
    assert calls == [("switch_profile", "large")], calls
