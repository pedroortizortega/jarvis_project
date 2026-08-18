"""Regression test for a bug found during live cluster verification (Amendment 4):
`read_namespaced_deployment_scale` (used by the scale-to-zero/scale-up steps
to read current replicas before patching) issues a GET against the
`deployments/scale` SUBRESOURCE — a distinct RBAC check from plain
`deployments`. The Role originally granted only `patch`/`update` on
`deployments/scale`, so every real switch failed 403 on the very first live
attempt: `cannot get resource "deployments/scale"`. This test parses the
checked-in manifest so a future edit cannot silently drop the verb again
without a real cluster to catch it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RBAC_PATH = Path(__file__).resolve().parent.parent / "rbac.yaml"


def _load_role_rules() -> list[dict]:
    docs = list(yaml.safe_load_all(RBAC_PATH.read_text()))
    for doc in docs:
        if doc and doc.get("kind") == "Role" and doc["metadata"]["name"] == "model-panel":
            return doc["rules"]
    raise AssertionError("model-panel Role not found in rbac.yaml")


def test_deployments_scale_subresource_grants_get():
    rules = _load_role_rules()
    matching = [
        rule
        for rule in rules
        if rule.get("apiGroups") == ["apps"]
        and "deployments/scale" in rule.get("resources", [])
    ]
    assert matching, "expected a rule covering the deployments/scale subresource"
    for rule in matching:
        assert "get" in rule["verbs"], (
            "deployments/scale requires its own 'get' grant — "
            "read_namespaced_deployment_scale is a GET against the "
            f"subresource, not plain 'deployments'; got verbs={rule['verbs']!r}"
        )
