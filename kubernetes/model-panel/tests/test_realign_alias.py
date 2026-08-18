"""Regression tests for a bug found live (Amendment 5): a routine `kubectl
apply -f litellm-config.yaml` reverts the `qwen3` alias to its file
baseline, silently undoing the panel's last live patch. `realign_litellm_alias`
is the guarded, GPU-free self-heal: re-patch the alias to match the
already-recorded `state.mode` and restart LiteLLM — no scaling, no drain,
no KEDA.
"""

from __future__ import annotations

from app.handoff import steps as steps_mod
from app.handoff.state import HandoffState, StateStore
from app.handoff.steps import HandoffContext, classify_qwen3_alias_target


def _drifted_configmap_data() -> dict:
    return {
        "config.yaml": (
            "model_list:\n"
            "  - model_name: qwen3\n"
            "    litellm_params:\n"
            "      model: openai/qwen3\n"
            "      api_base: http://vllm.llms.svc.cluster.local:8000/v1\n"
            "      api_key: os.environ/LLAMA_API_KEY\n"
        )
    }


def _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects, *, state_store, restart_calls):
    return HandoffContext(
        core_v1=fake_core_v1,
        apps_v1=fake_apps_v1,
        custom_objects_api=fake_custom_objects,
        fetch_router_slots=lambda: [],
        litellm_params_for=lambda target: {
            "model": f"openai/{target}-alias",
            "api_base": (
                "http://codex-shim.llms.svc.cluster.local:8080/v1"
                if target == "cloud"
                else "http://llama-router.llms.svc.cluster.local:8080/v1"
            ),
            "api_key": "os.environ/X",
        },
        codex_shim_client=None,
        preload_probe=None,
        restart_litellm=lambda: restart_calls.append(True),
        namespace="llms",
        drain_timeout=120,
        pod_delete_timeout=300,
        gpu_confirm_timeout=30,
        router_ready_timeout=300,
        poll_interval=0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        state_store=state_store,
    )


def test_realign_repatches_drifted_alias_to_match_recorded_mode(
    fake_core_v1, fake_apps_v1, fake_custom_objects
):
    fake_core_v1.seed_configmap(steps_mod.LITELLM_CONFIGMAP_NAME, "llms", _drifted_configmap_data())
    state_store = StateStore(core_v1=fake_core_v1, clock=lambda: 0.0)
    state_store.write(HandoffState(mode="cloud", profile=None, phase="idle"))

    restart_calls: list = []
    ctx = _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects, state_store=state_store, restart_calls=restart_calls)

    final_state = steps_mod.realign_litellm_alias(ctx)

    assert final_state.mode == "cloud"
    assert final_state.phase == "idle"
    cm = fake_core_v1.read_namespaced_config_map(steps_mod.LITELLM_CONFIGMAP_NAME, "llms")
    assert classify_qwen3_alias_target(cm.data["config.yaml"]) == "cloud"
    assert restart_calls == [True]


def test_realign_does_not_change_mode_or_profile():
    """Realign only repairs the ALREADY-recorded mode's alias — it must
    never itself decide a new mode/profile."""
    import inspect

    sig = inspect.signature(steps_mod.realign_litellm_alias)
    assert list(sig.parameters) == ["ctx"], (
        "realign_litellm_alias must not take a target/mode argument — it "
        "always re-asserts whatever state.mode already says"
    )
