"""Write-ahead state ConfigMap client (D6).

`model-panel-state` is written *before* each mutating step and reconciled
against live cluster state on every read — the ConfigMap is a claim, never
trusted alone. This survives pod restart and makes partial switches
detectable (spec: Current-State View / Partial-or-failed state surfaced).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATE_CONFIGMAP_NAME = os.environ.get("MODEL_PANEL_STATE_CONFIGMAP", "model-panel-state")
STATE_NAMESPACE = os.environ.get("MODEL_PANEL_NAMESPACE", "llms")
STATE_DATA_KEY = "state.json"


@dataclass
class HandoffState:
    mode: str = "local"                       # "local" | "cloud"
    profile: Optional[str] = "daily"           # local profile; None while Cloud
    phase: str = "idle"                        # "idle" | "transitioning" | "degraded"
    target: Optional[str] = None               # target of an in-flight/failed switch
    transition_id: Optional[str] = None
    error: Optional[str] = None
    updated_at: Optional[float] = None
    last_known_good: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "HandoffState":
        data = json.loads(raw)
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})


class StateStore:
    """Reads/patches the `model-panel-state` ConfigMap via the K8s API
    client. Constructed lazily (and injectable for tests), same pattern as
    `codex-shim`'s `TokenStore`."""

    def __init__(self, core_v1: Any = None, clock=time.time):
        self._core_v1 = core_v1
        self._clock = clock

    def _client(self) -> Any:
        if self._core_v1 is not None:
            return self._core_v1
        from kubernetes import client, config as k8s_config  # local import: optional dep at import time

        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        self._core_v1 = client.CoreV1Api()
        return self._core_v1

    def read(self) -> HandoffState:
        core_v1 = self._client()
        try:
            cm = core_v1.read_namespaced_config_map(STATE_CONFIGMAP_NAME, STATE_NAMESPACE)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 404:
                return HandoffState()
            raise

        data = getattr(cm, "data", None) or {}
        raw = data.get(STATE_DATA_KEY)
        if not raw:
            return HandoffState()
        try:
            return HandoffState.from_json(raw)
        except Exception:
            logger.warning("model-panel state: failed to parse %s, using defaults", STATE_DATA_KEY)
            return HandoffState()

    def write(self, state: HandoffState) -> HandoffState:
        state.updated_at = self._clock()
        core_v1 = self._client()
        body = {"data": {STATE_DATA_KEY: state.to_json()}}
        core_v1.patch_namespaced_config_map(STATE_CONFIGMAP_NAME, STATE_NAMESPACE, body)
        return state


def reconcile_against_live(
    state: HandoffState,
    *,
    router_replicas: int,
    gpu_pods_present: bool,
    qwen3_alias_target: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare the state ConfigMap's claim against live cluster signals.

    Read-only: never mutates `state` or the cluster. While a switch is
    `transitioning`, drift is not meaningful (the cluster is expected to be
    mid-flight) so this always reports consistent in that phase.

    `qwen3_alias_target` is the live `qwen3` LiteLLM alias's classified
    target ("cloud"/"local"/None for unrecognized), independent of GPU/router
    state — found live (Amendment 5): a routine `kubectl apply -f
    litellm-config.yaml` silently reverts the alias to the file's checked-in
    baseline, which is invisible to the router-replicas/GPU-pods checks
    alone (those stay consistent; only the alias — what Hermes actually
    calls — drifts). Omit the argument (default None) to skip this check,
    e.g. for callers that can't cheaply read the ConfigMap.
    """
    if state.phase == "transitioning":
        return {"drift": False, "consistent": True}

    expected_router_up = state.mode == "local"
    router_matches = (router_replicas >= 1) == expected_router_up
    expected_gpu_pods = state.mode == "local"
    gpu_matches = gpu_pods_present == expected_gpu_pods
    alias_matches = qwen3_alias_target is None or qwen3_alias_target == state.mode
    consistent = router_matches and gpu_matches and alias_matches
    result: Dict[str, Any] = {"drift": not consistent, "consistent": consistent}
    if qwen3_alias_target is not None:
        result["alias_drift"] = not alias_matches
    return result
