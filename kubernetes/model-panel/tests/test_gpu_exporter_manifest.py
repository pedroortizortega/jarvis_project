"""Manifest regression test: the GPU exporter must NEVER request the
`nvidia.com/gpu` Kubernetes resource. The node has exactly one GPU unit;
requesting it here would exclusively allocate it away from vLLM/
llama-service, breaking the whole point of model-panel's handoff feature
(see design doc decision 3). GPU visibility instead comes from
`runtimeClassName: nvidia` + `NVIDIA_VISIBLE_DEVICES=all`, which does not
touch the device-plugin's resource accounting."""
from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "gpu-exporter.yaml"


def _load_daemonset() -> dict:
    docs = list(yaml.safe_load_all(MANIFEST_PATH.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "DaemonSet":
            return doc
    raise AssertionError("nvidia-gpu-exporter DaemonSet not found in gpu-exporter.yaml")


def test_no_gpu_resource_requested():
    daemonset = _load_daemonset()
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    assert "nvidia.com/gpu" not in resources.get("requests", {})
    assert "nvidia.com/gpu" not in resources.get("limits", {})


def test_uses_nvidia_runtime_class_for_device_visibility():
    daemonset = _load_daemonset()
    pod_spec = daemonset["spec"]["template"]["spec"]
    assert pod_spec.get("runtimeClassName") == "nvidia"
    container = pod_spec["containers"][0]
    env_by_name = {e["name"]: e["value"] for e in container.get("env", [])}
    assert env_by_name.get("NVIDIA_VISIBLE_DEVICES") == "all"
