import tempfile
import unittest
from pathlib import Path

from knowledge_vault.decide import decide
from knowledge_vault.note import parse_frontmatter

PENDING = (
    "---\nproposal_id: p1\nversion: 1\ntype: infra-fact\ntags: [k3s]\n---\n"
    "# Un solo storage class\n\nCuerpo verificado.\n"
)


class DecideTests(unittest.TestCase):
    def pending(self, root):
        directory = Path(root) / "pending"
        directory.mkdir()
        (directory / "p1.md").write_text(PENDING, encoding="utf-8")
        return directory

    def test_it_records_the_decision_in_the_note(self):
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            path = decide("p1", "approved", "jarvis apruebo la nota", pending, reviewer="pedro")
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual("approved", fields["decision"])
            self.assertEqual("pedro", fields["reviewer"])
            self.assertEqual("jarvis apruebo la nota", fields["rationale"])

    def test_it_records_who_asked_for_it(self):
        """An approval an agent typed on your behalf must be told apart from
        one you wrote yourself, months later and without this conversation."""
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            path = decide("p1", "approved", "apruebo subir la nota", pending,
                          reviewer="pedro", source="telegram")
            self.assertEqual("telegram", parse_frontmatter(path.read_text(encoding="utf-8"))["source"])

    def test_the_note_keeps_its_own_fields_and_body(self):
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            text = decide("p1", "approved", "ok", pending).read_text(encoding="utf-8")
            fields = parse_frontmatter(text)
            self.assertEqual("infra-fact", fields["type"])
            self.assertEqual(["k3s"], fields["tags"])
            self.assertIn("Cuerpo verificado.", text)

    def test_it_refuses_a_decision_it_does_not_understand(self):
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            with self.assertRaises(ValueError):
                decide("p1", "maybe", "no estoy seguro", pending)

    def test_it_refuses_an_empty_rationale(self):
        """Recording why costs one sentence now and saves an archaeology
        session later."""
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            with self.assertRaises(ValueError):
                decide("p1", "approved", "   ", pending)

    def test_it_refuses_a_proposal_that_is_not_waiting(self):
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            with self.assertRaises(FileNotFoundError):
                decide("no-existe", "approved", "ok", pending)

    def test_it_refuses_to_decide_twice(self):
        with tempfile.TemporaryDirectory() as root:
            pending = self.pending(root)
            decide("p1", "approved", "ok", pending)
            with self.assertRaises(ValueError):
                decide("p1", "rejected", "me arrepenti", pending)


if __name__ == "__main__":
    unittest.main()
