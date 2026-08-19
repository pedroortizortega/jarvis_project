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

    def test_a_decided_proposal_is_never_projected_again(self):
        """Rejecting something must not mean seeing it again tomorrow."""
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            proposal = self.spool_proposal(spool)
            run_review(spool, pending, decisions)
            note = pending / f"{proposal.id}.md"
            note.write_text(
                f"---\nproposal_id: {proposal.id}\nversion: 1\nreviewer: pedro\n"
                "decision: rejected\nrationale: La fisica esta invertida\n---\n# Draft\n",
                encoding="utf-8",
            )
            run_review(spool, pending, decisions)
            projected, recorded = run_review(spool, pending, decisions)
            self.assertEqual([], projected, "a decided proposal came back for review")
            self.assertEqual([], recorded)
            self.assertEqual([], list(pending.glob("*.md")))

    def test_the_reviewed_text_is_what_gets_recorded(self):
        """The reviewer approves the text in front of them. Publishing the
        original instead discards their edits without a word."""
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            proposal = self.spool_proposal(spool, "---\ntype: fact\ntags: [jayvis]\n---\n# Draft\nCon error.")
            run_review(spool, pending, decisions)
            note = pending / f"{proposal.id}.md"
            note.write_text(
                f"---\nproposal_id: {proposal.id}\nversion: 1\nreviewer: pedro\n"
                "decision: approved\nrationale: Corregido\ntype: fact\ntags: [jarvis]\n---\n"
                "# Draft\nCorregido a mano.\n",
                encoding="utf-8",
            )
            run_review(spool, pending, decisions)
            recorded = json.loads((decisions / f"{proposal.id}.json").read_text(encoding="utf-8"))
            self.assertIn("Corregido a mano.", recorded["markdown"])
            self.assertIn("tags: [jarvis]", recorded["markdown"])
            self.assertNotIn("reviewer:", recorded["markdown"], "review fields leaked into the note")
            self.assertNotIn("proposal_id:", recorded["markdown"])

    def test_an_untouched_note_with_empty_fields_is_silent(self):
        """Every pending note now carries empty review fields. Reporting them
        as malformed would put the queue permanently in alarm."""
        failures = []
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            self.spool_proposal(spool)
            run_review(spool, pending, decisions, on_failure=failures.append)
            projected, recorded = run_review(spool, pending, decisions, on_failure=failures.append)
        self.assertEqual([], recorded)
        self.assertEqual([], failures, "an untouched note was reported as a problem")

    def test_a_reason_without_a_decision_is_reported(self):
        """The reviewer wrote why but not what: that is a half-finished
        decision, not an undecided note."""
        failures = []
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            note = pending / "half.md"
            note.write_text(
                "---\nproposal_id: p1\nversion: 1\nreviewer: pedro\n"
                "decision: \nrationale: no me convence\n---\n# Draft\n",
                encoding="utf-8",
            )
            run_review(spool, pending, decisions, on_failure=failures.append)
            self.assertTrue(note.exists())
        self.assertEqual(1, len(failures))
        self.assertIn("decision", failures[0].reason)

    def test_an_attempted_decision_missing_its_key_is_reported(self):
        """A typo in `decision` used to read as 'not decided yet': the reviewer
        believed they had answered and the system silently disagreed."""
        failures = []
        with tempfile.TemporaryDirectory() as root:
            spool, pending, decisions = self.layout(root)
            note = pending / "typo.md"
            note.write_text(
                "---\nproposal_id: p1\nversion: 1\nreviewer: pedro\n"
                "devision: approved\nrationale: se me fue una letra\n---\n# Draft\n",
                encoding="utf-8",
            )
            run_review(spool, pending, decisions, on_failure=failures.append)
            self.assertTrue(note.exists(), "the file must stay for the reviewer to fix")
        self.assertEqual(1, len(failures))
        self.assertIn("decision", failures[0].reason)

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


class ApprovalBridgeTests(unittest.TestCase):
    """The bridge stands in for the control plane; it must carry the reviewed
    text through, not the text the agent originally proposed."""

    def test_the_approved_record_carries_the_reviewed_text(self):
        import subprocess
        import sys

        script = Path(__file__).resolve().parent.parent / "scripts" / "approve_locally.py"
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            spool, decisions, approved = root / "spool", root / "decisions", root / "approved"
            for directory in (spool, decisions, approved):
                directory.mkdir()
            proposal = Proposal.create("---\ntype: fact\n---\n# Draft\nOriginal.", "k", {"agent": "x"})
            (spool / "p.json").write_text(json.dumps({"proposal": proposal.__dict__}), encoding="utf-8")
            (decisions / f"{proposal.id}.json").write_text(
                json.dumps({
                    "proposal_id": proposal.id, "version": 1, "reviewer": "pedro",
                    "decision": "approved", "rationale": "ok",
                    "markdown": "---\ntype: fact\n---\n# Draft\nCorregido por el revisor.\n",
                }),
                encoding="utf-8",
            )
            subprocess.run([sys.executable, str(script), str(spool), str(decisions), str(approved)], check=True)
            record = json.loads((approved / f"{proposal.id}.json").read_text(encoding="utf-8"))
            self.assertIn("Corregido por el revisor.", record["proposal"]["markdown"])
            self.assertNotIn("markdown", record["decision"], "the decision kept a field it does not own")


if __name__ == "__main__":
    unittest.main()
