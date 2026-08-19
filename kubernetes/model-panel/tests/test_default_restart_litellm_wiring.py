"""Regression test for a bug found during live cluster verification (Amendment
4): `create_app()`'s `restart_litellm` parameter always defaulted to `None`,
and nothing in `app/main.py` ever supplied a real implementation in
production — every existing test explicitly injects its own fake callback,
which is exactly why this was never caught. `_restart_litellm_step()` is a
silent no-op when `ctx.restart_litellm is None`, so `litellm-config` got
patched with the new `cloud`/local alias but the live LiteLLM pod kept
serving its stale in-memory `model_list` forever — the first real switch to
Cloud left inference broken (`Connection error`) even though every other
step of the guarded sequence succeeded.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.handoff import steps as steps_mod
from app.handoff.state import StateStore
from tests.conftest import FakeAppsV1Api, FakeCoreV1Api, FakeCustomObjectsApi

REAL_LITELLM_CONFIGMAP = (
    Path(__file__).resolve().parents[3] / "kubernetes" / "proxy" / "litellm-config.yaml"
)


def _real_configmap_data() -> dict:
    docs = list(yaml.safe_load_all(REAL_LITELLM_CONFIGMAP.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "ConfigMap" and doc.get("metadata", {}).get("name") == "litellm-config":
            return doc["data"]
    raise AssertionError("litellm-config ConfigMap not found in fixture file")


def test_create_app_default_restart_litellm_patches_the_deployment(monkeypatch):
    from app.main import create_app

    core_v1 = FakeCoreV1Api()
    core_v1.seed_configmap(steps_mod.LITELLM_CONFIGMAP_NAME, "llms", _real_configmap_data())
    apps_v1 = FakeAppsV1Api(replicas={"llama-router": 1}, available={"llama-router": 1})
    custom_objects_api = FakeCustomObjectsApi()
    state_store = StateStore(core_v1=core_v1)

    monkeypatch.setenv("MODEL_PANEL_AUTH_TOKEN", "test-token")

    # Deliberately do NOT pass restart_litellm — production behaviour.
    app = create_app(
        core_v1=core_v1,
        apps_v1=apps_v1,
        custom_objects_api=custom_objects_api,
        state_store=state_store,
        codex_shim_client=type(
            "Shim", (), {"get_session_status": lambda self: {"state": "valid"}}
        )(),
        fetch_router_slots=lambda: [],
        preload_probe=lambda alias: None,
        sleep=lambda _s: None,
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/switch",
            headers={"Authorization": "Bearer test-token"},
            json={"target": "cloud"},
        )
        assert resp.status_code == 202

        import time

        for _ in range(50):
            status = client.get(
                "/api/status", headers={"Authorization": "Bearer test-token"}
            ).json()
            if not status["transitioning"]:
                break
            time.sleep(0.01)

    assert apps_v1.patch_deployment_calls, (
        "expected the default restart_litellm to patch the litellm Deployment "
        "so it re-reads the just-patched ConfigMap; got no patch_namespaced_deployment "
        f"calls at all. Final status: {status!r}"
    )
    assert apps_v1.patch_deployment_calls[0]["name"] == "litellm"
