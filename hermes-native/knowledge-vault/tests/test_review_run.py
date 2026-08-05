import json
import tempfile
import unittest
from pathlib import Path

from knowledge_vault.models import Proposal
from knowledge_vault.review import DirectoryUnusable, run_review


class RunReviewTests(unittest.TestCase):
    def layout(self, root):
        paths = [Path(root) / name for name in ("spool", "pending", "decisions")]
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        return paths

    def spool_proposal(self, spool, markdown="# Draft\nBody"):
        proposal = Proposal.create(markdown, "review-run", {"agent": "hermes"})
        (spool / f"{proposal.id}.json").write_text(
            json.dumps({"proposal": proposal.__dict__}), encoding="utf-8"
        )
        return proposal

    def test_a_missing_or_unwritable_directory_fails_loudly(self):
        """Creating it silently is how a directory ends up owned by whoever ran
        the command first, leaving the service unable to write to it."""
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            self.assertRaises(DirectoryUnusable, run_review, spool, Path(root) / "absent", decisions)
            pending.chmod(0o500)
            try:
                self.assertRaises(DirectoryUnusable, run_review, spool, pending, decisions)
            finally:
                pending.chmod(0o770)

    def test_spooled_proposals_are_projected_for_obsidian(self):
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            proposal = self.spool_proposal(spool)
            projected, recorded = run_review(spool, pending, decisions)
            self.assertEqual([pending / f"{proposal.id}.md"], projected)
            self.assertEqual([], recorded)
            self.assertIn("# Draft", projected[0].read_text(encoding="utf-8"))

    def test_pending_file_awaiting_a_human_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            proposal = self.spool_proposal(spool)
            run_review(spool, pending, decisions)
            note = pending / f"{proposal.id}.md"
            note.write_text(note.read_text(encoding="utf-8") + "\nreviewer notes\n", encoding="utf-8")
            before = note.read_text(encoding="utf-8")
            projected, recorded = run_review(spool, pending, decisions)
            self.assertEqual([], projected, "an in-progress review was overwritten")
            self.assertEqual([], recorded)
            self.assertEqual(before, note.read_text(encoding="utf-8"))

    def test_decided_file_is_exported_and_removed_from_pending(self):
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            proposal = self.spool_proposal(spool)
            run_review(spool, pending, decisions)
            note = pending / f"{proposal.id}.md"
            note.write_text(
                f"---\nproposal_id: {proposal.id}\nversion: 1\nreviewer: pedro\n"
                "decision: approved\nrationale: Checked sources\n---\n# Draft\nBody\n",
                encoding="utf-8",
            )
            projected, recorded = run_review(spool, pending, decisions)
            self.assertEqual([], projected)
            self.assertEqual([proposal.id], [decision.proposal_id for decision in recorded])
            exported = json.loads((decisions / f"{proposal.id}.json").read_text(encoding="utf-8"))
            self.assertEqual("approved", exported["decision"])
            self.assertEqual("pedro", exported["reviewer"])
            self.assertFalse(note.exists())
            self.assertEqual(
                0o640,
                (decisions / f"{proposal.id}.json").stat().st_mode & 0o777,
                "the control plane runs as another user and must be able to read it",
            )

    def test_malformed_decision_is_reported_and_kept_for_the_reviewer(self):
        failures = []
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            note = pending / "broken.md"
            note.write_text(
                "---\nproposal_id: p1\nversion: 1\nreviewer: pedro\ndecision: maybe\n"
                "rationale: unsure\n---\n# Draft\n",
                encoding="utf-8",
            )
            projected, recorded = run_review(spool, pending, decisions, on_failure=failures.append)
            self.assertEqual([], recorded)
            self.assertTrue(note.exists(), "a malformed decision must stay for the reviewer")
            self.assertEqual([], list(decisions.glob("*.json")))
        self.assertEqual(1, len(failures))
        self.assertIn("maybe", failures[0].reason)


if __name__ == "__main__":
    unittest.main()
