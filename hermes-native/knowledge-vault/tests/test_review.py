import tempfile
import unittest
from pathlib import Path

from knowledge_vault.review import PENDING_FIELDS, _reviewed_note

PENDING_NOTE = (
    "---\ntype: concept\ntags: [fisica]\nreviewer: pedro\ndecision: approved\n"
    "rationale: Verificado\nidempotency_key: abc123\n---\n# Titulo\nCuerpo.\n"
)


class ReviewedNoteTests(unittest.TestCase):
    """`_reviewed_note()` is what promotion publishes: the reviewer's text,
    minus the fields that belong to the review process, not the note."""

    def test_pending_fields_are_stripped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p.md"
            path.write_text(PENDING_NOTE, encoding="utf-8")
            published = _reviewed_note(path)
            for field in PENDING_FIELDS:
                self.assertNotIn(f"{field}:", published, f"{field} leaked into the published note")

    def test_the_notes_own_fields_and_body_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p.md"
            path.write_text(PENDING_NOTE, encoding="utf-8")
            published = _reviewed_note(path)
            self.assertIn("type: concept", published)
            self.assertIn("tags: [fisica]", published)
            self.assertIn("# Titulo", published)
            self.assertIn("Cuerpo.", published)

    def test_pending_fields_is_the_exact_stripped_set(self):
        self.assertEqual(("reviewer", "decision", "rationale", "idempotency_key"), PENDING_FIELDS)


if __name__ == "__main__":
    unittest.main()
