import tempfile
import unittest
from pathlib import Path

from knowledge_vault.models import Proposal
from knowledge_vault.review import DecisionImporter, PendingProjector
class ReviewTests(unittest.TestCase):
    def proposal(self):
        return Proposal.create("# Pending\nReview me", "review-key", {"agent": "hermes"})

    def test_pending_file_is_visible_and_rejected_decision_is_recorded(self):
        decisions = []
        with tempfile.TemporaryDirectory() as directory:
            proposal = self.proposal()
            path = PendingProjector(directory).project(proposal)
            self.assertIn(proposal.id, path.read_text(encoding="utf-8"))
            path.write_text(
                f"---\nproposal_id: {proposal.id}\nversion: 1\nreviewer: alex\n"
                "decision: rejected\nrationale: Needs sources\n---\n# Pending\n",
                encoding="utf-8",
            )
            decision = DecisionImporter(decisions.append).import_file(path)
        self.assertEqual("rejected", decision.decision)
        self.assertEqual("Needs sources", decisions[0].rationale)
    def test_invalid_version_or_missing_rationale_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.md"
            path.write_text("---\nproposal_id: p\nversion: 2\nreviewer: alex\ndecision: approved\n---", encoding="utf-8")
            with self.assertRaises(ValueError):
                DecisionImporter(lambda _: None).import_file(path)


if __name__ == "__main__":
    unittest.main()
