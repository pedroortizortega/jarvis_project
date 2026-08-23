"""Manifest security/invariant regression tests for `hindsight-deployment`
(PR 2, D-08).

Unlike `local-embeddings`, `kubernetes/mcps/` has no service-local `tests/`
directory or `pytest.ini` to bridge from (D-08) — this file lives directly in
the enforced root suite (`python -m unittest discover -s tests`),
`unittest.TestCase` + `yaml.safe_load_all`, following the pattern of
`kubernetes/local-embeddings/tests/test_local_embeddings_manifest.py`.

Covers the security-context, statefulness, storage, secret-wiring,
embedding-provider (D-14), probe (D-11), and no-Ingress (D-01) invariants
recorded in design.md's Testing Strategy and Threat Matrix, plus the
cross-manifest secret-drift assertion against `memory-router-deployment.yaml`
(D-07/D-09).
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

MCPS_DIR = Path(__file__).resolve().parent.parent / "kubernetes" / "mcps"


def _load_all(filename: str) -> list[dict]:
    path = MCPS_DIR / filename
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def _deployment() -> dict:
    docs = _load_all("hindsight-deployment.yaml")
    return next(doc for doc in docs if doc.get("kind") == "Deployment")


def _pod_spec() -> dict:
    return _deployment()["spec"]["template"]["spec"]


def _container() -> dict:
    containers = _pod_spec()["containers"]
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


def _service() -> dict:
    docs = _load_all("hindsight-service.yaml")
    return next(doc for doc in docs if doc.get("kind") == "Service")


def _pvc() -> dict:
    docs = _load_all("hindsight-pvc.yaml")
    return next(doc for doc in docs if doc.get("kind") == "PersistentVolumeClaim")


def _memory_router_deployment() -> dict:
    docs = _load_all("memory-router-deployment.yaml")
    return next(doc for doc in docs if doc.get("kind") == "Deployment")


def _memory_router_container() -> dict:
    containers = _memory_router_deployment()["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "expected exactly one container in the pod spec"
    return containers[0]


class ImageTests(unittest.TestCase):
    def test_image_ref(self):
        self.assertEqual(_container()["image"], "ghcr.io/vectorize-io/hindsight:latest")

    def test_image_pull_policy_always(self):
        self.assertEqual(_container()["imagePullPolicy"], "Always")


class ContainerPortTests(unittest.TestCase):
    def test_container_port_8888(self):
        ports = _container()["ports"]
        self.assertTrue(any(p.get("containerPort") == 8888 for p in ports))


class ServiceTests(unittest.TestCase):
    def test_service_type_cluster_ip(self):
        self.assertEqual(_service()["spec"]["type"], "ClusterIP")

    def test_service_port_and_target_port_8888(self):
        ports = _service()["spec"]["ports"]
        self.assertTrue(
            any(p.get("port") == 8888 and p.get("targetPort") == 8888 for p in ports)
        )

    def test_service_selector_matches_pod_labels(self):
        selector = _service()["spec"]["selector"]
        pod_labels = _deployment()["spec"]["template"]["metadata"]["labels"]
        for key, value in selector.items():
            self.assertEqual(pod_labels.get(key), value)


class StatefulnessTests(unittest.TestCase):
    def test_replicas_is_one(self):
        self.assertEqual(_deployment()["spec"]["replicas"], 1)

    def test_strategy_type_recreate(self):
        self.assertEqual(_deployment()["spec"]["strategy"]["type"], "Recreate")


class PvcTests(unittest.TestCase):
    def test_storage_size(self):
        self.assertEqual(_pvc()["spec"]["resources"]["requests"]["storage"], "10Gi")

    def test_storage_class(self):
        self.assertEqual(_pvc()["spec"]["storageClassName"], "local-path")

    def test_access_mode_rwo(self):
        self.assertEqual(_pvc()["spec"]["accessModes"], ["ReadWriteOnce"])


class SecretWiringTests(unittest.TestCase):
    def test_llm_api_key_secret_ref(self):
        refs = _env_secret_refs(_container())
        self.assertEqual(
            refs.get("HINDSIGHT_API_LLM_API_KEY"),
            {"name": "hindsight-codex-shim-key", "key": "internal-key"},
        )

    def test_tenant_api_key_secret_ref(self):
        refs = _env_secret_refs(_container())
        self.assertEqual(
            refs.get("HINDSIGHT_API_TENANT_API_KEY"),
            {"name": "hindsight-tenant-key", "key": "tenant-api-key"},
        )

    def test_tenant_auth_extension_enabled(self):
        """Bug found via live validation (2026-08-22): HINDSIGHT_API_TENANT_API_KEY
        alone has no effect — an unauthenticated request was accepted with the key
        set but this extension unset. Only setting HINDSIGHT_API_TENANT_EXTENSION
        actually activates enforcement; the key is just the secret it checks."""
        env = _env_map(_container())
        self.assertEqual(
            env.get("HINDSIGHT_API_TENANT_EXTENSION"),
            "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension",
        )

    def test_no_plaintext_secret_value(self):
        secret_env_names = {"HINDSIGHT_API_LLM_API_KEY", "HINDSIGHT_API_TENANT_API_KEY"}
        for item in _env_list(_container()):
            if item["name"] in secret_env_names:
                self.assertNotIn(
                    "value",
                    item,
                    f"{item['name']} must not carry a plaintext value:",
                )
                self.assertIn("valueFrom", item)


class LlmBaseUrlTests(unittest.TestCase):
    def test_llm_base_url_targets_codex_shim(self):
        env = _env_map(_container())
        self.assertEqual(
            env.get("HINDSIGHT_API_LLM_BASE_URL"),
            "http://codex-shim.llms.svc.cluster.local:8080/v1",
        )


class EmbeddingProviderTests(unittest.TestCase):
    """D-14: bundled onnx provider, multilingual-e5-small."""

    def test_embeddings_provider_onnx(self):
        env = _env_map(_container())
        self.assertEqual(env.get("HINDSIGHT_API_EMBEDDINGS_PROVIDER"), "onnx")

    def test_embeddings_onnx_model_id(self):
        env = _env_map(_container())
        self.assertEqual(
            env.get("HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID"),
            "intfloat/multilingual-e5-small",
        )


class SecurityContextTests(unittest.TestCase):
    """D-02: verify, then pin invariants — survive whichever uid is real."""

    def test_run_as_non_root(self):
        pod_sc = _pod_spec()["securityContext"]
        self.assertIs(pod_sc["runAsNonRoot"], True)

    def test_run_as_user_numeric_and_non_zero(self):
        pod_sc = _pod_spec()["securityContext"]
        run_as_user = pod_sc["runAsUser"]
        self.assertIsInstance(run_as_user, int)
        self.assertNotEqual(run_as_user, 0)

    def test_fs_group_matches_run_as_user(self):
        pod_sc = _pod_spec()["securityContext"]
        self.assertEqual(pod_sc["fsGroup"], pod_sc["runAsUser"])

    def test_read_only_root_filesystem(self):
        self.assertIs(_container()["securityContext"]["readOnlyRootFilesystem"], True)

    def test_capabilities_drop_all(self):
        self.assertEqual(_container()["securityContext"]["capabilities"]["drop"], ["ALL"])

    def test_no_privilege_escalation(self):
        self.assertIs(_container()["securityContext"]["allowPrivilegeEscalation"], False)

    def test_automount_service_account_token_false(self):
        self.assertIs(_pod_spec()["automountServiceAccountToken"], False)


class StartupProbeTests(unittest.TestCase):
    def test_startup_probe_failure_threshold_at_least_60(self):
        startup_probe = _container()["startupProbe"]
        self.assertGreaterEqual(startup_probe["failureThreshold"], 60)


class NoIngressAnywhereTests(unittest.TestCase):
    def test_no_ingress_kind_in_hindsight_manifests(self):
        for path in MCPS_DIR.glob("hindsight-*.yaml"):
            for doc in yaml.safe_load_all(path.read_text()):
                if doc:
                    self.assertNotEqual(doc.get("kind"), "Ingress", f"found Ingress in {path.name}")


class CrossManifestSecretDriftTests(unittest.TestCase):
    """D-07/D-09: memory-router's HINDSIGHT_TOKEN must reference the exact
    same {name, key} pair as the Hindsight Deployment's
    HINDSIGHT_API_TENANT_API_KEY, and no HINDSIGHT_BASE_URL override may
    exist (the fixed adapter default already resolves correctly)."""

    def test_hindsight_token_matches_tenant_api_key_secret_ref(self):
        hindsight_refs = _env_secret_refs(_container())
        router_refs = _env_secret_refs(_memory_router_container())

        tenant_ref = hindsight_refs.get("HINDSIGHT_API_TENANT_API_KEY")
        token_ref = router_refs.get("HINDSIGHT_TOKEN")

        self.assertIsNotNone(tenant_ref)
        self.assertIsNotNone(token_ref)
        self.assertEqual(token_ref["name"], tenant_ref["name"])
        self.assertEqual(token_ref["key"], tenant_ref["key"])

    def test_hindsight_auth_mode_bearer(self):
        env = _env_map(_memory_router_container())
        self.assertEqual(env.get("HINDSIGHT_AUTH_MODE"), "bearer")

    def test_no_hindsight_base_url_override(self):
        env = _env_map(_memory_router_container())
        self.assertNotIn("HINDSIGHT_BASE_URL", env)


if __name__ == "__main__":
    unittest.main()
