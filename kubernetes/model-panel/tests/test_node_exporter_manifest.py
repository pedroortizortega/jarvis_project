"""Manifest regression test: node-exporter must only ever get read-only
host filesystem access — this is the whole justification for trusting an
unprivileged in-cluster scrape of host CPU/RAM (see design doc decision 4)."""
from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "node-exporter.yaml"


def _load_daemonset() -> dict:
    docs = list(yaml.safe_load_all(MANIFEST_PATH.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "DaemonSet":
            return doc
    raise AssertionError("node-exporter DaemonSet not found in node-exporter.yaml")


def test_host_volume_mounts_are_read_only():
    daemonset = _load_daemonset()
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    mounts_by_name = {m["name"]: m for m in container["volumeMounts"]}
    volumes_by_name = {v["name"]: v for v in daemonset["spec"]["template"]["spec"]["volumes"]}

    host_mounts = [name for name, v in volumes_by_name.items() if "hostPath" in v]
    assert host_mounts, "expected at least one hostPath volume"
    for name in host_mounts:
        assert mounts_by_name[name].get("readOnly") is True, (
            f"hostPath volume {name!r} must be mounted readOnly"
        )


def test_no_gpu_resource_requested():
    daemonset = _load_daemonset()
    container = daemonset["spec"]["template"]["spec"]["containers"][0]
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    assert "nvidia.com/gpu" not in requests
    assert "nvidia.com/gpu" not in limits
