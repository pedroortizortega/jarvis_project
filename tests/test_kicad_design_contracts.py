import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN_CANDIDATES = (
    ROOT / "openspec/changes/kicad-design-service/design.md",
    ROOT / "openspec/changes/archive/2026-08-30-kicad-design-service/design.md",
)
DESIGN = next((p for p in DESIGN_CANDIDATES if p.exists()), DESIGN_CANDIDATES[0])
BRAVE = ROOT / "kubernetes/mcps/brave-search-mcp-deployment.yaml"
KICAD_DEPLOY = ROOT / "kubernetes/mcps/kicad-mcp-deployment.yaml"
FREECAD_DEPLOY = ROOT / "kubernetes/mcps/freecad-mcp-deployment.yaml"
KICAD_SVC = ROOT / "kubernetes/mcps/kicad-mcp-service.yaml"
FREECAD_SVC = ROOT / "kubernetes/mcps/freecad-mcp-service.yaml"
INGEST_CFG = ROOT / "kubernetes/mcps/sketch-ingest-config.yaml"


class KiCadDesignContractsTests(unittest.TestCase):
    def test_brave_bytes_are_unchanged(self):
        digest = hashlib.sha256(BRAVE.read_bytes()).hexdigest()
        self.assertEqual(
            "84effee82870bd6189e1cac984cf324a654c45b0d7e05afc5a32753eadad4c91",
            digest,
        )

    def test_design_documents_unresolved_deployment_inputs(self):
        design = DESIGN.read_text(encoding="utf-8")
        self.assertIn("## Unresolved Deployment Inputs", design)
        self.assertIn("Target CNI and Hermes `hostNetwork` behavior", design)
        self.assertIn("Identity issuer or proxy", design)

    def test_commit_gate_requires_drc_erc_pass(self):
        design = DESIGN.read_text(encoding="utf-8")
        self.assertIn("A plan is committed only when DRC/ERC pass", design)
        self.assertIn("violations are reported", design)

    def test_low_confidence_never_auto_commits(self):
        design = DESIGN.read_text(encoding="utf-8")
        self.assertIn("low confidence never auto-commits", design)
        self.assertIn("flagged for review", design)

    def test_onboarding_fails_closed_until_cni_and_hermes_evidence(self):
        design = DESIGN.read_text(encoding="utf-8")
        self.assertIn("Onboarding remains blocked without CNI NetworkPolicy evidence", design)
        self.assertIn("Hermes `hostNetwork` connectivity evidence", design)
        self.assertIn("isolation is not claimed", design)

    def test_freecad_runs_headless(self):
        design = DESIGN.read_text(encoding="utf-8")
        self.assertIn("headless", design)
        self.assertIn("rejects an unroutable layout before 3D generation", design)

    def test_manifests_declare_mcps_namespace(self):
        for path in (KICAD_DEPLOY, FREECAD_DEPLOY, KICAD_SVC, FREECAD_SVC, INGEST_CFG):
            self.assertTrue(path.exists(), f"missing {path.name}")
            text = path.read_text(encoding="utf-8")
            self.assertIn("namespace: mcps", text)

    def test_deployments_disable_token_automount_and_run_non_root(self):
        for path in (KICAD_DEPLOY, FREECAD_DEPLOY):
            text = path.read_text(encoding="utf-8")
            self.assertIn("automountServiceAccountToken: false", text)
            self.assertIn("runAsNonRoot: true", text)
            self.assertIn("readOnlyRootFilesystem: true", text)
            self.assertIn('drop: ["ALL"]', text)

    def test_freecad_deployment_is_headless(self):
        text = FREECAD_DEPLOY.read_text(encoding="utf-8")
        self.assertIn("headless", text)

    def test_services_are_clusterip_only(self):
        for path in (KICAD_SVC, FREECAD_SVC):
            text = path.read_text(encoding="utf-8")
            self.assertIn("type: ClusterIP", text)
            self.assertNotIn("type: LoadBalancer", text)
            self.assertNotIn("type: NodePort", text)

    def test_ingest_config_has_confidence_threshold(self):
        text = INGEST_CFG.read_text(encoding="utf-8")
        self.assertIn("confidence_threshold", text)
        self.assertIn("auto_commit", text)


if __name__ == "__main__":
    unittest.main()
