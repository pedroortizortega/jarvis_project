import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "openspec/changes/shared-mcp-services/design.md"
BRAVE = ROOT / "kubernetes/mcps/brave-search-mcp-deployment.yaml"


class SharedMcpContractsTests(unittest.TestCase):
    def test_unresolved_deployment_inputs_remain_documented(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("## Unresolved Deployment Inputs", design)
        self.assertIn("Target CNI and Hermes `hostNetwork` behavior", design)
        self.assertIn("Identity issuer or proxy", design)
        self.assertIn("Immutable artifact store and retention", design)

    def test_new_resources_are_owned_by_mcps_without_changing_brave_bytes(self):
        design = DESIGN.read_text(encoding="utf-8")
        brave_digest = hashlib.sha256(BRAVE.read_bytes()).hexdigest()

        self.assertIn("metadata.namespace: mcps", design)
        self.assertEqual(
            "84effee82870bd6189e1cac984cf324a654c45b0d7e05afc5a32753eadad4c91",
            brave_digest,
        )

    def test_ci_accepts_only_allow_listed_repository_ids(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("CI accepts only allow-listed repository IDs", design)
        self.assertIn("rejects relative and absolute path overrides", design)

    def test_revision_publication_requires_sanitized_atomic_digest_and_fallback(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("sanitized, validated immutable artifact digest", design)
        self.assertIn("atomically promotes only the approved pointer", design)
        self.assertIn("last approved revision remains served on publication failure", design)

    def test_private_identity_is_scoped_and_missing_or_unauthorized_identity_is_denied(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("private TLS with repository-scoped authenticated identity", design)
        self.assertIn("missing or unauthorized identity is denied", design)
        self.assertIn("shared application tokens are forbidden", design)

    def test_serving_hardening_disables_token_automount_and_drops_privileges(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("automountServiceAccountToken: false", design)
        self.assertIn("non-root, read-only root filesystem", design)
        self.assertIn("all Linux capabilities dropped", design)

    def test_codegraph_snapshots_are_query_only_and_repository_isolated(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("snapshot mutation and cross-repository access are denied", design)
        self.assertIn("unsupported outside query operations", design)

    def test_codegraph_adapter_never_mounts_live_sqlite_or_wal_state(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("does not mount `.codegraph` SQLite or WAL state", design)
        self.assertIn("immutable snapshot per repository", design)

    def test_onboarding_fails_closed_until_cni_and_hermes_evidence_exists(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("Onboarding remains blocked without CNI NetworkPolicy evidence", design)
        self.assertIn("Hermes `hostNetwork` connectivity evidence", design)
        self.assertIn("isolation is not claimed", design)

    def test_configured_contract_suite_is_no_longer_described_as_absent(self):
        design = DESIGN.read_text(encoding="utf-8")

        self.assertIn("python -m unittest discover -s tests", design)
        self.assertNotIn("currently fails because root `tests/` does not exist", design)


if __name__ == "__main__":
    unittest.main()
