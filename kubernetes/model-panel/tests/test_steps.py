"""RED (6.2-6.5): ordered switch sequences, LiteLLM alias patch, GPU-free
timeout abort/restore, and the D17 fail-closed precondition wired end to end.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.clients.codex_shim import SwitchBlocked
from app.handoff.runner import HandoffError
from app.handoff.state import STATE_CONFIGMAP_NAME, HandoffState, StateStore
from app.handoff import steps as steps_mod
from tests.conftest import make_pod

REAL_LITELLM_CONFIGMAP = (
    Path(__file__).resolve().parents[3] / "kubernetes" / "proxy" / "litellm-config.yaml"
)


def _real_configmap_data() -> dict:
    """Load the actual litellm-config.yaml and return its ConfigMap `data` map,
    exactly as the K8s API would hand it back."""
    docs = list(yaml.safe_load_all(REAL_LITELLM_CONFIGMAP.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "ConfigMap" and doc.get("metadata", {}).get("name") == "litellm-config":
            return doc["data"]
    raise AssertionError("litellm-config ConfigMap not found in fixture file")


def _stub_shim_client(state="valid"):
    class _Client:
        def get_session_status(self):
            return {"state": state}

    return _Client()


def _make_ctx(
    fake_core_v1,
    fake_apps_v1,
    fake_custom_objects,
    *,
    fetch_slots=lambda: [{"id_task": -1, "state": 0}],
    codex_shim_client=None,
    litellm_params_for=lambda target: {"model": f"openai/{target}-alias"},
    preload_probe=None,
    restart_litellm=None,
    drain_timeout=120,
    pod_delete_timeout=300,
    gpu_confirm_timeout=30,
    router_ready_timeout=300,
    state_store=None,
):
    return steps_mod.HandoffContext(
        core_v1=fake_core_v1,
        apps_v1=fake_apps_v1,
        custom_objects_api=fake_custom_objects,
        fetch_router_slots=fetch_slots,
        litellm_params_for=litellm_params_for,
        codex_shim_client=codex_shim_client,
        preload_probe=preload_probe,
        restart_litellm=restart_litellm,
        namespace="llms",
        drain_timeout=drain_timeout,
        pod_delete_timeout=pod_delete_timeout,
        gpu_confirm_timeout=gpu_confirm_timeout,
        router_ready_timeout=router_ready_timeout,
        poll_interval=0,
        sleep=lambda s: None,
        clock=_counter(),
        state_store=state_store,
    )


def _counter():
    value = [0]

    def _clock():
        value[0] += 1
        return value[0]

    return _clock


def _seed_running_local(fake_apps_v1, fake_core_v1):
    for name in steps_mod.GPU_DEPLOYMENTS:
        fake_apps_v1._replicas[name] = 1 if name == steps_mod.ROUTER_DEPLOYMENT else 0
    fake_core_v1.seed_configmap(
        steps_mod.LITELLM_CONFIGMAP_NAME,
        "llms",
        _real_configmap_data(),
    )
    fake_core_v1.set_pods(
        "llms",
        [make_pod("llama-router-abc", steps_mod.ROUTER_DEPLOYMENT, gpu=True)],
    )


# ---------------------------------------------------------------------------
# 6.2 — drain timeout aborts before any scale-down happens
# ---------------------------------------------------------------------------


def test_drain_timeout_aborts_before_scale(fake_core_v1, fake_apps_v1, fake_custom_objects):
    _seed_running_local(fake_apps_v1, fake_core_v1)

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        fetch_slots=lambda: [{"id_task": 1, "state": 1}],  # always busy
        codex_shim_client=_stub_shim_client("valid"),
        drain_timeout=5,
    )

    with pytest.raises(HandoffError) as excinfo:
        steps_mod.switch_to("cloud", ctx)

    assert excinfo.value.phase == "drain"
    assert fake_apps_v1.scale_calls == []  # never reached scale-to-zero


# ---------------------------------------------------------------------------
# 6.3 — litellm-config patch preserves unrelated entries
# ---------------------------------------------------------------------------


def test_litellm_config_patch_preserves_unrelated_entries():
    data = _real_configmap_data()
    original_callbacks = data["litellm_callbacks.py"]
    original_doc = yaml.safe_load(data["config.yaml"])

    patched = steps_mod.compute_patched_configmap_data(
        data, litellm_params={"model": "openai/cloud-model", "api_base": "http://x", "api_key": "y"}
    )

    # Untouched key, byte-identical.
    assert patched["litellm_callbacks.py"] == original_callbacks

    patched_doc = yaml.safe_load(patched["config.yaml"])
    # Only the qwen3 alias entry changed.
    original_by_name = {e["model_name"]: e for e in original_doc["model_list"]}
    patched_by_name = {e["model_name"]: e for e in patched_doc["model_list"]}
    assert set(original_by_name) == set(patched_by_name)
    for name in original_by_name:
        if name == steps_mod.LITELLM_ALIAS_MODEL_NAME:
            assert patched_by_name[name]["litellm_params"]["model"] == "openai/cloud-model"
        else:
            assert patched_by_name[name] == original_by_name[name]
    assert patched_doc["general_settings"] == original_doc["general_settings"]
    assert patched_doc["litellm_settings"] == original_doc["litellm_settings"]


def test_litellm_config_patch_missing_alias_raises():
    with pytest.raises(HandoffError):
        steps_mod.compute_patched_configmap_data(
            {"config.yaml": "model_list: []\n"}, litellm_params={"model": "x"}
        )


# ---------------------------------------------------------------------------
# 6.4 — GPU-not-free-within-window aborts and restores
# ---------------------------------------------------------------------------


def test_gpu_not_free_within_window_aborts_and_restores(fake_core_v1, fake_apps_v1, fake_custom_objects):
    _seed_running_local(fake_apps_v1, fake_core_v1)
    original_router_replicas = fake_apps_v1._replicas[steps_mod.ROUTER_DEPLOYMENT]
    assert original_router_replicas == 1

    # Pod never actually disappears -> stuck terminating.
    fake_core_v1.set_pods(
        "llms",
        [make_pod("llama-router-abc", steps_mod.ROUTER_DEPLOYMENT, gpu=True, deletion_timestamp="now")],
    )

    store = StateStore(core_v1=fake_core_v1, clock=lambda: 42.0)

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        codex_shim_client=_stub_shim_client("valid"),
        pod_delete_timeout=1,
        state_store=store,
    )

    with pytest.raises(HandoffError):
        steps_mod.switch_to("cloud", ctx)

    # Replicas were restored by the undo stack (scale-to-zero unwound).
    assert fake_apps_v1._replicas[steps_mod.ROUTER_DEPLOYMENT] == original_router_replicas

    # LiteLLM config was never touched — the patch step is after the GPU
    # confirmation step in sequence and never ran. Only the state ConfigMap
    # (transitioning, then degraded) was patched.
    assert steps_mod.LITELLM_CONFIGMAP_NAME not in fake_core_v1.patched_configmap_names
    assert fake_core_v1.patched_configmap_names.count(STATE_CONFIGMAP_NAME) == 2

    final_state = store.read()
    assert final_state.phase == "degraded"
    assert final_state.mode == "local"  # last known-consistent mode preserved
    assert final_state.error


# ---------------------------------------------------------------------------
# 6.5 — fail-closed on non-valid session, wired at switch_to() level
# ---------------------------------------------------------------------------


def test_switch_to_cloud_fail_closed_makes_zero_k8s_calls(fake_core_v1, fake_apps_v1, fake_custom_objects):
    _seed_running_local(fake_apps_v1, fake_core_v1)
    store = StateStore(core_v1=fake_core_v1)

    ctx = _make_ctx(
        fake_core_v1,
        fake_apps_v1,
        fake_custom_objects,
        codex_shim_client=_stub_shim_client("expired_needs_relogin"),
        state_store=store,
    )

    with pytest.raises(SwitchBlocked):
        steps_mod.switch_to("cloud", ctx)

    assert fake_core_v1.calls == []
    assert fake_apps_v1.calls == []
    assert fake_custom_objects.calls == []


# ---------------------------------------------------------------------------
# Successful switch-to-Local always brings up the FIXED default profile
# ---------------------------------------------------------------------------


def test_switch_to_local_always_uses_fixed_default_profile(fake_core_v1, fake_apps_v1, fake_custom_objects):
    fake_apps_v1._replicas[steps_mod.ROUTER_DEPLOYMENT] = 0
    fake_core_v1.seed_configmap(steps_mod.LITELLM_CONFIGMAP_NAME, "llms", _real_configmap_data())
    fake_core_v1.set_pods("llms", [])

    store = StateStore(core_v1=fake_core_v1)
    # Simulate "large" having been active before a prior Cloud switch.
    store.write(HandoffState(mode="cloud", profile=None, phase="idle"))

    ctx = _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects, state_store=store)

    result = steps_mod.switch_to("local", ctx)

    assert result.mode == "local"
    assert result.profile == steps_mod.FIXED_DEFAULT_PROFILE
    assert fake_apps_v1._replicas[steps_mod.ROUTER_DEPLOYMENT] == 1
