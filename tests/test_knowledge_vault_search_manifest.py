"""Manifest security/invariant regression tests for
`knowledge-vault-search-deployment` (PR 1, Phase 3, strict TDD).

Follows the pattern of `tests/test_hindsight_manifest.py`: `unittest.TestCase`
+ `yaml.safe_load_all`, living directly in the enforced root suite
(`python -m unittest discover -s tests`).

Covers the Service/EndpointSlice shape (headless, selector-less, port 8088,
EndpointSlice address `10.42.0.1`, `ready: true`, no Ingress) and the
cross-manifest secret-wiring assertions against
`memory-router-deployment.yaml` (design.md Testing Strategy rows 1-2,
Threat Matrix "Secret handling" row).
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

MCPS_DIR = Path(__file__).resolve().parent.parent / "kubernetes" / "mcps"

ENDPOINTS_FILE = "knowledge-vault-search-endpoints.yaml"
ROUTER_FILE = "memory-router-deployment.yaml"


def _load_all(filename: str) -> list[dict]:
    path = MCPS_DIR / filename
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def _service() -> dict:
    docs = _load_all(ENDPOINTS_FILE)
    return next(doc for doc in docs if doc.get("kind") == "Service")


def _endpoint_slice() -> dict:
    docs = _load_all(ENDPOINTS_FILE)
    return next(doc for doc in docs if doc.get("kind") == "EndpointSlice")


def _memory_router_deployment() -> dict:
    docs = _load_all(ROUTER_FILE)
    return next(doc for doc in docs if doc.get("kind") == "Deployment")


def _memory_router_container() -> dict:
    containers = _memory_router_deployment()["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "expected exactly one container in the pod spec"
    return containers[0]


def _env_list(container: dict) -> list[dict]:
    return container.get("env", [])


def _env_map(container: dict) -> dict:
    return {item["name"]: item.get("value") for item in _env_list(container)}


def _env_secret_refs(container: dict) -> dict:
    """Map env var name -> secretKeyRef dict, for vars sourced from a Secret."""
    refs = {}
    for item in _env_list(container):
        value_from = item.get("valueFrom") or {}
        secret_ref = value_from.get("secretKeyRef")
        if secret_ref:
            refs[item["name"]] = secret_ref
    return refs


class ServiceTests(unittest.TestCase):
    def test_service_is_headless(self):
        # Kubernetes headless Services use the literal string "None" here,
        # not YAML null — assert the exact string k8s itself expects.
        self.assertEqual(_service()["spec"]["clusterIP"], "None")

    def test_service_has_no_selector(self):
        self.assertNotIn("selector", _service()["spec"])

    def test_service_port_and_target_port_8088(self):
        ports = _service()["spec"]["ports"]
        self.assertTrue(
            any(p.get("port") == 8088 and p.get("targetPort") == 8088 for p in ports)
        )


class EndpointSliceTests(unittest.TestCase):
    def test_address_type_ipv4(self):
        self.assertEqual(_endpoint_slice()["addressType"], "IPv4")

    def test_address_is_exactly_10_42_0_1(self):
        endpoints = _endpoint_slice()["endpoints"]
        addresses = [addr for ep in endpoints for addr in ep.get("addresses", [])]
        self.assertEqual(addresses, ["10.42.0.1"])

    def test_ready_true(self):
        endpoints = _endpoint_slice()["endpoints"]
        for ep in endpoints:
            self.assertIs(ep["conditions"]["ready"], True)

    def test_service_name_label_matches_service(self):
        service_name = _service()["metadata"]["name"]
        label = _endpoint_slice()["metadata"]["labels"]["kubernetes.io/service-name"]
        self.assertEqual(label, service_name)


class NoIngressAnywhereTests(unittest.TestCase):
    def test_no_ingress_kind_in_endpoints_manifest(self):
        for doc in _load_all(ENDPOINTS_FILE):
            self.assertNotEqual(doc.get("kind"), "Ingress")


class CrossManifestSecretWiringTests(unittest.TestCase):
    """design.md D-02/D-06, Threat Matrix "Secret handling": memory-router's
    KNOWLEDGE_VAULT_TOKEN must be a secretKeyRef (never an inline value) to
    exactly {knowledge-vault-search-token, search-token}, auth mode must be
    "bearer", and no KNOWLEDGE_VAULT_BASE_URL override may exist."""

    def test_knowledge_vault_token_secret_ref(self):
        refs = _env_secret_refs(_memory_router_container())
        self.assertEqual(
            refs.get("KNOWLEDGE_VAULT_TOKEN"),
            {"name": "knowledge-vault-search-token", "key": "search-token"},
        )

    def test_knowledge_vault_token_has_no_inline_value(self):
        for item in _env_list(_memory_router_container()):
            if item["name"] == "KNOWLEDGE_VAULT_TOKEN":
                self.assertNotIn("value", item)

    def test_knowledge_vault_auth_mode_bearer(self):
        env = _env_map(_memory_router_container())
        self.assertEqual(env.get("KNOWLEDGE_VAULT_AUTH_MODE"), "bearer")

    def test_no_knowledge_vault_base_url_override(self):
        env = _env_map(_memory_router_container())
        self.assertNotIn("KNOWLEDGE_VAULT_BASE_URL", env)


if __name__ == "__main__":
    unittest.main()
