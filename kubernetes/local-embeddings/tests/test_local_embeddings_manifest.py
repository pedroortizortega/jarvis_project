"""Manifest security/invariant regression tests for `local-embeddings` (PR 3).

`unittest.TestCase` + `yaml.safe_load_all`, per D-15 and the
`model-panel/tests/test_rbac_manifest.py` convention — bridged into the
repo-root enforced suite via `tests/test_local_embeddings.py`. Covers the
security-context, resource-sizing (D-14), offline/zero-egress (D-03),
startup-probe (D-13), RBAC/no-Ingress (D-04), and kustomization invariants
recorded in design.md's Threat Matrix and Architecture Decisions.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

SERVICE_DIR = Path(__file__).resolve().parent.parent


def _load_all(filename: str) -> list[dict]:
    path = SERVICE_DIR / filename
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def _deployment() -> dict:
    docs = _load_all("deployment.yaml")
    return next(doc for doc in docs if doc.get("kind") == "Deployment")


def _pod_spec() -> dict:
    return _deployment()["spec"]["template"]["spec"]


def _container() -> dict:
    containers = _pod_spec()["containers"]
    assert len(containers) == 1, "expected exactly one container in the pod spec"
    return containers[0]


def _env_map(container: dict) -> dict:
    return {item["name"]: item.get("value") for item in container.get("env", [])}


class NoGpuRequestTests(unittest.TestCase):
    def test_no_gpu_in_requests_or_limits(self):
        resources = _container().get("resources", {})
        self.assertNotIn("nvidia.com/gpu", resources.get("requests", {}))
        self.assertNotIn("nvidia.com/gpu", resources.get("limits", {}))


class SecurityContextTests(unittest.TestCase):
    def test_read_only_root_filesystem(self):
        self.assertIs(_container()["securityContext"]["readOnlyRootFilesystem"], True)

    def test_run_as_non_root_and_pinned_uid(self):
        pod_sc = _pod_spec()["securityContext"]
        self.assertIs(pod_sc["runAsNonRoot"], True)
        self.assertEqual(pod_sc["runAsUser"], 10001)

    def test_capabilities_drop_all(self):
        self.assertEqual(_container()["securityContext"]["capabilities"]["drop"], ["ALL"])

    def test_seccomp_profile_runtime_default(self):
        pod_sc = _pod_spec()["securityContext"]
        self.assertEqual(pod_sc["seccompProfile"]["type"], "RuntimeDefault")

    def test_no_privilege_escalation(self):
        self.assertIs(_container()["securityContext"]["allowPrivilegeEscalation"], False)

    def test_automount_service_account_token_false(self):
        self.assertIs(_pod_spec()["automountServiceAccountToken"], False)


class ResourceSizingTests(unittest.TestCase):
    """D-14 (revised 2026-08-22 for multilingual-e5-large): requests
    1/3Gi, limits 3/6Gi, OMP_NUM_THREADS=3."""

    def test_requests_match_d14(self):
        requests = _container()["resources"]["requests"]
        self.assertEqual(requests["cpu"], "1")
        self.assertEqual(requests["memory"], "3Gi")

    def test_limits_match_d14(self):
        limits = _container()["resources"]["limits"]
        self.assertEqual(limits["cpu"], "3")
        self.assertEqual(limits["memory"], "6Gi")

    def test_omp_num_threads_env(self):
        self.assertEqual(_env_map(_container()).get("OMP_NUM_THREADS"), "3")


class OfflineCacheEnvTests(unittest.TestCase):
    """D-03: enforced zero-runtime-egress env vars, plus the /tmp emptyDir
    that catches any stray HF write against readOnlyRootFilesystem."""

    def test_offline_env_vars_present(self):
        env = _env_map(_container())
        self.assertEqual(env.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(env.get("TRANSFORMERS_OFFLINE"), "1")
        self.assertEqual(env.get("HOME"), "/tmp")
        self.assertEqual(env.get("XDG_CACHE_HOME"), "/tmp")

    def test_tmp_emptydir_mounted(self):
        container = _container()
        mounts = container.get("volumeMounts", [])
        tmp_mount = next((m for m in mounts if m.get("mountPath") == "/tmp"), None)
        self.assertIsNotNone(tmp_mount, "expected a volumeMount at /tmp")

        volumes = {v["name"]: v for v in _pod_spec().get("volumes", [])}
        self.assertIn(tmp_mount["name"], volumes)
        self.assertIn("emptyDir", volumes[tmp_mount["name"]])


class StartupProbeTests(unittest.TestCase):
    def test_startup_probe_present(self):
        self.assertIn("startupProbe", _container())


class ServiceTests(unittest.TestCase):
    def test_service_type_cluster_ip(self):
        docs = _load_all("service.yaml")
        service = next(doc for doc in docs if doc.get("kind") == "Service")
        self.assertEqual(service["spec"]["type"], "ClusterIP")


class NoIngressAnywhereTests(unittest.TestCase):
    def test_no_ingress_kind_anywhere_in_directory(self):
        for path in SERVICE_DIR.glob("*.yaml"):
            for doc in yaml.safe_load_all(path.read_text()):
                if doc:
                    self.assertNotEqual(doc.get("kind"), "Ingress", f"found Ingress in {path.name}")


class KustomizationTests(unittest.TestCase):
    def test_lists_exactly_the_three_manifests(self):
        docs = _load_all("kustomization.yaml")
        self.assertEqual(len(docs), 1)
        self.assertEqual(set(docs[0]["resources"]), {"rbac.yaml", "deployment.yaml", "service.yaml"})


if __name__ == "__main__":
    unittest.main()
