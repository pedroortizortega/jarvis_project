"""Manifest regression test for tasks 13.3/13.4 (D-19 boundary + D-15
dependency): the session-alert signing secret must be mounted in
model-panel's own `deployment.yaml` only, `codex-shim/deployment.yaml`
must stay at zero diff with no webhook reference anywhere, and
`replicas: 1` / `strategy.type: Recreate` (D-15's single-replica,
no-double-fire assumption) must still hold.
"""

from __future__ import annotations

from pathlib import Path

import yaml

MODEL_PANEL_DEPLOYMENT = Path(__file__).resolve().parent.parent / "deployment.yaml"
CODEX_SHIM_DEPLOYMENT = (
    Path(__file__).resolve().parents[2] / "codex-shim" / "deployment.yaml"
)


def _load_deployment(path: Path) -> dict:
    docs = list(yaml.safe_load_all(path.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "Deployment":
            return doc
    raise AssertionError(f"Deployment not found in {path}")


def _container(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def test_model_panel_mounts_webhook_secret_via_secret_key_ref():
    deployment = _load_deployment(MODEL_PANEL_DEPLOYMENT)
    env = _container(deployment).get("env", [])
    by_name = {e["name"]: e for e in env}

    assert "HERMES_WEBHOOK_URL" in by_name
    assert by_name["HERMES_WEBHOOK_URL"].get("value")

    secret_env = by_name.get("MODEL_PANEL_WEBHOOK_SECRET")
    assert secret_env is not None, "MODEL_PANEL_WEBHOOK_SECRET env var missing"
    secret_ref = secret_env.get("valueFrom", {}).get("secretKeyRef")
    assert secret_ref is not None, "MODEL_PANEL_WEBHOOK_SECRET must come from a secretKeyRef"
    assert secret_ref["name"] == "model-panel-webhook"
    assert secret_ref["key"] == "secret"


def test_model_panel_still_single_replica_recreate_strategy():
    deployment = _load_deployment(MODEL_PANEL_DEPLOYMENT)
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"]["type"] == "Recreate"


def test_codex_shim_deployment_has_no_webhook_reference():
    text = CODEX_SHIM_DEPLOYMENT.read_text()
    assert "model-panel-webhook" not in text
    assert "HERMES_WEBHOOK_URL" not in text
    assert "MODEL_PANEL_WEBHOOK_SECRET" not in text
