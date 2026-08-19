from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

# Allow `import app.xxx` when tests run from the repo root or this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_pod(
    name: str,
    app_label: str,
    *,
    phase: str = "Running",
    gpu: bool = False,
    deletion_timestamp: Optional[str] = None,
) -> SimpleNamespace:
    resources = SimpleNamespace(
        requests={"nvidia.com/gpu": "1"} if gpu else {},
        limits={"nvidia.com/gpu": "1"} if gpu else {},
    )
    container = SimpleNamespace(resources=resources)
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            labels={"app": app_label},
            deletion_timestamp=deletion_timestamp,
        ),
        status=SimpleNamespace(phase=phase),
        spec=SimpleNamespace(containers=[container]),
    )


class _NotFound(Exception):
    status = 404


class FakeCoreV1Api:
    """In-memory stand-in for kubernetes.client.CoreV1Api (pods + configmaps)."""

    def __init__(self, pods: Optional[Dict[str, List[SimpleNamespace]]] = None):
        self._pods: Dict[str, List[SimpleNamespace]] = pods or {}
        self._configmaps: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.calls: List[str] = []
        self.patched_configmap_names: List[str] = []

    def seed_configmap(self, name: str, namespace: str, data: Dict[str, str]) -> None:
        self._configmaps.setdefault(namespace, {})[name] = dict(data)

    def set_pods(self, namespace: str, pods: List[SimpleNamespace]) -> None:
        self._pods[namespace] = pods

    def list_namespaced_pod(self, namespace: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append("list_namespaced_pod")
        return SimpleNamespace(items=list(self._pods.get(namespace, [])))

    def read_namespaced_config_map(self, name: str, namespace: str) -> SimpleNamespace:
        self.calls.append("read_namespaced_config_map")
        ns = self._configmaps.get(namespace, {})
        if name not in ns:
            raise _NotFound(f"configmap {namespace}/{name} not found")
        return SimpleNamespace(data=dict(ns[name]))

    def patch_namespaced_config_map(self, name: str, namespace: str, body: Dict[str, Any]) -> SimpleNamespace:
        self.calls.append("patch_namespaced_config_map")
        self.patched_configmap_names.append(name)
        ns = self._configmaps.setdefault(namespace, {})
        existing = dict(ns.get(name, {}))
        existing.update(body.get("data", {}))
        ns[name] = existing
        return SimpleNamespace(data=dict(existing))


class FakeAppsV1Api:
    """In-memory stand-in for kubernetes.client.AppsV1Api (deployment scale)."""

    def __init__(self, replicas: Optional[Dict[str, int]] = None, available: Optional[Dict[str, int]] = None):
        self._replicas: Dict[str, int] = dict(replicas or {})
        self._available: Dict[str, int] = dict(available or {})
        self.calls: List[str] = []
        self.scale_calls: List[Dict[str, Any]] = []
        self.patch_deployment_calls: List[Dict[str, Any]] = []

    def read_namespaced_deployment_scale(self, name: str, namespace: str) -> SimpleNamespace:
        self.calls.append("read_namespaced_deployment_scale")
        replicas = self._replicas.get(name, 0)
        return SimpleNamespace(spec=SimpleNamespace(replicas=replicas))

    def patch_namespaced_deployment_scale(self, name: str, namespace: str, body: Dict[str, Any]) -> SimpleNamespace:
        self.calls.append("patch_namespaced_deployment_scale")
        replicas = body["spec"]["replicas"]
        self._replicas[name] = replicas
        self.scale_calls.append({"name": name, "namespace": namespace, "replicas": replicas})
        # A scale-up makes the deployment immediately "available" for these fakes
        # unless a test overrides it via `set_available`.
        if replicas > 0 and name not in self._available:
            self._available[name] = replicas
        return SimpleNamespace(spec=SimpleNamespace(replicas=replicas))

    def set_available(self, name: str, available: int) -> None:
        self._available[name] = available

    def read_namespaced_deployment(self, name: str, namespace: str) -> SimpleNamespace:
        self.calls.append("read_namespaced_deployment")
        return SimpleNamespace(status=SimpleNamespace(available_replicas=self._available.get(name, 0)))

    def patch_namespaced_deployment(self, name: str, namespace: str, body: Dict[str, Any]) -> SimpleNamespace:
        self.calls.append("patch_namespaced_deployment")
        self.patch_deployment_calls.append({"name": name, "namespace": namespace, "body": body})
        return SimpleNamespace()


class FakeCustomObjectsApi:
    """In-memory stand-in for kubernetes.client.CustomObjectsApi (KEDA SOs)."""

    def __init__(self) -> None:
        self._objects: Dict[str, Dict[str, Any]] = {}
        self.calls: List[str] = []
        self.patch_calls: List[Dict[str, Any]] = []

    def seed(self, name: str, annotations: Dict[str, str]) -> None:
        self._objects[name] = {"metadata": {"name": name, "annotations": dict(annotations)}}

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        self.calls.append("get_namespaced_custom_object")
        return self._objects.get(name, {"metadata": {"name": name, "annotations": {}}})

    def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body):
        self.calls.append("patch_namespaced_custom_object")
        self.patch_calls.append({"name": name, "body": body})
        obj = self._objects.setdefault(name, {"metadata": {"name": name, "annotations": {}}})
        obj["metadata"].setdefault("annotations", {}).update(
            body.get("metadata", {}).get("annotations", {})
        )
        return obj


@pytest.fixture
def fake_core_v1() -> FakeCoreV1Api:
    return FakeCoreV1Api()


@pytest.fixture
def fake_apps_v1() -> FakeAppsV1Api:
    return FakeAppsV1Api()


@pytest.fixture
def fake_custom_objects() -> FakeCustomObjectsApi:
    return FakeCustomObjectsApi()
