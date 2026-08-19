"""Integration test for the full alias-drift self-heal wiring found live
(Amendment 5): a routine `kubectl apply -f litellm-config.yaml` reverts the
`qwen3` alias to its file baseline. `GET /api/status` must detect this
(`alias_drift: true`) and trigger exactly one background realign — not one
per poll, since the panel is polled every 2s.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.handoff.state import HandoffState, StateStore
from tests.conftest import FakeAppsV1Api, FakeCoreV1Api, FakeCustomObjectsApi

TOKEN = "test-bearer-token"

DRIFTED_CONFIGMAP_DATA = {
    "config.yaml": (
        "model_list:\n"
        "  - model_name: qwen3\n"
        "    litellm_params:\n"
        "      model: openai/qwen3\n"
        "      api_base: http://vllm.llms.svc.cluster.local:8000/v1\n"
        "      api_key: os.environ/LLAMA_API_KEY\n"
    )
}


def build_app_with_drifted_alias(monkeypatch, restart_calls):
    from app.main import create_app

    core_v1 = FakeCoreV1Api()
    core_v1.seed_configmap("litellm-config", "llms", DRIFTED_CONFIGMAP_DATA)
    apps_v1 = FakeAppsV1Api(replicas={"llama-router": 0}, available={})
    custom_objects_api = FakeCustomObjectsApi()

    state_store = StateStore(core_v1=core_v1)
    # Recorded state says cloud (GPU freed) — but the ConfigMap above is the
    # drifted vllm baseline, not codex-shim.
    state_store.write(HandoffState(mode="cloud", profile=None, phase="idle"))

    monkeypatch.setenv("MODEL_PANEL_AUTH_TOKEN", TOKEN)

    shim_client = type(
        "Shim", (), {"get_session_status": lambda self: {"state": "valid"}}
    )()

    app = create_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        custom_objects_api=custom_objects_api,
        state_store=state_store,
        codex_shim_client=shim_client,
        fetch_router_slots=lambda: [],
        preload_probe=lambda alias: None,
        restart_litellm=lambda: restart_calls.append(True),
        sleep=lambda _s: None,
    )
    return app, core_v1, state_store


def _poll_status(client):
    return client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})


def test_status_poll_detects_and_self_heals_alias_drift(monkeypatch):
    restart_calls: list = []
    app, core_v1, state_store = build_app_with_drifted_alias(monkeypatch, restart_calls)

    with TestClient(app) as client:
        resp = _poll_status(client)
        assert resp.json()["alias_drift"] is True

        # Give the background executor a moment to run the realign.
        for _ in range(50):
            if restart_calls:
                break
            time.sleep(0.02)
        app.state.executor.shutdown(wait=True)

    assert restart_calls == [True], "expected exactly one self-heal restart"

    cm = core_v1.read_namespaced_config_map("litellm-config", "llms")
    from app.handoff.steps import classify_qwen3_alias_target

    assert classify_qwen3_alias_target(cm.data["config.yaml"]) == "cloud"
    assert state_store.read().mode == "cloud"  # unchanged, only the alias was repaired


def test_rapid_repeated_polls_only_trigger_one_heal_attempt(monkeypatch):
    """The panel polls /api/status every 2s — repeated polls within the
    debounce window must not resubmit a realign for each one."""
    restart_calls: list = []
    app, core_v1, state_store = build_app_with_drifted_alias(monkeypatch, restart_calls)

    with TestClient(app) as client:
        for _ in range(5):
            _poll_status(client)
        for _ in range(50):
            if restart_calls:
                break
            time.sleep(0.02)
        app.state.executor.shutdown(wait=True)

    assert restart_calls == [True], (
        f"expected exactly one heal attempt across 5 rapid polls, got {len(restart_calls)}"
    )
