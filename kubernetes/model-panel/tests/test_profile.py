"""RED (9.1-9.4): Local Profile Picker backend (D18/D18a, Amendment 3).

Covers `steps.switch_profile()` (preload-before-alias-patch ordering, undo
behavior, drain-timeout abort) and `POST /api/profile`'s four fail-closed
preconditions with zero-mutation assertions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml
from fastapi.testclient import TestClient

from app.handoff.runner import HandoffError
from app.handoff.state import HandoffState, StateStore
from app.handoff import steps as steps_mod
from tests.conftest import FakeAppsV1Api, FakeCoreV1Api, FakeCustomObjectsApi, make_pod

REAL_LITELLM_CONFIGMAP = (
    Path(__file__).resolve().parents[3] / "kubernetes" / "proxy" / "litellm-config.yaml"
)

TOKEN = "test-bearer-token"


def _real_configmap_data() -> dict:
    docs = list(yaml.safe_load_all(REAL_LITELLM_CONFIGMAP.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "ConfigMap" and doc.get("metadata", {}).get("name") == "litellm-config":
            return doc["data"]
    raise AssertionError("litellm-config ConfigMap not found in fixture file")


def _counter():
    value = [0]

    def _clock():
        value[0] += 1
        return value[0]

    return _clock


class FakeRouterClient:
    """Fake `app.clients.llama_router.LlamaRouterClient` stand-in."""

    def __init__(self, fail_confirm: bool = False, fail_preload: bool = False):
        self.preload_calls: list[str] = []
        self.confirm_calls: list[str] = []
        self._fail_confirm = fail_confirm
        self._fail_preload = fail_preload

    def preload(self, preset: str) -> None:
        if self._fail_preload:
            raise RuntimeError("preload failed")
        self.preload_calls.append(preset)

    def confirm_loaded(self, preset: str) -> bool:
        self.confirm_calls.append(preset)
        if self._fail_confirm:
            raise RuntimeError("confirm failed")
        return True


def _make_ctx(
    fake_core_v1,
    fake_apps_v1,
    fake_custom_objects,
    *,
    fetch_slots=lambda: [{"id_task": -1, "state": 0}],
    router_client=None,
    litellm_params_for=lambda target: {"model": f"openai/{target}"},
    restart_litellm=None,
    drain_timeout=120,
    state_store=None,
):
    ctx = steps_mod.HandoffContext(
        core_v1=fake_core_v1,
        apps_v1=fake_apps_v1,
        custom_objects_api=fake_custom_objects,
        fetch_router_slots=fetch_slots,
        litellm_params_for=litellm_params_for,
        codex_shim_client=None,
        preload_probe=None,
        restart_litellm=restart_litellm,
        namespace="llms",
        drain_timeout=drain_timeout,
        poll_interval=0,
        sleep=lambda s: None,
        clock=_counter(),
        state_store=state_store,
    )
    # router_client is a profile-switch-only dependency, attached separately
    # so the existing HandoffContext dataclass (frozen shape, PR3/PR4) is not
    # widened for a Phase 9-only concern.
    ctx.router_client = router_client  # type: ignore[attr-defined]
    return ctx


def _seed_local(fake_apps_v1, fake_core_v1, profile="daily"):
    fake_apps_v1._replicas[steps_mod.ROUTER_DEPLOYMENT] = 1
    fake_core_v1.seed_configmap(
        steps_mod.LITELLM_CONFIGMAP_NAME,
        "llms",
        _real_configmap_data(),
    )
    fake_core_v1.set_pods("llms", [])


# ---------------------------------------------------------------------------
# 9.1 — preload happens before the alias is patched
# ---------------------------------------------------------------------------


def test_profile_switch_preloads_before_alias_patch(fake_core_v1, fake_apps_v1, fake_custom_objects):
    _seed_local(fake_apps_v1, fake_core_v1)
    store = StateStore(core_v1=fake_core_v1)
    store.write(HandoffState(mode="local", profile="daily", phase="idle"))

    router_client = FakeRouterClient()
    call_order: list[str] = []

    def fake_litellm_params_for(target: str) -> Dict[str, Any]:
        call_order.append("patch_alias")
        return {"model": f"openai/{steps_mod.PROFILE_MODEL_ALIASES['large']}"}

    orig_preload = router_client.preload

    def tracking_preload(preset: str) -> None:
        call_order.append("preload")
        orig_preload(preset)

    router_client.preload = tracking_preload  # type: ignore[assignment]

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        router_client=router_client,
        litellm_params_for=fake_litellm_params_for,
        state_store=store,
    )

    steps_mod.switch_profile("large", ctx)

    assert call_order == ["preload", "patch_alias"]
    assert router_client.preload_calls == ["qwen3.6-27b-q3"]

    cm = fake_core_v1.read_namespaced_config_map(steps_mod.LITELLM_CONFIGMAP_NAME, "llms")
    doc = yaml.safe_load(cm.data["config.yaml"])
    qwen3_entry = next(e for e in doc["model_list"] if e["model_name"] == "qwen3")
    assert qwen3_entry["litellm_params"]["model"] == "openai/qwen3.6-27b-q3"

    final_state = store.read()
    assert final_state.profile == "large"
    assert final_state.phase == "idle"


# ---------------------------------------------------------------------------
# 9.2 — main.py preconditions, zero mutations (table-driven)
# ---------------------------------------------------------------------------


class FakeShimClient:
    def get_session_status(self) -> Dict[str, Any]:
        return {"state": "valid"}


def _build_profile_app(
    *,
    core_v1=None,
    apps_v1=None,
    custom_objects_api=None,
    initial_state=None,
    monkeypatch=None,
    token=TOKEN,
):
    from app.main import create_app

    core_v1 = core_v1 if core_v1 is not None else FakeCoreV1Api()
    apps_v1 = apps_v1 if apps_v1 is not None else FakeAppsV1Api(replicas={"llama-router": 1}, available={"llama-router": 1})
    custom_objects_api = custom_objects_api if custom_objects_api is not None else FakeCustomObjectsApi()

    state_store = StateStore(core_v1=core_v1)
    if initial_state is not None:
        state_store.write(initial_state)
    else:
        state_store.write(HandoffState(mode="local", profile="daily", phase="idle"))

    if monkeypatch is not None and token is not None:
        monkeypatch.setenv("MODEL_PANEL_AUTH_TOKEN", token)
    elif monkeypatch is not None:
        monkeypatch.delenv("MODEL_PANEL_AUTH_TOKEN", raising=False)

    router_client = FakeRouterClient()

    app = create_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        custom_objects_api=custom_objects_api,
        state_store=state_store,
        codex_shim_client=FakeShimClient(),
        fetch_router_slots=lambda: [],
        restart_litellm=lambda: None,
        sleep=lambda _s: None,
        router_client=router_client,
    )
    return app, core_v1, apps_v1, custom_objects_api, state_store, router_client


def auth_headers(token: str = TOKEN) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_profile_precondition_not_local_when_cloud(monkeypatch):
    app, core_v1, apps_v1, custom_objects_api, state_store, router_client = _build_profile_app(
        initial_state=HandoffState(mode="cloud", profile=None, phase="idle"),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/profile", json={"profile": "large"}, headers=auth_headers())
    assert resp.status_code == 409
    assert resp.json()["error"] == "not_local"
    assert apps_v1.calls == []
    assert steps_mod.LITELLM_CONFIGMAP_NAME not in core_v1.patched_configmap_names
    assert router_client.preload_calls == []
    assert router_client.confirm_calls == []


def test_profile_precondition_transition_in_progress(monkeypatch):
    app, core_v1, apps_v1, custom_objects_api, state_store, router_client = _build_profile_app(
        initial_state=HandoffState(mode="local", profile="daily", phase="transitioning", target="cloud"),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/profile", json={"profile": "large"}, headers=auth_headers())
    assert resp.status_code == 409
    assert resp.json()["error"] == "transition_in_progress"
    assert apps_v1.calls == []
    assert steps_mod.LITELLM_CONFIGMAP_NAME not in core_v1.patched_configmap_names
    assert router_client.preload_calls == []
    assert router_client.confirm_calls == []


def test_profile_precondition_invalid_profile(monkeypatch):
    app, core_v1, apps_v1, custom_objects_api, state_store, router_client = _build_profile_app(
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/profile", json={"profile": "xlarge"}, headers=auth_headers())
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_profile"
    assert apps_v1.calls == []
    assert steps_mod.LITELLM_CONFIGMAP_NAME not in core_v1.patched_configmap_names
    assert router_client.preload_calls == []
    assert router_client.confirm_calls == []


def test_profile_precondition_already_active_returns_unchanged(monkeypatch):
    app, core_v1, apps_v1, custom_objects_api, state_store, router_client = _build_profile_app(
        initial_state=HandoffState(mode="local", profile="daily", phase="idle"),
        monkeypatch=monkeypatch,
    )
    with TestClient(app) as client:
        resp = client.post("/api/profile", json={"profile": "daily"}, headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"unchanged": True}
    assert apps_v1.calls == []
    assert steps_mod.LITELLM_CONFIGMAP_NAME not in core_v1.patched_configmap_names
    assert router_client.preload_calls == []
    assert router_client.confirm_calls == []


# ---------------------------------------------------------------------------
# 9.3 — undo restores config, tolerates best-effort preload-undo failure
# ---------------------------------------------------------------------------


def test_profile_switch_undo_restores_config_and_tolerates_preload_undo_failure(
    fake_core_v1, fake_apps_v1, fake_custom_objects
):
    _seed_local(fake_apps_v1, fake_core_v1)
    store = StateStore(core_v1=fake_core_v1)
    store.write(HandoffState(mode="local", profile="daily", phase="idle"))

    original_cm = fake_core_v1.read_namespaced_config_map(steps_mod.LITELLM_CONFIGMAP_NAME, "llms")
    original_data = dict(original_cm.data)

    router_client = FakeRouterClient()

    def failing_restart_litellm() -> None:
        raise RuntimeError("litellm restart failed")

    def fake_litellm_params_for(target: str) -> Dict[str, Any]:
        return {"model": f"openai/{steps_mod.PROFILE_MODEL_ALIASES['large']}"}

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        router_client=router_client,
        litellm_params_for=fake_litellm_params_for,
        restart_litellm=failing_restart_litellm,
        state_store=store,
    )

    with pytest.raises(HandoffError) as excinfo:
        steps_mod.switch_profile("large", ctx)

    assert excinfo.value.phase == "restart_litellm"

    restored_cm = fake_core_v1.read_namespaced_config_map(steps_mod.LITELLM_CONFIGMAP_NAME, "llms")
    assert restored_cm.data["config.yaml"] == original_data["config.yaml"]

    # The preload undo is best-effort — the (fake) re-preload of the prior
    # preset ("qwen3.5-9b") is attempted, but even if it failed it must not
    # escalate past the already-raised HandoffError (routing correctness is
    # already restored by the alias undo).
    assert "qwen3.5-9b" in router_client.preload_calls

    final_state = store.read()
    assert final_state.phase == "degraded"
    assert final_state.profile == "daily"


def test_profile_switch_undo_tolerates_failing_preload_reundo(fake_core_v1, fake_apps_v1, fake_custom_objects):
    _seed_local(fake_apps_v1, fake_core_v1)
    store = StateStore(core_v1=fake_core_v1)
    store.write(HandoffState(mode="local", profile="daily", phase="idle"))

    router_client = FakeRouterClient()
    call_count = {"n": 0}
    orig_preload = router_client.preload

    def preload_fail_on_undo(preset: str) -> None:
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise RuntimeError("re-preload undo failed")
        orig_preload(preset)

    router_client.preload = preload_fail_on_undo  # type: ignore[assignment]

    def fake_litellm_params_for(target: str) -> Dict[str, Any]:
        return {"model": f"openai/{steps_mod.PROFILE_MODEL_ALIASES['large']}"}

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        router_client=router_client,
        litellm_params_for=fake_litellm_params_for,
        restart_litellm=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        state_store=store,
    )

    # Must not raise the re-preload undo's own error — it is logged, not
    # escalated. The original HandoffError from restart_litellm still
    # surfaces.
    with pytest.raises(HandoffError) as excinfo:
        steps_mod.switch_profile("large", ctx)
    assert excinfo.value.phase == "restart_litellm"


# ---------------------------------------------------------------------------
# 9.4 — drain timeout aborts before preload/alias patch, zero mutation
# ---------------------------------------------------------------------------


def test_profile_switch_drain_timeout_aborts(fake_core_v1, fake_apps_v1, fake_custom_objects):
    _seed_local(fake_apps_v1, fake_core_v1)
    store = StateStore(core_v1=fake_core_v1)
    store.write(HandoffState(mode="local", profile="daily", phase="idle"))

    router_client = FakeRouterClient()

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        fetch_slots=lambda: [{"id_task": 1, "state": 1}],  # always busy
        router_client=router_client,
        drain_timeout=5,
        state_store=store,
    )

    with pytest.raises(HandoffError) as excinfo:
        steps_mod.switch_profile("large", ctx)

    assert excinfo.value.phase == "drain"
    assert router_client.preload_calls == []
    assert router_client.confirm_calls == []
    assert steps_mod.LITELLM_CONFIGMAP_NAME not in fake_core_v1.patched_configmap_names

    final_state = store.read()
    assert final_state.phase == "degraded"
    assert final_state.profile == "daily"
