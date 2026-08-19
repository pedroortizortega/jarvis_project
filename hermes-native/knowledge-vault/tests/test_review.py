import tempfile
import unittest
from pathlib import Path

from knowledge_vault.models import Proposal
from knowledge_vault.note import body_of, parse_frontmatter
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
    def test_the_review_fields_merge_into_the_notes_own_frontmatter(self):
        """Wrapping produced two frontmatter blocks: Obsidian parses only the
        first, so the note's own type rendered as body text."""
        proposal = Proposal.create(
            "---\ntype: concept\ntags: [fisica]\n---\n# Titulo\nCuerpo.",
            "merge-key",
            {"agent": "jarvis"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = PendingProjector(directory).project(proposal)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(2, text.count("---\n"), "the note has nested frontmatter")
            fields = parse_frontmatter(text)
            self.assertEqual(proposal.id, fields["proposal_id"])
            self.assertEqual("concept", fields["type"])
            self.assertEqual(["fisica"], fields["tags"])
            self.assertTrue(body_of(text).startswith("# Titulo"))

    def test_the_projected_note_carries_empty_review_fields(self):
        """Typing the key from memory produced `devision`. If the field is
        already there, the reviewer only fills a value."""
        with tempfile.TemporaryDirectory() as directory:
            path = PendingProjector(directory).project(self.proposal())
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
            for key in ("reviewer", "decision", "rationale"):
                self.assertIn(key, fields)
                self.assertEqual("", fields[key])

    def test_projected_file_is_writable_by_the_reviewer_group(self):
        with tempfile.TemporaryDirectory() as directory:
            path = PendingProjector(directory).project(self.proposal())
            self.assertEqual(0o660, path.stat().st_mode & 0o777)

    def test_invalid_version_or_missing_rationale_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.md"
            path.write_text("---\nproposal_id: p\nversion: 2\nreviewer: alex\ndecision: approved\n---", encoding="utf-8")
            with self.assertRaises(ValueError):
                DecisionImporter(lambda _: None).import_file(path)


if __name__ == "__main__":
    unittest.main()
