"""Regression test for a bug found during live cluster verification (Amendment
4): the Kubernetes Scale subresource omits `spec.replicas` on the wire when
it equals 0 (`omitempty`), so the real API server returns `spec.replicas is
None` — not `0` — for any deployment already scaled to zero. `int(None)`
raised `TypeError`, so the very first `switch_to("cloud")` against the real
cluster failed on the first `_get_replicas` call for `vllm`/`vllm-big-model`/
etc., all committed at `replicas: 0`. The fake K8s API used by the rest of
this suite always defaults to a real `0` int, so this never surfaced without
a live cluster.
"""

from __future__ import annotations

from kubernetes.client.exceptions import ApiException

from app.handoff.steps import _get_replicas, _set_replicas, HandoffContext


def _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects):
    return HandoffContext(
        core_v1=fake_core_v1,
        apps_v1=fake_apps_v1,
        custom_objects_api=fake_custom_objects,
        fetch_router_slots=lambda: [],
        litellm_params_for=lambda target: {"model": f"openai/{target}"},
        codex_shim_client=None,
        preload_probe=None,
        restart_litellm=None,
        namespace="llms",
        drain_timeout=120,
        pod_delete_timeout=300,
        gpu_confirm_timeout=30,
        router_ready_timeout=300,
        poll_interval=0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        state_store=None,
    )


def test_get_replicas_treats_none_as_zero(fake_core_v1, fake_apps_v1, fake_custom_objects):
    # Simulate the real API's omitempty quirk directly: spec.replicas is None.
    fake_apps_v1._replicas["vllm"] = None
    ctx = _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects)

    assert _get_replicas(ctx, "vllm") == 0


def test_get_replicas_treats_404_as_zero(fake_core_v1, fake_apps_v1, fake_custom_objects):
    """`llama-server-q6` has manifests in the repo but is not applied to
    every environment — confirmed live. A deployment that doesn't exist
    cannot be holding the GPU, so a 404 must not abort the whole switch."""

    def _raise_404(name, namespace):
        raise ApiException(status=404, reason="Not Found")

    fake_apps_v1.read_namespaced_deployment_scale = _raise_404
    ctx = _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects)

    assert _get_replicas(ctx, "llama-server-q6") == 0


def test_set_replicas_tolerates_404_on_undo(fake_core_v1, fake_apps_v1, fake_custom_objects):
    def _raise_404(name, namespace, body):
        raise ApiException(status=404, reason="Not Found")

    fake_apps_v1.patch_namespaced_deployment_scale = _raise_404
    ctx = _make_ctx(fake_core_v1, fake_apps_v1, fake_custom_objects)

    _set_replicas(ctx, "llama-server-q6", 1)  # must not raise
