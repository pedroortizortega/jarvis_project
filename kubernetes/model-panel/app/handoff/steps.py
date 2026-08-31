"""Ordered switch-to-Cloud / switch-to-Local sequences (D1-D6, D9, D17).

Wires `runner.StepRunner`, `drain.py`, `gpu.py`, `state.py`, and the
`codex_shim` client's fail-closed precondition together into the two
guarded sequences from design.md's Data Flow section:

Switch -> Cloud: check shim session (fail closed, D17) -> state=transitioning
-> ensure KEDA paused -> drain /slots -> scale GPU deployments to 0 -> wait
pod delete -> confirm GPU free -> patch litellm-config -> restart LiteLLM ->
state=cloud.

Switch -> Local: always the FIXED default profile -> scale llama-router to 1
-> wait ready -> preload probe -> patch alias -> restart LiteLLM ->
state=local.

This module builds the *core* sequences only — the HTTP routes that call
`switch_to()` are wired in a later phase (Phase 8).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import yaml
from kubernetes.client.exceptions import ApiException

from app.clients.codex_shim import SwitchBlocked, assert_switch_to_cloud_allowed
from app.handoff.drain import DEFAULT_DRAIN_TIMEOUT_SECONDS, wait_for_drain
from app.handoff.gpu import DEFAULT_GPU_WAIT_TIMEOUT_SECONDS, wait_gpu_free
from app.handoff.runner import FunctionStep, HandoffError, StepRunner
from app.handoff.state import HandoffState, StateStore

logger = logging.getLogger(__name__)

NAMESPACE_DEFAULT = "llms"

# The full set of deployments that must be at 0 replicas while in Cloud mode
# — this is exactly D5's GPU-free set (see design.md's switch-to-Cloud
# sequence and the RBAC table's "8 named deployments", the 8th being
# `litellm` which is scaled by the restart step, not this list).
GPU_DEPLOYMENTS: List[str] = [
    "llama-router",
    "vllm",
    "vllm-big-model",
    "vllm-small-model",
    "llama-server",
    "llama-server-q3",
    "llama-server-q6",
]
ROUTER_DEPLOYMENT = "llama-router"
LITELLM_DEPLOYMENT = "litellm"
KEDA_SCALED_OBJECTS: List[str] = ["vllm-big-model", "vllm-small-model"]
KEDA_PAUSED_ANNOTATION = "autoscaling.keda.sh/paused-replicas"
KEDA_GROUP = "keda.sh"
KEDA_VERSION = "v1alpha1"
KEDA_PLURAL = "scaledobjects"

# switch-to-Local always brings up the FIXED default profile — never the
# previously active one (spec: "Return-to-Local ignores previous profile").
FIXED_DEFAULT_PROFILE = "daily"
FIXED_DEFAULT_MODEL_ALIAS = "qwen3.8-27b-iq2s"  # matches switch-model.sh's `daily` mapping

LITELLM_CONFIGMAP_NAME = "litellm-config"
LITELLM_CONFIGMAP_DATA_KEY = "config.yaml"
LITELLM_ALIAS_MODEL_NAME = "qwen3"  # D1: the one stable alias the panel rewrites

# D18 — the whole profile<->preset mapping, copied verbatim from
# switch-model.sh's mini/daily/large case statement.
PROFILE_MODEL_ALIASES: Dict[str, str] = {
    "mini": "qwen3.5-9b",
    "daily": "qwen3.8-27b-iq2s",
    "large": "qwen3.6-27b-q3",
}


@dataclass
class HandoffContext:
    core_v1: Any
    apps_v1: Any
    custom_objects_api: Any
    fetch_router_slots: Callable[[], List[Dict[str, Any]]]
    litellm_params_for: Callable[[str], Dict[str, Any]]
    codex_shim_client: Optional[Any] = None
    preload_probe: Optional[Callable[[str], None]] = None
    restart_litellm: Optional[Callable[[], None]] = None
    router_client: Optional[Any] = None  # D18: `app.clients.llama_router.LlamaRouterClient`
    namespace: str = NAMESPACE_DEFAULT
    drain_timeout: int = DEFAULT_DRAIN_TIMEOUT_SECONDS
    pod_delete_timeout: int = 300
    gpu_confirm_timeout: int = DEFAULT_GPU_WAIT_TIMEOUT_SECONDS
    router_ready_timeout: int = 300
    poll_interval: float = 2.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    state_store: Optional[StateStore] = None


# ---------------------------------------------------------------------------
# LiteLLM alias patch (D1) — stable-alias indirection, generic over params.
# ---------------------------------------------------------------------------


def patch_litellm_alias_yaml(raw_config_yaml: str, *, litellm_params: Dict[str, Any]) -> str:
    """Rewrite only the `qwen3` `model_list` entry's `litellm_params`. Every
    other top-level key and every other `model_list` entry is left
    unchanged in parsed form."""
    doc = yaml.safe_load(raw_config_yaml) or {}
    model_list = doc.get("model_list") or []
    found = False
    for entry in model_list:
        if entry.get("model_name") == LITELLM_ALIAS_MODEL_NAME:
            entry["litellm_params"] = dict(litellm_params)
            found = True
            break
    if not found:
        raise HandoffError(
            phase="patch_litellm_config",
            message=f"{LITELLM_ALIAS_MODEL_NAME!r} entry not found in litellm-config",
            recoverable=False,
        )
    return yaml.safe_dump(doc, sort_keys=False)


def classify_qwen3_alias_target(raw_config_yaml: str) -> Optional[str]:
    """Read-only: classify what the live `qwen3` alias's `api_base` actually
    points at right now — `"cloud"` (codex-shim), `"local"` (llama-router,
    or anything else including the file's checked-in `vllm` baseline — see
    below), or `None` if the entry/api_base can't be found at all.

    Found live (Amendment 5): a routine `kubectl apply -f
    litellm-config.yaml` reverts `qwen3` to the file's checked-in baseline
    (historically `vllm.llms.svc.cluster.local`), silently undoing whatever
    the panel last live-patched — Hermes then 500s in "cloud" mode with no
    panel-visible signal, since the panel's own state ConfigMap was never
    touched. Anything that isn't `codex-shim` is treated as "local" here
    (not "unknown"): from Hermes's perspective, any non-cloud `api_base`
    only serves real traffic correctly while `state.mode == "local"`, so
    this must still register as drift when the state claims "cloud" —
    returning `None` for the baseline case would silently skip that check.
    """
    try:
        doc = yaml.safe_load(raw_config_yaml) or {}
    except yaml.YAMLError:
        return None
    for entry in doc.get("model_list") or []:
        if entry.get("model_name") == LITELLM_ALIAS_MODEL_NAME:
            api_base = (entry.get("litellm_params") or {}).get("api_base") or ""
            if "codex-shim" in api_base:
                return "cloud"
            if api_base:
                return "local"
            return None
    return None


def compute_patched_configmap_data(
    data: Dict[str, str], *, litellm_params: Dict[str, Any]
) -> Dict[str, str]:
    """Patch only the `config.yaml` key of the `litellm-config` ConfigMap
    `data` map; every other key (e.g. `litellm_callbacks.py`) is copied
    through byte-identical."""
    raw = data.get(LITELLM_CONFIGMAP_DATA_KEY, "")
    new_raw = patch_litellm_alias_yaml(raw, litellm_params=litellm_params)
    patched = dict(data)
    patched[LITELLM_CONFIGMAP_DATA_KEY] = new_raw
    return patched


# ---------------------------------------------------------------------------
# K8s helpers
# ---------------------------------------------------------------------------


def _get_replicas(ctx: HandoffContext, name: str) -> int:
    # GPU_DEPLOYMENTS is a defensive superset of everything that could ever
    # hold the GPU; not every entry is necessarily deployed in every
    # environment (e.g. `llama-server-q6` has manifests in the repo but is
    # not applied to this cluster — confirmed live, design.md Amendment 4).
    # A deployment that does not exist cannot be holding the GPU, so treat
    # 404 the same as "already at 0", not as a fatal error that aborts the
    # whole switch.
    try:
        scale = ctx.apps_v1.read_namespaced_deployment_scale(name, ctx.namespace)
    except ApiException as exc:
        if exc.status == 404:
            return 0
        raise
    # `replicas: 0` is `omitempty` on the wire, so the Scale subresource
    # comes back with `spec.replicas is None` rather than `0` for any
    # deployment already scaled to zero — confirmed live against a real
    # cluster (design.md Amendment 4). `int(None)` raises TypeError.
    return int(scale.spec.replicas or 0)


def _set_replicas(ctx: HandoffContext, name: str, replicas: int) -> None:
    body = {"spec": {"replicas": replicas}}
    try:
        ctx.apps_v1.patch_namespaced_deployment_scale(name, ctx.namespace, body)
    except ApiException as exc:
        if exc.status == 404:
            # Nothing to scale — see _get_replicas' rationale above. Scaling
            # a non-existent deployment "up" on the undo path is likewise a
            # no-op: there was nothing running to restore.
            return
        raise


def _deployment_available(ctx: HandoffContext, name: str) -> int:
    dep = ctx.apps_v1.read_namespaced_deployment(name, ctx.namespace)
    return int(getattr(dep.status, "available_replicas", None) or 0)


def _list_pods_for(ctx: HandoffContext, app_names: List[str]) -> Callable[[], List[Any]]:
    names = set(app_names)

    def _list() -> List[Any]:
        result = ctx.core_v1.list_namespaced_pod(ctx.namespace)
        pods = []
        for pod in result.items:
            labels = getattr(getattr(pod, "metadata", None), "labels", None) or {}
            if labels.get("app") in names:
                pods.append(pod)
        return pods

    return _list


def _list_all_pods(ctx: HandoffContext) -> Callable[[], List[Any]]:
    def _list() -> List[Any]:
        return list(ctx.core_v1.list_namespaced_pod(ctx.namespace).items)

    return _list


# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------


def _drain_step(ctx: HandoffContext) -> FunctionStep:
    def apply_fn(_ctx: HandoffContext) -> None:
        try:
            wait_for_drain(
                fetch_slots=ctx.fetch_router_slots,
                timeout=ctx.drain_timeout,
                interval=ctx.poll_interval,
                sleep=ctx.sleep,
                clock=ctx.clock,
            )
        except Exception as exc:
            raise HandoffError(phase="drain", message=str(exc), recoverable=True) from exc

    return FunctionStep(name="drain", apply_fn=apply_fn)


def _ensure_keda_paused_step() -> FunctionStep:
    """D4: ensure the ScaledObjects are paused; never unpauses. Idempotent
    both directions, so `undo` is a no-op."""

    def apply_fn(ctx: HandoffContext) -> None:
        for name in KEDA_SCALED_OBJECTS:
            obj = ctx.custom_objects_api.get_namespaced_custom_object(
                KEDA_GROUP, KEDA_VERSION, ctx.namespace, KEDA_PLURAL, name
            )
            annotations = (obj.get("metadata", {}) or {}).get("annotations", {}) or {}
            if annotations.get(KEDA_PAUSED_ANNOTATION) != "0":
                ctx.custom_objects_api.patch_namespaced_custom_object(
                    KEDA_GROUP,
                    KEDA_VERSION,
                    ctx.namespace,
                    KEDA_PLURAL,
                    name,
                    {"metadata": {"annotations": {KEDA_PAUSED_ANNOTATION: "0"}}},
                )

    return FunctionStep(name="ensure_keda_paused", apply_fn=apply_fn)


def _scale_to_zero_step() -> FunctionStep:
    prior_replicas: Dict[str, int] = {}

    def apply_fn(ctx: HandoffContext) -> None:
        for name in GPU_DEPLOYMENTS:
            prior_replicas[name] = _get_replicas(ctx, name)
        for name in GPU_DEPLOYMENTS:
            _set_replicas(ctx, name, 0)

    def undo_fn(ctx: HandoffContext) -> None:
        for name, replicas in prior_replicas.items():
            _set_replicas(ctx, name, replicas)

    return FunctionStep(name="scale_to_zero", apply_fn=apply_fn, undo_fn=undo_fn)


def _wait_pods_deleted_step() -> FunctionStep:
    def apply_fn(ctx: HandoffContext) -> None:
        list_pods = _list_pods_for(ctx, GPU_DEPLOYMENTS)
        try:
            wait_gpu_free(
                list_pods=lambda: list_pods(),
                timeout=ctx.pod_delete_timeout,
                interval=ctx.poll_interval,
                sleep=ctx.sleep,
                clock=ctx.clock,
            )
        except Exception as exc:
            raise HandoffError(phase="wait_pod_delete", message=str(exc), recoverable=True) from exc

    return FunctionStep(name="wait_pod_delete", apply_fn=apply_fn)


def _confirm_gpu_free_step() -> FunctionStep:
    def apply_fn(ctx: HandoffContext) -> None:
        list_pods = _list_all_pods(ctx)
        try:
            wait_gpu_free(
                list_pods=list_pods,
                timeout=ctx.gpu_confirm_timeout,
                interval=ctx.poll_interval,
                sleep=ctx.sleep,
                clock=ctx.clock,
            )
        except Exception as exc:
            raise HandoffError(phase="confirm_gpu_free", message=str(exc), recoverable=True) from exc

    return FunctionStep(name="confirm_gpu_free", apply_fn=apply_fn)


def _patch_litellm_config_step(*, target: str) -> FunctionStep:
    prior_data: Dict[str, str] = {}

    def apply_fn(ctx: HandoffContext) -> None:
        try:
            cm = ctx.core_v1.read_namespaced_config_map(LITELLM_CONFIGMAP_NAME, ctx.namespace)
            data = dict(getattr(cm, "data", None) or {})
            prior_data.clear()
            prior_data.update(data)
            patched = compute_patched_configmap_data(
                data, litellm_params=ctx.litellm_params_for(target)
            )
            ctx.core_v1.patch_namespaced_config_map(
                LITELLM_CONFIGMAP_NAME, ctx.namespace, {"data": patched}
            )
        except HandoffError:
            raise
        except Exception as exc:
            raise HandoffError(phase="patch_litellm_config", message=str(exc), recoverable=True) from exc

    def undo_fn(ctx: HandoffContext) -> None:
        if prior_data:
            ctx.core_v1.patch_namespaced_config_map(
                LITELLM_CONFIGMAP_NAME, ctx.namespace, {"data": prior_data}
            )

    return FunctionStep(name="patch_litellm_config", apply_fn=apply_fn, undo_fn=undo_fn)


def _restart_litellm_step() -> FunctionStep:
    def apply_fn(ctx: HandoffContext) -> None:
        if ctx.restart_litellm is not None:
            try:
                ctx.restart_litellm()
            except Exception as exc:
                raise HandoffError(phase="restart_litellm", message=str(exc), recoverable=True) from exc

    return FunctionStep(name="restart_litellm", apply_fn=apply_fn)


def _scale_router_up_step() -> FunctionStep:
    prior_replicas: Dict[str, int] = {}

    def apply_fn(ctx: HandoffContext) -> None:
        prior_replicas["value"] = _get_replicas(ctx, ROUTER_DEPLOYMENT)
        _set_replicas(ctx, ROUTER_DEPLOYMENT, 1)

    def undo_fn(ctx: HandoffContext) -> None:
        if "value" in prior_replicas:
            _set_replicas(ctx, ROUTER_DEPLOYMENT, prior_replicas["value"])

    return FunctionStep(name="scale_router_up", apply_fn=apply_fn, undo_fn=undo_fn)


def _wait_router_ready_step() -> FunctionStep:
    def apply_fn(ctx: HandoffContext) -> None:
        deadline = ctx.clock() + ctx.router_ready_timeout
        while True:
            if _deployment_available(ctx, ROUTER_DEPLOYMENT) >= 1:
                return
            if ctx.clock() >= deadline:
                raise HandoffError(
                    phase="wait_router_ready",
                    message=f"llama-router not ready within {ctx.router_ready_timeout}s",
                    recoverable=True,
                )
            ctx.sleep(ctx.poll_interval)

    return FunctionStep(name="wait_router_ready", apply_fn=apply_fn)


def _preload_probe_step() -> FunctionStep:
    def apply_fn(ctx: HandoffContext) -> None:
        if ctx.preload_probe is not None:
            try:
                ctx.preload_probe(FIXED_DEFAULT_MODEL_ALIAS)
            except Exception as exc:
                raise HandoffError(phase="preload_probe", message=str(exc), recoverable=True) from exc

    return FunctionStep(name="preload_probe", apply_fn=apply_fn)


def _preload_profile_step(*, target_preset: str, prior_preset: str) -> FunctionStep:
    """D18: preload the target preset on `llama-router` BEFORE the alias is
    patched (so no client request is left waiting on a cold model load —
    spec: "Target profile is loaded before traffic is repointed"). Undo is
    a best-effort re-preload of the *previous* preset (D18a) — its failure
    is logged, never escalated, since the alias undo already restores
    routing correctness; only the next request's latency suffers."""

    def apply_fn(ctx: HandoffContext) -> None:
        if ctx.router_client is None:
            return
        try:
            ctx.router_client.preload(target_preset)
            ctx.router_client.confirm_loaded(target_preset)
        except Exception as exc:
            raise HandoffError(phase="preload_profile", message=str(exc), recoverable=True) from exc

    def undo_fn(ctx: HandoffContext) -> None:
        if ctx.router_client is None:
            return
        try:
            ctx.router_client.preload(prior_preset)
        except Exception:
            logger.exception(
                "model-panel: best-effort re-preload undo failed for %s", prior_preset
            )

    return FunctionStep(name="preload_profile", apply_fn=apply_fn, undo_fn=undo_fn)


# ---------------------------------------------------------------------------
# Ordered sequences
# ---------------------------------------------------------------------------


def build_switch_to_cloud_steps(ctx: HandoffContext) -> List[FunctionStep]:
    return [
        _drain_step(ctx),
        _ensure_keda_paused_step(),
        _scale_to_zero_step(),
        _wait_pods_deleted_step(),
        _confirm_gpu_free_step(),
        _patch_litellm_config_step(target="cloud"),
        _restart_litellm_step(),
    ]


def build_switch_to_local_steps(ctx: HandoffContext) -> List[FunctionStep]:
    return [
        _ensure_keda_paused_step(),
        _scale_router_up_step(),
        _wait_router_ready_step(),
        _preload_probe_step(),
        _patch_litellm_config_step(target="local"),
        _restart_litellm_step(),
    ]


def build_switch_profile_steps(
    ctx: HandoffContext, *, target_preset: str, prior_preset: str
) -> List[FunctionStep]:
    """D18/D18a: drain -> preload target preset -> confirm loaded -> patch
    the `qwen3` alias -> restart LiteLLM. No pod is scaled and no GPU pod
    churns — the router swaps the loaded model in place."""
    return [
        _drain_step(ctx),
        _preload_profile_step(target_preset=target_preset, prior_preset=prior_preset),
        _patch_litellm_config_step(target=target_preset),
        _restart_litellm_step(),
    ]


def build_realign_alias_steps(ctx: HandoffContext, *, target: str) -> List[FunctionStep]:
    """Found live (Amendment 5): a routine `kubectl apply -f
    litellm-config.yaml` reverts the `qwen3` alias to the file's checked-in
    baseline, silently undoing whatever mode the panel last enacted — no GPU
    state changed, so no GPU/drain/KEDA step belongs here. This is a cheap,
    idempotent, safe-to-repeat re-assertion of the ALREADY-recorded
    `state.mode`, not a new decision."""
    return [
        _patch_litellm_config_step(target=target),
        _restart_litellm_step(),
    ]


def realign_litellm_alias(ctx: HandoffContext) -> HandoffState:
    """Self-heal alias drift (see `classify_qwen3_alias_target`): re-patch
    the `qwen3` alias to match the already-recorded `state.mode` and restart
    LiteLLM. Does not change `mode`/`profile`/`last_known_good` — the
    recorded state was already correct; only the live ConfigMap had
    drifted. Guarded the same way as any other mutating sequence: written
    ahead as `transitioning`, `degraded` (with the failing reason) on
    failure so it surfaces through the normal repair path."""
    state_store = ctx.state_store
    prior_state = state_store.read() if state_store is not None else HandoffState()
    transition_id = str(uuid.uuid4())

    if state_store is not None:
        state_store.write(
            HandoffState(
                mode=prior_state.mode,
                profile=prior_state.profile,
                phase="transitioning",
                target=prior_state.mode,
                transition_id=transition_id,
                last_known_good=prior_state.last_known_good,
            )
        )

    runner = StepRunner(build_realign_alias_steps(ctx, target=prior_state.mode))
    try:
        runner.run(ctx)
    except HandoffError as exc:
        if state_store is not None:
            state_store.write(
                HandoffState(
                    mode=prior_state.mode,
                    profile=prior_state.profile,
                    phase="degraded",
                    target=prior_state.mode,
                    transition_id=transition_id,
                    error=f"alias realign failed: {exc}",
                    last_known_good=prior_state.last_known_good,
                )
            )
        raise

    final_state = HandoffState(
        mode=prior_state.mode,
        profile=prior_state.profile,
        phase="idle",
        target=None,
        transition_id=None,
        error=None,
        last_known_good=prior_state.last_known_good,
    )
    if state_store is not None:
        state_store.write(final_state)
    return final_state


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def switch_to(target: str, ctx: HandoffContext) -> HandoffState:
    """Run the guarded switch-to-`target` sequence.

    For `target == "cloud"`, the D17 fail-closed precondition (the shim's
    `/internal/session` must report `valid`/`expiring_soon`) is checked
    FIRST — before the state ConfigMap is even written, let alone any other
    K8s call. Any failure of a later step unwinds the undo stack, writes
    `phase=degraded` with the failing reason, and preserves the last
    known-consistent `mode`/`profile` for the UI's retry/repair action.
    """
    if target not in ("cloud", "local"):
        raise ValueError(f"unknown switch target: {target!r}")

    if target == "cloud":
        assert_switch_to_cloud_allowed(ctx.codex_shim_client)

    state_store = ctx.state_store
    transition_id = str(uuid.uuid4())
    prior_state = HandoffState()

    if state_store is not None:
        prior_state = state_store.read()
        state_store.write(
            HandoffState(
                mode=prior_state.mode,
                profile=prior_state.profile,
                phase="transitioning",
                target=target,
                transition_id=transition_id,
                last_known_good=prior_state.last_known_good,
            )
        )

    steps = (
        build_switch_to_cloud_steps(ctx)
        if target == "cloud"
        else build_switch_to_local_steps(ctx)
    )
    runner = StepRunner(steps)

    try:
        runner.run(ctx)
    except HandoffError as exc:
        if state_store is not None:
            state_store.write(
                HandoffState(
                    mode=prior_state.mode,
                    profile=prior_state.profile,
                    phase="degraded",
                    target=target,
                    transition_id=transition_id,
                    error=str(exc),
                    last_known_good=prior_state.last_known_good,
                )
            )
        raise

    new_mode = target
    new_profile = FIXED_DEFAULT_PROFILE if target == "local" else None
    final_state = HandoffState(
        mode=new_mode,
        profile=new_profile,
        phase="idle",
        target=None,
        transition_id=None,
        error=None,
        last_known_good={"mode": new_mode, "profile": new_profile},
    )
    if state_store is not None:
        state_store.write(final_state)
    return final_state


def switch_profile(profile: str, ctx: HandoffContext) -> HandoffState:
    """Run the guarded profile-switch sequence (D18/D18a): drain -> preload
    the target preset -> confirm loaded -> patch the `qwen3` alias ->
    restart LiteLLM -> state `profile=<target>`.

    Preconditions (`mode == "local"`, no transition in progress, valid
    profile, not-already-active) are the caller's responsibility (checked
    synchronously in `main.py`'s `POST /api/profile` handler before this is
    ever invoked, same split as `switch_to()`'s D17 check) — this function
    only validates the profile name itself, since it is also called
    directly in tests.
    """
    if profile not in PROFILE_MODEL_ALIASES:
        raise ValueError(f"unknown profile: {profile!r}")

    target_preset = PROFILE_MODEL_ALIASES[profile]

    state_store = ctx.state_store
    transition_id = str(uuid.uuid4())
    prior_state = HandoffState()
    prior_profile = FIXED_DEFAULT_PROFILE

    if state_store is not None:
        prior_state = state_store.read()
        prior_profile = prior_state.profile or FIXED_DEFAULT_PROFILE
        state_store.write(
            HandoffState(
                mode=prior_state.mode,
                profile=prior_state.profile,
                phase="transitioning",
                target=profile,
                transition_id=transition_id,
                last_known_good=prior_state.last_known_good,
            )
        )

    prior_preset = PROFILE_MODEL_ALIASES.get(prior_profile, FIXED_DEFAULT_MODEL_ALIAS)

    steps = build_switch_profile_steps(ctx, target_preset=target_preset, prior_preset=prior_preset)
    runner = StepRunner(steps)

    try:
        runner.run(ctx)
    except HandoffError as exc:
        if state_store is not None:
            state_store.write(
                HandoffState(
                    mode=prior_state.mode,
                    profile=prior_state.profile,
                    phase="degraded",
                    target=profile,
                    transition_id=transition_id,
                    error=str(exc),
                    last_known_good=prior_state.last_known_good,
                )
            )
        raise

    final_state = HandoffState(
        mode="local",
        profile=profile,
        phase="idle",
        target=None,
        transition_id=None,
        error=None,
        last_known_good={"mode": "local", "profile": profile},
    )
    if state_store is not None:
        state_store.write(final_state)
    return final_state
